import streamlit as st
import pandas as pd
from datetime import datetime
import calendar

st.set_page_config(page_title="Nurse Roster (3 Shifts, Range Demand, MustOff & WishOff)", layout="wide")

st.title("🩺 三班制排班｜固定班別 + 區間需求 + 必休/想休")
st.caption("ID 支援中/英/數字；每日需求可填最少/最多；想放假=必休(硬性)、另外提供想休(軟性)。等量休假在各班別池內達成。")

# ===== Helpers =====
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6

SHIFT = {
    "D": {"start": 8,  "end": 16, "hours": 8},
    "E": {"start": 16, "end": 24, "hours": 8},
    "N": {"start": 0,  "end": 8,  "hours": 8},
    "O": {"hours": 0},
}
ORDER = ["D", "E", "N"]

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

def seed_demand_df(y, m, wdD_min, wdD_max, sunD_min, sunD_max,
                   wdE_min, wdE_max, sunE_min, sunE_max,
                   wdN_min, wdN_max, sunN_min, sunN_max):
    rows = []
    for d in range(1, days_in_month(y, m)+1):
        sun = is_sunday(y, m, d)
        rows.append({
            "day": d,
            "D_min": int(sunD_min if sun else wdD_min), "D_max": int(sunD_max if sun else wdD_max),
            "E_min": int(sunE_min if sun else wdE_min), "E_max": int(sunE_max if sun else wdE_max),
            "N_min": int(sunN_min if sun else wdN_min), "N_max": int(sunN_max if sun else wdN_max),
        })
    return pd.DataFrame(rows, columns=[
        "day","D_min","D_max","E_min","E_max","N_min","N_max"
    ])

# ===== Initial scheduling (fixed role, min/max) =====
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df):
    days = days_in_month(year, month)

    # 角色：id -> role(D/E/N)
    role_map = {}
    for r in roles_df.itertuples(index=False):
        nid = normalize_id(r.id)
        role = normalize_id(r.shift).upper()
        if nid and role in ("D","E","N"):
            role_map[nid] = role
    id_list = sorted(role_map.keys(), key=lambda s: s)

    # 必休(硬 O) / 想休(軟)
    must_map = {nid: set() for nid in id_list}
    if not must_off_df.empty:
        for r in must_off_df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            try:
                dt = pd.to_datetime(getattr(r, "date", ""))
            except Exception:
                continue
            if nid in must_map and dt.year == year and dt.month == month:
                must_map[nid].add(int(dt.day))

    wish_map = {nid: set() for nid in id_list}
    if not wish_off_df.empty:
        for r in wish_off_df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            try:
                dt = pd.to_datetime(getattr(r, "date", ""))
            except Exception:
                continue
            if nid in wish_map and dt.year == year and dt.month == month:
                wish_map[nid].add(int(dt.day))

    # 需求：day -> ranges
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min), int(r.D_max)),
            "E": (int(r.E_min), int(r.E_max)),
            "N": (int(r.N_min), int(r.N_max)),
        }

    # 初始化
    sched = {nid: {d: "" for d in range(1, days+1)} for nid in id_list}

    # 先標必休 O
    for nid in id_list:
        for d in must_map[nid]:
            if 1 <= d <= days:
                sched[nid][d] = "O"

    # 統計：各自本班已上次數（公平）
    role_count = {nid: 0 for nid in id_list}

    # 逐日逐班：先達成「最少(min)」，再補到「最多(max)」
    for d in range(1, days+1):
        for s in ORDER:
            min_req, max_req = demand.get(d, {}).get(s, (0, 0))

            # 先補到 min
            def pick_candidates(limit_needed):
                # 候選：此班別、當天尚未排、非必休(O)、休息 OK
                # 排序優先：未許願休 > 許願休；已上本班較少 > 較多；ID
                candidates = []
                for nid in id_list:
                    if role_map[nid] != s: continue
                    if sched[nid][d] == "O":  # 必休不可動
                        continue
                    if sched[nid][d] != "":  # 已安排（理論上不會）
                        continue
                    if not rest_ok(sched[nid].get(d-1, ""), s):  # 前後日休息檢查
                        continue
                    wished = 1 if d in wish_map[nid] else 0  # 0=沒許願休(優先)、1=有許願休(後選)
                    candidates.append((wished, role_count[nid], nid))
                candidates.sort()
                chosen = [nid for (_,_,nid) in candidates[:limit_needed]]
                return chosen

            # 先安排到 min
            chosen = pick_candidates(min_req)
            for nid in chosen:
                sched[nid][d] = s
                role_count[nid] += 1

            # 計算目前人数
            cur = sum(1 for nid in id_list if sched[nid][d] == s)
            # 若已達 min，再看是否要補到 max
            if cur < max_req:
                more = pick_candidates(max_req - cur)
                for nid in more:
                    sched[nid][d] = s
                    role_count[nid] += 1

        # 其餘未標記者補 O（當天最多一班）
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list

