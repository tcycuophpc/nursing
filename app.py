import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

st.set_page_config(page_title="Nurse Roster • Beds & Ratios + Holiday Factor", layout="wide")

st.title("🩺 三班制排班｜以總床數 + 護病比計算需求（含假日係數）")
st.caption("固定班別 D/E/N；必休/想休；capacity 能力權重、weekly_cap 每週上限；同日跨班平衡（可選）；每日需求由總床數與護病比區間自動計算，週日/假日可套用假日係數。")

# ============= Helpers & Config =============
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

# 以「總床數 + 護病比區間 + 假日係數」產生每日需求（min/max）
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          apply_holiday=True, holiday_factor=1.15,
                          holiday_dates=None):
    """
    護病比填「每位護理師可照護的病人數」：例如白班 1:6-7 -> min=ceil(beds/7), max=ceil(beds/6)
    假日係數：對週日與自訂假日乘上係數並進位。
    """
    if holiday_dates is None: holiday_dates = set()
    rows = []
    ndays = days_in_month(y, m)
    for d in range(1, ndays + 1):
        # 基礎 min/max（以較寬鬆的比數作 min、較嚴格作 max）
        D_min = ceil(total_beds / d_ratio_max) if d_ratio_max > 0 else 0
        D_max = ceil(total_beds / d_ratio_min) if d_ratio_min > 0 else D_min
        E_min = ceil(total_beds / e_ratio_max) if e_ratio_max > 0 else 0
        E_max = ceil(total_beds / e_ratio_min) if e_ratio_min > 0 else E_min
        N_min = ceil(total_beds / n_ratio_max) if n_ratio_max > 0 else 0
        N_max = ceil(total_beds / n_ratio_min) if n_ratio_min > 0 else N_min

        # 假日判斷與係數
        is_holiday = False
        if apply_holiday:
            if is_sunday(y, m, d):
                is_holiday = True
            # 若在自訂假日清單
            if date(y, m, d) in holiday_dates:
                is_holiday = True

        factor = holiday_factor if (apply_holiday and is_holiday) else 1.0

        if factor != 1.0:
            D_min = ceil(D_min * factor); D_max = ceil(D_max * factor)
            E_min = ceil(E_min * factor); E_max = ceil(E_max * factor)
            N_min = ceil(N_min * factor); N_max = ceil(N_max * factor)

        # 保證 max ≥ min
        D_max = max(D_max, D_min); E_max = max(E_max, E_min); N_max = max(N_max, N_min)

        rows.append({
            "day": d,
            "holiday_factor": factor,
            "D_min": D_min, "D_max": D_max,
            "E_min": E_min, "E_max": E_max,
            "N_min": N_min, "N_max": N_max,
        })
    return pd.DataFrame(rows)

# ============= Core Scheduling (固定班別 + capacity/weekly_cap) =============
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df):
    ndays = days_in_month(year, month)

    # 角色資料
    tmp = roles_df.copy()
    tmp["id"] = tmp["id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(lambda s: s if s in ("D","E","N") else "")
    tmp = tmp[tmp["id"].astype(str).str.len() > 0]
    tmp = tmp[tmp["shift"].isin(["D","E","N"])]

    # capacity 預設 1.0
    if "capacity" not in tmp.columns: tmp["capacity"] = 1.0
    tmp["capacity"] = pd.to_numeric(tmp["capacity"], errors="coerce").fillna(1.0).clip(lower=0.05)

    # weekly_cap 允許空白
    if "weekly_cap" not in tmp.columns: tmp["weekly_cap"] = ""
    def to_wcap(x):
        try:
            v = int(float(x))
            return v if v >= 0 else None
        except:
            return None
    tmp["weekly_cap"] = tmp["weekly_cap"].apply(to_wcap)

    role_map = {r.id: r.shift for r in tmp.itertuples(index=False)}
    capa_map = {r.id: float(r.capacity) for r in tmp.itertuples(index=False)}
    wcap_map = {r.id: (None if r.weekly_cap is None else int(r.weekly_cap)) for r in tmp.itertuples(index=False)}
    id_list = sorted(role_map.keys(), key=lambda s: s)

    # 必休/想休清單
    def build_date_map(df):
        m = {nid: set() for nid in id_list}
        if df is None or df.empty: return m
        for r in df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            if nid not in m: continue
            raw_date = getattr(r, "date", "")
            if pd.isna(raw_date) or str(raw_date).strip() == "": continue
            dt = pd.to_datetime(raw_date, errors="coerce")
            if pd.isna(dt):  # NaT
                continue
            if int(dt.year) == int(year) and int(dt.month) == int(month):
                m[nid].add(int(dt.day))
        return m

    must_map = build_date_map(must_off_df)
    wish_map = build_date_map(wish_off_df)

    # 需求
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min), int(r.D_max)),
            "E": (int(r.E_min), int(r.E_max)),
            "N": (int(r.N_min), int(r.N_max)),
        }

    # 初始化
    sched = {nid: {d: "" for d in range(1, ndays+1)} for nid in id_list}
    assigned_total = {nid: 0 for nid in id_list}

    def week_assigned(nid, w):
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, ndays+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    # 先標必休
    for nid in id_list:
        for d in must_map[nid]:
            if 1 <= d <= ndays:
                sched[nid][d] = "O"

    # 選人（capacity 權重、weekly_cap）
    def pick_candidates(d, s, need):
        pool = []
        wk = week_index(d)
        for nid in id_list:
            if role_map[nid] != s: continue
            if sched[nid][d] != "": continue  # O or already assigned
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            # weekly cap
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap:
                continue
            # 愿休者優先靠後
            wished_penalty = 1 if d in wish_map[nid] else 0
            # 加權負荷：assigned / capacity，越小越優先
            wl = assigned_total[nid] / max(0.05, capa_map.get(nid,1.0))
            pool.append((wished_penalty, wl, nid))
        pool.sort()
        chosen = [nid for (_,_,nid) in pool[:need]]
        return chosen

    # 先到 min，再到 max
    for d in range(1, ndays+1):
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
        # 其餘補 O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, capa_map, wcap_map

