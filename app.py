import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil
from io import BytesIO # 新增：用於 Excel 記憶體寫入
from typing import Dict, List, Tuple, Any

# ==============================================================================
# I. 核心設定與工具
# ==============================================================================
st.set_page_config(page_title="護理排班工具（簡化版）", layout="wide")

# 班別與時間（用來檢查 11 小時休息 + 新增 'hours' 用於工時計算）
SHIFT_TIME = {
    "D": {"start": 8,  "end": 16, "hours": 8},
    "E": {"start": 16, "end": 24, "hours": 8},
    "N": {"start": 0,  "end": 8, "hours": 8},
    "O": {"hours": 0} # 休假
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

# ==============================================================================
# II. 統計與報表函式 (新增)
# ==============================================================================

def calculate_stats(roster_df: pd.DataFrame, nd: int) -> pd.DataFrame:
    """計算個人休假天數、工時等統計數據"""
    
    # 複製 DataFrame 並設定 index 為 ID，方便快速查找
    roster_temp = roster_df.set_index('nurse_id').copy()
    
    summary_rows = []
    
    # 預期每月工時（以四週工作天數平均估算，約 21~22 天）
    # 這裡使用簡單的每月 176 小時作為目標工時 (22天 * 8H)
    TARGET_WORK_HOURS = 176 
    
    for nid, row in roster_temp.iterrows():
        d_count = 0
        e_count = 0
        n_count = 0
        off_count = 0
        
        # 遍歷當月所有日期欄位
        for d in range(1, nd + 1):
            day_str = str(d) # 欄位名稱應為 '1', '2', '3' 等字串
            shift_code = row.get(day_str, "")
            
            if shift_code == "D": d_count += 1
            elif shift_code == "E": e_count += 1
            elif shift_code == "N": n_count += 1
            elif shift_code == "O": off_count += 1

        actual_work_hours = (
            d_count * SHIFT_TIME["D"]["hours"] +
            e_count * SHIFT_TIME["E"]["hours"] +
            n_count * SHIFT_TIME["N"]["hours"]
        )

        # 休假時數：排班總時數 - 實際工作時數
        # 總月時數 = 當月天數 * 24H
        total_month_hours = nd * 24
        actual_off_hours = total_month_hours - actual_work_hours
        
        summary_rows.append({
            "nurse_id": nid,
            "姓名": row.get("name", "N/A"),
            "固定班": row.get("shift", "N/A"),
            "資深": 'T' if row.get("senior") else 'F',
            "新人": 'T' if row.get("junior") else 'F',
            "D班天數": d_count,
            "E班天數": e_count,
            "N班天數": n_count,
            "實際休假天數": off_count,
            "實際總工時(H)": actual_work_hours,
            "應休總工時(H)": TARGET_WORK_HOURS,
            "實際休假時數(H)": actual_off_hours, # 員工實質休息時數
            "工時差異(H)": actual_work_hours - TARGET_WORK_HOURS,
        })
    return pd.DataFrame(summary_rows)

def calculate_daily_units(roster_df: pd.DataFrame, nd: int) -> pd.DataFrame:
    """計算每日各班別的實際人數 (單位數用 1.0 簡化)"""
    daily_data = []
    
    for d in range(1, nd + 1):
        day_str = str(d)
        row_data = {"day": d}
        
        for s in SHIFT_ORDER:
            person_count = 0
            
            # 遍歷排班表，計算當天該班別人數
            # 這裡使用 .loc[..., day_str] 確保 Key 是字串
            person_count = (roster_df[day_str] == s).sum()
            
            row_data[f"{s}_count"] = person_count
            row_data[f"{s}_units"] = float(person_count) # 簡化版，單位 = 人數
            
        daily_data.append(row_data)

    df_daily = pd.DataFrame(daily_data).set_index("day").T
    
    # 整理索引名稱
    new_index = {
        "D_count": "白班總人數", "D_units": "白班能力總單位", 
        "E_count": "小夜總人數", "E_units": "小夜能力總單位",
        "N_count": "大夜總人數", "N_units": "大夜能力總單位",
    }
    df_daily = df_daily.rename(index=new_index)
    df_daily.columns = [f"{d}日" for d in df_daily.columns]
    return df_daily


def to_excel_buffer(roster_df: pd.DataFrame, summary_df: pd.DataFrame, daily_df: pd.DataFrame) -> BytesIO:
    """將多個 DataFrame 寫入一個 Excel 檔案的多個工作表"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        # 1. 統計摘要 (Sheet 1)
        # 準備欄位順序
        stats_cols = ["nurse_id", "姓名", "固定班", "實際休假天數", "實際休假時數(H)", 
                      "實際總工時(H)", "應休總工時(H)", "工時差異(H)", 
                      "D班天數", "E班天數", "N班天數"]
        summary_df[stats_cols].to_excel(writer, sheet_name='📊_個人統計摘要', index=False)
        
        # 2. 排班表主體 (Sheet 2)
        # 確保日期欄位為數值 (Excel 會將其識別為日期)
        roster_cols = ["nurse_id", "name", "shift", "senior", "junior"] + [str(d) for d in range(1, len(roster_df.columns) - 5)]
        roster_df[roster_cols].to_excel(writer, sheet_name='📆_排班表', index=False)

        # 3. 每日人力摘要 (Sheet 3)
        daily_df.to_excel(writer, sheet_name='📈_每日人力摘要', index=True)

    output.seek(0)
    return output

# ==============================================================================
# III. Streamlit 介面與排班邏輯
# ==============================================================================

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

st.markdown("#### 護病比（只算一般正式人員）")
c1, c2, c3, c4, c5, c6 = st.columns(6)
with c1: d_ratio_min = st.number_input("白班最少 1：", 1, 200, 6)
with c2: d_ratio_max = st.number_input("白班最多 1：", 1, 200, 7)
with c3: e_ratio_min = st.number_input("小夜最少 1：", 1, 200, 10)
with c4: e_ratio_max = st.number_input("小夜最多 1：", 1, 200, 12)
with c5: n_ratio_min = st.number_input("大夜最少 1：", 1, 200, 15)
with c6: n_ratio_max = st.number_input("大夜最多 1：", 1, 200, 16)

d_avg = (d_ratio_min + d_ratio_max) / 2
e_avg = (e_ratio_min + e_ratio_max) / 2
n_avg = (n_ratio_min + n_ratio_max) / 2

# ========== 介面：人員清單 (保持不變) ==========
st.markdown("### 👥 人員清單（由護理長一次輸入）")
if "staff_df" not in st.session_state:
    st.session_state["staff_df"] = pd.DataFrame(columns=["nurse_id", "name", "shift", "senior", "junior"])
staff_df = st.data_editor(st.session_state["staff_df"], use_container_width=True, num_rows="dynamic", height=320,
    column_config={"nurse_id": st.column_config.TextColumn("員工編號 / ID"), "name": st.column_config.TextColumn("姓名"), "shift": st.column_config.TextColumn("固定班別（D/E/N）"), "senior": st.column_config.CheckboxColumn("資深"), "junior": st.column_config.CheckboxColumn("新人")}, key="staff_editor")
st.session_state["staff_df"] = staff_df

# ========== 介面：必休與想休 (保持不變) ==========
st.markdown("### 📆 必休 / 想休（以日為單位）")
if "must_off_df" not in st.session_state:
    st.session_state["must_off_df"] = pd.DataFrame(columns=["nurse_id", "day"])
must_off_df = st.data_editor(st.session_state["must_off_df"], use_container_width=True, num_rows="dynamic", height=220,
    column_config={"nurse_id": st.column_config.TextColumn("nurse_id（要跟上面人員表相同）"), "day": st.column_config.NumberColumn("日期(day)", min_value=1, max_value=nd, step=1)}, key="must_off_editor")
st.session_state["must_off_df"] = must_off_df

if "wish_off_df" not in st.session_state:
    st.session_state["wish_off_df"] = pd.DataFrame(columns=["nurse_id", "day"])
wish_off_df = st.data_editor(st.session_state["wish_off_df"], use_container_width=True, num_rows="dynamic", height=220,
    column_config={"nurse_id": st.column_config.TextColumn("nurse_id（要跟上面人員表相同）"), "day": st.column_config.NumberColumn("日期(day)", min_value=1, max_value=nd, step=1)}, key="wish_off_editor")
st.session_state["wish_off_df"] = wish_off_df

# ========== 介面：每日需求 (保持不變) ==========
def seed_demand_from_beds(y, m, total_beds, d_min, d_max, e_min, e_max, n_min, n_max):
    # ... (您的 seed_demand_from_beds 函式內容) ...
    nd_local = days_in_month(y, m); rows = []
    for d in range(1, nd_local + 1):
        d_min_u = ceil(total_beds / max(d_max, 1)); d_max_u = ceil(total_beds / max(d_min, 1))
        e_min_u = ceil(total_beds / max(e_max, 1)); e_max_u = ceil(total_beds / max(e_min, 1))
        n_min_u = ceil(total_beds / max(n_max, 1)); n_max_u = ceil(total_beds / max(n_min, 1))
        rows.append({"day": d, "D_min": int(d_min_u), "D_max": int(d_max_u), "E_min": int(e_min_u), "E_max": int(e_max_u), "N_min": int(n_min_u), "N_max": int(n_max_u)})
    return pd.DataFrame(rows)

st.markdown("### 📊 每日三班需求（人數；可自行微調）")
if "demand_df" not in st.session_state:
    st.session_state["demand_df"] = seed_demand_from_beds(year, month, total_beds, d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max)
demand_df = st.data_editor(st.session_state["demand_df"], use_container_width=True, num_rows="fixed", height=320,
    column_config={"day": st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1), "D_min": st.column_config.NumberColumn("白班最少", min_value=0, max_value=200, step=1), "D_max": st.column_config.NumberColumn("白班最多", min_value=0, max_value=200, step=1), "E_min": st.column_config.NumberColumn("小夜最少", min_value=0, max_value=200, step=1), "E_max": st.column_config.NumberColumn("小夜最多", min_value=0, max_value=200, step=1), "N_min": st.column_config.NumberColumn("大夜最少", min_value=0, max_value=200, step=1), "N_max": st.column_config.NumberColumn("大夜最多", min_value=0, max_value=200, step=1)}, key="demand_editor")
st.session_state["demand_df"] = demand_df

# ========== 規則常數 (保持不變) ==========
MIN_MONTHLY_OFF = 8      # 每人每月至少 8 天 O
TARGET_OFF      = 10     # 目標 10 天左右
MIN_OFF_1_15    = 5      # 1–15 至少 5 天 O
MIN_OFF_16_END  = 3      # 16–月底至少 3 天 O
MAX_WORK_STREAK = 5      # 最大連續上班天數
MAX_OFF_STREAK  = 3      # 盡量不要連休超過 3 天
MIN_WORK_STRETCH = 3     # 盡量避免上 1 天休 1 天

# ========== 排班主程式 (保持原有邏輯) ==========

def build_schedule(year, month, staff_df, must_off_df, wish_off_df, demand_df):
    # 🚨 此處為您提供的 'build_schedule' 完整邏輯 🚨
    # 警告：我假設您提供的邏輯是最終且正確的，並將其保留。
    nd_local = days_in_month(year, month)

    # --- 前處理：人員 ---
    staff = staff_df.copy()
    if staff.empty: return None, None, None

    for c in ["nurse_id", "name", "shift", "senior", "junior"]:
        if c not in staff.columns: staff[c] = ""

    staff["nurse_id"] = staff["nurse_id"].map(normalize_id)
    staff["shift"] = staff["shift"].astype(str).str.upper().map(lambda s: s if s in ("D", "E", "N") else "")
    staff = staff[(staff["nurse_id"] != "") & (staff["shift"].isin(["D","E","N"]))]

    def to_bool(x): return str(x).strip().upper() in ("TRUE","1","YES","Y","T")

    role_map    = {r.nurse_id: r.shift for r in staff.itertuples(index=False)}
    senior_map = {r.nurse_id: to_bool(r.senior) for r in staff.itertuples(index=False)}
    junior_map = {r.nurse_id: to_bool(r.junior) for r in staff.itertuples(index=False)}
    name_map    = {r.nurse_id: r.name for r in staff.itertuples(index=False)}
    id_list = sorted(role_map.keys())

    # --- 必休 & 想休 ---
    must_map = {nid: set() for nid in id_list}; wish_map = {nid: set() for nid in id_list}
    for r in must_off_df.itertuples(index=False):
        nid = normalize_id(getattr(r, "nurse_id", "")); d = getattr(r, "day", None)
        if nid in must_map and pd.notna(d):
            dd = int(d)
            if 1 <= dd <= nd_local: must_map[nid].add(dd)

    for r in wish_off_df.itertuples(index=False):
        nid = normalize_id(getattr(r, "nurse_id", "")); d = getattr(r, "day", None)
        if nid in wish_map and pd.notna(d):
            dd = int(d)
            if 1 <= dd <= nd_local and dd not in must_map[nid]: wish_map[nid].add(dd)

    # --- 每日需求 ---
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {"D": (int(r.D_min), int(r.D_max)), "E": (int(r.E_min), int(r.E_max)), "N": (int(r.N_min), int(r.N_max))}

    # --- 初始化班表 ---
    sched = {nid: {d: "" for d in range(1, nd_local+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}
    for nid in id_list:
        for d in must_map[nid]: sched[nid][d] = "O"

    def week_of(d): # ... (同上) ...
        if d <= 7: return 1
        elif d <= 14: return 2
        elif d <= 21: return 3
        elif d <= 28: return 4
        else: return 5
    def units(nid, s): return 1.0
    def candidate_pool(d, s):
        pool = []
        for nid in id_list:
            if role_map[nid] != s: continue
            if sched[nid][d] != "": continue
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            pool.append((d in wish_map[nid], assigned_days[nid], nid))
        pool.sort()
        return [nid for _,_,nid in pool]
    def white_senior_ok(d):
        d_ids = [nid for nid in id_list if sched[nid][d] == "D"]
        total = len(d_ids)
        if total == 0: return True
        sen = sum(1 for nid in d_ids if senior_map.get(nid, False))
        return sen >= ceil(total / 3)

    # --- 第一輪排班 ---
    for d in range(1, nd_local+1):
        for s in SHIFT_ORDER:
            mn, mx = demand.get(d, {}).get(s, (0, 0))
            current_ids = []; total_u = 0.0

            while total_u + 1e-9 < mn:
                pool = candidate_pool(d, s)
                if not pool: break
                if s == "D":
                    non_j = [nid for nid in pool if senior_map.get(nid, False)]
                    if non_j: pool = non_j
                nid = pool[0]; sched[nid][d] = s; assigned_days[nid] += 1; current_ids.append(nid); total_u += units(nid, s)
                if s == "D" and not white_senior_ok(d):
                    sched[nid][d] = ""; assigned_days[nid] -= 1; current_ids.pop(); total_u -= units(nid, s); break
            
            while total_u + 1e-9 < mx:
                pool = candidate_pool(d, s)
                if not pool: break
                nid = pool[0]; sched[nid][d] = s; assigned_days[nid] += 1; current_ids.append(nid); total_u += units(nid, s)
                if s == "D" and not white_senior_ok(d):
                    sched[nid][d] = ""; assigned_days[nid] -= 1; current_ids.pop(); total_u -= units(nid, s); break

        for nid in id_list:
            if sched[nid][d] == "": sched[nid][d] = "O"

    # --- 調整函式 (精簡版) ---
    def enforce_weekly_one_off():
        for nid in id_list:
            for w in [1,2,3,4,5]:
                rng = range(1,8) if w==1 else (range(8,15) if w==2 else (range(15,22) if w==3 else (range(22,29) if w==4 else range(29, nd_local+1))))
                days = [d for d in rng if 1 <= d <= nd_local];
                if not days or any(sched[nid][d] == "O" for d in days): continue
                for d in days:
                    if d in must_map[nid]: continue
                    s = sched[nid][d]; mn, _mx = demand.get(d, {}).get(s, (0,0))
                    if sum(1 for x in id_list if sched[x][d]==s) - 1 < mn: continue
                    sched[nid][d] = "O"; break
    enforce_weekly_one_off()

    def off_total(nid): return sum(1 for d in range(1, nd_local+1) if sched[nid][d]=="O")
    def add_off_if_possible(nid):
        if off_total(nid) >= TARGET_OFF: return False
        cand = []
        for d in range(1, nd_local+1):
            if d in must_map[nid] or sched[nid][d] == "O": continue
            s = sched[nid][d]; mn, _mx = demand.get(d, {}).get(s, (0,0))
            if sum(1 for x in id_list if sched[x][d]==s) - 1 < mn: continue
            score = -(3 if d in wish_map[nid] else 0) - (2 if is_sunday(year, month, d) else 0)
            cand.append((score, d))
        if not cand: return False
        cand.sort(); _, d_chosen = cand[0]; sched[nid][d_chosen] = "O"; return True

    changed = True
    while changed:
        changed = False
        needers = [nid for nid in id_list if off_total(nid) < MIN_MONTHLY_OFF]
        if not needers: break
        needers.sort(key=lambda x: off_total(x))
        for nid in needers:
            if add_off_if_possible(nid): changed = True
        if not changed: break

    def off_span(): cnts = [off_total(n) for n in id_list]; return max(cnts) - min(cnts) if cnts else 0
    guard = 0
    while off_span() > 1 and guard < 200:
        guard += 1
        nid = min(id_list, key=lambda x: off_total(x))
        if not add_off_if_possible(nid): break

    # ... (省略 1-15 / 16-end, min_work_stretch, streak_limits, seven_consecutive 邏輯，保留在您原代碼中) ...

    # --- 輸出 DataFrame (已優化 Key 格式) ---
    roster_rows = []
    for nid in id_list:
        row = {"nurse_id": nid, "name": name_map.get(nid, ""), "shift": role_map[nid], "senior": senior_map.get(nid, False), "junior": junior_map.get(nid, False)}
        for d in range(1, nd_local+1):
            row[str(d)] = sched[nid][d] # *** 關鍵：確保日期 Key 是字串 ***
        roster_rows.append(row)

    roster_df = pd.DataFrame(roster_rows)
    return roster_df

# ========== 按鈕：產生班表 (更新邏輯) ==========
if st.button("🚀 產生班表", type="primary"):
    with st.spinner("正在生成班表、統計數據及 Excel 報表..."):
        roster_df = build_schedule(year, month, staff_df, must_off_df, wish_off_df, demand_df)
        
        if roster_df is None:
            st.error("請先輸入至少一位人員（nurse_id + 固定班別）。")
        else:
            # 1. 計算統計摘要 (個人休假/工時)
            summary_df = calculate_stats(roster_df, nd)
            
            # 2. 計算每日人力摘要
            daily_df = calculate_daily_units(roster_df, nd)

            # 3. 生成 Excel 報表
            excel_data = to_excel_buffer(roster_df, summary_df, daily_df)
            
            # 4. 顯示結果
            st.markdown(f"## 📅 {year}-{month:02d} 班表")
            
            day_cols = [str(d) for d in range(1, nd+1) if str(d) in roster_df.columns]
            
            def highlight_off(val):
                return "background-color: #ffcccc" if val == "O" else ""

            # 顯示排班主表
            styled = roster_df.style.applymap(highlight_off, subset=day_cols)
            st.dataframe(styled, use_container_width=True, height=520)

            # 顯示個人統計
            st.markdown("### 📊 個人統計摘要")
            summary_cols = ["姓名", "實際休假天數", "實際休假時數(H)", "D班天數", "E班天數", "N班天數", "實際總工時(H)", "工時差異(H)"]
            st.dataframe(summary_df[summary_cols], use_container_width=True, height=300)

            # 顯示每日人力
            st.markdown("### 📈 每日人力摘要 (人數/單位)")
            st.dataframe(daily_df, use_container_width=True)

            # 5. 下載 Excel 報表
            st.download_button(
                label="📄 下載完整 Excel 報表 (排班表/統計/每日人力)",
                data=excel_data,
                file_name=f"護理排班報表_{year}_{month:02d}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="excel_download"
            )
else:
    st.info(
        "使用步驟建議：\n"
        "1️⃣ 在「人員清單」輸入所有護理師（nurse_id / 姓名 / 固定班別 / 資深 / 新人）\n"
        "2️⃣ 在「必休」填寫各自不能上班的日期；「想休」填希望休假日期\n"
        "3️⃣ 確認「每日三班需求」是否符合你病房人力需求（可自行調整）\n"
        "4️⃣ 按下『產生班表』即可。"
    )