def weekly_rest_soft_guard(sched, nid, days):
    for rng in [range(1,8), range(8,15), range(15,22), range(22,29), range(29, days+1)]:
        if sum(1 for dd in rng if sched[nid][dd] == "O") == 0:
            return False
    return True

# ===== 等量休假（各班別池，維持當日該班人數不變） =====
def equalize_off_by_pool(year, month, id_list, sched, role_map):
    days = days_in_month(year, month)
    def off_count(nid): return sum(1 for d in range(1, days+1) if sched[nid][d] == "O")

    target_by_pool = {}
    for s in ORDER:
        pool = [nid for nid in id_list if role_map[nid] == s]
        if not pool:
            target_by_pool[s] = 0
            continue
        # 以「目前實際該班總上班數」計算目標 O（更貼近現況）
        total_s = sum(1 for d in range(1, days+1) for nid in pool if sched[nid][d] == s)
        n = len(pool)
        avg_off = (n*days - total_s)/n if n else 0
        target = int(round(avg_off))
        target_by_pool[s] = target

        # 若已全等，略過
        offs = {nid: off_count(nid) for nid in pool}
        if offs and min(offs.values()) == max(offs.values()) == target:
            continue

        # 只在同班別之間互換（不改變每日該班人數）
        for _ in range(3000):
            over = [nid for nid in pool if off_count(nid) > target]
            under = [nid for nid in pool if off_count(nid) < target]
            if not over or not under:
                break
            over.sort(key=lambda nid: (-off_count(nid), nid))
            under.sort(key=lambda nid: (off_count(nid), nid))
            moved = False
            for nid_over in over:
                for d in range(1, days+1):
                    if sched[nid_over][d] != "O":
                        continue
                    # 該日此班正在上班的 under 候選
                    cand = [u for u in under if sched[u][d] == s]
                    cand.sort(key=lambda u: (off_count(u), u))
                    # 休息檢查（over 從 O -> s）
                    if not (rest_ok(sched[nid_over].get(d-1,""), s) and rest_ok(s, sched[nid_over].get(d+1,""))):
                        continue
                    for nid_under in cand:
                        # under 從 s -> O，檢查其每週至少一休仍成立
                        old = sched[nid_under][d]
                        sched[nid_under][d] = "O"
                        ok_week = weekly_rest_soft_guard(sched, nid_under, days)
                        sched[nid_under][d] = old
                        if not ok_week:
                            continue
                        # 交換
                        sched[nid_over][d] = s
                        sched[nid_under][d] = "O"
                        moved = True
                        break
                    if moved: break
                if moved: break
            if not moved: break
    return sched, target_by_pool