# 同日跨班平衡（把 >max 或可動人力挪去 <min）
def cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand, role_map, capa_map):
    ndays = days_in_month(year, month)
    for d in range(1, ndays+1):
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
            search_src = [s for s,_ in surplus] + [s for s in ORDER]
            for tgt, need in shortages:
                if need <= 0: continue
                for src in search_src:
                    if src == tgt: continue
                    if actual.get(src,0) <= mins.get(src,0): continue
                    candidates = [nid for nid in id_list if sched[nid][d] == src]
                    feasible = [nid for nid in candidates if rest_ok(sched[nid].get(d-1,""), tgt) and rest_ok(tgt, sched[nid].get(d+1,""))]
                    if not feasible: continue
                    feasible.sort(key=lambda nid: (-capa_map.get(nid,1.0), nid))  # capacity 大者優先移動
                    mover = feasible[0]
                    sched[mover][d] = tgt
                    actual[src] -= 1
                    actual[tgt] += 1
                    changed = True
                    need -= 1
                    if actual[tgt] >= mins[tgt]: break
    return sched

# ============= Sidebar =============
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    ndays = days_in_month(year, month)

    st.subheader("以『總床數 + 護病比區間』計算每日需求")
    total_beds = st.number_input("總床數（住院占床數）", min_value=0, max_value=2000, value=120, step=1)

    col1, col2 = st.columns(2)
    with col1:
        d_ratio_min = st.number_input("白班 1:最少（例 6）", 1, 200, 6)
        e_ratio_min = st.number_input("小夜 1:最少（例 10）", 1, 200, 10)
        n_ratio_min = st.number_input("大夜 1:最少（例 15）", 1, 200, 15)
    with col2:
        d_ratio_max = st.number_input("白班 1:最多（例 7）", 1, 200, 7)
        e_ratio_max = st.number_input("小夜 1:最多（例 12）", 1, 200, 12)
        n_ratio_max = st.number_input("大夜 1:最多（例 16）", 1, 200, 16)

    st.caption("說明：白班 1:6–7 代表每位護理師可照護 6–7 位病人；系統會用 7 算最少人力、6 算最多人力。")

    st.subheader("假日係數（週日與指定假日）")
    apply_holiday = st.checkbox("套用假日係數於週日與下方假日清單", value=True)
    holiday_factor = st.number_input("假日係數（例如 1.15 代表多 15% 人力，向上取整）", 1.00, 3.00, 1.15, step=0.05, format="%.2f")

    st.caption("若要加入國定或院內假日，請在主畫面下方輸入或上傳。")

    st.subheader("選項")
    allow_cross = st.checkbox("允許同日跨班平衡（最後一步）", value=True)

# ============= 主畫面：資料輸入 =============
st.subheader("👥 人員班別（可大量輸入；ID 可中英；capacity 小→分配較少）")
example_rows = []
for i in range(1, 17):
    example_rows.append({"id": f"護理{i:02d}", "shift": "D" if i<=8 else ("E" if i<=12 else "N"),
                         "capacity": 1.0 if i<=12 else 0.6, "weekly_cap": ""})
