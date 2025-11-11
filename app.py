import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

st.set_page_config(page_title="Nurse Roster • 連動表格 + 月休最少8天", layout="wide")

st.title("🩺 三班制排班｜連動表格（必休/想休）+ 白班資深≥1/3 + 新人1:4–1:5 + 週/月休規則")
st.caption("移除醫院類型與假日係數；人員填完後，自動把 id 帶入必休/想休；可一鍵把『除必休外的所有日期』填入想休。")

# ================= 基本工具 =================
ORDER = ["D", "E", "N"]
SHIFT = {"D": {"start": 8, "end": 16}, "E": {"start": 16, "end": 24}, "N": {"start": 0, "end": 8}, "O": {}}

def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6

def rest_ok(prev_code: str, next_code: str) -> bool:
    # 11小時休息原則（O 不受限）
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

# 需求（以床數與護病比「區間」換算『單位』需求；不再有假日係數）
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16):
    rows = []
    nd = days_in_month(y, m)
    for d in range(1, nd + 1):
        D_min = ceil(total_beds / d_ratio_max) if d_ratio_max>0 else 0
        D_max = ceil(total_beds / d_ratio_min) if d_ratio_min>0 else D_min
        E_min = ceil(total_beds / e_ratio_max) if e_ratio_max>0 else 0
        E_max = ceil(total_beds / e_ratio_min) if e_ratio_min>0 else E_min
        N_min = ceil(total_beds / n_ratio_max) if n_ratio_max>0 else 0
        N_max = ceil(total_beds / n_ratio_min) if n_ratio_min>0 else N_min
        rows.append({
            "day": d,
            "D_min_units": int(D_min), "D_max_units": int(D_max),
            "E_min_units": int(E_min), "E_max_units": int(E_max),
            "N_min_units": int(N_min), "N_max_units": int(N_max),
        })
    return pd.DataFrame(rows)

# 新人能力單位：一般=1.0；新人= (新人平均護病比 / 班別平均護病比)
def per_person_units(is_junior: bool, shift_code: str,
                     d_avg: float, e_avg: float, n_avg: float,
                     jr_avg: float = 4.5):
    if not is_junior:
        return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0: return 1.0
    return max(0.1, jr_avg / base)

# ================= Sidebar =================
with st.sidebar:
    st.header("排班設定")
    year  = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    nd = days_in_month(year, month)

    st.subheader("以『總床數 + 護病比區間』計算每日單位需求（不再有假日係數）")
    total_beds = st.number_input("總床數（住院占床數）", 0, 2000, 120, 1)
    col1, col2 = st.columns(2)
    with col1:
        d_ratio_min = st.number_input("白班 1:最少（例 6）", 1, 200, 6)
        e_ratio_min = st.number_input("小夜 1:最少（例 10）", 1, 200, 10)
        n_ratio_min = st.number_input("大夜 1:最少（例 15）", 1, 200, 15)
    with col2:
        d_ratio_max = st.number_input("白班 1:最多（例 7）", 1, 200, 7)
        e_ratio_max = st.number_input("小夜 1:最多（例 12）", 1, 200, 12)
        n_ratio_max = st.number_input("大夜 1:最多（例 16）", 1, 200, 16)

    d_avg = (d_ratio_min + d_ratio_max) / 2.0
    e_avg = (e_ratio_min + e_ratio_max) / 2.0
    n_avg = (n_ratio_min + n_ratio_max) / 2.0

    st.subheader("新人護病比（固定 1:4–1:5）")
    st.caption("新人單位 = 4.5 / 班別平均護病比（白~6.5、小夜~11、大夜~15.5）；只影響每日單位達標，不影響休假天數。")

    st.subheader("其他選項")
    allow_cross = st.checkbox("允許同日跨班平衡（以單位計）", value=True)
    prefer_off_holiday = st.checkbox("假日優先排休（能休就自動打 O）", value=True)

    st.subheader("月休規則")
    min_monthly_off = st.number_input("每人每月最少 O 天數", min_value=0, max_value=31, value=8, step=1)
    balance_monthly_off = st.checkbox("盡量讓每人 O 天數接近（平衡）", value=True)