# ===== 可選：跨班平衡（同日，把超編移去不足） =====
def cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand, role_map):
    days = days_in_month(year, month)
    for d in range(1, days+1):
        # 統計當日現況
        actual = {s: sum(1 for nid in id_list if sched[nid][d] == s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}
        maxs = {s: demand.get(d,{}).get(s,(0,0))[1] for s in ORDER}

        changed = True
        while changed:
            changed = False
            # 缺口（低於 min）
            shortages = [(s, mins[s] - actual[s]) for s in ORDER if actual[s] < mins[s]]
            if not shortages:
                break
            shortages.sort(key=lambda x: -x[1])  # 先補缺最多的

            # 超編（高於 max）
            surplus = [(s, actual[s] - maxs[s]) for s in ORDER if actual[s] > maxs[s]]
            surplus.sort(key=lambda x: -x[1])

            for tgt, need in shortages:
                if need <= 0: continue
                # 先從「超編>max」的班挪
                moved_here = False
                src_list = [s for s,_ in surplus] + [s for s in ORDER if s != tgt]  # 若沒有超編，也嘗試一般班別
                for src in src_list:
                    if src == tgt: continue
                    # 只有在 src 還有可移動的時候才做
                    if actual.get(src,0) <= mins.get(src,0):
                        continue
                    # 可移動候選（原本在 src）
                    candidates = [nid for nid in id_list if sched[nid][d] == src]
                    feasible = []
                    for nid in candidates:
                        if not (rest_ok(sched[nid].get(d-1,""), tgt) and rest_ok(tgt, sched[nid].get(d+1,""))):
                            continue
                        feasible.append(nid)
                    if not feasible:
                        continue
                    feasible.sort(key=lambda nid: (0 if role_map.get(nid,"")==tgt else 1, nid))
                    mover = feasible[0]
                    # 移動
                    sched[mover][d] = tgt
                    actual[src] -= 1
                    actual[tgt] += 1
                    need -= 1
                    changed = True
                    moved_here = True
                    if actual[tgt] >= mins[tgt]:
                        break
                if moved_here:
                    continue
                # 若完全找不到人可移，跳出
                #（代表在固定班別、休息規則、以及其他班也接近下限時，無法平衡）
                break
    return sched

# ===== UI: sidebar =====
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（可在主頁表格調整；區間值）")
    wdD_min = st.number_input("平日 白(D) 最少", 0, 200, 2)
    wdD_max = st.number_input("平日 白(D) 最多", 0, 200, 3)
    wdE_min = st.number_input("平日 小夜(E) 最少", 0, 200, 1)
    wdE_max = st.number_input("平日 小夜(E) 最多", 0, 200, 2)
    wdN_min = st.number_input("平日 大夜(N) 最少", 0, 200, 1)
    wdN_max = st.number_input("平日 大夜(N) 最多", 0, 200, 2)

    sunD_min = st.number_input("週日 白(D) 最少", 0, 200, 3)
    sunD_max = st.number_input("週日 白(D) 最多", 0, 200, 4)
    sunE_min = st.number_input("週日 小夜(E) 最少", 0, 200, 1)
    sunE_max = st.number_input("週日 小夜(E) 最多", 0, 200, 2)
    sunN_min = st.number_input("週日 大夜(N) 最少", 0, 200, 1)
    sunN_max = st.number_input("週日 大夜(N) 最多", 0, 200, 2)

    st.subheader("選項")
    allow_cross = st.checkbox("允許跨班以平衡缺額（最後一步）", value=True)

    st.subheader("上傳資料（可選）")
    roles_file = st.file_uploader("人員班別 CSV（欄位：id,shift；shift ∈ {D,E,N}；id 可中/英/數字）", type=["csv"])
    mustoff_file = st.file_uploader("必休 CSV（欄位：nurse_id,date，YYYY-MM-DD；硬性 O）", type=["csv"])
    wishoff_file = st.file_uploader("想休 CSV（欄位：nurse_id,date，YYYY-MM-DD；軟性偏好）", type=["csv"])
    demand_file = st.file_uploader("每日需求 CSV（欄位：day,D_min,D_max,E_min,E_max,N_min,N_max 或含 date）", type=["csv"])

# ===== 人員班別 =====
st.subheader("👥 人員班別（固定，不跨班）")
if roles_file:
    roles_df = pd.read_csv(roles_file, dtype=str)
else:
    roles_df = pd.DataFrame({
        "id": ["王小美","李大為","Amy","Ben","Carol","張護理","Night01","Night02"],
        "shift": ["D","D","D","E","E","E","N","N"]
    })
roles_df["id"] = roles_df["id"].map(normalize_id)
roles_df["shift"] = roles_df["shift"].map(lambda x: normalize_id(x).upper())
roles_df = roles_df[roles_df["id"].astype(str).str.len()>0]
roles_df = roles_df[roles_df["shift"].isin(["D","E","N"])]
roles_df = st.data_editor(roles_df, use_container_width=True, num_rows="dynamic", height=240)

# ===== 必休 / 想休 =====
st.subheader("⛔ 必休（硬性 O）")
if mustoff_file:
    must_off_df = pd.read_csv(mustoff_file, dtype=str)
else:
    must_off_df = pd.DataFrame(columns=["nurse_id","date"])
must_off_df["nurse_id"] = must_off_df.get("nurse_id","").map(normalize_id)
month_prefix = f"{year}-{month:02d}-"
show_must = must_off_df[must_off_df.get("date","").astype(str).str.startswith(month_prefix)].copy()
must_edit = st.data_editor(show_must, use_container_width=True, num_rows="dynamic", height=220, key="must_edit")

st.subheader("📝 想休（軟性，盡量尊重）")
if wishoff_file:
    wish_off_df = pd.read_csv(wishoff_file, dtype=str)
else:
    wish_off_df = pd.DataFrame(columns=["nurse_id","date"])
wish_off_df["nurse_id"] = wish_off_df.get("nurse_id","").map(normalize_id)
show_wish = wish_off_df[wish_off_df.get("date","").astype(str).str.startswith(month_prefix)].copy()
wish_edit = st.data_editor(show_wish, use_container_width=True, num_rows="dynamic", height=220, key="wish_edit")

# ===== 每日需求（區間） =====
st.subheader("📋 每日三班需求（最少/最多，可編輯）")
if demand_file:
    raw = pd.read_csv(demand_file)
    if "day" in raw.columns:
        df_demand = raw.copy()
    elif "date" in raw.columns:
        tmp = raw.copy(); tmp["day"] = pd.to_datetime(tmp["date"]).dt.day
        df_demand = tmp[["day","D_min","D_max","E_min","E_max","N_min","N_max"]]
    else:
        st.error("每日需求 CSV 需有 'day' 欄，並包含 D_min,D_max,E_min,E_max,N_min,N_max。")
        st.stop()
else:
    df_demand = seed_demand_df(
        year, month,
        wdD_min, wdD_max, sunD_min, sunD_max,
        wdE_min, wdE_max, sunE_min, sunE_max,
        wdN_min, wdN_max, sunN_min, sunN_max
    )

# 型別/排序
for col in ["D_min","D_max","E_min","E_max","N_min","N_max"]:
    df_demand[col] = df_demand[col].astype(int)
df_demand = df_demand.sort_values("day").reset_index(drop=True)

df_demand = st.data_editor(
    df_demand,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=days, step=1),
        "D_min": st.column_config.NumberColumn("D_min", min_value=0, max_value=500, step=1),
        "D_max": st.column_config.NumberColumn("D_max", min_value=0, max_value=500, step=1),
        "E_min": st.column_config.NumberColumn("E_min", min_value=0, max_value=500, step=1),
        "E_max": st.column_config.NumberColumn("E_max", min_value=0, max_value=500, step=1),
        "N_min": st.column_config.NumberColumn("N_min", min_value=0, max_value=500, step=1),
        "N_max": st.column_config.NumberColumn("N_max", min_value=0, max_value=500, step=1),
    },
    height=360
)

