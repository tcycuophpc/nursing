import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil
from typing import Dict, List, Tuple, Any
from io import BytesIO # 用於 Excel 輸出

# 確保運行環境安裝了 openpyxl: pip install openpyxl

# ==============================================================================
# I. 基本設定與資料路徑
# ==============================================================================
st.set_page_config(page_title="Nurse Roster • 自助註冊版", layout="wide")

# 資料目錄設在目前工作目錄
DATA_DIR = os.path.join(os.getcwd(), "nursing_data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_CSV = os.path.join(DATA_DIR, "users.csv")              # 人員清單
PREFS_CSV_TMPL = os.path.join(DATA_DIR, "prefs_{year}_{month}.csv") # 員工請休
HOLIDAYS_CSV_TMPL = os.path.join(DATA_DIR, "holidays_{year}_{month}.csv") # 例假日
EXTRA_CSV_TMPL = os.path.join(DATA_DIR, "extra_{year}_{month}.csv")       # 加開人力
SCHEDULE_CSV_TMPL = os.path.join(DATA_DIR, "schedule_{year}_{month}.csv") # 排班結果

# 預設護理長帳密
ADMIN_USER = "headnurse"
ADMIN_PASS = "admin123"

# 班別時間（含時數）
SHIFT = {
    "D": {"start": 8,  "end": 16, "hours": 8},
    "E": {"start": 16, "end": 24, "hours": 8},
    "N": {"start": 0,  "end": 8, "hours": 8},
    "O": {"hours": 0}  # 休假
}

ORDER = ["D", "E", "N"]  # 排班處理順序

# ==============================================================================
# II. 工具函式
# ==============================================================================
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
    if rest < 0:
        rest += 24
    return rest >= 11

def normalize_id(x) -> str:
    if pd.isna(x): return ""
    return str(x).strip()

def to_bool(x) -> bool:
    return str(x).strip().upper() in ("TRUE","1","YES","Y","T")

def to_wcap(x):
    try:
        v = int(float(x))
        return v if v >= 0 else None
    except:
        return None

# 輔助函式：用於排班調整時檢查資深比例
def white_senior_ok_if_remove_fn(d, id_list, sched, senior_map):
    """檢查白班移除某人後資深比例是否仍符合 ceil(N/3)"""
    def check(nid_remove):
        if sched[nid_remove].get(d) != "D": return True
        d_people = [x for x in id_list if sched[x].get(d)=="D" and x != nid_remove]
        total = len(d_people)
        if total==0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    return check

def white_senior_ok_if_move_fn(d, id_list, sched, senior_map):
    """檢查白班人員移動前後資深比例是否仍符合 ceil(N/3)"""
    def check(nid_move, from_s, to_s):
        if from_s!="D" and to_s!="D": return True
        
        d_people = [x for x in id_list if sched[x].get(d)=="D"]
        if from_s=="D" and nid_move in d_people: d_people.remove(nid_move)
        if to_s=="D": d_people.append(nid_move)
        
        total = len(d_people)
        if total==0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    return check
    
# ==============================================================================
# III. 資料存取 (Load/Save Functions)
# ==============================================================================

# ... (load_users, save_users, load_prefs, save_prefs, load_holidays, save_holidays, 
# load_extra, save_extra, load_schedule, save_schedule 函式定義請使用您原有的完整代碼) ...

def load_users():
    if os.path.exists(USERS_CSV):
        df = pd.read_csv(USERS_CSV, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=["employee_id","name","pwd4","shift","weekly_cap","senior","junior"])
        df.to_csv(USERS_CSV, index=False)
    for c in ["employee_id","name","pwd4","shift","weekly_cap","senior","junior"]:
        if c not in df.columns: df[c] = ""
    return df

def save_users(df):
    df.to_csv(USERS_CSV, index=False)

def prefs_path(year, month): return PREFS_CSV_TMPL.format(year=year, month=f"{month:02d}")
def load_prefs(year, month):
    p = prefs_path(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        for c in ["nurse_id","date","type"]:
            if c not in df.columns: df[c] = ""
        return df
    return pd.DataFrame(columns=["nurse_id","date","type"])
def save_prefs(df, year, month): df.to_csv(prefs_path(year, month), index=False)

def load_holidays(year, month):
    p = HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        if "date" not in df.columns: df["date"] = ""
        return df
    return pd.DataFrame(columns=["date"])
def save_holidays(df, year, month): df.to_csv(HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

def load_extra(year, month):
    p = EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}")
    nd = days_in_month(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p).fillna(0)
    else:
        df = pd.DataFrame({"day": list(range(1, nd+1)), "D_extra": [0]*nd, "E_extra": [0]*nd, "N_extra": [0]*nd})
    for c in ["day","D_extra","E_extra","N_extra"]:
        if c not in df.columns: df[c] = 0
    return df
def save_extra(df, year, month): df.to_csv(EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

def load_schedule(year, month):
    p = SCHEDULE_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p): return pd.read_csv(p, index_col="employee_id", dtype=str).fillna("")
    return pd.DataFrame()

def save_schedule(df: pd.DataFrame, year, month):
    df.to_csv(SCHEDULE_CSV_TMPL.format(year=year, month=f"{month:02d}"))


# ==============================================================================
# IV. 能力單位與排班核心 (僅框架，需填充完整邏輯)
# ==============================================================================

def seed_demand_from_beds(y, m, total_beds, d_ratio_min=6, d_ratio_max=7, e_ratio_min=10, e_ratio_max=12, n_ratio_min=15, n_ratio_max=16, extra_df=None):
    # ... (您的 seed_demand_from_beds 函式內容) ...
    rows = []
    nd = days_in_month(y, m)
    ext = extra_df if extra_df is not None else pd.DataFrame(columns=["day","D_extra","E_extra","N_extra"])
    if "day" in ext.columns: ext = ext.set_index("day")
    for d in range(1, nd+1):
        D_min = ceil(total_beds / max(d_ratio_max,1)); D_max = ceil(total_beds / max(d_ratio_min,1))
        E_min = ceil(total_beds / max(e_ratio_max,1)); E_max = ceil(total_beds / max(e_ratio_min,1))
        N_min = ceil(total_beds / max(n_ratio_max,1)); N_max = ceil(total_beds / max(n_ratio_min,1))
        d_ex = int(ext.at[d,"D_extra"]) if d in ext.index else 0
        e_ex = int(ext.at[d,"E_extra"]) if d in ext.index else 0
        n_ex = int(ext.at[d,"N_extra"]) if d in ext.index else 0
        rows.append({"day": d, "D_min_units": int(D_min + d_ex), "D_max_units": int(D_max + d_ex),
                     "E_min_units": int(E_min + e_ex), "E_max_units": int(E_max + e_ex),
                     "N_min_units": int(N_min + n_ex), "N_max_units": int(N_max + n_ex)})
    return pd.DataFrame(rows)

def per_person_units(is_junior: bool, shift_code: str, d_avg: float, e_avg: float, n_avg: float, jr_ratio: float = 4.0):
    # ... (您的 per_person_units 函式內容) ...
    if not is_junior: return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0: return 1.0
    return jr_ratio / base

# 以下排班核心函式僅為框架，您需要將您原先的完整實作貼到對應的位置
def build_initial_schedule(year, month, users_df, prefs_df, demand_df, d_avg, e_avg, n_avg) -> Tuple[Dict[str, Dict[int, str]], Dict, Dict, List, Dict, Dict, Dict, Dict, Dict]:
    # 🚨 請將您原先的完整 build_initial_schedule 函式內容貼到此處 🚨
    st.error("【程式碼不完整】請將 build_initial_schedule 的完整邏輯貼到這裡。")
    # 預設返回空值，防止程序崩潰
    nd = days_in_month(year, month)
    id_list = sorted(users_df['employee_id'].dropna().unique().tolist())
    sched = {nid: {d:"" for d in range(1, nd+1)} for nid in id_list}
    demand = {d: {"D": (0,0), "E": (0,0), "N": (0,0)} for d in range(1, nd+1)}
    return sched, demand, {}, id_list, {}, {}, {}, {}, {}

def cross_shift_balance_with_units(year, month, id_list, sched, demand, junior_map, senior_map, d_avg, e_avg, n_avg):
    # 🚨 請將您原先的完整 cross_shift_balance_with_units 函式內容貼到此處 🚨
    return sched

def prefer_off_on_holidays(year, month, sched, demand_df, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set):
    # 🚨 請將您原先的完整 prefer_off_on_holidays 函式內容貼到此處 🚨
    return sched

def enforce_weekly_one_off(year, month, sched, demand_df, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set):
    # 🚨 請將您原先的完整 enforce_weekly_one_off 函式內容貼到此處 🚨
    return sched

def enforce_min_monthly_off(year, month, sched, demand_df, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, min_off=8, balance=True, holiday_set=None, target_off=10):
    # 🚨 請將您原先的完整 enforce_min_monthly_off 函式內容貼到此處 🚨
    return sched

def enforce_consecutive_streaks(year, month, sched, id_list, max_work=5, max_off=2, min_work=3):
    # 🚨 請將您原先的完整 enforce_consecutive_streaks 函式內容貼到此處 🚨
    return sched

# ==============================================================================
# V. 排班統計與 Excel 報表
# ==============================================================================

def analyze_schedule(df_schedule: pd.DataFrame, users_raw: pd.DataFrame, nd: int, min_monthly_off: int, target_off: int) -> pd.DataFrame:
    """計算每人的實際班數、休假時數及合規性 (用於統計表)"""
    stats = []
    df_schedule = df_schedule.fillna("") 
    
    for nid, row in df_schedule.iterrows():
        d_count = sum(1 for d in range(1, nd + 1) if row.get(str(d), "") == "D")
        e_count = sum(1 for d in range(1, nd + 1) if row.get(str(d), "") == "E")
        n_count = sum(1 for d in range(1, nd + 1) if row.get(str(d), "") == "N")
        off_days = sum(1 for d in range(1, nd + 1) if row.get(str(d), "") == "O")
        work_days = d_count + e_count + n_count

        actual_work_hours = (d_count * SHIFT["D"]["hours"] + e_count * SHIFT["E"]["hours"] + n_count * SHIFT["N"]["hours"])

        user_row = users_raw[users_raw["employee_id"] == nid]
        user_info = user_row.iloc[0] if not user_row.empty else {}
        
        # --- 休假時數計算公式 (可根據您的需求修改此處) ---
        total_month_hours = nd * 24
        target_work_hours = (22 * 8) # 範例: 假設每月目標工時 176H
        expected_off_hours = total_month_hours - target_work_hours
        actual_off_hours = total_month_hours - actual_work_hours
        # -----------------------------------------------
        
        is_compliant = "✅ 合格" if off_days >= min_monthly_off else f"❌ 不足 ({off_days}/{min_monthly_off})"
        
        stats.append({
            "員工ID": nid,
            "姓名": user_info.get("name", "N/A"),
            "固定班": user_info.get("shift", "N/A"),
            "資深": 'T' if to_bool(user_info.get("senior")) else 'F',
            "新人": 'T' if to_bool(user_info.get("junior")) else 'F',
            "實際休假天數": off_days,
            "實際休假時數(H)": actual_off_hours,
            "月休天數合規": is_compliant,
            "實際總工時(H)": actual_work_hours,
            "工時差異(H)": actual_work_hours - target_work_hours,
            "D班總數": d_count,
            "E班總數": e_count,
            "N班總數": n_count,
        })
    
    return pd.DataFrame(stats)

def calculate_daily_units(df_schedule: pd.DataFrame, id_list: List[str], users_raw: pd.DataFrame, nd: int, d_avg, e_avg, n_avg) -> pd.DataFrame:
    """計算每日各班別的實際人數和能力單位總和 (用於每日人力表)"""
    senior_map = {r.employee_id: to_bool(r.senior) for r in users_raw.itertuples(index=False)}
    junior_map = {r.employee_id: to_bool(r.junior) for r in users_raw.itertuples(index=False)}
    get_units = lambda nid, s: per_person_units(junior_map.get(nid, False), s, d_avg, e_avg, n_avg, 4.0)

    daily_data = []
    for d in range(1, nd + 1):
        day_str = str(d)
        row_data = {"day": d}
        
        for s in ORDER:
            units_sum = 0.0
            person_count = 0
            for nid in id_list:
                if df_schedule.loc[nid, day_str] == s:
                    units_sum += get_units(nid, s)
                    person_count += 1
            
            row_data[f"{s}_units"] = units_sum
            row_data[f"{s}_count"] = person_count
            
        d_count = row_data.get("D_count", 0)
        d_senior = sum(1 for nid in id_list if df_schedule.loc[nid, day_str] == "D" and senior_map.get(nid, False))
        row_data["D_senior_ratio"] = f"{d_senior}/{d_count}" if d_count > 0 else "0/0"
        
        daily_data.append(row_data)

    df_daily = pd.DataFrame(daily_data)
    df_daily = df_daily.set_index("day").T
    new_index = {
        "D_units": "白班能力總單位", "D_count": "白班總人數", "D_senior_ratio": "白班資深比",
        "E_units": "小夜能力總單位", "E_count": "小夜總人數",
        "N_units": "大夜能力總單位", "N_count": "大夜總人數",
    }
    df_daily = df_daily.rename(index=new_index)
    return df_daily


def to_excel_buffer(df_schedule_display: pd.DataFrame, df_stats: pd.DataFrame, df_daily_units: pd.DataFrame, year: int, month: int) -> BytesIO:
    """將排班表、統計表和每日人力寫入一個 Excel 檔案的多個工作表"""
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        
        # 1. 統計摘要 (Sheet 1)
        stats_cols = [c for c in df_stats.columns if c not in ["目標月休天數"]]
        df_stats[stats_cols].to_excel(writer, sheet_name='📊_個人統計摘要', index=False)
        
        # 2. 排班表主體 (Sheet 2)
        df_schedule_display_out = df_schedule_display.copy()
        date_cols_map = {f"{d:02d}": int(d) for d in range(1, len(df_schedule_display.columns) - 4)}
        df_schedule_display_out = df_schedule_display_out.rename(columns=date_cols_map)
        
        main_cols = ['ID', '姓名', '固定班', '資深', '新人'] + sorted([c for c in df_schedule_display_out.columns if isinstance(c, int)])
        df_schedule_display_out[main_cols].to_excel(writer, sheet_name='📆_排班表', index=False)

        # 3. 每日人力摘要 (Sheet 3)
        daily_df_out = df_daily_units.rename(columns={c: f"{int(c)}日" for c in df_daily_units.columns})
        daily_df_out.to_excel(writer, sheet_name='📈_每日人力與單位', index=True)

    output.seek(0)
    return output

# ==============================================================================
# VI. Streamlit UI 流程
# ==============================================================================

# ... (sidebar_auth 函式和 Session State 初始化) ...
def sidebar_auth():
    # ... (您的 sidebar_auth 函式內容) ...
    pass # 這裡省略實作，請確保在您的環境中它是完整的

if "role" not in st.session_state:
    st.session_state["role"] = None
    st.session_state["my_id"] = None

sidebar_auth()

# ... (參數設定區塊：年份、月份、床數、護病比計算) ...
st.header("排班月份與需求參數")
colA, colB, colC, colD = st.columns([1,1,2,2])
with colA: year  = st.number_input("年份", 2024, 2100, value=2025, step=1)
with colB: month = st.number_input("月份", 1, 12, value=11, step=1)
nd = days_in_month(year, month)
with colC: total_beds = st.number_input("總床數（住院占床數）", 0, 2000, 120, 1)
with colD:
    st.caption("護病比區間"); c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1: d_ratio_min = st.number_input("白最少", 1, 200, 6, key="drm")
    with c2: d_ratio_max = st.number_input("白最多", 1, 200, 7, key="drx")
    with c3: e_ratio_min = st.number_input("小最少", 1, 200, 10, key="erm")
    with c4: e_ratio_max = st.number_input("小最多", 1, 200, 12, key="erx")
    with c5: n_ratio_min = st.number_input("大最少", 1, 200, 15, key="nrm")
    with c6: n_ratio_max = st.number_input("大最多", 1, 200, 16, key="nrx")
d_avg = (d_ratio_min + d_ratio_max) / 2.0; e_avg = (e_ratio_min + e_ratio_max) / 2.0; n_avg = (n_ratio_min + n_ratio_max) / 2.0
role = st.session_state.get("role", None)

# ... (員工端邏輯) ...
if role == "user": st.stop() # 這裡假設員工端邏輯已完成或被跳過

# ... (未登入檢查) ...
if role != "admin":
    st.info("請先登入。"); st.stop()

# ================== 管理端介面核心 ==================

users_raw = load_users().copy()
prefs_df = load_prefs(year, month)
hol_df = load_holidays(year, month)
extra_df = load_extra(year, month)
df_demand = st.session_state.get(f"demand_{year}_{month}", seed_demand_from_beds(year, month, total_beds, d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max, extra_df=extra_df))

# ... (假日集合計算) ...
holiday_set = set()
for r in hol_df.itertuples(index=False):
    raw = getattr(r, "date", ""); dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        try: holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))
        except ValueError: continue