# ================= 主畫面輸入（連動） =================
st.subheader("👥 人員（ID 可中英；勾選 senior/junior；weekly_cap 每週上限，可留空）")
default_people = []
for i in range(1, 19):
    default_people.append({
        "id": f"護理{i:02d}",
        "shift": "D" if i<=8 else ("E" if i<=13 else "N"),
        "weekly_cap": "",
        "senior": True if i in (1,2,3,4,9,13,17) else False,
        "junior": True if i in (15,18) else False,
    })

if "roles_df" not in st.session_state:
    st.session_state.roles_df = pd.DataFrame(default_people)

roles_df = st.data_editor(
    st.session_state.roles_df, use_container_width=True, num_rows="dynamic", height=360,
    column_config={
        "id": st.column_config.TextColumn("id"),
        "shift": st.column_config.TextColumn("shift（D/E/N）"),
        "weekly_cap": st.column_config.TextColumn("weekly_cap（每週最多天，可空白）"),
        "senior": st.column_config.CheckboxColumn("senior（資深）"),
        "junior": st.column_config.CheckboxColumn("junior（新人）"),
    }, key="roles_editor"
)
st.session_state.roles_df = roles_df.copy()

# —— 自動建立「必休/想休」模板用工具 —— #
def current_ids():
    tmp = roles_df.copy()
    tmp["id"] = tmp["id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper()
    tmp = tmp[(tmp["id"].astype(str).str.len()>0) & (tmp["shift"].isin(["D","E","N"]))]
    return sorted(tmp["id"].unique().tolist())

def empty_must_template(ids):
    # 先給每個人一列，date留空：方便直接挑日子
    return pd.DataFrame({"nurse_id": ids, "date": [""]*len(ids)})

def auto_fill_wish_from_must(ids, must_df, year, month):
    # 把「除必休外的所有日期」都寫進 wish（每人1..ndays，去掉必休）
    nd = days_in_month(year, month)
    must_map = {i:set() for i in ids}
    if must_df is not None and not must_df.empty:
        for r in must_df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            raw = getattr(r, "date", "")
            if nid not in must_map: continue
            if pd.isna(raw) or str(raw).strip()=="":
                continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                must_map[nid].add(int(dt.day))
    rows = []
    for nid in ids:
        for d in range(1, nd+1):
            if d not in must_map[nid]:
                rows.append({"nurse_id": nid, "date": f"{year}-{month:02d}-{d:02d}"})
    return pd.DataFrame(rows)

# 初始化 / 維持 session state
ids_now = current_ids()
if "must_off_df" not in st.session_state or set(st.session_state.get("must_ids_snapshot", [])) != set(ids_now):
    st.session_state.must_off_df = empty_must_template(ids_now)
    st.session_state.must_ids_snapshot = ids_now[:]

if "wish_off_df" not in st.session_state:
    st.session_state.wish_off_df = pd.DataFrame(columns=["nurse_id","date"])

# —— 必休（自動有 id 模板） —— #
st.subheader("⛔ 必休（硬性 O）— 已自動帶入人員 id，請直接填日期")
must_off_df = st.data_editor(
    st.session_state.must_off_df,
    use_container_width=True, num_rows="dynamic", height=260, key="must_edit"
)
st.session_state.must_off_df = must_off_df.copy()

# —— 一鍵：把「除必休外的所有日期」填入 想休 —— #
col_w1, col_w2 = st.columns([1,2])
with col_w1:
    if st.button("🧩 一鍵自動產生『想休』：除必休以外的所有日期", type="secondary"):
        st.session_state.wish_off_df = auto_fill_wish_from_must(ids_now, st.session_state.must_off_df, year, month)
with col_w2:
    st.caption("貼心提醒：若你沒按上面按鈕，按下『產生班表』時系統也會自動先幫你把想休補好。")

# —— 想休（可人工再刪減/加） —— #
st.subheader("📝 想休（軟性）— 預設會自動填滿『除必休之外』的所有日期，可在此微調")
wish_off_df = st.data_editor(
    st.session_state.wish_off_df,
    use_container_width=True, num_rows="dynamic", height=300, key="wish_edit"
)
st.session_state.wish_off_df = wish_off_df.copy()

# —— 假日清單（保留給『假日優先 O』與統計『本月例假日放假數』） —— #
st.subheader("📅 指定假日清單（影響『假日優先 O』與『本月例假日放假數』；不再有假日係數）")
holiday_df = st.data_editor(pd.DataFrame(columns=["date"]), use_container_width=True, num_rows="dynamic", height=180, key="holidays")
holiday_set = set()
for r in holiday_df.itertuples(index=False):
    raw = getattr(r,"date","")
    if pd.isna(raw) or str(raw).strip()=="": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

# —— 依床數與比率產生每日需求（不再有假日係數） —— #
st.subheader("📋 每日三班需求（單位；自動計算，可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max
)
df_demand = st.data_editor(
    df_demand_auto,
    use_container_width=True, num_rows="fixed", height=380,
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_min_units": st.column_config.NumberColumn("D_min_units", min_value=0, max_value=1000, step=1),
        "D_max_units": st.column_config.NumberColumn("D_max_units", min_value=0, max_value=1000, step=1),
        "E_min_units": st.column_config.NumberColumn("E_min_units", min_value=0, max_value=1000, step=1),
        "E_max_units": st.column_config.NumberColumn("E_max_units", min_value=0, max_value=1000, step=1),
        "N_min_units": st.column_config.NumberColumn("N_min_units", min_value=0, max_value=1000, step=1),
        "N_max_units": st.column_config.NumberColumn("N_max_units", min_value=0, max_value=1000, step=1),
    }, key="demand_editor"
)