# ===== 產生 =====
if st.button("🚀 產生班表（固定班別 + 區間需求 + 必休/想休）"):
    sched, demand_map, role_map, id_list = build_initial_schedule(year, month, roles_df, must_edit, wish_edit, df_demand)

    # 等量休假（各班別池，維持每日該班人數）
    sched, target_by_pool = equalize_off_by_pool(year, month, id_list, sched, role_map)

    # 可選：跨班平衡（把 >max 的班挪到 <min 的班）
    if allow_cross:
        sched = cross_shift_balance_same_day_with_ranges(year, month, id_list, sched, demand_map, role_map)

    # 統計輸出
    days = days_in_month(year, month)
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid]}
        row.update({str(d): sched[nid][d] for d in range(1, days+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","id"]).reset_index(drop=True)

    def count_code(nid, code): return sum(1 for d in range(1, days+1) if sched[nid][d] == code)
    summary_df = pd.DataFrame([{
        "id": nid,
        "shift": role_map[nid],
        "D天數": count_code(nid,"D"),
        "E天數": count_code(nid,"E"),
        "N天數": count_code(nid,"N"),
        "O天數": count_code(nid,"O"),
    } for nid in id_list]).sort_values(["shift","id"]).reset_index(drop=True)

    # 每日燈號（min/max）
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
    st.subheader(f"📅 {year}-{month:02d} 班表（固定班別）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要")
    st.dataframe(summary_df, use_container_width=True, height=320)
    st.info("各班別等量休假目標（以目前實際上班量計）： " + "、".join([f"{s}:{target_by_pool.get(s,0)}天/人" for s in ORDER]))

    st.subheader("📊 每日達標（區間燈號）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 下載
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"roster_{year}-{month:02d}_range_must_wish.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"summary_{year}-{month:02d}_range_must_wish.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"compliance_{year}-{month:02d}_range_must
