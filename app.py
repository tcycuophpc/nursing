import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

st.set_page_config(page_title="Nurse Roster • Capacity Units + Senior Ratio + Holiday Off Count", layout="wide")

st.title("🩺 三班制排班｜能力單位(capacity)＋白班資深≥1/3＋例假日放假統計")
st.caption("固定班別 D/E/N；必休(硬)／想休(軟)；capacity 以『能力單位』計算每日達標，不影響出勤天數公平；白班每日資深比例≥1/3；統計本月例假日(O)天數。")

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

# 以「總床數 + 護病比區間 + 假日係數」產生每日需求（min_units/max_units；以能力單位計）
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          apply_holiday=True, holiday_factor=1.15,
                          holiday_dates=None):
    """
    護病比填『每位護理師可照護的病人數』：白 1:6–7 => min_units=ceil(beds/7), max_units=ceil(beds/6)。
    假日係數：對週日與自訂假日乘上係數並進位。這裡的 min/max 都是「能力單位」。
    """
    if holiday_dates is None: holiday_dates = set()
    rows = []
    ndays = days_in_month(y, m)
    for d in range(1, ndays + 1):
        D_min = ceil(total_beds / d_ratio_max) if d_ratio_max > 0 else 0
        D_max = ceil(total_beds / d_ratio_min) if d_ratio_min > 0 else D_min
        E_min = ceil(total_beds / e_ratio_max) if e_ratio_max > 0 else 0
        E_max = ceil(total_beds / e_ratio_min) if e_ratio_min > 0 else E_min
        N_min = ceil(total_beds / n_ratio_max) if n_ratio_max > 0 else 0
        N_max = ceil(total_beds / n_ratio_min) if n_ratio_min > 0 else N_min

        is_holiday = False
        if apply_holiday:
            if is_sunday(y, m, d): is_holiday = True
            if date(y, m, d) in holiday_dates: is_holiday = True
        factor = holiday_factor if (apply_holiday and is_holiday) else 1.0

        if factor != 1.0:
            D_min = ceil(D_min * factor); D_max = ceil(D_max * factor)
            E_min = ceil(E_min * factor); E_max = ceil(E_max * factor)
            N_min = ceil(N_min * factor); N_max = ceil(N_max * factor)

        D_max = max(D_max, D_min); E_max = max(E_max, E_min); N_max = max(N_max, N_min)

        rows.append({
            "day": d,
            "holiday_factor": factor,
            "D_min_units": D_min, "D_max_units": D_max,
            "E_min_units": E_min, "E_max_units": E_max,
            "N_min_units": N_min, "N_max_units": N_max,
        })
    return pd.DataFrame(rows)

