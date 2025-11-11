import streamlit as st
import pandas as pd
from datetime import datetime
import calendar

st.set_page_config(page_title="Nurse Roster (3 Shifts, Equal Off)", layout="wide")

st.title("🩺 護理師排班工具｜三班制（白D／小夜E／大夜N）＋ 等量休假")
st.caption("輸入/上傳 ID、每日 D/E/N 需求與想休後，按下按鈕產生班表。系統會嘗試讓每人當月休假(O)天數一致，並遵守 11 小時休息。")

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

# 定義三班的起迄時間（簡化版，用於跨日 11h 休息檢查）
SHIFT = {
    "D": {"start": 8,  "end": 16, "hours": 8},
    "E": {"start": 16, "end": 24, "hours": 8},
    "N": {"start": 0,  "end": 8,  "hours": 8},
    "O": {"hours": 0},
}
ORDER = ["D", "E", "N"]

def rest_ok(prev_code: str, next_code: str) -> bool:
    """跨日休息：檢查前一日班別到下一日班別是否 >= 11 小時。
       O 視為不限制；同日只排一班。"""
    if prev_code in (None, "", "O") or next_code in (None, "", "O"):
        return True
    s1, e1 = SHIFT[prev_code]["start"], SHIFT[prev_code]["end"]
    s2, e2 = SHIFT[next_code]["start"], SHIFT[next_code]["end"]
    rest = s2 - e1
    if rest < 0: rest += 24
    return rest >= 11

def seed_demand_df(y, m, wd_need_D, sun_need_D, wd_need_E, sun_need_E, wd_need_N, sun_need_N):
    rows = []
    for d in range(1, days_in_month(y, m) + 1):
        is_sun = is_sunday(y, m, d)
        rows.append({
            "day": d,
            "D_required": int(sun_need_D if is_sun else wd_need_D),
            "E_required": int(sun_need_E if is_sun else wd_need_E),
            "N_required": int(sun_need_N if is_sun else wd_need_N),
        })
    return pd.DataFrame(rows, columns=["day","D_required","E_required","N_required"])

def parse_id_list(text: str):
    if not text:
        return []
    tokens = [t.strip() for t in text.replace("\n", " ").replace(",", " ").split(" ") if t.strip()]
    ids = []
    for t in tokens:
        try: ids.append(int(t))
        except: pass
    return sorted(list(set(ids)))

# ===== Core scheduling =====
def build_initial_schedule(year, month, id_list, prefs_df, demand_df):
    """1) 先以想休標 O；2) 逐日逐班補足需求，公平分配；3) 其他補 O。"""
    days = days_in_month(year, month)
    # 偏好 map：{id: set(day)}
    pref_map = {nid: set() for nid in id_list}
    for r in prefs_df.itertuples(index=False):
        try:
            dt = pd.to_datetime(r.date); nid = int(r.nurse_id)
            if nid in pref_map and dt.year == year and dt.month == month:
                pref_map[nid].add(int(dt.day))
        except:
            pass

    # 需求 map：day -> {D,E,N}
    demand = {}
    for r in demand_df.itertuples(index=False):
        demand[int(r.day)] = {
            "D": int(r.D_required),
            "E": int(r.E_required),
            "N": int(r.N_required),
        }

    # 初始化：每人每天空字串
    sched = {nid: {d: "" for d in range(1, days+1)} for nid in id_list}

    # 先放想休 O
    for nid in id_list:
        for d in pref_map[nid]:
            if 1 <= d <= days:
                sched[nid][d] = "O"

    # 計數器：每人各班被分配數量（為公平分配）
    count_shift = {nid: {"D":0,"E":0,"N":0} for nid in id_list}

    # 逐日逐班分配
    for d in range(1, days+1):
        # 按班別順序，將需求補足
        for s in ORDER:
            req = demand.get(d, {}).get(s, 0)
            # 候選：當天不是 O、尚未有班別的人（避免同日多班），且跨日休息 OK
            candidates = []
            for nid in id_list:
                if sched[nid][d] != "":  # 已有 O 或已安排其他班
                    continue
                prev_code = sched[nid].get(d-1, "")
                if rest_ok(prev_code, s):
                    candidates.append(nid)
            # 按「該班次被分配較少、總量較少、ID」排序，求公平
            candidates.sort(key=lambda k: (count_shift[k][s],
                                           count_shift[k]["D"]+count_shift[k]["E"]+count_shift[k]["N"],
                                           k))
            chosen = candidates[:req]
            for nid in chosen:
                sched[nid][d] = s
                count_shift[nid][s] += 1

        # 其餘空白補 O（保持每人每日最多一班）
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand

def weekly_rest_ok(sched, nid, days):
    """檢查每週至少一個 O（軟性目標，不作硬阻擋，調整時盡量維持）"""
    for w, rng in enumerate([range(1,8), range(8,15), range(15,22), range(22,29), range(29, days+1)], start=1):
        if sum(1 for dd in rng if sched[nid][dd] == "O") == 0:
            return False
    return True

