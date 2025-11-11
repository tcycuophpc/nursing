import streamlit as st
import pandas as pd
from datetime import datetime
import calendar

st.set_page_config(page_title="Nurse Roster (3 Shifts, Fixed Role, Equal Off + Balancer)", layout="wide")

st.title("🩺 三班制排班｜固定班別＋等量休假｜ID 支援中文英文｜可選跨班平衡")
st.caption("固定班別(D/E/N) 不跨班為預設；可選擇在出現缺額/超編時啟用『跨班平衡』。ID 支援中文/英文/數字。")

# ===== Helpers =====
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

def seed_demand_df(y, m, wdD, sunD, wdE, sunE, wdN, sunN):
    rows = []
    for d in range(1, days_in_month(y, m)+1):
        sun = is_sunday(y, m, d)
        rows.append({
            "day": d,
            "D_required": int(sunD if sun else wdD),
            "E_required": int(sunE if sun else wdE),
            "N_required": int(sunN if sun else wdN),
        })
    return pd.DataFrame(rows, columns=["day","D_required","E_required","N_required"])

def normalize_id(x) -> str:
    if pd.isna(x): return ""
    return str(x).strip()

# ===== 初排：固定班別，不跨班 =====
def build_initial_schedule(year, month, roles_df, prefs_df, demand_df):
    days = days_in_month(year, month)
    # 角色表：id(str)->role
    role_map = {}
    for r in roles_df.itertuples(index=False):
        nid = normalize_id(r.id)
        role = normalize_id(r.shift).upper()
        if nid and role in ("D","E","N"):
            role_map[nid] = role
    id_list = sorted(role_map.keys(), key=lambda s: s)

    # 偏好 O
    pref_map = {nid: set() for nid in id_list}
    for r in prefs_df.itertuples(index=False):
        nid = normalize_id(r.nurse_id)
        try:
            dt = pd.to_datetime(r.date)
        except Exception:
            continue
        if nid in pref_map and dt.year == year and dt.month == month:
            pref_map[nid].add(int(dt.day))

    # 需求
    demand = {}
    for r in demand_df.itertuples(index=False):
        demand[int(r.day)] = {
            "D": int(r.D_required),
            "E": int(r.E_required),
            "N": int(r.N_required),
        }

    # 初始化
    sched = {nid: {d: "" for d in range(1, days+1)} for nid in id_list}

    # 先放 O（偏好）
    for nid in id_list:
        for d in pref_map[nid]:
            if 1 <= d <= days:
                sched[nid][d] = "O"

    # 公平分配本班
    role_count = {nid: 0 for nid in id_list}
    shortage_log = []
    for d in range(1, days+1):
        for s in ORDER:
            req = demand[d][s]
            # 候選：此班別、當天尚未排、非 O、休息 OK
            candidates = []
            for nid in id_list:
                if role_map[nid] != s: continue
                if sched[nid][d] != "":  # 已 O 或已排
                    continue
                if rest_ok(sched[nid].get(d-1,""), s):
                    candidates.append(nid)
            candidates.sort(key=lambda k: (role_count[k], k))
            chosen = candidates[:req]
            for nid in chosen:
                sched[nid][d] = s
                role_count[nid] += 1

            # 記錄不足
            actual = sum(1 for nid in id_list if sched[nid][d] == s)
            if actual < req:
                shortage_log.append((d, s, req-actual))

        # 其餘補 O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, shortage_log

def weekly_rest_soft_guard(sched, nid, days):
    for rng in [range(1,8), range(8,15), range(15,22), range(22,29), range(29, days+1)]:
        if sum(1 for dd in rng if sched[nid][dd] == "O") == 0:
            return False
    return True

# ===== 等量休假（各班別池內，不跨班） =====
def equalize_off_by_pool(year, month, id_list, sched, demand, role_map):
    days = days_in_month(year, month)
    def off_count(nid): return sum(1 for d in range(1, days+1) if sched[nid][d] == "O")
    target_by_pool = {}

    for s in ORDER:
        pool = [nid for nid in id_list if role_map[nid] == s]
        if not pool:
            target_by_pool[s] = 0
            continue
        total_req_s = sum(demand.get(d,{}).get(s,0) for d in range(1, days+1))
        n = len(pool)
        avg_off = (n*days - total_req_s)/n if n else 0
        target = int(round(avg_off))
        target_by_pool[s] = target

        # 若已全等，略過
        offs = {nid: off_count(nid) for nid in pool}
        if offs and min(offs.values()) == max(offs.values()) == target:
            continue

        # 池內交換
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
                    # 當天 s 班的 under 候選
                    cand = [u for u in under if sched[u][d] == s]
                    cand.sort(key=lambda u: (off_count(u), u))
                    # 檢查休息
                    if not (rest_ok(sched[nid_over].get(d-1,""), s) and rest_ok(s, sched[nid_over].get(d+1,""))):
                        continue
                    for nid_under in cand:
                        # 把 under 改 O，是否仍保有每週至少一休
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