# ================= Core Scheduling =================
def build_initial_schedule(year, month, roles_df, must_off_df, wish_off_df, demand_df):
    ndays = days_in_month(year, month)

    # 角色資料：id, shift(D/E/N), capacity(>=1, 整數), weekly_cap, senior(True/False)
    tmp = roles_df.copy()
    tmp["id"] = tmp["id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(lambda s: s if s in ("D","E","N") else "")
    tmp = tmp[tmp["id"].astype(str).str.len() > 0]
    tmp = tmp[tmp["shift"].isin(["D","E","N"])]

    # capacity：1 的倍數（整數 >=1）
    if "capacity" not in tmp.columns: tmp["capacity"] = 1
    tmp["capacity"] = pd.to_numeric(tmp["capacity"], errors="coerce").fillna(1).astype(int)
    tmp.loc[tmp["capacity"] < 1, "capacity"] = 1

    # weekly_cap（每週上限；可空白）
    if "weekly_cap" not in tmp.columns: tmp["weekly_cap"] = ""
    def to_wcap(x):
        try:
            v = int(float(x))
            return v if v >= 0 else None
        except:
            return None
    tmp["weekly_cap"] = tmp["weekly_cap"].apply(to_wcap)

    # senior 勾選
    if "senior" not in tmp.columns: tmp["senior"] = False
    tmp["senior"] = tmp["senior"].astype(bool)

    role_map   = {r.id: r.shift for r in tmp.itertuples(index=False)}
    capa_map   = {r.id: int(r.capacity) for r in tmp.itertuples(index=False)}
    wcap_map   = {r.id: (None if r.weekly_cap is None else int(r.weekly_cap)) for r in tmp.itertuples(index=False)}
    senior_map = {r.id: bool(r.senior) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # 必休/想休
    def build_date_map(df):
        m = {nid: set() for nid in id_list}
        if df is None or df.empty: return m
        for r in df.itertuples(index=False):
            nid = normalize_id(getattr(r, "nurse_id", ""))
            if nid not in m: continue
            raw = getattr(r, "date", "")
            if pd.isna(raw) or str(raw).strip()== "": continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                m[nid].add(int(dt.day))
        return m

    must_map = build_date_map(must_off_df)
    wish_map = build_date_map(wish_off_df)

    # 需求：以能力單位表示
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min_units), int(r.D_max_units)),
            "E": (int(r.E_min_units), int(r.E_max_units)),
            "N": (int(r.N_min_units), int(r.N_max_units)),
        }

    # 初始化
    sched = {nid: {d: "" for d in range(1, ndays+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}  # 出勤天數（不看 capacity）

    # 每週出勤統計
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

    # 依天依班排：以「能力單位」達到 min，再朝 max
    def pick_pool(d, s):
        wk = week_index(d)
        pool = []
        for nid in id_list:
            if role_map[nid] != s: continue
            if sched[nid][d] != "": continue  # 已 O 或已排
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            # 週上限
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap:
                continue
            wished = 1 if d in wish_map[nid] else 0  # 0 代表沒許願休，優先
            pool.append((wished, assigned_days[nid], nid))
        pool.sort()
        return [nid for (_,_,nid) in pool]

    for d in range(1, ndays+1):
        for s in ORDER:
            min_units, max_units = demand.get(d, {}).get(s, (0,0))
            assigned_today = []        # 當日此班的人
            units_sum = 0              # 當日此班的能力單位加總
            seniors_count = 0          # 當日此班資深人員數（只在白班檢查 1/3）

            # Step 1: 優先達到 min_units
            while units_sum < min_units:
                pool = pick_pool(d, s)
                if not pool: break
                # 白班：若資深比例不足，優先從 pool 中選資深
                if s == "D":
                    # 需要的資深人數門檻（以加入下一位後估算）
                    need_senior = ceil((len(assigned_today)+1) / 3)
                    # 先找資深
                    cand_senior = [nid for nid in pool if senior_map.get(nid, False)]
                    cand_general = [nid for nid in pool if not senior_map.get(nid, False)]
                    pick_list = cand_senior if seniors_count < need_senior and cand_senior else pool
                else:
                    pick_list = pool

                if not pick_list: break
                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned_today.append(nid)
                units_sum += capa_map.get(nid, 1)
                if s == "D" and senior_map.get(nid, False):
                    seniors_count += 1

            # Step 2: 若未達 min_units 再次嘗試（沒有可用人就放棄）
            # 已在 while 中處理，無人可用會跳出

            # Step 3: 在不超過 max_units 下嘗試加人（仍同日不可重複安排）
            while units_sum < max_units:
                pool = pick_pool(d, s)
                if not pool: break
                # 白班仍維持 1/3 資深
                if s == "D":
                    need_senior = ceil((len(assigned_today)+1) / 3)
                    cand_senior = [nid for nid in pool if senior_map.get(nid, False)]
                    pick_list = cand_senior if seniors_count < need_senior and cand_senior else pool
                else:
                    pick_list = pool
                if not pick_list: break
                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned_today.append(nid)
                units_sum += capa_map.get(nid, 1)
                if s == "D" and senior_map.get(nid, False):
                    seniors_count += 1

        # 其餘補 O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, capa_map, wcap_map, senior_map

# 同日跨班平衡（以能力單位判斷不足/超編；保 11h）
def cross_shift_balance_same_day_with_units(year, month, id_list, sched, demand, role_map, capa_map, senior_map):
    ndays = days_in_month(year, month)
    for d in range(1, ndays+1):
        actual_units = {s: sum(capa_map.get(nid,1) for nid in id_list if sched[nid][d] == s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}
        maxs = {s: demand.get(d,{}).get(s,(0,0))[1] for s in ORDER}

        # 反覆移動直到各班 >= min 或不可移動
        changed = True
        while changed:
            changed = False
            shortages = [(s, mins[s] - actual_units[s]) for s in ORDER if actual_units[s] < mins[s]]
            if not shortages: break
            shortages.sort(key=lambda x: -x[1])

            surplus = [(s, actual_units[s] - maxs[s]) for s in ORDER if actual_units[s] > maxs[s]]
            # 若沒有明顯 >max 的班，也可嘗試從其他班借（但避免把該班壓到 <min）
            srcs = [s for s,_ in surplus] + [s for s in ORDER]

            for tgt, need in shortages:
                if need <= 0: continue
                for src in srcs:
                    if src == tgt: continue
                    if actual_units.get(src,0) <= mins.get(src,0): continue
                    # 候選搬移者（能力高者優先補單位）
                    candidates = [nid for nid in id_list if sched[nid][d] == src]
                    feasible = [nid for nid in candidates if rest_ok(sched[nid].get(d-1,""), tgt) and rest_ok(tgt, sched[nid].get(d+1,""))]
                    if not feasible: continue
                    # 白班資深比例限制：若目標是 D，要避免搬入後資深<1/3；若來源是 D，要避免搬出後資深<1/3
                    def senior_ok_after_move(nid_move, from_s, to_s):
                        if to_s != "D" and from_s != "D": return True
                        # 計算搬移後 D 班的資深比例（僅對 day shift）
                        def count_d(arr): 
                            return sum(1 for x in arr if x == "D")
                        # 收集當日 D 班人員
                        d_people = [x for x in id_list if sched[x][d] == "D"]
                        if from_s == "D": 
                            if nid_move in d_people: d_people.remove(nid_move)
                        if to_s == "D":
                            d_people = d_people + [nid_move]
                        total = len(d_people)
                        if total == 0: return True
                        senior_cnt = sum(1 for x in d_people if senior_map.get(x, False))
                        return senior_cnt >= ceil(total / 3)

                    # 先選 capacity 高者，效率較好
                    feasible.sort(key=lambda nid: (-capa_map.get(nid,1), nid))
                    moved_here = False
                    for mv in feasible:
                        if not senior_ok_after_move(mv, src, tgt):
                            continue
                        units = capa_map.get(mv,1)
                        sched[mv][d] = tgt
                        actual_units[src] -= units
                        actual_units[tgt] += units
                        changed = True
                        moved_here = True
                        break
                    if moved_here:
                        break
    return sched

# ================= Sidebar =================
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    ndays = days_in_month(year, month)

    st.subheader("以『總床數 + 護病比區間』計算每日能力單位需求")
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
    st.caption("白 1:6–7 表示一位護理師可照護 6–7 位病人；系統會以 7 算最少單位、6 算最多單位。")

    st.subheader("假日係數與同日跨班平衡")
    apply_holiday = st.checkbox("套用假日係數於週日與下方假日清單", value=True)
    holiday_factor = st.number_input("假日係數（例如 1.15）", 1.00, 3.00, 1.15, step=0.05, format="%.2f")
    allow_cross = st.checkbox("允許同日跨班平衡（能力單位）", value=True)

# ================= 主畫面輸入 =================
st.subheader("👥 人員班別（ID 可中英；capacity 必為整數≥1；勾選 senior 表示資深）")
example_rows = []
for i in range(1, 17):
    example_rows.append({
        "id": f"護理{i:02d}",
        "shift": "D" if i<=8 else ("E" if i<=12 else "N"),
        "capacity": 1 if i<=12 else 2,
        "weekly_cap": "",
        "senior": True if i in (1,2,3,4,9,13) else False
    })
roles_df = pd.DataFrame(example_rows)
roles_df = st.data_editor(
    roles_df, use_container_width=True, num_rows="dynamic", height=340,
    column_config={
        "id": st.column_config.TextColumn("id"),
        "shift": st.column_config.TextColumn("shift（D/E/N）"),
        "capacity": st.column_config.NumberColumn("capacity（整數≥1）", min_value=1, max_value=5, step=1),
        "weekly_cap": st.column_config.TextColumn("weekly_cap（每週最多天，可空白）"),
        "senior": st.column_config.CheckboxColumn("senior（資深）"),
    }, key="roles_editor"
)

st.subheader("⛔ 必休（硬性 O）")
must_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="must_edit")

st.subheader("📝 想休（軟性）")
wish_off_df = st.data_editor(pd.DataFrame(columns=["nurse_id","date"]),
                             use_container_width=True, num_rows="dynamic", height=220, key="wish_edit")

st.subheader("📅 指定假日清單（會套用假日係數，亦計入『例假日放假數』）")
holiday_df = st.data_editor(pd.DataFrame(columns=["date"]), use_container_width=True, num_rows="dynamic", height=200, key="holidays")
holiday_set = set()
for r in holiday_df.itertuples(index=False):
    raw = getattr(r, "date", "")
    if pd.isna(raw) or str(raw).strip()== "": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

st.subheader("📋 每日三班需求（能力單位；自動計算，可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max,
    apply_holiday, holiday_factor, holiday_set
)
df_demand = st.data_editor(
    df_demand_auto,
    use_container_width=True, num_rows="fixed", height=400,
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=ndays, step=1),
        "holiday_factor": st.column_config.NumberColumn("holiday_factor", min_value=1.0, max_value=3.0, step=0.01, format="%.2f"),
        "D_min_units": st.column_config.NumberColumn("D_min_units", min_value=0, max_value=1000, step=1),
        "D_max_units": st.column_config.NumberColumn("D_max_units", min_value=0, max_value=1000, step=1),
        "E_min_units": st.column_config.NumberColumn("E_min_units", min_value=0, max_value=1000, step=1),
        "E_max_units": st.column_config.NumberColumn("E_max_units", min_value=0, max_value=1000, step=1),
        "N_min_units": st.column_config.NumberColumn("N_min_units", min_value=0, max_value=1000, step=1),
        "N_max_units": st.column_config.NumberColumn("N_max_units", min_value=0, max_value=1000, step=1),
    }, key="demand_editor"
)

