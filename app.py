import streamlit as st
import pandas as pd
from datetime import datetime
import calendar
from math import inf

st.set_page_config(page_title="Nurse Roster • Weighted Capacity", layout="wide")

st.title("🩺 三班制排班｜大量人員 + 區間需求 + 必休/想休 + 能力權重")
st.caption("固定班別（D/E/N），人員可設定 capacity（能力/負擔係數）與 weekly_cap（每週最多上班天）；必休(硬)、想休(軟)；每日需求用最少/最多區間；可選同日跨班平衡。")

# ===== Helpers =====
ORDER = ["D", "E", "N"]
SHIFT = {"D": {"start": 8, "end": 16}, "E": {"start": 16, "end": 24}, "N": {"start": 0, "end": 8}, "O": {}}

def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6

def week_index(day: int) -> int:
    if day <= 7: return 1
    if day <= 14: return 2
    if day <= 21: return 3
    if day <= 28: return 4
    return 5

def rest_ok(prev_code: str, next_code: str) -> bool:
    if prev_code in (None, "", "O") or next_code in (None, "", "O"):
        return True
    s1, e1 = SHIFT[prev_code]["start"], SHIFT[prev_code]["end"]
    s2, e2 = SHIFT[next_code]["start"], SHIFT[next_code]["end"]
    rest = s2 - e1
    if rest < 0: rest += 24
    return rest >= 11

def normalize_id(x) -> str:
    if pd.isna(x): return ""
    return str(x).strip()

def seed_demand_df(y, m,
                   wdD_min=2, wdD_max=3, sunD_min=3, sunD_max=4,
                   wdE_min=1, wdE_max=2, sunE_min=1, sunE_max=2,
                   wdN_min=1, wdN_max=2, sunN_min=1, sunN_max=2):
    rows = []
    for d in range(1, days_in_month(y, m) + 1):
        sun = is_sunday(y, m, d)
        rows.append({
            "day": d,
            "D_min": int(sunD_min if sun else wdD_min), "D_max": int(sunD_max if sun else wdD_max),
            "E_min": int(sunE_min if sun else wdE_min), "E_max": int(sunE_max if sun else wdE_max),
            "N_min": int(sunN_min if sun else wdN_min), "N_max": int(sunN_max if sun else wdN_max),
        })
    return pd.DataFrame(rows)

# ===== Core scheduling (fixed roles, weighted by capacity) =====
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df):
    days = days_in_month(year, month)

    # --- roles: id, shift, capacity(float), weekly_cap(int/None) ---
    tmp = roles_df.copy()
    tmp["id"] = tmp["id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(lambda s: s if s in ("D","E","N") else "")
    tmp = tmp[tmp["id"].astype(str).str.len() > 0]
    tmp = tmp[tmp["shift"].isin(["D","E","N"])]

    # capacity default 1.0
    if "capacity" not in tmp.columns: tmp["capacity"] = 1.0
    tmp["capacity"] = pd.to_numeric(tmp["capacity"], errors="coerce").fillna(1.0).clip(lower=0.05)

    # weekly_cap allow blank
    if "weekly_cap" not in tmp.columns: tmp["weekly_cap"] = ""
    def to_cap(x):
        try:
            v = int(float(x))
            return v if v >= 0 else None
        except:
            return None
    tmp["weekly_cap"] = tmp["weekly_cap"].apply(to_cap)

    role_map   = {r.id: r.shift for r in tmp.itertuples(index=False)}
    capa_map   = {r.id: float(r.capacity) for r in tmp.itertuples(index=False)}
    wcap_map   = {r.id: (None if r.weekly_cap is None else int(r.weekly_cap)) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # --- must off / wish off maps ---
    def build_date_map(df):
        m = {nid: set() for nid in id_list}
        if df is None or df.empty: return m
        for r in df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            try: dt = pd.to_datetime(getattr(r, "date", ""))
            except: continue
            if nid in m and dt.year == year and dt.month == month:
                m[nid].add(int(dt.day))
        return m

    must_map = build_date_map(must_off_df)
    wish_map = build_date_map(wish_off_df)

    # --- demand map: day -> shift -> (min,max) ---
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min), int(r.D_max)),
                     "E": (int(r.E_min), int(r.E_max)),
                     "N": (int(r.N_min), int(r.N_max))}

    # --- init structures ---
    sched = {nid: {d: "" for d in range(1, days+1)} for nid in id_list}
    assigned_total = {nid: 0 for nid in id_list}  # 工作日累計（不含 O）

    # weekly count helper
    def week_assigned(nid, w):
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, days+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    # mark must-off
    for nid in id_list:
        for d in must_map[nid]:
            if 1 <= d <= days:
                sched[nid][d] = "O"

    # candidate picking weighted by capacity
    def pick_candidates(d, s, need):
        pool = []
        wk = week_index(d)
        for nid in id_list:
            if role_map[nid] != s: continue
            if sched[nid][d] != "": continue  # O or already scheduled
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            # wish_off penalty: prefer not to break wishes
            wished_penalty = 1 if d in wish_map[nid] else 0
            # weekly cap check
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap:
                continue
            # weighted load = assigned_total / capacity -> 小者優先
            weight = capa_map.get(nid, 1.0)
            wl = assigned_total[nid] / max(0.05, weight)
            pool.append((wished_penalty, wl, nid))
        pool.sort()
        chosen = [nid for (_,_,nid) in pool[:need]]
        return chosen

    # main loop: first reach min, then up to max (weighted)
    for d in range(1, days+1):
        for s in ORDER:
            mn, mx = demand.get(d, {}).get(s, (0,0))
            if mn > 0:
                chosen = pick_candidates(d, s, mn)
                for nid in chosen:
                    sched[nid][d] = s
                    assigned_total[nid] += 1
            cur = sum(1 for nid in id_list if sched[nid][d] == s)
            if cur < mx:
                more = pick_candidates(d, s, mx - cur)
                for nid in more:
                    sched[nid][d] = s
                    assigned_total[nid] += 1
        # others -> O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, capa_map, wcap_map

def cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand, role_map, capa_map):
    """同日跨班：把 >max 或可動的人挪到 <min 的班；仍檢查 11h；優先移動 capacity 較大的。"""
    days = days_in_month(year, month)
    for d in range(1, days+1):
        actual = {s: sum(1 for nid in id_list if sched[nid][d] == s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}
        maxs = {s: demand.get(d,{}).get(s,(0,0))[1] for s in ORDER}

        changed = True
        while changed:
            changed = False
            shortages = [(s, mins[s] - actual[s]) for s in ORDER if actual[s] < mins[s]]
            if not shortages: break
            shortages.sort(key=lambda x: -x[1])

            surplus = [(s, actual[s] - maxs[s]) for s in ORDER if actual[s] > maxs[s]]
            # 若沒有明顯 >max 的班，也可嘗試從其他班借 1 人（但不可低於對方 min）
            search_src = [s for s,_ in surplus] + [s for s in ORDER]

            for tgt, need in shortages:
                if need <= 0: continue
                for src in search_src:
                    if src == tgt: continue
                    if actual.get(src,0) <= mins.get(src,0):  # 保護 src 不低於 min
                        continue
                    candidates = [nid for nid in id_list if sched[nid][d] == src]
                    feasible = [nid for nid in candidates if rest_ok(sched[nid].get(d-1,""), tgt) and rest_ok(tgt, sched[nid].get(d+1,""))]
                    if not feasible: continue
                    # 先移動 capacity 大的人，對整體滿足度更有效
                    feasible.sort(key=lambda nid: (-capa_map.get(nid,1.0), nid))
                    mover = feasible[0]
                    sched[mover][d] = tgt
                    actual[src] -= 1
                    actual[tgt] += 1
                    changed = True
                    need -= 1
                    if actual[tgt] >= mins[tgt]: break
    return sched

# ===== UI =====
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（區間）")
    wdD_min = st.number_input("平日 白 最少", 0, 200, 2)
    wdD_max = st.number_input("平日 白 最多", 0, 200, 3)
    wdE_min = st.number_input("平日 小夜 最少", 0, 200, 1)
    wdE_max = st.number_input("平日 小夜 最多", 0, 200, 2)
    wdN_min = st.number_input("平日 大夜 最少", 0, 200, 1)
    wdN_max = st.number_input("平日 大夜 最多", 0, 200, 2)

    sunD_min = st.number_input("週日 白 最少", 0, 200, 3)
    sunD_max = st.number_input("週日 白 最多", 0, 200, 4)
    sunE_min = st.number_input("週日 小夜 最少", 0, 200, 1)
    sunE_max = st.number_input("週日 小夜 最多", 0, 200, 2)
    sunN_min = st.number_input("週日 大夜 最少", 0, 200, 1)
    sunN_max = st.number_input("週日 大夜 最多", 0, 200, 2)

    allow_cross = st.checkbox("允許同日跨班平衡（最後一步）", value=True)

st.subheader("👥 人員班別（可大量輸入；ID 可中英文；capacity 小者分配較少）")
example_rows = []
for i in range(1, 13):
    example_rows.append({"id": f"護理{i:02d}", "shift": "D" if i<=6 else ("E" if i<=9 else "N"),
                         "capacity": 1.0 if i<=8 else 0.6, "weekly_cap": ""})
roles_df = pd.DataFrame(example_rows)
roles_df = st.data_editor(
    roles_df,
    use_container_width=True,
    num_rows="dynamic",
    height=300,
    column_config={
        "id": st.column_config.TextColumn("id"),
        "shift": st.column_config.TextColumn("shift（D/E/N）"),
        "capacity": st.column_config.NumberColumn("capacity（能力係數）", min_value=0.05, max_value=5.0, step=0.05),
        "weekly_cap": st.column_config.TextColumn("weekly_cap（每週最多天，可空白）"),
    },
    key="roles_editor"
)

st.subheader("⛔ 必休（硬性 O）")
must_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="must_edit")