# ================= 核心排班 =================
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df, d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)

    tmp = roles_df.copy()
    tmp["id"] = tmp["id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(lambda s: s if s in ("D","E","N") else "")
    tmp = tmp[tmp["id"].astype(str).str.len()>0]
    tmp = tmp[tmp["shift"].isin(["D","E","N"])]

    # weekly_cap 可空白；senior/junior 勾選
    if "weekly_cap" not in tmp.columns: tmp["weekly_cap"] = ""
    def to_wcap(x):
        try:
            v = int(float(x)); return v if v>=0 else None
        except: return None
    tmp["weekly_cap"] = tmp["weekly_cap"].apply(to_wcap)
    if "senior" not in tmp.columns: tmp["senior"] = False
    tmp["senior"] = tmp["senior"].astype(bool)
    if "junior" not in tmp.columns: tmp["junior"] = False
    tmp["junior"] = tmp["junior"].astype(bool)

    role_map   = {r.id: r.shift for r in tmp.itertuples(index=False)}
    wcap_map   = {r.id: (None if r.weekly_cap is None else int(r.weekly_cap)) for r in tmp.itertuples(index=False)}
    senior_map = {r.id: bool(r.senior) for r in tmp.itertuples(index=False)}
    junior_map = {r.id: bool(r.junior) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # 必休/想休 轉 map
    def build_date_map(df):
        m = {nid:set() for nid in id_list}
        if df is None or df.empty: return m
        for r in df.itertuples(index=False):
            nid = normalize_id(getattr(r,"nurse_id",""))
            if nid not in m: continue
            raw = getattr(r,"date","")
            if pd.isna(raw) or str(raw).strip()=="": continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                m[nid].add(int(dt.day))
        return m
    must_map = build_date_map(must_off_df)
    wish_map = build_date_map(wish_off_df)

    # 需求（單位）
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min_units), int(r.D_max_units)),
            "E": (int(r.E_min_units), int(r.E_max_units)),
            "N": (int(r.N_min_units), int(r.N_max_units)),
        }

    # 初始化
    sched = {nid: {d:"" for d in range(1, nd+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}

    def week_index(day: int) -> int:
        if day <= 7: return 1
        if day <= 14: return 2
        if day <= 21: return 3
        if day <= 28: return 4
        return 5

    def week_assigned(nid,w):
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, nd+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    def person_units_on(nid, s):
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)

    # 先標必休
    for nid in id_list:
        for d in must_map[nid]:
            if 1<=d<=nd:
                sched[nid][d] = "O"

    # 選人池：先沒許願休，再看累積出勤天數；檢查 weekly_cap 與 11 小時休息
    def pick_pool(d, s):
        wk = week_index(d)
        pool = []
        for nid in id_list:
            if role_map[nid] != s: continue
            if sched[nid][d] != "": continue
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap:
                continue
            wished = 1 if d in wish_map[nid] else 0
            pool.append((wished, assigned_days[nid], nid))
        pool.sort()
        return [nid for (_,_,nid) in pool]

    # 逐日逐班：先達 min，再補到 max；白班資深≥1/3（以人數）
    for d in range(1, nd+1):
        for s in ORDER:
            mn_u, mx_u = demand.get(d,{}).get(s,(0,0))
            assigned = []
            units_sum = 0.0
            senior_cnt = 0

            # 達成 min_units
            while units_sum + 1e-9 < mn_u:
                pool = pick_pool(d, s)
                if not pool: break
                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    pick_list = cand_sen if (senior_cnt < need_sen and cand_sen) else pool
                else:
                    pick_list = pool
                if not pick_list: break
                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if s=="D" and senior_map.get(nid,False): senior_cnt += 1

            # 補到不超過 max_units
            while units_sum + 1e-9 < mx_u:
                pool = pick_pool(d, s)
                if not pool: break
                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    pick_list = cand_sen if (senior_cnt < need_sen and cand_sen) else pool
                else:
                    pick_list = pool
                if not pick_list: break
                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if s=="D" and senior_map.get(nid,False): senior_cnt += 1

        # 其餘補 O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, senior_map, junior_map

# 跨班平衡（以單位；白班維持資深≥1/3，檢查11h）
def cross_shift_balance_with_units(year, month, id_list, sched, demand, role_map, senior_map, junior_map, d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)
    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)

    for d in range(1, nd+1):
        actual = {s: sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}

        changed = True
        while changed:
            changed = False
            shortages = [(s, mins[s]-actual[s]) for s in ORDER if actual[s] + 1e-9 < mins[s]]
            if not shortages: break
            shortages.sort(key=lambda x: -x[1])
            for tgt, _need in shortages:
                for src in ORDER:
                    if src == tgt: continue
                    if actual[src] - 1e-9 <= mins.get(src,0): continue
                    candidates = [nid for nid in id_list if sched[nid][d]==src]
                    candidates.sort(key=lambda nid: -units_of(nid, src))  # 單位高者先移
                    moved = False
                    for mv in candidates:
                        # 白班資深比例檢查
                        def senior_ok_after_move(nid_move, from_s, to_s):
                            if from_s!="D" and to_s!="D": return True
                            d_people = [x for x in id_list if sched[x][d]=="D"]
                            if from_s=="D" and nid_move in d_people: d_people.remove(nid_move)
                            if to_s=="D": d_people.append(nid_move)
                            total = len(d_people)
                            if total==0: return True
                            sen = sum(1 for x in d_people if senior_map.get(x,False))
                            return sen >= ceil(total/3)
                        if not senior_ok_after_move(mv, src, tgt):
                            continue
                        if not (rest_ok(sched[mv].get(d-1,""), tgt) and rest_ok(tgt, sched[mv].get(d+1,""))):
                            continue
                        u_from = units_of(mv, src)
                        u_to   = units_of(mv, tgt)
                        sched[mv][d] = tgt
                        actual[src] -= u_from
                        actual[tgt] += u_to
                        changed = True
                        moved = True
                        break
                    if moved: break
    return sched