# ================= Run =================
def run_schedule():
    sched, demand_map, role_map, id_list, capa_map, wcap_map, senior_map = build_initial_schedule(
        year, month, roles_df, must_off_df, wish_off_df, df_demand
    )
    if allow_cross:
        sched = cross_shift_balance_same_day_with_units(year, month, id_list, sched, demand_map, role_map, capa_map, senior_map)

    nd = days_in_month(year, month)

    # 班表輸出（每日每人一欄）
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid], "senior": senior_map.get(nid, False), "capacity": capa_map.get(nid,1)}
        row.update({str(d): sched[nid][d] for d in range(1, nd+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","senior","id"]).reset_index(drop=True)

    # 統計（以出勤天數公平；另列本月例假日放假數）
    def count_code(nid, code): return sum(1 for d in range(1, nd+1) if sched[nid][d] == code)
    # 例假日定義：週日 + holiday_set
    def is_holiday_day(d):
        return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)

    holiday_off_count = {nid: sum(1 for d in range(1, nd+1) if is_holiday_day(d) and sched[nid][d] == "O") for nid in id_list}

    summary_df = pd.DataFrame([{
        "id": nid,
        "shift": role_map[nid],
        "senior": senior_map.get(nid, False),
        "capacity": capa_map.get(nid,1),
        "D天數": count_code(nid,"D"),
        "E天數": count_code(nid,"E"),
        "N天數": count_code(nid,"N"),
        "O天數": count_code(nid,"O"),
        "本月例假日放假數": holiday_off_count[nid],
    } for nid in id_list]).sort_values(["shift","senior","id"]).reset_index(drop=True)

    # 達標（以能力單位）
    comp_rows = []
    for d in range(1, nd+1):
        row = df_demand[df_demand["day"] == d]
        factor = float(row["holiday_factor"].iloc[0]) if not row.empty and "holiday_factor" in row.columns else 1.0
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act_units = sum(capa_map.get(nid,1) for nid in id_list if sched[nid][d] == s)
            if act_units < mn:
                status = f"🔴 不足(-{mn-act_units})"
            elif act_units > mx:
                status = f"🟡 超編(+{act_units-mx})"
            else:
                status = "🟢 達標"
            comp_rows.append({
                "day": d, "shift": s, "holiday_factor": factor,
                "min_units": mn, "max_units": mx, "actual_units": act_units, "狀態": status
            })
    compliance_df = pd.DataFrame(comp_rows)

    # 額外檢查：白班資深比例（資訊用）
    check_rows = []
    for d in range(1, nd+1):
        d_people = [nid for nid in id_list if sched[nid][d] == "D"]
        total = len(d_people)
        senior_cnt = sum(1 for nid in d_people if senior_map.get(nid, False))
        ok = (senior_cnt >= ceil(total/3)) if total>0 else True
        check_rows.append({"day": d, "D_total_persons": total, "D_senior_persons": senior_cnt, "符合白班資深≥1/3": "✅" if ok else "❌"})
    senior_check_df = pd.DataFrame(check_rows)

    return roster_df, summary_df, compliance_df, senior_check_df

if st.button("🚀 產生班表", type="primary"):
    roster_df, summary_df, compliance_df, senior_check_df = run_schedule()

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 capacity / senior / 本月例假日放假數）")
    st.dataframe(summary_df, use_container_width=True, height=380)

    st.subheader("📊 每日達標（能力單位）")
    st.dataframe(compliance_df, use_container_width=True, height=380)

    st.subheader("🧭 白班資深比例檢查（資訊）")
    st.dataframe(senior_check_df, use_container_width=True, height=360)

    # 下載（單行 f-string，避免斷行）
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"roster_{year}-{month:02d}_capacity_units.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"summary_{year}-{month:02d}_capacity_units.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"compliance_{year}-{month:02d}_capacity_units.csv")
    st.download_button("⬇️ 下載 CSV 白班資深比例檢查", data=senior_check_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"senior_check_{year}-{month:02d}.csv")
else:
    st.info("請輸入人員（含 capacity/weekly_cap/senior）、必休/想休、總床數與護病比，必要時設定假日係數與假日日期，然後按「產生班表」。")

