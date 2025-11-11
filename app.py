import streamlit as st
import pandas as pd
from datetime import datetime
import calendar

st.set_page_config(page_title="Nurse Roster (3 Shifts, Fixed Role, Equal Off)", layout="wide")

st.title("🩺 三班制排班｜固定班別（不可跨班）＋等量休假")
st.caption("每位人員固定 D/E/N 班別；每日三班需求可自訂；想休(O)優先；同班別內做等量休假調整；符合 11 小時休息。")

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

# 三班時間（跨日休息檢查用；固定單班者天然 >= 11h）
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

def parse_id_list(text: str):
    if not text: return []
    tokens = [t.strip() for t in text.replace("\n"," ").replace(","," ").split(" ") if t.strip()]
    ids = []
    for t in tokens:
        try: ids.append(int(t))
        except: pass
    return sorted(list(set(ids)))

# ===== Build initial schedule (respect fixed shift role) =====
def build_initial_schedule(year, month, roster_roles_df, prefs_df, demand_df):
    """每位人員固定班別；先依偏好標 O，再在各自班別內公平補足需求，剩餘補 O。"""
    days = days_in_month(year, month)

    # 1) 角色表：id -> role (D/E/N)
    role_map = {}
    for r in roster_roles_df.itertuples(index=False):
        try:
            nid = int(r.id); role = str(r.shift).strip().upper()
            if role in ("D","E","N"): role_map[nid] = role
        except: pass
    id_list = sorted(role_map.keys())

    # 2) 偏好 map
    pref_map = {nid: set() for nid in id_list}
    for r in prefs_df.itertuples(index=False):
        try:
            dt = pd.to_datetime(r.date); nid = int(r.nurse_id)
            if nid in pref_map and dt.year == year and dt.month == month:
                pref_map[nid].add(int(dt.day))
        except: pass

    # 3) 需求 map
    demand = {}
    for r in demand_df.itertuples(index=False):
        demand[int(r.day)] = {"D": int(r.D_required), "E": int(r.E_required), "N": int(r.N_required)}

    # 4) 初始化
    sched = {nid: {d: "" for d in range(1, days+1)} for nid in id_list}

    # 先放 O（偏好）
    for nid in id_list:
        for d in pref_map[nid]:
            if 1 <= d <= days:
                sched[nid][d] = "O"

    # 計數器：各自角色的工作次數，公平分配
    role_count = {nid: 0 for nid in id_list}

    # 5) 逐日補足各班需求（人員只能上自己的班）
    shortage_log = []
    for d in range(1, days+1):
        for s in ORDER:
            req = demand[d][s]
            # 候選：此班別的成員、當天未排、不是 O、跨日休息OK
            candidates = []
            for nid in id_list:
                if role_map[nid] != s: continue
                if sched[nid][d] != "":  # 已 O 或已排
                    continue
                prev_code = sched[nid].get(d-1, "")
                # 因為不可跨班，若前一天也是本班或 O，休息一定OK；保險檢查:
                if rest_ok(prev_code, s):
                    candidates.append(nid)
            candidates.sort(key=lambda k: (role_count[k], k))
            chosen = candidates[:req]
            for nid in chosen:
                sched[nid][d] = s
                role_count[nid] += 1

            # 記錄不足/超編
            actual = sum(1 for nid in id_list if sched[nid][d] == s)
            if actual < req:
                shortage_log.append((d, s, req-actual))

        # 其餘補 O
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, shortage_log

def weekly_rest_soft_guard(sched, nid, days):
    """軟性：盡量保留每週至少一天 O"""
    for rng in [range(1,8), range(8,15), range(15,22), range(22,29), range(29, days+1)]:
        if sum(1 for dd in rng if sched[nid][dd] == "O") == 0:
            return False
    return True