# 假日優先排休（不壓到 min；保白班資深與 11h）
def prefer_off_on_holidays(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map,
                           d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min_units), int(r.D_max_units)),
                     "E": (int(r.E_min_units), int(r.E_max_units)),
                     "N": (int(r.N_min_units), int(r.N_max_units))}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid, False), s, d_avg, e_avg, n_avg, 4.5)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people)
        if total == 0: return True
        sen = sum(1 for x in d_people if senior_map.get(x, False))
        return sen >= ceil(total / 3)

    for d in range(1, nd + 1):
        if not is_hday(d): continue
        for s in ("D", "E", "N"):
            mn, _mx = demand.get(d, {}).get(s, (0, 0))
            def actual_units():
                return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)
            changed = True
            while changed:
                changed = False
                cur_units = actual_units()
                if cur_units <= mn + 1e-9: break
                candidates = [nid for nid in id_list if sched[nid][d] == s]
                candidates.sort(key=lambda nid: (units_of(nid, s), not junior_map.get(nid, False)))
                moved = False
                for nid in candidates:
                    u = units_of(nid, s)
                    if cur_units - u + 1e-9 < mn: continue
                    if not white_senior_ok_if_remove(d, nid): continue
                    if not (rest_ok(sched[nid].get(d-1, ""), "O") and rest_ok("O", sched[nid].get(d+1, ""))): continue
                    sched[nid][d] = "O"
                    changed = True
                    moved = True
                    break
                if not moved: break
    return sched