roles_df = pd.DataFrame(example_rows)
roles_df = st.data_editor(
    roles_df, use_container_width=True, num_rows="dynamic", height=320,
    column_config={
        "id": st.column_config.TextColumn("id"),
        "shift": st.column_config.TextColumn("shift（D/E/N）"),
        "capacity": st.column_config.NumberColumn("capacity（能力係數）", min_value=0.05, max_value=5.0, step=0.05),
        "weekly_cap": st.column_config.TextColumn("weekly_cap（每週最多天，可空白）"),
    }, key="roles_editor"
)

st.subheader("⛔ 必休（硬性 O）")
must_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="must_edit")

st.subheader("📝 想休（軟性）")
wish_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="wish_edit")

st.subheader("📅 指定假日清單（會套用假日係數）")
holiday_df = st.data_editor(pd.DataFrame(columns=["date"]), use_container_width=True, num_rows="dynamic", height=200, key="holidays")
# 轉成 set
holiday_set = set()
for r in holiday_df.itertuples(index=False):
    raw = getattr(r, "date", "")
    if pd.isna(raw) or str(raw).strip() == "": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

# 依總床數 + 比率 + 假日係數產生需求（可再人工調整）
st.subheader("📋 每日三班需求（由總床數 + 護病比 + 假日係數自動計算，可再編輯）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max,
    apply_holiday, holiday_factor, holiday_set
)
df_demand = st.data_editor(
    df_demand_auto,
    use_container_width=True, num_rows="fixed", height=380,
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=ndays, step=1),
        "holiday_factor": st.column_config.NumberColumn("holiday_factor", min_value=1.0, max_value=3.0, step=0.01, format="%.2f"),
        "D_min": st.column_config.NumberColumn("D_min", min_value=0, max_value=500, step=1),
        "D_max": st.column_config.NumberColumn("D_max", min_value=0, max_value=500, step=1),
        "E_min": st.column_config.NumberColumn("E_min", min_value=0, max_value=500, step=1),
        "E_max": st.column_config.NumberColumn("E_max", min_value=0, max_value=500, step=1),
        "N_min": st.column_config.NumberColumn("N_min", min_value=0, max_value=500, step=1),
        "N_max": st.column_config.NumberColumn("N_max", min_value=0, max_value=500, step=1),
    },
    key="demand_editor"
)

# ============= 產生班表 =============
def run_schedule():
    sched, demand_map, role_map, id_list, capa_map, wcap_map = build_initial_schedule(
        year, month, roles_df, must_off_df, wish_off_df, df_demand
    )
    if allow_cross:
        sched = cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand_map, role_map, capa_map)

    nd = days_in_month(year, month)

    # 班表輸出
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid]}
        row.update({str(d): sched[nid][d] for d in range(1, nd+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","id"]).reset_index(drop=True)

    # 統計
    def count_code(nid, code): return sum(1 for d in range(1, nd+1) if sched[nid][d] == code)
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

    # 達標（含假日係數）
    comp_rows = []
    for d in range(1, nd+1):
        # 找對應日的 factor
        row = df_demand[df_demand["day"] == d]
        factor = float(row["holiday_factor"].iloc[0]) if not row.empty and "holiday_factor" in row.columns else 1.0
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(1 for nid in id_list if sched[nid][d] == s)
            if act < mn:
                status = f"🔴 不足(-{mn-act})"
            elif act > mx:
                status = f"🟡 超編(+{act-mx})"
            else:
                status = "🟢 達標"
            comp_rows.append({"day": d, "shift": s, "holiday_factor": factor, "min": mn, "max": mx, "actual": act, "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    return roster_df, summary_df, compliance_df

if st.button("🚀 產生班表", type="primary"):
    roster_df, summary_df, compliance_df = run_schedule()

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 capacity / weekly_cap）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📊 每日達標（含假日係數欄位）")
    st.dataframe(compliance_df, use_container_width=True, height=380)

    # 下載（單行 f-string，避免斷行）
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"roster_{year}-{month:02d}_beds_ratios.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"summary_{year}-{month:02d}_beds_ratios.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"compliance_{year}-{month:02d}_beds_ratios.csv")
else:
    st.info("請輸入大量人員（含 capacity/weekly_cap）、必休/想休、總床數與護病比，必要時設定假日係數與假日日期，然後按「產生班表」。")