# ... (排班規則參數讀取) ...
st.subheader("⚙️ 排班規則")
col_r1, col_r2, col_r3 = st.columns(3)
with col_r1:
    allow_cross = st.checkbox("允許同日跨班平衡（以能力單位）", value=True)
    prefer_off_holiday = st.checkbox("假日優先排休（能休就自動打 O）", value=True)
    balance_monthly_off = st.checkbox("盡量讓每人 O 天數接近（平衡）", value=True)
with col_r2:
    min_monthly_off = st.number_input("每人每月最少 O 天數", 0, 31, 8, 1, key="min_off")
    min_work_stretch = st.number_input("最小連續上班天數（避免上一兩天就休）", 2, 7, 3, 1, key="min_work")
with col_r3:
    TARGET_OFF_DAYS = st.number_input("目標月休天數 (用於平衡)", 0, 31, 10, 1, key="target_off")
    MAX_WORK_STREAK = st.number_input("最大連續上班天數", 3, 7, 5, 1, key="max_work")
    MAX_OFF_STREAK = st.number_input("最大連續休假天數", 1, 5, 2, 1, key="max_off")

# ================== 排班執行與結果展示 ==================
st.subheader("🤖 排班執行")
if st.button("🚀 執行排班", type="primary", key="run_schedule"):
    if users_raw.empty:
        st.error("人員清單空白，無法執行排班。")
    else:
        with st.spinner("正在執行初始排班與調整..."):
            # 1. 執行排班並取得 sched (排班結果字典)
            try:
                # 警告：此處調用的是框架函式，請確保您已補齊邏輯
                sched, demand_map, role_map, id_list, senior_map, junior_map, wcap_map, must_map, wish_map = \
                    build_initial_schedule(year, month, users_raw, prefs_df, df_demand, d_avg, e_avg, n_avg)
                
                # 執行調整邏輯
                if allow_cross: sched = cross_shift_balance_with_units(year, month, id_list, sched, demand_map, junior_map, senior_map, d_avg, e_avg, n_avg)
                if prefer_off_holiday: sched = prefer_off_on_holidays(year, month, sched, df_demand, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set)
                sched = enforce_weekly_one_off(year, month, sched, df_demand, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set)
                sched = enforce_min_monthly_off(year, month, sched, df_demand, id_list, junior_map, senior_map, d_avg, e_avg, n_avg, min_off=min_monthly_off, balance=balance_monthly_off, holiday_set=holiday_set, target_off=TARGET_OFF_DAYS)
                sched = enforce_consecutive_streaks(year, month, sched, id_list, max_work=MAX_WORK_STREAK, max_off=MAX_OFF_STREAK, min_work=min_work_stretch)

            except Exception as e:
                st.error(f"排班執行失敗，請檢查邏輯錯誤：{e}")
                st.stop()

            # 2. 轉換為 DataFrame
            df_schedule_raw = pd.DataFrame(sched).T.reset_index(names="day")
            df_schedule = df_schedule_raw.set_index("day").T
            df_schedule.index.name = "employee_id"
            id_list = sorted(df_schedule.index.tolist())

            # 3. 執行統計分析
            df_stats = analyze_schedule(df_schedule, users_raw, nd, min_monthly_off, TARGET_OFF_DAYS)
            df_daily_units = calculate_daily_units(df_schedule, id_list, users_raw, nd, d_avg, e_avg, n_avg)

            # 4. 存入 session state
            st.session_state["last_schedule"] = df_schedule.copy()
            st.session_state["last_stats"] = df_stats.copy()
            st.session_state["last_daily_units"] = df_daily_units.copy()
            save_schedule(df_schedule, year, month)

        st.success("🎉 排班完成！請查看下方結果並下載 Excel 報表。")