# 每週至少1日O；月休最少；平衡 O 天數
def enforce_weekly_one_off(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min_units), int(r.D_max_units)),
                     "E": (int(r.E_min_units), int(r.E_max_units)),
                     "N": (int(r.N_min_units), int(r.N_max_units))}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    def actual_units(d, s): return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)
    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people)
        if total == 0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    def week_range(w):
        if w==1: return range(1,8)
        if w==2: return range(8,15)
        if w==3: return range(15,22)
        if w==4: return range(22,29)
        return range(29, nd+1)

    for nid in id_list:
        for w in [1,2,3,4,5]:
            rng = [d for d in week_range(w) if 1 <= d <= nd]
            if not rng: continue
            if any(sched[nid][d] == "O" for d in rng):
                continue
            candidates = sorted(rng, key=lambda d: (0 if is_hday(d) else 1,))
            for d in candidates:
                cur = sched[nid][d]
                if cur == "O": break
                mn = demand.get(d,{}).get(cur,(0,0))[0]
                u = units_of(nid, cur)
                if actual_units(d, cur) - u + 1e-9 < mn: continue
                if not white_senior_ok_if_remove(d, nid): continue
                if not (rest_ok(sched[nid].get(d-1,""), "O") and rest_ok("O", sched[nid].get(d+1,""))): continue
                sched[nid][d] = "O"
                break
    return sched

def enforce_min_monthly_off(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map,
                            d_avg, e_avg, n_avg, min_off=8, balance=True, holiday_set=None):
    nd = days_in_month(year, month)
    if holiday_set is None: holiday_set = set()
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min_units), int(r.D_max_units)),
                     "E": (int(r.E_min_units), int(r.E_max_units)),
                     "N": (int(r.N_min_units), int(r.N_max_units))}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    def actual_units(d, s): return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)
    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people)
        if total == 0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    def off_count(nid): return sum(1 for d in range(1, nd+1) if sched[nid][d] == "O")

    def try_add_one_off(nid):
        work_days = [(d, sched[nid][d]) for d in range(1, nd+1) if sched[nid][d] in ("D","E","N")]
        if not work_days: return False
        scored = []
        for d, s in work_days:
            mn = demand.get(d,{}).get(s,(0,0))[0]
            u = units_of(nid, s)
            slack = sum(units_of(x, s) for x in id_list if sched[x][d]==s) - mn
            feasible = (slack + 1e-9 >= u) and white_senior_ok_if_remove(d, nid) \
                       and rest_ok(sched[nid].get(d-1,""), "O") and rest_ok("O", sched[nid].get(d+1,""))
            if feasible:
                scored.append((0 if is_hday(d) else 1, -slack, d))
        if not scored: return False
        scored.sort()
        chosen_d = scored[0][2]
        sched[nid][chosen_d] = "O"
        return True

    # 先確保每人 >= min_off
    changed = True
    while changed:
        changed = False
        needs = sorted([nid for nid in id_list if off_count(nid) < min_off],
                       key=lambda x: off_count(x))
        if not needs: break
        for nid in needs:
            if try_add_one_off(nid):
                changed = True
        if not changed:
            break

    if not balance:
        return sched

    # 平衡：縮小 O 的差距（盡量讓 max-min <= 1）
    def off_span():
        cnts = [off_count(n) for n in id_list]
        return (max(cnts) if cnts else 0) - (min(cnts) if cnts else 0)

    guard = 0
    while off_span() > 1 and guard < 200:
        guard += 1
        nid_low = min(id_list, key=lambda x: off_count(x))
        if not try_add_one_off(nid_low):
            break
    return sched

