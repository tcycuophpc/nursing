import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

st.set_page_config(page_title="Nurse Roster • Legal Min + Senior 1/3 + Weekly Off Auto", layout="wide")

st.title("🩺 三班制排班｜法定最低 + 白班資深≥1/3 + 新人1:4–1:5 + 每週至少1日O自動補")
st.caption("固定班別D/E/N；需求=床數×護病比(區間)×假日係數；依醫院層級自動套用衛福部法定最低；白班每日資深≥1/3；新人以1:4–1:5換算能力單位；每人每週至少1日O，未排則自動補，且不破壞法定最低與11小時休息。")

# ================= Helpers =================
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

def ceil_div(beds, r):
    return 0 if r <= 0 else (beds + r - 1) // r

# 以「總床數 + 護病比區間 + 假日係數」產生每日『單位』需求（min_units/max_units）
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          apply_holiday=True, holiday_factor=1.15,
                          holiday_dates=None):
    if holiday_dates is None: holiday_dates = set()
    rows = []
    nd = days_in_month(y, m)
    for d in range(1, nd + 1):
        D_min = ceil(total_beds / d_ratio_max) if d_ratio_max>0 else 0
        D_max = ceil(total_beds / d_ratio_min) if d_ratio_min>0 else D_min
        E_min = ceil(total_beds / e_ratio_max) if e_ratio_max>0 else 0
        E_max = ceil(total_beds / e_ratio_min) if e_ratio_min>0 else E_min
        N_min = ceil(total_beds / n_ratio_max) if n_ratio_max>0 else 0
        N_max = ceil(total_beds / n_ratio_min) if n_ratio_min>0 else N_min

        is_holiday = (apply_holiday and (is_sunday(y,m,d) or date(y,m,d) in holiday_dates))
        factor = holiday_factor if is_holiday else 1.0

        if factor != 1.0:
            D_min = ceil(D_min*factor); D_max = ceil(D_max*factor)
            E_min = ceil(E_min*factor); E_max = ceil(E_max*factor)
            N_min = ceil(N_min*factor); N_max = ceil(N_max*factor)

        rows.append({
            "day": d,
            "holiday_factor": float(factor),
            "D_min_units": int(D_min), "D_max_units": int(D_max),
            "E_min_units": int(E_min), "E_max_units": int(E_max),
            "N_min_units": int(N_min), "N_max_units": int(N_max),
        })
    return pd.DataFrame(rows)

# 新人能力單位：一般=1.0；新人= (新人平均護病比 / 班別平均護病比)
def per_person_units(is_junior: bool, shift_code: str,
                     d_avg: float, e_avg: float, n_avg: float,
                     jr_avg: float):
    if not is_junior:
        return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0: return 1.0
    return max(0.1, jr_avg / base)

# 衛福部三班護病比（法定最低，依醫院層級）
def legal_shift_ratios(level: str):
    # 醫學中心(白6/小夜9/大夜11)；區域(7/11/13)；地區(10/13/15)
    if level == "醫學中心":
        return {"D": 6, "E": 9, "N": 11}
    if level == "區域醫院":
        return {"D": 7, "E": 11, "N": 13}
    return {"D": 10, "E": 13, "N": 15}  # 地區

# ================= Sidebar =================
with st.sidebar:
    st.header("排班設定")
    year  = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    nd = days_in_month(year, month)

    st.subheader("以『總床數 + 護病比區間』計算每日單位需求")
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
    st.caption("新人單位 = 4.5 / 班別平均護病比（白~6.5、小夜~11、大夜~15.5），只影響每日單位達標，不影響休假天數。")
    jr_avg = 4.5

    st.subheader("假日係數與跨班平衡")
    apply_holiday = st.checkbox("套用假日係數於週日與下方假日清單", value=True)
    holiday_factor = st.number_input("假日係數（例如 1.15）", 1.00, 3.00, 1.15, step=0.05, format="%.2f")
    allow_cross = st.checkbox("允許同日跨班平衡（以單位計）", value=True)

    st.subheader("醫院層級（影響法定最低）")
    hospital_level = st.selectbox("醫院層級（決定法定三班護病比）", ["醫學中心", "區域醫院", "地區醫院"], index=0)

# ================= 主畫面輸入 =================
st.subheader("👥 人員（ID 可中英；勾選 senior/junior；weekly_cap 每週上限，可留空）")
example_rows = []
for i in range(1, 19):
    example_rows.append({
        "id": f"護理{i:02d}",
        "shift": "D" if i<=8 else ("E" if i<=13 else "N"),
        "weekly_cap": "",
        "senior": True if i in (1,2,3,4,9,13,17) else False,
        "junior": True if i in (15,18) else False,
    })