# ---- 7) 排班結果展示區塊 ----
if "last_stats" in st.session_state:
    df_stats = st.session_state["last_stats"]
    df_schedule = st.session_state["last_schedule"]
    df_daily_units = st.session_state["last_daily_units"]

    # 準備排班表主體 (用於展示和 Excel)
    day_cols = {str(d): f"{d:02d}" for d in range(1, nd + 1)}
    display_df = df_schedule.rename(columns=day_cols).reset_index()
    users_info = users_raw[["employee_id", "name", "shift", "senior", "junior"]].set_index("employee_id")
    display_df = display_df.join(users_info, on="employee_id")
    display_df = display_df.rename(columns={"employee_id": "ID", "name": "姓名", "shift": "固定班", "senior": "資深", "junior": "新人"})
    
    # --- 顯示統計摘要 ---
    st.subheader("📊 排班統計摘要")
    st.dataframe(
        df_stats,
        use_container_width=True,
        height=min(len(df_stats) * 35 + 40, 600),
        hide_index=True,
        column_order=["員工ID", "姓名", "實際休假天數", "實際休假時數(H)", "月休天數合規", "實際總工時(H)", "工時差異(H)", "D班總數", "E班總數", "N班總數", "固定班", "資深", "新人"]
    )

    # --- Excel 下載按鈕 ---
    excel_data = to_excel_buffer(display_df, df_stats, df_daily_units, year, month)
    st.download_button(
        label="📄 下載完整 Excel 報表 (排班表/統計/每日人力)",
        data=excel_data,
        file_name=f"護理排班報表_{year}_{month:02d}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="excel_download"
    )
    st.caption("Excel 報表包含：排班表、個人休假統計、每日人力/單位總和（分頁）。")

    # --- 顯示排班詳細表格 ---
    st.subheader("📆 排班詳細表格")
    cols = ["ID", "姓名", "固定班", "資深", "新人"] + [f"{d:02d}" for d in range(1, nd + 1)]
    st.dataframe(
        display_df[cols],
        use_container_width=True,
        height=min(len(display_df) * 35 + 40, 600),
        hide_index=True
    )

else:
    st.info("請設定好所有參數後，點擊上方的『執行排班』按鈕。")