# ================= Run =================
def run_schedule():
    # 若使用者沒按「一鍵自動產生想休」，在產生班表前自動補一次
    if st.session_state.wish_off_df.empty:
        st.session_state.wish_off_df = auto_fill_wish_from_must(current_ids(), st.session_state.must_off_df, year, month)

    sched, demand_map, role_map, id_list, senior_map, junior_map = build_initial_schedule(
        year, month, st.session_state.roles_df, st.session_state.must_off_df, st.session_state.wish_off_df,
        df_demand, d_avg, e_avg, n_avg
    )

    if allow_cross:
        sched = cross_shift_balance_with_units(year, month, id_list, sched, demand_map, role_map, senior_map, junior_map, d_avg, e_avg, n_avg)

    # 假日可休就打 O（先行清空多餘人力）
    if prefer_off_holiday:
        sched = prefer_off_on_holidays(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set)

    # 自動補「每週至少1日O」
    sched = enforce_weekly_one_off(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set)

    # 每人每月至少 O（預設8），並盡量平衡
    sched = enforce_min_monthly_off(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map,
                                    d_avg, e_avg, n_avg, min_off=min_monthly_off,
                                    balance=balance_monthly_off, holiday_set=holiday_set)

    ndays = days_in_month(year, month)

    # 班表
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid], "senior": senior_map.get(nid,False), "junior": junior_map.get(nid,False)}
        row.update({str(d): sched[nid][d] for d in range(1, ndays+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","senior","junior","id"]).reset_index(drop=True)

    # 統計（出勤天數；例假日放假數）
    def count_code(nid, code): return sum(1 for d in range(1, ndays+1) if sched[nid][d] == code)
    def is_hday(d): return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)
    holiday_off = {nid: sum(1 for d in range(1, ndays+1) if is_hday(d) and sched[nid][d]=="O") for nid in id_list}
    summary_df = pd.DataFrame([{
        "id": nid, "shift": role_map[nid], "senior": senior_map.get(nid,False), "junior": junior_map.get(nid,False),
        "D天數": count_code(nid,"D"), "E天數": count_code(nid,"E"), "N天數": count_code(nid,"N"), "O天數": count_code(nid,"O"),
        "本月例假日放假數": holiday_off[nid]
    } for nid in id_list]).sort_values(["shift","senior","junior","id"]).reset_index(drop=True)

    # 達標（以單位）
    def person_units_on(nid, s):  # for display
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    comp_rows = []
    for d in range(1, ndays+1):
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(person_units_on(nid,s) for nid in id_list if sched[nid][d]==s)
            status = "🟢 達標" if (act + 1e-9 >= mn and act <= mx + 1e-9) else ("🔴 不足" if act < mn - 1e-9 else "🟡 超編")
            comp_rows.append({"day": d, "shift": s, "min_units": mn, "max_units": mx, "actual_units": round(act,2), "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    # 每週至少1日O 檢核
    weekly_rows = []
    def week_range(w):
        if w==1: return range(1,8)
        if w==2: return range(8,15)
        if w==3: return range(15,22)
        if w==4: return range(22,29)
        return range(29, ndays+1)
    for nid in id_list:
        for w in [1,2,3,4,5]:
            rng = [d for d in week_range(w) if d <= ndays]
            if not rng: continue
            off_cnt = sum(1 for d in rng if sched[nid][d] == "O")
            weekly_rows.append({"id": nid, "week": w, "該週O天數": off_cnt, "符合每7日≥1日例假": "✅" if off_cnt>=1 else "❌"})
    weekly_rest_df = pd.DataFrame(weekly_rows)

    return roster_df, summary_df, compliance_df, weekly_rest_df

# 產生
if st.button("🚀 產生班表", type="primary"):
    roster_df, summary_df, compliance_df, weekly_rest_df = run_schedule()

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 senior/junior、例假日放假數）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📊 每日達標（以能力單位）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    st.subheader("🗓 每週至少 1 日例假（O）檢核")
    st.dataframe(weekly_rest_df, use_container_width=True, height=320)

    # 下載（全部單行，避免 f-string 斷行）
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"roster_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"summary_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"compliance_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 每週例假檢核", data=weekly_rest_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"weekly_off_check_{year}-{month:02d}.csv")
else:
    st.info("步驟：1) 先在『人員』填 id 與班別 → 2) 必休填日期（已帶入 id）→ 3) 按『一鍵自動產生想休』→ 4) 如需微調，再按『產生班表』。若你忘了按，一樣會在產生前自動補想休。")