roles_df = pd.DataFrame(example_rows)
roles_df = st.data_editor(
    roles_df, use_container_width=True, num_rows="dynamic", height=360,
    column_config={
        "id": st.column_config.TextColumn("id"),
        "shift": st.column_config.TextColumn("shift（D/E/N）"),
        "weekly_cap": st.column_config.TextColumn("weekly_cap（每週最多天，可空白）"),
        "senior": st.column_config.CheckboxColumn("senior（資深）"),
        "junior": st.column_config.CheckboxColumn("junior（新人）"),
    }, key="roles_editor"
)

st.subheader("⛔ 必休（硬性 O）")
must_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="must_edit")

st.subheader("📝 想休（軟性）")
wish_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="wish_edit")

st.subheader("📅 指定假日清單（影響假日係數與『例假日放假數』）")
holiday_df = st.data_editor(pd.DataFrame(columns=["date"]), use_container_width=True, num_rows="dynamic", height=200, key="holidays")
holiday_set = set()
for r in holiday_df.itertuples(index=False):
    raw = getattr(r,"date","")
    if pd.isna(raw) or str(raw).strip()=="": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

# 依床數 + 比率 + 假日係數產生需求
st.subheader("📋 每日三班需求（單位；自動計算，可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max,
    apply_holiday, holiday_factor, holiday_set
)

# 加上「法定最低」欄位並下限對齊
ratios = legal_shift_ratios(hospital_level)
df_demand_auto["D_legal_min_units"] = int(ceil_div(total_beds, ratios["D"]))
df_demand_auto["E_legal_min_units"] = int(ceil_div(total_beds, ratios["E"]))
df_demand_auto["N_legal_min_units"] = int(ceil_div(total_beds, ratios["N"]))

for idx, row in df_demand_auto.iterrows():
    df_demand_auto.at[idx, "D_min_units"] = max(int(row["D_min_units"]), int(row["D_legal_min_units"]))
    df_demand_auto.at[idx, "E_min_units"] = max(int(row["E_min_units"]), int(row["E_legal_min_units"]))
    df_demand_auto.at[idx, "N_min_units"] = max(int(row["N_min_units"]), int(row["N_legal_min_units"]))

df_demand = st.data_editor(
    df_demand_auto,
    use_container_width=True, num_rows="fixed", height=420,
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "holiday_factor": st.column_config.NumberColumn("holiday_factor", min_value=1.0, max_value=3.0, step=0.01, format="%.2f"),
        "D_min_units": st.column_config.NumberColumn("D_min_units", min_value=0, max_value=1000, step=1),
        "D_max_units": st.column_config.NumberColumn("D_max_units", min_value=0, max_value=1000, step=1),
        "E_min_units": st.column_config.NumberColumn("E_min_units", min_value=0, max_value=1000, step=1),
        "E_max_units": st.column_config.NumberColumn("E_max_units", min_value=0, max_value=1000, step=1),
        "N_min_units": st.column_config.NumberColumn("N_min_units", min_value=0, max_value=1000, step=1),
        "N_max_units": st.column_config.NumberColumn("N_max_units", min_value=0, max_value=1000, step=1),
        "D_legal_min_units": st.column_config.NumberColumn("D_legal_min_units", disabled=True),
        "E_legal_min_units": st.column_config.NumberColumn("E_legal_min_units", disabled=True),
        "N_legal_min_units": st.column_config.NumberColumn("N_legal_min_units", disabled=True),
    }, key="demand_editor"
)

# ================= Core Scheduling =================
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df, d_avg, e_avg, n_avg, jr_avg):
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

    # 必休/想休
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
    legal_min_by_day = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min_units), int(r.D_max_units)),
            "E": (int(r.E_min_units), int(r.E_max_units)),
            "N": (int(r.N_min_units), int(r.N_max_units)),
        }
        legal_min_by_day[d] = {
            "D": int(getattr(r,"D_legal_min_units",0)),
            "E": int(getattr(r,"E_legal_min_units",0)),
            "N": int(getattr(r,"N_legal_min_units",0)),
        }

    # 初始化
    sched = {nid: {d:"" for d in range(1, nd+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}

    def week_assigned(nid,w):
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, nd+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    def person_units_on(nid, s):
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, jr_avg)

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

    # 逐日逐班：先達 "min_units"（已含與法定下限對齊），再補到 "max_units"；白班資深≥1/3（以人數）
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

# 同日跨班平衡（以單位；白班維持資深≥1/3，檢查11h）
def cross_shift_balance_with_units(year, month, id_list, sched, demand, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, jr_avg):
    nd = days_in_month(year, month)
    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, jr_avg)

    for d in range(1, nd+1):
        actual = {s: sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}
        maxs = {s: demand.get(d,{}).get(s,(0,0))[1] for s in ORDER}

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