# ===== Equalize off days within each shift pool =====
def equalize_off_by_pool(year, month, id_list, sched, demand, role_map):
    """在每個班別池內（D池、E池、N池）讓 O 盡量相等；不跨班。"""
    days = days_in_month(year, month)

    def off_count(nid): return sum(1 for d in range(1, days+1) if sched[nid][d] == "O")

    results = {}
    for s in ORDER:
        pool = [nid for nid in id_list if role_map[nid] == s]
        if not pool:
            results[s] = 0
            continue

        # 該班總需求
        total_req_s = sum(demand.get(d, {}).get(s, 0) for d in range(1, days+1))
        n = len(pool)
        avg_off = (n*days - total_req_s) / n if n else 0
        target_off = int(round(avg_off))

        # 若已經全等，略過
        offs = {nid: off_count(nid) for nid in pool}
        if min(offs.values(), default=0) == max(offs.values(), default=0) == target_off:
            results[s] = target_off
            continue

        # 迭代同班別池內交換：把 O 過多者在某日的 O 換成本班工作，與 O 過少者在同日同班對調
        for _ in range(4000):
            over = [nid for nid in pool if off_count(nid) > target_off]
            under = [nid for nid in pool if off_count(nid) < target_off]
            if not over or not under:
                break

            over.sort(key=lambda nid: (-off_count(nid), nid))
            under.sort(key=lambda nid: (off_count(nid), nid))
            moved = False

            for nid_over in over:
                # 尋找他的一天 O，嘗試與當天本班的某位 under 交換
                for d in range(1, days+1):
                    if sched[nid_over][d] != "O":
                        continue
                    # 當天本班需求數
                    req = demand.get(d, {}).get(s, 0)
                    # 找在當天本班上班、屬於 under 的人
                    candidates = [nid for nid in under if sched[nid][d] == s]
                    candidates.sort(key=lambda nid: (off_count(nid), nid))
                    # 休息檢查
                    prev_over = sched[nid_over].get(d-1, "O")
                    next_over = sched[nid_over].get(d+1, "O")
                    if not (rest_ok(prev_over, s) and rest_ok(s, next_over)):
                        continue
                    # 嘗試交換
                    for nid_under in candidates:
                        # 將 under 改為 O 是否破壞他每週至少一休？
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

        results[s] = target_off

    return sched, results

# ===== UI: sidebar =====
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（可在主頁表格調整）")
    wd_D = st.number_input("平日：白(D)", 0, 200, 2)
    wd_E = st.number_input("平日：小夜(E)", 0, 200, 1)
    wd_N = st.number_input("平日：大夜(N)", 0, 200, 1)
    sun_D = st.number_input("週日：白(D)", 0, 200, 3)
    sun_E = st.number_input("週日：小夜(E)", 0, 200, 1)
    sun_N = st.number_input("週日：大夜(N)", 0, 200, 1)

    st.subheader("資料上傳（可選）")
    roles_file = st.file_uploader("人員班別 CSV（欄位：id,shift；shift ∈ {D,E,N}）", type=["csv"])
    prefs_file = st.file_uploader("想休 CSV（欄位：nurse_id,date，YYYY-MM-DD）", type=["csv"])
    demand_file = st.file_uploader("每日需求 CSV（欄位：day,D_required,E_required,N_required 或含 date 欄）", type=["csv"])

# ===== 人員班別資料 =====
st.subheader("👥 人員班別設定（每人固定班別，不可跨班）")
if roles_file:
    roles_df = pd.read_csv(roles_file)
else:
    # 提供可編輯範例：10 位，預設 D5/E3/N2
    roles_df = pd.DataFrame({
        "id": list(range(101, 111)),
        "shift": ["D"]*5 + ["E"]*3 + ["N"]*2
    })
roles_df = st.data_editor(
    roles_df, use_container_width=True, num_rows="dynamic",
    height=240
)
# 只保留合法 shift
roles_df["shift"] = roles_df["shift"].astype(str).str.upper().map(lambda x: x if x in ("D","E","N") else "")
roles_df = roles_df[roles_df["shift"].isin(["D","E","N"])].dropna(subset=["id"])

# ===== 想休資料 =====
st.subheader("📝 員工想休（本月）")
if prefs_file:
    prefs_df = pd.read_csv(prefs_file)
else:
    prefs_df = pd.DataFrame(columns=["nurse_id", "date"])