# ===== 最後一步（可選）：跨班平衡當日缺額 =====
def cross_shift_balance_same_day(year, month, id_list, sched, demand, role_map):
    """僅在同一天：把其他班的『超編』人員調到有『缺額』的班，保證不破壞 11h 與每週至少一休。"""
    days = days_in_month(year, month)
    # 計算每日現況
    for d in range(1, days+1):
        reqD, reqE, reqN = (demand.get(d,{}).get("D",0), demand.get(d,{}).get("E",0), demand.get(d,{}).get("N",0))
        cur = {
            "D": sum(1 for nid in id_list if sched[nid][d] == "D"),
            "E": sum(1 for nid in id_list if sched[nid][d] == "E"),
            "N": sum(1 for nid in id_list if sched[nid][d] == "N"),
        }
        # 反覆把多的移到少的，直到三班都達標或無法再動
        changed = True
        while changed:
            changed = False
            # 找缺額班
            shortages = [(s, (reqD if s=="D" else reqE if s=="E" else reqN) - cur[s]) for s in ORDER]
            shortages = [(s, k) for s,k in shortages if k > 0]
            if not shortages: break
            # 依缺口大小排序
            shortages.sort(key=lambda x: -x[1])

            # 找超編班
            surplus = [(s, cur[s] - (reqD if s=="D" else reqE if s=="E" else reqN)) for s in ORDER]
            surplus = [(s, k) for s,k in surplus if k > 0]
            if not surplus: break
            surplus.sort(key=lambda x: -x[1])

            for tgt, need in shortages:
                if need <= 0: continue
                for src, extra in surplus:
                    if extra <= 0 or tgt == src: 
                        continue
                    # 從 src 班挑一位可移動的人（優先：非該班固定、或你也可允許固定班別被跨派）
                    # 這裡允許跨班：不限制角色，但可加權：優先移動role_map==tgt的（若你希望仍尊重偏好，可自行調整）
                    candidates = [nid for nid in id_list if sched[nid][d] == src]
                    # 休息檢查 + 每週至少一休檢查（對於被改成 O 的情境不會發生，因為是 src->tgt）
                    # 這是跨班：檢查前後日休息
                    feasible = []
                    for nid in candidates:
                        if not (rest_ok(sched[nid].get(d-1,""), tgt) and rest_ok(tgt, sched[nid].get(d+1,""))):
                            continue
                        feasible.append(nid)
                    if not feasible:
                        continue
                    # 選一位（若你想偏好 role==tgt 可排序）
                    feasible.sort(key=lambda nid: (0 if role_map.get(nid,"") == tgt else 1, nid))
                    mover = feasible[0]
                    # 移動
                    sched[mover][d] = tgt
                    cur[src] -= 1
                    cur[tgt] += 1
                    changed = True
                    need -= 1
                    extra -= 1
                    # 更新 surplus/shortages 記錄
                    for i,(s,k) in enumerate(surplus):
                        if s == src:
                            surplus[i] = (s, cur[src] - (reqD if s=="D" else reqE if s=="E" else reqN))
                    for i,(s,k) in enumerate(shortages):
                        if s == tgt:
                            shortages[i] = (s, (reqD if s=="D" else reqE if s=="E" else reqN) - cur[s])
                    if need <= 0:
                        break
                # 下一個缺口
    return sched

# ===== UI: sidebar =====
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（可在主頁調整）")
    wd_D = st.number_input("平日：白(D)", 0, 200, 2)
    wd_E = st.number_input("平日：小夜(E)", 0, 200, 1)
    wd_N = st.number_input("平日：大夜(N)", 0, 200, 1)
    sun_D = st.number_input("週日：白(D)", 0, 200, 3)
    sun_E = st.number_input("週日：小夜(E)", 0, 200, 1)
    sun_N = st.number_input("週日：大夜(N)", 0, 200, 1)

    st.subheader("選項")
    allow_cross = st.checkbox("允許跨班以平衡缺額（最後一步）", value=True)

    st.subheader("上傳資料（可選）")
    roles_file = st.file_uploader("人員班別 CSV（欄位：id,shift；shift ∈ {D,E,N}；id 可中文/英文）", type=["csv"])
    prefs_file = st.file_uploader("想休 CSV（欄位：nurse_id,date，YYYY-MM-DD；nurse_id 可中文/英文）", type=["csv"])
    demand_file = st.file_uploader("每日需求 CSV（欄位：day,D_required,E_required,N_required 或含 date 欄）", type=["csv"])

# ===== 人員班別 =====
st.subheader("👥 人員班別設定（固定，不跨班）")
if roles_file:
    roles_df = pd.read_csv(roles_file, dtype=str)
else:
    roles_df = pd.DataFrame({
        "id": ["王小美","李大為","Amy","Ben","Carol","張護理","Night01","Night02"],
        "shift": ["D","D","D","E","E","E","N","N"]
    })