def equalize_off_days(year, month, id_list, sched, demand):
    """嘗試讓每人 O 天數一樣：計算目標 O（四捨五入的平均），
       對於 O 過多的人，嘗試與同日某班的人做交換（對方 O+1、自己 O-1），
       不破壞需求、不破壞 11h 休息，盡量維持每週至少一休。"""
    days = days_in_month(year, month)

    def off_count(nid):
        return sum(1 for d in range(1, days+1) if sched[nid][d] == "O")

    total_required = sum(demand.get(d, {}).get("D",0) +
                         demand.get(d, {}).get("E",0) +
                         demand.get(d, {}).get("N",0)
                         for d in range(1, days+1))
    n = len(id_list)
    avg_off = (n*days - total_required) / n if n else 0
    target_off = int(round(avg_off))  # 以四捨五入平均 O 當目標

    # 計算每日各班實際已排人數（用來保持需求不變）
    def day_counts(d):
        return {
            "D": sum(1 for nid in id_list if sched[nid][d] == "D"),
            "E": sum(1 for nid in id_list if sched[nid][d] == "E"),
            "N": sum(1 for nid in id_list if sched[nid][d] == "N"),
        }

    # 先快速退出條件
    offs = {nid: off_count(nid) for nid in id_list}
    if min(offs.values()) == max(offs.values()) == target_off:
        return sched, target_off

    # 迭代嘗試交換（有限次避免無限循環）
    for _ in range(5000):
        # 找到 O 過多的人與 O 過少的人
        over_list  = [nid for nid in id_list if off_count(nid) > target_off]
        under_list = [nid for nid in id_list if off_count(nid) < target_off]
        if not over_list or not under_list:
            break

        over_list.sort(key=lambda nid: (-off_count(nid), nid))
        under_list.sort(key=lambda nid: (off_count(nid), nid))

        moved = False
        for nid_over in over_list:
            # 過多的人，找他 O 的某一天，嘗試接手別人的班（互換）
            for d in range(1, days+1):
                if sched[nid_over][d] != "O":
                    continue

                # 嘗試三個班別
                for s in ORDER:
                    # 當天 s 班實際人數、需求
                    cnt = day_counts(d)
                    req = demand.get(d, {}).get(s, 0)
                    # 我們不增加/減少日需求，僅交換：找目前在該班的某人 nid_under
                    candidates = [nid for nid in under_list if sched[nid][d] == s]
                    # 為了公平，少休的優先釋出
                    candidates.sort(key=lambda x: (off_count(x), x))

                    for nid_under in candidates:
                        # 交換條件：雙方休息間隔合法、週休不被破壞
                        prev_over = sched[nid_over].get(d-1, "")
                        next_over = sched[nid_over].get(d+1, "")
                        prev_under = sched[nid_under].get(d-1, "")
                        next_under = sched[nid_under].get(d+1, "")

                        if not rest_ok(prev_over, s) or not rest_ok(s, next_over):
                            continue
                        # 對方被換成 O，要檢查他/她是否還保有每週至少一休
                        old_under_code = s
                        # 暫時修改檢查週休
                        old_under_d = sched[nid_under][d]
                        sched[nid_under][d] = "O"
                        ok_week = weekly_rest_ok(sched, nid_under, days)
                        sched[nid_under][d] = old_under_d
                        if not ok_week:
                            continue
                        # O 過多者從 O -> s，也要確保每週至少一休仍可能達成（寬鬆：不把該週唯一 O 用光）
                        w = week_index(d)
                        def week_offs(nid, w):
                            if w==1: rng = range(1,8)
                            elif w==2: rng = range(8,15)
                            elif w==3: rng = range(15,22)
                            elif w==4: rng = range(22,29)
                            else: rng = range(29, days+1)
                            return sum(1 for dd in rng if sched[nid][dd] == "O")
                        if week_offs(nid_over, w) <= 1:
                            continue

                        # 通過檢查，做交換：over 接 s，under 改 O
                        sched[nid_over][d] = s
                        sched[nid_under][d] = "O"
                        moved = True
                        break
                    if moved:
                        break
                if moved:
                    break
            if moved:
                break

        if not moved:
            break

        # 若已達到目標，提前結束
        offs = {nid: sum(1 for d in range(1, days+1) if sched[nid][d] == "O") for nid in id_list}
        if min(offs.values()) == max(offs.values()) == target_off:
            break

    return sched, target_off