st.subheader("📝 想休（軟性）")
wish_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="wish_edit")

st.subheader("📋 每日三班需求（最少/最多）")
df_demand = seed_demand_df(year, month,
    wdD_min, wdD_max, sunD_min, sunD_max,
    wdE_min, wdE_max, sunE_min, sunE_max,
    wdN_min, wdN_max, sunN_min, sunN_max
)
df_demand = st.data_editor(df_demand, use_container_width=True, num_rows="fixed", height=360, key="demand_edit")

# ===== Run =====
if st.button("🚀 產生班表", type="primary"):
    sched, demand_map, role_map, id_list, capa_map, wcap_map = build_initial_schedule(
        year, month, roles_df, must_off_df, wish_off_df, df_demand
    )
    if allow_cross:
        sched = cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand_map, role_map, capa_map)

    days = days_in_month(year, month)

    # 班表輸出
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid]}
        row.update({str(d): sched[nid][d] for d in range(1, days+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","id"]).reset_index(drop=True)

    # 統計
    def count_code(nid, code): return sum(1 for d in range(1, days+1) if sched[nid][d] == code)
    summary_df = pd.DataFrame([{
        "id": nid,
        "shift": role_map[nid],
        "capacity": capa_map.get(nid,1.0),
        "weekly_cap": wcap_map.get(nid, None) if wcap_map.get(nid, None) is not None else "",
        "D天數": count_code(nid, "D"),
        "E天數": count_code(nid, "E"),
        "N天數": count_code(nid, "N"),
        "O天數": count_code(nid, "O"),
    } for nid in id_list]).sort_values(["shift","id"]).reset_index(drop=True)

    # 燈號（min/max）
    comp_rows = []
    for d in range(1, days+1):
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(1 for nid in id_list if sched[nid][d] == s)
            if act < mn:
                status = f"🔴 不足(-{mn-act})"
            elif act > mx:
                status = f"🟡 超編(+{act-mx})"
            else:
                status = "🟢 達標"
            comp_rows.append({"day": d, "shift": s, "min": mn, "max": mx, "actual": act, "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    # 顯示
    st.subheader(f"📅 {year}-{month:02d} 班表")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 capacity / weekly_cap）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📊 每日達標（區間燈號）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 下載（單行，避免斷行）
    st.download_button("⬇️ 下載 CSV 班表",
        data=roster_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"roster_{year}-{month:02d}_weighted.csv")
    st.download_button("⬇️ 下載 CSV 統計",
        data=summary_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"summary_{year}-{month:02d}_weighted.csv")
    st.download_button("⬇️ 下載 CSV 達標",
        data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"compliance_{year}-{month:02d}_weighted.csv")
else:
    st.info("請在上方輸入：人員（含 capacity/weekly_cap）、必休/想休、每日需求（區間），再按「產生班表」。")