# ===== 自動補「每週至少1日O」：優先假日，避免破壞法定最低與白班資深比例與11h =====
def enforce_weekly_one_off(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, jr_avg, holiday_set):
    nd = days_in_month(year, month)

    # 需求與法定下限（已對齊過）
    demand = {}
    legal_min = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min_units), int(r.D_max_units)), "E": (int(r.E_min_units), int(r.E_max_units)), "N": (int(r.N_min_units), int(r.N_max_units))}
        legal_min[d] = {"D": int(getattr(r,"D_legal_min_units",0)), "E": int(getattr(r,"E_legal_min_units",0)), "N": int(getattr(r,"N_legal_min_units",0))}

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, jr_avg)

    def actual_units(d, s):
        return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)

    def white_senior_ok_if_remove(d, nid):
        # 若該日該人是白班，移除後仍需滿足資深≥1/3
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people)
        if total == 0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    # 週區間
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
            if any(sched[nid][d] == "O" for d in rng):  # 已有O
                continue

            # 候選日：先週日/假日，再非假日；皆需可行：不破壞當日 min（含法定）；檢查11h；白班資深比例
            candidates = sorted(rng, key=lambda d: (0 if is_hday(d) else 1,))  # 假日優先
            picked = False
            for d in candidates:
                cur = sched[nid][d]
                if cur == "O": 
                    picked = True
                    break
                # 移除該人是否會壓到 min（需求 min 已≥法定）
                mn = demand.get(d,{}).get(cur,(0,0))[0]
                act_before = actual_units(d, cur)
                unit = units_of(nid, cur)
                if act_before - unit + 1e-9 < mn:  # 會低於min
                    continue
                # 白班資深比例
                if not white_senior_ok_if_remove(d, nid):
                    continue
                # 11小時休息（O 作為休息不違反規則，但確認鄰日變化）
                if not (rest_ok(sched[nid].get(d-1,""), "O") and rest_ok("O", sched[nid].get(d+1,""))):
                    continue
                # 通過 → 設為O
                sched[nid][d] = "O"
                picked = True
                break

            # 若假日與最小影響都不行，則維持原狀（將在檢核表顯示違規）
    return sched

# ================= Run =================
def run_schedule():
    sched, demand_map, role_map, id_list, senior_map, junior_map = build_initial_schedule(
        year, month, roles_df, must_off_df, wish_off_df, df_demand, d_avg, e_avg, n_avg, jr_avg
    )
    if allow_cross:
        sched = cross_shift_balance_with_units(year, month, id_list, sched, demand_map, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, jr_avg)

    # 自動補「每週至少1日O」
    sched = enforce_weekly_one_off(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, jr_avg, holiday_set)

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

    # 顯示單位用
    def person_units_on(nid, s):  # for display
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, jr_avg)

    # 達標（以單位）
    comp_rows = []
    for d in range(1, ndays+1):
        row = df_demand[df_demand["day"] == d]
        factor = float(row["holiday_factor"].iloc[0]) if not row.empty and "holiday_factor" in row.columns else 1.0
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(person_units_on(nid,s) for nid in id_list if sched[nid][d]==s)
            status = "🟢 達標" if (act + 1e-9 >= mn and act <= mx + 1e-9) else ("🔴 不足" if act < mn - 1e-9 else "🟡 超編")
            comp_rows.append({"day": d, "shift": s, "holiday_factor": factor, "min_units": mn, "max_units": mx, "actual_units": round(act,2), "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    # 法定最低檢核（以單位）
    legal_rows = []
    for d in range(1, ndays+1):
        row = df_demand[df_demand["day"] == d].iloc[0]
        legal_min = {"D": int(row["D_legal_min_units"]), "E": int(row["E_legal_min_units"]), "N": int(row["N_legal_min_units"])}
        for s in ORDER:
            act = sum(person_units_on(nid,s) for nid in id_list if sched[nid][d]==s)
            legal_rows.append({"day": d, "shift": s, "法定最低單位": legal_min[s], "實際單位": round(act,2), "符合法定最低": "✅" if act + 1e-9 >= legal_min[s] else "❌"})
    legal_df = pd.DataFrame(legal_rows)

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

    return roster_df, summary_df, compliance_df, legal_df, weekly_rest_df

# 產生
if st.button("🚀 產生班表", type="primary"):
    roster_df, summary_df, compliance_df, legal_df, weekly_rest_df = run_schedule()

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 senior/junior、例假日放假數）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📊 每日達標（以能力單位）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    st.subheader("📑 法定最低單位檢核（依醫院層級）")
    st.dataframe(legal_df, use_container_width=True, height=320)

    st.subheader("🗓 每週至少 1 日例假（O）檢核")
    st.dataframe(weekly_rest_df, use_container_width=True, height=320)

    # 下載（全部單行，避免 f-string 斷行）
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"roster_{year}-{month:02d}_legal.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"summary_{year}-{month:02d}_legal.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"compliance_{year}-{month:02d}_legal.csv")
    st.download_button("⬇️ 下載 CSV 法定檢核", data=legal_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"legal_check_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 每週例假檢核", data=weekly_rest_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"weekly_off_check_{year}-{month:02d}.csv")
else:
    st.info("請輸入人員（senior/junior/weekly_cap）、必休/想休、總床數與護病比、醫院層級、假日係數與假日日期，然後按「產生班表」。")