month_prefix = f"{year}-{month:02d}-"
show_prefs = prefs_df[prefs_df["date"].astype(str).str.startswith(month_prefix)].copy()
prefs_edit = st.data_editor(show_prefs, use_container_width=True, num_rows="dynamic", height=260, key="prefs_edit")

# ===== 每日三班需求 =====
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

df_demand = df_demand.sort_values("day").reset_index(drop=True)
for col in ["D_required","E_required","N_required"]:
    df_demand[col] = df_demand[col].astype(int)
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

# ===== 產生班表 =====
if st.button("🚀 產生班表（固定班別 + 等量休假）"):
    sched, demand_map, role_map, shortage_log = build_initial_schedule(year, month, roles_df, prefs_edit, df_demand)

    id_list = sorted(role_map.keys())
    sched_equal, target_off_by_pool = equalize_off_by_pool(year, month, id_list, sched, demand_map, role_map)

    days = days_in_month(year, month)
    # 班表輸出
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid]}
        row.update({str(d): sched_equal[nid][d] for d in range(1, days+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","id"]).reset_index(drop=True)

    # 統計摘要
    def count_code(nid, code): return sum(1 for d in range(1, days+1) if sched_equal[nid][d] == code)
    summary_rows = []
    for nid in id_list:
        summary_rows.append({
            "id": nid,
            "shift": role_map[nid],
            "D天數": count_code(nid, "D"),
            "E天數": count_code(nid, "E"),
            "N天數": count_code(nid, "N"),
            "O天數": count_code(nid, "O"),
        })
    summary_df = pd.DataFrame(summary_rows).sort_values(["shift","id"]).reset_index(drop=True)

    # 每日達標檢視
    comp_rows = []
    for d in range(1, days+1):
        actual = {
            "D": sum(1 for nid in id_list if sched_equal[nid][d] == "D"),
            "E": sum(1 for nid in id_list if sched_equal[nid][d] == "E"),
            "N": sum(1 for nid in id_list if sched_equal[nid][d] == "N"),
        }
        req = demand_map.get(d, {"D":0,"E":0,"N":0})
        for s in ORDER:
            delta = actual[s] - req[s]
            status = "🟢達標" if delta == 0 else ("🟡超編(+{})".format(delta) if delta > 0 else "🔴不足({})".format(delta))
            comp_rows.append({"day": d, "shift": s, "required": req[s], "actual": actual[s], "差額": delta, "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    # 顯示
    st.subheader(f"📅 {year}-{month:02d} 班表（ID｜固定班別）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要")
    st.dataframe(summary_df, use_container_width=True, height=320)

    st.subheader("📊 每日達標檢視")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 目標 O 天數（各班別）
    msg = "、".join([f"{s} 班目標 O：{target_off_by_pool.get(s,0)} 天/人" for s in ORDER])
    st.info(f"等量休假目標（以各班別池內平均四捨五入）：{msg}")

    # 不足提示（若因同班人數不足導致某些日無法達標）
    if shortage_log:
        lines = [f"{d}日 {s} 班缺 {k} 人" for (d,s,k) in shortage_log[:50]]
        st.warning("⚠️ 部分日/班人力不足（固定班別限制下無法補齊）：\n- " + "\n- ".join(lines) + ("\n..." if len(shortage_log)>50 else ""))

    # 下載
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"roster_{year}-{month:02d}_fixedrole_equaloff.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"summary_{year}-{month:02d}_fixedrole_equaloff.csv")
    st.download_button("⬇️ 下載 CSV 每日達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"compliance_{year}-{month:02d}_fixedrole_equaloff.csv")
else:
    st.info("請確認：人員班別表（id,shift）、每日三班需求、想休(O)，再按「產生班表」。")

st.markdown("""
---
**規則與說明**
- 每位人員固定班別（D/E/N），不可跨班分配。
- 想休 (O) 會先標記，再在各自班別內公平補足每日需求；剩餘補 O。
- 「等量休假」只在同班別池內做交換，不跨班；確保不改變每日各班人數，也盡量保留每週至少一休。
- 若某班別在某些日子本來就人力不足，系統會標出不足清單與每日達標表（紅/黃/綠）。
""")