# ===== UI: sidebar =====
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（可在主頁表格調整）")
    wd_D = st.number_input("平日：白班(D)", 0, 200, 2)
    wd_E = st.number_input("平日：小夜(E)", 0, 200, 1)
    wd_N = st.number_input("平日：大夜(N)", 0, 200, 1)
    sun_D = st.number_input("週日：白班(D)", 0, 200, 3)
    sun_E = st.number_input("週日：小夜(E)", 0, 200, 1)
    sun_N = st.number_input("週日：大夜(N)", 0, 200, 1)

    st.subheader("資料上傳（可選）")
    nurses_file = st.file_uploader("名單 CSV（欄位：id,name，可留空）", type=["csv"])
    prefs_file  = st.file_uploader("想休 CSV（欄位：nurse_id,date，YYYY-MM-DD）", type=["csv"])
    demand_file = st.file_uploader("每日需求 CSV（欄位：day,D_required,E_required,N_required 或含 date 欄位）", type=["csv"])

# ===== ID 來源 =====
st.subheader("🆔 護理師 ID 清單（可直接貼上）")
id_text = st.text_area("輸入 ID（逗號/空白/換行分隔；例：101 102 103 或 101,102,103）", height=90)

if nurses_file:
    nurses_df = pd.read_csv(nurses_file)
    uploaded_ids = [int(x) for x in pd.Series(nurses_df["id"]).dropna().unique().tolist()]
else:
    nurses_df = pd.DataFrame(columns=["id","name"])
    uploaded_ids = []

ids_manual = parse_id_list(id_text)

# 想休
if prefs_file:
    prefs_df = pd.read_csv(prefs_file)
else:
    prefs_df = pd.DataFrame(columns=["nurse_id","date"])

ids_from_prefs = [int(x) for x in pd.Series(prefs_df["nurse_id"]).dropna().unique().tolist()] if "nurse_id" in prefs_df.columns else []

id_list = sorted(list(set(ids_manual) | set(uploaded_ids) | set(ids_from_prefs)))
if len(id_list) == 0:
    id_list = list(range(1, 21))  # fallback 示範

st.info(f"將以 **{len(id_list)} 位**護理師進行排班。ID：{', '.join(map(str, id_list[:50]))}{' ...' if len(id_list)>50 else ''}")

# ===== 每日需求表 =====
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

# ===== 想休（可編輯） =====
st.subheader("📝 員工想休（本月）")
month_prefix = f"{year}-{month:02d}-"
show_prefs = prefs_df[prefs_df["date"].astype(str).str.startswith(month_prefix)].copy()
prefs_edit = st.data_editor(show_prefs, num_rows="dynamic", use_container_width=True, height=260, key="prefs_edit")

# ===== 產生班表 =====
if st.button("🚀 產生班表（三班 + 等量休假）"):
    # 初排
    sched, demand_map = build_initial_schedule(year, month, id_list, prefs_edit, df_demand)

    # 等量休假調整
    sched_equal, target_off = equalize_off_days(year, month, id_list, sched, demand_map)

    # 輸出表格
    days = days_in_month(year, month)
    roster_rows = []
    for nid in id_list:
        row = {"id": nid}
        row.update({str(d): sched_equal[nid][d] for d in range(1, days+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values("id").reset_index(drop=True)

    # 統計摘要
    def count_code(nid, code):
        return sum(1 for d in range(1, days+1) if sched_equal[nid][d] == code)
    summary_rows = []
    for nid in id_list:
        summary_rows.append({
            "id": nid,
            "D天數": count_code(nid, "D"),
            "E天數": count_code(nid, "E"),
            "N天數": count_code(nid, "N"),
            "O天數": count_code(nid, "O"),
        })
    summary_df = pd.DataFrame(summary_rows).sort_values("id").reset_index(drop=True)

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
    st.subheader(f"📅 {year}-{month:02d} 班表（ID）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要")
    st.dataframe(summary_df, use_container_width=True, height=320)
    st.info(f"目標等量休假天數（四捨五入平均）：**{target_off} 天／人**")

    st.subheader("📊 每日達標檢視")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 下載
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"roster_{year}-{month:02d}_3shifts_equal_off.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"summary_{year}-{month:02d}_3shifts_equal_off.csv")
    st.download_button("⬇️ 下載 CSV 每日達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"compliance_{year}-{month:02d}_3shifts_equal_off.csv")
else:
    st.info("請先確認：ID、每日三班需求與想休，然後按「產生班表（三班 + 等量休假）」。")

st.markdown("""
---
**說明 & 限制**
- 先以想休(O)標記，再公平補足每日 D/E/N 需求；之後進行「等量休假」交換：讓 O 過多的人在不破壞需求與 11 小時休息的前提下，與 O 過少的人**同日同班互換**，以拉齊 O 天數。
- 週休：交換時盡量維持每週至少一休（若該週只剩 1 天 O，將避免動到那天）。
- 若需求配置本身就很緊或偏好過多，可能無法完全達到「人人 O 完全相同」，系統會盡量接近目標值。
- 如需「某人某日**必上**某班」功能、或「最大連續夜班數」等更嚴格規則，也可再加強（會讓演算法更偏向整數規劃/CP-SAT）。
""")