roles_df["id"] = roles_df["id"].map(normalize_id)
roles_df["shift"] = roles_df["shift"].map(lambda x: normalize_id(x).upper())
roles_df = st.data_editor(roles_df, use_container_width=True, num_rows="dynamic", height=240)
roles_df = roles_df[roles_df["id"].astype(str).str.len()>0]
roles_df = roles_df[roles_df["shift"].isin(["D","E","N"])]

# ===== 想休 =====
st.subheader("📝 員工想休（本月）")
if prefs_file:
    prefs_df = pd.read_csv(prefs_file, dtype=str)
else:
    prefs_df = pd.DataFrame(columns=["nurse_id","date"])
prefs_df["nurse_id"] = prefs_df.get("nurse_id","").map(normalize_id)
month_prefix = f"{year}-{month:02d}-"
show_prefs = prefs_df[prefs_df.get("date","").astype(str).str.startswith(month_prefix)].copy()
prefs_edit = st.data_editor(show_prefs, use_container_width=True, num_rows="dynamic", height=260, key="prefs_edit")

# ===== 每日需求 =====
st.subheader("📋 每日三班需求（可編輯）")
if demand_file:
    raw = pd.read_csv(demand_file)
    if "day" in raw.columns:
        df_demand = raw.copy()
    elif "date" in raw.columns:
        tmp = raw.copy(); tmp["day"] = pd.to_datetime(tmp["date"]).dt.day
        df_demand = tmp[["day","D_required","E_required","N_required"]]
    else:
        st.error("每日需求 CSV 需有 'day' 欄，並包含 D_required,E_required,N_required。")
        st.stop()
else:
    df_demand = seed_demand_df(year, month, wd_D, sun_D, wd_E, sun_E, wd_N, sun_N)

for col in ["D_required","E_required","N_required"]:
    df_demand[col] = df_demand[col].astype(int)
df_demand = df_demand.sort_values("day").reset_index(drop=True)
df_demand = st.data_editor(
    df_demand,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=days, step=1),
        "D_required": st.column_config.NumberColumn("D_required", min_value=0, max_value=200, step=1),
        "E_required": st.column_config.NumberColumn("E_required", min_value=0, max_value=200, step=1),
        "N_required": st.column_config.NumberColumn("N_required", min_value=0, max_value=200, step=1),
    },
    height=340
)

# ===== 產生 =====
if st.button("🚀 產生班表（固定班別＋等量休假＋可選跨班平衡）"):
    sched, demand_map, role_map, id_list, shortage = build_initial_schedule(year, month, roles_df, prefs_edit, df_demand)
    sched, target_by_pool = equalize_off_by_pool(year, month, id_list, sched, demand_map, role_map)

    # 紅黃綠：等量後的達標檢視
    days = days_in_month(year, month)
    def day_actual(d, s): return sum(1 for nid in id_list if sched[nid][d] == s)
    comp_rows = []
    for d in range(1, days+1):
        for s in ORDER:
            req = demand_map.get(d,{}).get(s,0)
            act = day_actual(d, s)
            delta = act - req
            status = "🟢達標" if delta == 0 else ("🟡超編(+{})".format(delta) if delta > 0 else "🔴不足({})".format(delta))
            comp_rows.append({"day": d, "shift": s, "required": req, "actual": act, "差額": delta, "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    # 可選：跨班平衡（同日內）
    if allow_cross:
        sched = cross_shift_balance_same_day(year, month, id_list, sched, demand_map, role_map)
        # 重新計算達標
        comp_rows = []
        for d in range(1, days+1):
            for s in ORDER:
                req = demand_map.get(d,{}).get(s,0)
                act = sum(1 for nid in id_list if sched[nid][d] == s)
                delta = act - req
                status = "🟢達標" if delta == 0 else ("🟡超編(+{})".format(delta) if delta > 0 else "🔴不足({})".format(delta))
                comp_rows.append({"day": d, "shift": s, "required": req, "actual": act, "差額": delta, "狀態": status})
        compliance_df = pd.DataFrame(comp_rows)

    # 輸出表
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

    # 顯示
    st.subheader(f"📅 {year}-{month:02d} 班表（ID 支援中英）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要")
    st.dataframe(summary_df, use_container_width=True, height=320)
    st.info("各班別等量休假目標（平均四捨五入）： " + "、".join([f"{s}:{target_by_pool.get(s,0)}天/人" for s in ORDER]))

    st.subheader("📊 每日達標檢視（🟢達標｜🟡超編｜🔴不足）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 下載
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"roster_{year}-{month:02d}_fixed_equal_balance.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"summary_{year}-{month:02d}_fixed_equal_balance.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"compliance_{year}-{month:02d}_fixed_equal_balance.csv")
else:
    st.info("請確認：人員班別(id 可中文/英文)、每日三班需求、想休(O)，再按「產生班表」。")
