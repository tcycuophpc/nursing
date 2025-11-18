import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

st.set_page_config(page_title="護理排班工具（簡化版）", layout="wide")

# 班別與時間（用來檢查 11 小時休息）
SHIFT_TIME = {
    "D": {"start": 8,  "end": 16},
    "E": {"start": 16, "end": 24},
    "N": {"start": 0,  "end": 8},
    "O": {}
}
SHIFT_ORDER = ["D", "E", "N"]


# ========== 基本工具 ==========
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6


def rest_ok(prev_code: str, next_code: str) -> bool:
    """跨班別是否有 >= 11 小時休息，O 不檢查"""
    if prev_code in (None, "", "O") or next_code in (None, "", "O"):
        return True
    s1, e1 = SHIFT_TIME[prev_code]["start"], SHIFT_TIME[prev_code]["end"]
    s2, e2 = SHIFT_TIME[next_code]["start"], SHIFT_TIME[next_code]["end"]
    rest = s2 - e1
    if rest < 0:
        rest += 24
    return rest >= 11


def normalize_id(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


# ========== 介面：基本設定 ==========
st.title("🏥 護理排班工具（簡化穩定版）")

col_a, col_b, col_c = st.columns([1, 1, 2])
with col_a:
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
with col_b:
    month = st.number_input("月份", 1, 12, value=11, step=1)
nd = days_in_month(year, month)

with col_c:
    total_beds = st.number_input("總床數（住院占床數）", 0, 1000, value=60, step=1)

st.markdown("#### 護病比（只算一般正式人員；這版為簡化版）")
c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1:
    d_ratio_min = st.number_input("白班最少 1：", 1, 200, 6)
with c2:
    d_ratio_max = st.number_input("白班最多 1：", 1, 200, 7)
with c3:
    e_ratio_min = st.number_input("小夜最少 1：", 1, 200, 10)
with c4:
    e_ratio_max = st.number_input("小夜最多 1：", 1, 200, 12)
with c5:
    n_ratio_min = st.number_input("大夜最少 1：", 1, 200, 15)
with c6:
    n_ratio_max = st.number_input("大夜最多 1：", 1, 200, 16)

d_avg = (d_ratio_min + d_ratio_max) / 2
e_avg = (e_ratio_min + e_ratio_max) / 2
n_avg = (n_ratio_min + n_ratio_max) / 2


# ========== 介面：人員清單 ==========
st.markdown("### 👥 人員清單（由護理長一次輸入）")

if "staff_df" not in st.session_state:
    st.session_state["staff_df"] = pd.DataFrame(
        columns=["nurse_id", "name", "shift", "senior", "junior"]
    )

staff_df = st.data_editor(
    st.session_state["staff_df"],
    use_container_width=True,
    num_rows="dynamic",
    height=320,
    column_config={
        "nurse_id": st.column_config.TextColumn("員工編號 / ID"),
        "name":     st.column_config.TextColumn("姓名"),
        "shift":    st.column_config.TextColumn("固定班別（D/E/N）"),
        "senior":   st.column_config.CheckboxColumn("資深"),
        "junior":   st.column_config.CheckboxColumn("新人"),
    },
    key="staff_editor"
)
st.session_state["staff_df"] = staff_df


# ========== 介面：必休與想休 ==========
st.markdown("### 📆 必休 / 想休（以日為單位）")

# 必休
if "must_off_df" not in st.session_state:
    st.session_state["must_off_df"] = pd.DataFrame(columns=["nurse_id", "day"])

must_off_df = st.data_editor(
    st.session_state["must_off_df"],
    use_container_width=True,
    num_rows="dynamic",
    height=220,
    column_config={
        "nurse_id": st.column_config.TextColumn("nurse_id（要跟上面人員表相同）"),
        "day":      st.column_config.NumberColumn("日期(day)", min_value=1, max_value=nd, step=1),
    },
    key="must_off_editor"
)
st.session_state["must_off_df"] = must_off_df

# 想休
if "wish_off_df" not in st.session_state:
    st.session_state["wish_off_df"] = pd.DataFrame(columns=["nurse_id", "day"])

wish_off_df = st.data_editor(
    st.session_state["wish_off_df"],
    use_container_width=True,
    num_rows="dynamic",
    height=220,
    column_config={
        "nurse_id": st.column_config.TextColumn("nurse_id（要跟上面人員表相同）"),
        "day":      st.column_config.NumberColumn("日期(day)", min_value=1, max_value=nd, step=1),
    },
    key="wish_off_editor"
)
st.session_state["wish_off_df"] = wish_off_df


# ========== 介面：每日需求（依床數自動＋可微調） ==========
def seed_demand_from_beds(y, m, total_beds,
                          d_min, d_max,
                          e_min, e_max,
                          n_min, n_max):
    nd_local = days_in_month(y, m)
    rows = []
    for d in range(1, nd_local + 1):
        d_min_u = ceil(total_beds / max(d_max, 1))
        d_max_u = ceil(total_beds / max(d_min, 1))
        e_min_u = ceil(total_beds / max(e_max, 1))
        e_max_u = ceil(total_beds / max(e_min, 1))
        n_min_u = ceil(total_beds / max(n_max, 1))
        n_max_u = ceil(total_beds / max(n_min, 1))
        rows.append({
            "day": d,
            "D_min": int(d_min_u),
            "D_max": int(d_max_u),
            "E_min": int(e_min_u),
            "E_max": int(e_max_u),
            "N_min": int(n_min_u),
            "N_max": int(n_max_u),
        })
    return pd.DataFrame(rows)


st.markdown("### 📊 每日三班需求（人數；可自行微調）")

if "demand_df" not in st.session_state:
    st.session_state["demand_df"] = seed_demand_from_beds(
        year, month, total_beds,
        d_ratio_min, d_ratio_max,
        e_ratio_min, e_ratio_max,
        n_ratio_min, n_ratio_max
    )

demand_df = st.data_editor(
    st.session_state["demand_df"],
    use_container_width=True,
    num_rows="fixed",
    height=320,
    column_config={
        "day":   st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_min": st.column_config.NumberColumn("白班最少", min_value=0, max_value=200, step=1),
        "D_max": st.column_config.NumberColumn("白班最多", min_value=0, max_value=200, step=1),
        "E_min": st.column_config.NumberColumn("小夜最少", min_value=0, max_value=200, step=1),
        "E_max": st.column_config.NumberColumn("小夜最多", min_value=0, max_value=200, step=1),
        "N_min": st.column_config.NumberColumn("大夜最少", min_value=0, max_value=200, step=1),
        "N_max": st.column_config.NumberColumn("大夜最多", min_value=0, max_value=200, step=1),
    },
    key="demand_editor"
)
st.session_state["demand_df"] = demand_df


# ========== 規則常數 ==========
MIN_MONTHLY_OFF = 8      # 每人每月至少 8 天 O
TARGET_OFF      = 10     # 目標 10 天左右
MIN_OFF_1_15    = 5      # 1–15 至少 5 天 O
MIN_OFF_16_END  = 3      # 16–月底至少 3 天 O
MAX_WORK_STREAK = 5      # 最大連續上班天數
MAX_OFF_STREAK  = 3      # 盡量不要連休超過 3 天
MIN_WORK_STRETCH = 3     # 盡量避免上 1 天休 1 天


# ========== 排班主程式 ==========

def build_schedule(year, month, staff_df, must_off_df, wish_off_df, demand_df):
    nd_local = days_in_month(year, month)

    # --- 前處理：人員 ---
    staff = staff_df.copy()
    if staff.empty:
        return None, None, None

    for c in ["nurse_id", "name", "shift", "senior", "junior"]:
        if c not in staff.columns:
            staff[c] = ""

    staff["nurse_id"] = staff["nurse_id"].map(normalize_id)
    staff["shift"] = staff["shift"].astype(str).str.upper().map(
        lambda s: s if s in ("D", "E", "N") else ""
    )
    staff = staff[(staff["nurse_id"] != "") & (staff["shift"].isin(["D","E","N"]))]

    def to_bool(x):
        return str(x).strip().upper() in ("TRUE","1","YES","Y","T")

    role_map   = {r.nurse_id: r.shift for r in staff.itertuples(index=False)}
    senior_map = {r.nurse_id: to_bool(r.senior) for r in staff.itertuples(index=False)}
    junior_map = {r.nurse_id: to_bool(r.junior) for r in staff.itertuples(index=False)}
    name_map   = {r.nurse_id: r.name for r in staff.itertuples(index=False)}

    id_list = sorted(role_map.keys())

    # --- 必休 & 想休 ---
    must_map = {nid: set() for nid in id_list}
    wish_map = {nid: set() for nid in id_list}

    for r in must_off_df.itertuples(index=False):
        nid = normalize_id(getattr(r, "nurse_id", ""))
        d   = getattr(r, "day", None)
        if nid in must_map and pd.notna(d):
            dd = int(d)
            if 1 <= dd <= nd_local:
                must_map[nid].add(dd)

    for r in wish_off_df.itertuples(index=False):
        nid = normalize_id(getattr(r, "nurse_id", ""))
        d   = getattr(r, "day", None)
        if nid in wish_map and pd.notna(d):
            dd = int(d)
            if 1 <= dd <= nd_local and dd not in must_map[nid]:
                wish_map[nid].add(dd)

    # --- 每日需求 ---
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min), int(r.D_max)),
            "E": (int(r.E_min), int(r.E_max)),
            "N": (int(r.N_min), int(r.N_max)),
        }

    # --- 初始化班表 ---
    sched = {nid: {d: "" for d in range(1, nd_local+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}

    # 先把必休日標 O
    for nid in id_list:
        for d in must_map[nid]:
            sched[nid][d] = "O"

    # 一些小工具
    def week_of(d):
        if d <= 7: return 1
        elif d <= 14: return 2
        elif d <= 21: return 3
        elif d <= 28: return 4
        else: return 5

    def week_assigned(nid, w):
        if w == 1:
            rng = range(1, 8)
        elif w == 2:
            rng = range(8, 15)
        elif w == 3:
            rng = range(15, 22)
        elif w == 4:
            rng = range(22, 29)
        else:
            rng = range(29, nd_local+1)
        return sum(1 for d in rng if sched[nid][d] in ("D","E","N"))

    # 這版所有人算一單位，簡化
    def units(nid, s):
        return 1.0

    # 選人池
    def candidate_pool(d, s):
        w = week_of(d)
        pool = []
        for nid in id_list:
            if role_map[nid] != s:
                continue
            if sched[nid][d] != "":
                continue
            if not rest_ok(sched[nid].get(d-1,""), s):
                continue
            # 簡化：不加週上限，只用總天數平衡
            pool.append((d in wish_map[nid], assigned_days[nid], nid))
        pool.sort()  # 想休日優先、已排少的優先
        return [nid for _,_,nid in pool]

    # 白班資深比例檢查（至少 1/3）
    def white_senior_ok(d):
        d_ids = [nid for nid in id_list if sched[nid][d] == "D"]
        total = len(d_ids)
        if total == 0:
            return True
        sen = sum(1 for nid in d_ids if senior_map.get(nid, False))
        return sen >= ceil(total / 3)

    # --- 第一輪排班：達到 min，再補到 max ---
    for d in range(1, nd_local+1):
        for s in SHIFT_ORDER:
            mn, mx = demand.get(d, {}).get(s, (0, 0))
            current_ids = []
            total_u = 0.0

            # 先排到 min
            while total_u + 1e-9 < mn:
                pool = candidate_pool(d, s)
                if not pool:
                    break

                # 白班至少要有資深
                if s == "D":
                    non_j = [nid for nid in pool if senior_map.get(nid, False)]
                    if non_j:
                        pool = non_j

                nid = pool[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                current_ids.append(nid)
                total_u += units(nid, s)
                if s == "D" and not white_senior_ok(d):
                    # 不夠資深就撤銷
                    sched[nid][d] = ""
                    assigned_days[nid] -= 1
                    current_ids.pop()
                    total_u -= units(nid, s)
                    break

            # 再補到 max（如果還有空）
            while total_u + 1e-9 < mx:
                pool = candidate_pool(d, s)
                if not pool:
                    break
                nid = pool[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                current_ids.append(nid)
                total_u += units(nid, s)
                if s == "D" and not white_senior_ok(d):
                    sched[nid][d] = ""
                    assigned_days[nid] -= 1
                    current_ids.pop()
                    total_u -= units(nid, s)
                    break

        # 當天剩下沒排到的人 → O（但不覆蓋原本必休 O）
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    # ========== 調整：週至少一天 O ==========
    def enforce_weekly_one_off():
        for nid in id_list:
            for w in [1,2,3,4,5]:
                if w == 1:
                    rng = range(1,8)
                elif w == 2:
                    rng = range(8,15)
                elif w == 3:
                    rng = range(15,22)
                elif w == 4:
                    rng = range(22,29)
                else:
                    rng = range(29, nd_local+1)
                days = [d for d in rng if 1 <= d <= nd_local]
                if not days:
                    continue
                if any(sched[nid][d] == "O" for d in days):
                    continue
                # 這週沒 O，找一天改 O
                for d in days:
                    if d in must_map[nid]:
                        continue
                    s = sched[nid][d]
                    mn, _mx = demand.get(d, {}).get(s, (0,0))
                    if sum(1 for x in id_list if sched[x][d]==s) - 1 < mn:
                        continue
                    sched[nid][d] = "O"
                    break

    enforce_weekly_one_off()

    # ========== 調整：每月休假至少 8 天，盡量逼近 10 天 ==========
    def off_total(nid):
        return sum(1 for d in range(1, nd_local+1) if sched[nid][d]=="O")

    def add_off_if_possible(nid):
        if off_total(nid) >= TARGET_OFF:
            return False
        # 優先在想休日、週日、排班少的日子加 O
        cand = []
        for d in range(1, nd_local+1):
            if d in must_map[nid]:
                continue
            if sched[nid][d] == "O":
                continue
            s = sched[nid][d]
            mn, _mx = demand.get(d, {}).get(s, (0,0))
            if sum(1 for x in id_list if sched[x][d]==s) - 1 < mn:
                continue
            score = 0
            if d in wish_map[nid]:
                score -= 3
            if is_sunday(year, month, d):
                score -= 2
            cand.append((score, d))
        if not cand:
            return False
        cand.sort()
        _, d_chosen = cand[0]
        sched[nid][d_chosen] = "O"
        return True

    # 先補到至少 8 天
    changed = True
    while changed:
        changed = False
        needers = [nid for nid in id_list if off_total(nid) < MIN_MONTHLY_OFF]
        if not needers:
            break
        needers.sort(key=lambda x: off_total(x))
        for nid in needers:
            if add_off_if_possible(nid):
                changed = True
        if not changed:
            break

    # 再平衡，讓大家接近 10 天
    def off_span():
        cnts = [off_total(n) for n in id_list]
        if not cnts:
            return 0
        return max(cnts) - min(cnts)

    guard = 0
    while off_span() > 1 and guard < 200:
        guard += 1
        nid = min(id_list, key=lambda x: off_total(x))
        if not add_off_if_possible(nid):
            break

    # ========== 調整：1–15 >=5 天、16–月底 >=3 天 ==========
    def off_1_15(nid):
        return sum(1 for d in range(1, min(15,nd_local)+1) if sched[nid][d]=="O")

    def off_16_end(nid):
        return sum(1 for d in range(16, nd_local+1) if sched[nid][d]=="O")

    def add_off_in_range(nid, start, end):
        if off_total(nid) >= TARGET_OFF + 2:
            return False
        cand = []
        for d in range(start, end+1):
            if d < 1 or d > nd_local:
                continue
            if d in must_map[nid]:
                continue
            if sched[nid][d] == "O":
                continue
            s = sched[nid][d]
            mn, _mx = demand.get(d, {}).get(s, (0,0))
            if sum(1 for x in id_list if sched[x][d]==s) - 1 < mn:
                continue
            cand.append((0 if is_sunday(year,month,d) else 1, d))
        if not cand:
            return False
        cand.sort()
        _, d_chosen = cand[0]
        sched[nid][d_chosen] = "O"
        return True

    # 1–15
    for nid in id_list:
        while off_1_15(nid) < MIN_OFF_1_15:
            if not add_off_in_range(nid, 1, 15):
                break
    # 16–月底
    for nid in id_list:
        while off_16_end(nid) < MIN_OFF_16_END:
            if not add_off_in_range(nid, 16, nd_local):
                break

    # ========== 調整：避免上 1 天休 1 天、小段上班 ==========
    def enforce_min_work_stretch():
        for nid in id_list:
            d = 1
            while d <= nd_local:
                if sched[nid][d] not in ("D","E","N"):
                    d += 1
                    continue
                start = d
                while d+1 <= nd_local and sched[nid][d+1] in ("D","E","N"):
                    d += 1
                end = d
                length = end - start + 1
                if length < MIN_WORK_STRETCH:
                    # 嘗試把前後的 O 改成上班
                    # 左邊
                    if start > 1 and sched[nid][start-1] == "O" and (start-1) not in must_map[nid]:
                        s_fixed = role_map[nid]
                        mn, mx = demand.get(start-1, {}).get(s_fixed, (0,0))
                        if sum(1 for x in id_list if sched[x][start-1]==s_fixed) + 1 <= mx:
                            if rest_ok(sched[nid].get(start-2,""), s_fixed) and \
                               rest_ok(s_fixed, sched[nid].get(start,"")):
                                sched[nid][start-1] = s_fixed
                    # 右邊
                    if end < nd_local and sched[nid][end+1] == "O" and (end+1) not in must_map[nid]:
                        s_fixed = role_map[nid]
                        mn, mx = demand.get(end+1, {}).get(s_fixed, (0,0))
                        if sum(1 for x in id_list if sched[x][end+1]==s_fixed) + 1 <= mx:
                            if rest_ok(sched[nid].get(end,""), s_fixed) and \
                               rest_ok(s_fixed, sched[nid].get(end+2,"")):
                                sched[nid][end+1] = s_fixed
                d += 1

    enforce_min_work_stretch()

    # ========== 調整：限制連續上班 ≤5、連休 ≤3 ==========
    def enforce_streak_limits():
        for nid in id_list:
            # 先限制連續 O 不超過 3（有就插回上班）
            d = 1
            while d <= nd_local:
                if sched[nid][d] != "O":
                    d += 1
                    continue
                start = d
                while d+1 <= nd_local and sched[nid][d+1] == "O":
                    d += 1
                end = d
                length = end - start + 1
                if length > MAX_OFF_STREAK:
                    mid = (start + end) // 2
                    if mid not in must_map[nid]:
                        s_fixed = role_map[nid]
                        mn, mx = demand.get(mid, {}).get(s_fixed, (0,0))
                        if sum(1 for x in id_list if sched[x][mid]==s_fixed) + 1 <= mx:
                            if rest_ok(sched[nid].get(mid-1,""), s_fixed) and \
                               rest_ok(s_fixed, sched[nid].get(mid+1,"")):
                                sched[nid][mid] = "O"
                d += 1

            # 再限制連續上班不超過 6（接著用最後防線擋 7 以上）
            d = 1
            while d <= nd_local:
                if sched[nid][d] not in ("D","E","N"):
                    d += 1
                    continue
                start = d
                while d+1 <= nd_local and sched[nid][d+1] in ("D","E","N"):
                    d += 1
                end = d
                length = end - start + 1
                if length > MAX_WORK_STREAK:
                    # 嘗試把中間某一天改成 O
                    for mid in range(start+1, end):
                        if mid in must_map[nid]:
                            continue
                        s_mid = sched[nid][mid]
                        mn, _mx = demand.get(mid, {}).get(s_mid, (0,0))
                        if sum(1 for x in id_list if sched[x][mid]==s_mid) - 1 < mn:
                            continue
                        sched[nid][mid] = "O"
                        break
                d += 1

    enforce_streak_limits()

    # ========== 最後防線：不准連 7 天上班 ==========
    def ensure_no_seven_consecutive():
        for nid in id_list:
            d = 1
            while d <= nd_local:
                if sched[nid][d] not in ("D","E","N"):
                    d += 1
                    continue
                start = d
                while d+1 <= nd_local and sched[nid][d+1] in ("D","E","N"):
                    d += 1
                end = d
                length = end - start + 1
                if length >= 7:
                    need_breaks = (length - 1) // 6
                    base = start + 5
                    breaks = []
                    while base <= end and len(breaks) < need_breaks:
                        breaks.append(base)
                        base += 6
                    for day in breaks:
                        # 儘量在附近插 O（即使略掉每日 min，也先符合法規）
                        if day in must_map[nid]:
                            continue
                        sched[nid][day] = "O"
                d += 1

    ensure_no_seven_consecutive()

    # ========== 輸出 DataFrame ==========
    roster_rows = []
    for nid in id_list:
        row = {
            "nurse_id": nid,
            "name": name_map.get(nid, ""),
            "shift": role_map[nid],
            "senior": senior_map.get(nid, False),
            "junior": junior_map.get(nid, False),
        }
        for d in range(1, nd_local+1):
            row[str(d)] = sched[nid][d]
        roster_rows.append(row)

    roster_df = pd.DataFrame(roster_rows)

    # 統計摘要
    def count_code(nid, code):
        return sum(1 for d in range(1, nd_local+1) if sched[nid][d] == code)

    summary_rows = []
    for nid in id_list:
        summary_rows.append({
            "nurse_id": nid,
            "name": name_map.get(nid, ""),
            "shift": role_map[nid],
            "senior": senior_map.get(nid, False),
            "junior": junior_map.get(nid, False),
            "D天數": count_code(nid, "D"),
            "E天數": count_code(nid, "E"),
            "N天數": count_code(nid, "N"),
            "O天數": count_code(nid, "O"),
        })
    summary_df = pd.DataFrame(summary_rows)

    # 簡單每日達標檢查
    comp_rows = []
    for d in range(1, nd_local+1):
        for s in SHIFT_ORDER:
            mn, mx = demand.get(d, {}).get(s, (0,0))
            actual = sum(1 for nid in id_list if sched[nid][d] == s)
            if actual < mn:
                status = "🔴 不足"
            elif actual <= mx:
                status = "🟢 達標"
            else:
                status = "🟡 超編"
            comp_rows.append({
                "day": d,
                "shift": s,
                "min": mn,
                "max": mx,
                "actual": actual,
                "狀態": status,
            })
    compliance_df = pd.DataFrame(comp_rows)

    return roster_df, summary_df, compliance_df


# ========== 按鈕：產生班表 ==========
if st.button("🚀 產生班表", type="primary"):
    roster_df, summary_df, compliance_df = build_schedule(
        year, month, staff_df, must_off_df, wish_off_df, demand_df
    )
    if roster_df is None:
        st.error("請先輸入至少一位人員（nurse_id + 固定班別）。")
    else:
        st.markdown(f"## 📅 {year}-{month:02d} 班表")

        day_cols = [str(d) for d in range(1, nd+1) if str(d) in roster_df.columns]

        def highlight_off(val):
            return "background-color: #ffcccc" if val == "O" else ""

        styled = roster_df.style.applymap(highlight_off, subset=day_cols)
        st.dataframe(styled, use_container_width=True, height=520)

        st.markdown("### 📊 統計摘要")
        st.dataframe(summary_df, use_container_width=True, height=300)

        st.markdown("### 📈 每日人力達標情況")
        st.dataframe(compliance_df, use_container_width=True, height=300)

        st.download_button(
            "⬇️ 下載 CSV 班表",
            data=roster_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"roster_{year}-{month:02d}.csv"
        )
        st.download_button(
            "⬇️ 下載 CSV 統計",
            data=summary_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"summary_{year}-{month:02d}.csv"
        )
        st.download_button(
            "⬇️ 下載 CSV 每日達標",
            data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"compliance_{year}-{month:02d}.csv"
        )
else:
    st.info(
        "使用步驟建議：\n"
        "1️⃣ 在「人員清單」輸入所有護理師（nurse_id / 姓名 / 固定班別 / 資深 / 新人）\n"
        "2️⃣ 在「必休」填寫各自不能上班的日期；「想休」填希望休假日期\n"
        "3️⃣ 確認「每日三班需求」是否符合你病房人力需求（可自行調整）\n"
        "4️⃣ 按下『產生班表』即可。"
    )


