import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil
from typing import Dict, List, Tuple, Any

# ================== 基本設定與資料路徑 ==================
st.set_page_config(page_title="Nurse Roster • 自助註冊版", layout="wide")

# 資料目錄設在目前工作目錄，避免無權限路徑
DATA_DIR = os.path.join(os.getcwd(), "nursing_data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_CSV = os.path.join(DATA_DIR, "users.csv")              # 人員清單
PREFS_CSV_TMPL = os.path.join(DATA_DIR, "prefs_{year}_{month}.csv") # 員工請休
HOLIDAYS_CSV_TMPL = os.path.join(DATA_DIR, "holidays_{year}_{month}.csv") # 例假日
EXTRA_CSV_TMPL = os.path.join(DATA_DIR, "extra_{year}_{month}.csv")       # 加開人力
SCHEDULE_CSV_TMPL = os.path.join(DATA_DIR, "schedule_{year}_{month}.csv") # 排班結果

# 預設護理長帳密（建議實際使用時改掉）
ADMIN_USER = "headnurse"
ADMIN_PASS = "admin123"

# 班別時間（24 小時制，用於計算 11 小時休息）
SHIFT = {
    "D": {"start": 8,  "end": 16},
    "E": {"start": 16, "end": 24},
    "N": {"start": 0,  "end": 8},
    "O": {}  # 休假
}

ORDER = ["D", "E", "N"]  # 排班處理順序

# ================== 工具函式 ==================
def days_in_month(year: int, month: int) -> int:
    """計算指定年月份的天數"""
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    """檢查指定日期是否為週日"""
    return datetime(y, m, d).weekday() == 6  # 週日

def week_index(day: int) -> int:
    """計算日期在月中的第幾週 (1-5)"""
    if day <= 7: return 1
    if day <= 14: return 2
    if day <= 21: return 3
    if day <= 28: return 4
    return 5

def rest_ok(prev_code: str, next_code: str) -> bool:
    """
    檢查前一日班別(prev_code)與當日班別(next_code)之間是否有 >= 11 小時休息
    O（休假）不列入限制。
    """
    if prev_code in (None, "", "O") or next_code in (None, "", "O"):
        return True
    s1, e1 = SHIFT[prev_code]["start"], SHIFT[prev_code]["end"]
    s2, e2 = SHIFT[next_code]["start"], SHIFT[next_code]["end"]
    rest = s2 - e1
    if rest < 0:
        rest += 24 # 跨日班別 (例如 E 接 N)
    return rest >= 11

def normalize_id(x) -> str:
    """標準化員工編號"""
    if pd.isna(x):
        return ""
    return str(x).strip()

def to_bool(x) -> bool:
    """將字串轉換為布林值"""
    return str(x).strip().upper() in ("TRUE","1","YES","Y","T")

# ================== 資料存取 ==================
def load_users():
    """載入人員清單"""
    if os.path.exists(USERS_CSV):
        df = pd.read_csv(USERS_CSV, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=["employee_id","name","pwd4","shift","weekly_cap","senior","junior"])
        df.to_csv(USERS_CSV, index=False)
    for c in ["employee_id","name","pwd4","shift","weekly_cap","senior","junior"]:
        if c not in df.columns:
            df[c] = ""
    return df

def save_users(df):
    """儲存人員清單"""
    df.to_csv(USERS_CSV, index=False)

def prefs_path(year, month):
    """取得請休檔案路徑"""
    return PREFS_CSV_TMPL.format(year=year, month=f"{month:02d}")

def load_prefs(year, month):
    """載入請休資料"""
    p = prefs_path(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        for c in ["nurse_id","date","type"]:
            if c not in df.columns:
                df[c] = ""
        return df
    return pd.DataFrame(columns=["nurse_id","date","type"])

def save_prefs(df, year, month):
    """儲存請休資料"""
    df.to_csv(prefs_path(year, month), index=False)

def load_holidays(year, month):
    """載入假日清單"""
    p = HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        if "date" not in df.columns:
            df["date"] = ""
        return df
    return pd.DataFrame(columns=["date"])

def save_holidays(df, year, month):
    """儲存假日清單"""
    df.to_csv(HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

def load_extra(year, month):
    """載入每日加開人力"""
    p = EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}")
    nd = days_in_month(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p).fillna(0)
    else:
        df = pd.DataFrame({
            "day": list(range(1, nd+1)),
            "D_extra": [0]*nd,
            "E_extra": [0]*nd,
            "N_extra": [0]*nd,
        })
    for c in ["day","D_extra","E_extra","N_extra"]:
        if c not in df.columns:
            df[c] = 0
    return df

def save_extra(df, year, month):
    """儲存每日加開人力"""
    df.to_csv(EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

def load_schedule(year, month):
    """載入排班結果"""
    p = SCHEDULE_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p):
        return pd.read_csv(p, index_col="employee_id", dtype=str).fillna("")
    return pd.DataFrame()

def save_schedule(df: pd.DataFrame, year, month):
    """儲存排班結果"""
    df.to_csv(SCHEDULE_CSV_TMPL.format(year=year, month=f"{month:02d}"))

# ================== 護病比 → 每日需求（能力單位） ==================
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          extra_df=None):
    """根據床數和護病比計算每日所需能力單位區間"""
    rows = []
    nd = days_in_month(y, m)
    ext = extra_df if extra_df is not None else pd.DataFrame(columns=["day","D_extra","E_extra","N_extra"])
    if "day" in ext.columns:
        ext = ext.set_index("day")
    for d in range(1, nd+1):
        D_min = ceil(total_beds / max(d_ratio_max,1))
        D_max = ceil(total_beds / max(d_ratio_min,1))
        E_min = ceil(total_beds / max(e_ratio_max,1))
        E_max = ceil(total_beds / max(e_ratio_min,1))
        N_min = ceil(total_beds / max(n_ratio_max,1))
        N_max = ceil(total_beds / max(n_ratio_min,1))
        d_ex = int(ext.at[d,"D_extra"]) if d in ext.index else 0
        e_ex = int(ext.at[d,"E_extra"]) if d in ext.index else 0
        n_ex = int(ext.at[d,"N_extra"]) if d in ext.index else 0
        rows.append({
            "day": d,
            "D_min_units": int(D_min + d_ex),
            "D_max_units": int(D_max + d_ex),
            "E_min_units": int(E_min + e_ex),
            "E_max_units": int(E_max + e_ex),
            "N_min_units": int(N_min + n_ex),
            "N_max_units": int(N_max + n_ex),
        })
    return pd.DataFrame(rows)

# ================== 能力單位：新人護病比 1:4 ==================
def per_person_units(is_junior: bool, shift_code: str,
                     d_avg: float, e_avg: float, n_avg: float,
                     jr_ratio: float = 4.0):
    """
    計算個人能力單位值。正式人員為 1.0；新人則根據護病比調整。
    """
    if not is_junior:
        return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0:
        return 1.0
    # 新人能力 = 新人護病比(4.0) / 該班別平均護病比
    return jr_ratio / base

# ================== 登入與自助註冊 ==================
def sidebar_auth():
    """側邊欄登入與自助註冊邏輯"""
    st.sidebar.subheader("登入")
    acct = st.sidebar.text_input("帳號（員工編號／護理長）",
                                 value=st.session_state.get("acct",""))
    pwd  = st.sidebar.text_input("密碼（員工：身分證末四碼）",
                                 type="password",
                                 value=st.session_state.get("pwd",""))
    login_btn = st.sidebar.button("登入 / 驗證")

    with st.sidebar.expander("首次使用？點我自助註冊"):
        rid   = st.text_input("員工編號（作為帳號）", key="reg_id")
        rname = st.text_input("姓名", key="reg_name")
        rpwd  = st.text_input("身分證末四碼（做為密碼）", key="reg_pwd",
                              type="password", max_chars=4)
        rshift = st.selectbox("固定班別", ["D","E","N"], key="reg_shift")
        rsen   = st.checkbox("資深", value=False, key="reg_sen")
        rjun   = st.checkbox("新人", value=False, key="reg_jun")
        if st.button("建立帳號", key="reg_btn"):
            users = load_users()
            if (users["employee_id"].astype(str).str.strip() == rid.strip()).any():
                st.warning("此員工編號已存在，請直接登入。")
            elif rid.strip()=="" or rpwd.strip()=="":
                st.error("員編與末四碼不可空白。")
            else:
                new = pd.DataFrame([{
                    "employee_id": rid.strip(),
                    "name": rname.strip(),
                    "pwd4": rpwd.strip(),
                    "shift": rshift,
                    "weekly_cap": "",
                    "senior": "TRUE" if rsen else "FALSE",
                    "junior": "TRUE" if rjun else "FALSE",
                }])
                users = pd.concat([users, new], ignore_index=True)
                save_users(users)
                st.success("註冊成功！請回到上方欄位用員編＋末四碼登入。")

    if login_btn:
        st.session_state["acct"] = acct
        st.session_state["pwd"]  = pwd
        # 管理者
        if acct == ADMIN_USER and pwd == ADMIN_PASS:
            st.session_state["role"] = "admin"
            st.sidebar.success("已以管理者登入")
            return
        # 一般員工
        users = load_users()
        row = users[users["employee_id"].astype(str).str.strip() == acct.strip()]
        if row.empty:
            st.sidebar.error("查無此員工。請先在下方『自助註冊』建立帳號。")
            return
        # 密碼檢查，須先處理 NaN 和空白
        if str(row.iloc[0]["pwd4"]).strip() != str(pwd).strip():
            st.sidebar.error("密碼錯誤（請輸入身分證末四碼）")
            return
        st.session_state["role"] = "user"
        st.session_state["my_id"] = acct
        st.sidebar.success(f"已以員工 {acct} 登入")

if "role" not in st.session_state:
    st.session_state["role"] = None
    st.session_state["my_id"] = None

sidebar_auth()

# ================== 上方共同設定：年月、床數、護病比 ==================
st.header("排班月份與需求參數")

colA, colB, colC, colD = st.columns([1,1,2,2])
with colA:
    year  = st.number_input("年份", 2024, 2100, value=2025, step=1)
with colB:
    month = st.number_input("月份", 1, 12, value=11, step=1)
nd = days_in_month(year, month)

with colC:
    total_beds = st.number_input("總床數（住院占床數）", 0, 2000, 120, 1)
with colD:
    st.caption("護病比區間（不使用假日係數）")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1: d_ratio_min = st.number_input("白最少", 1, 200, 6, key="drm")
    with c2: d_ratio_max = st.number_input("白最多", 1, 200, 7, key="drx")
    with c3: e_ratio_min = st.number_input("小最少", 1, 200, 10, key="erm")
    with c4: e_ratio_max = st.number_input("小最多", 1, 200, 12, key="erx")
    with c5: n_ratio_min = st.number_input("大最少", 1, 200, 15, key="nrm")
    with c6: n_ratio_max = st.number_input("大最多", 1, 200, 16, key="nrx")

d_avg = (d_ratio_min + d_ratio_max) / 2.0
e_avg = (e_ratio_min + e_ratio_max) / 2.0
n_avg = (n_ratio_min + n_ratio_max) / 2.0

role = st.session_state.get("role", None)

# ================== 員工端（必休選取，其餘自動想休） ==================
if role == "user":
    users = load_users()
    my_id = st.session_state["my_id"]
    row = users[users["employee_id"] == my_id]
    if row.empty:
        st.error("使用者資料遺失，請重新登入。")
        st.stop()
    me = row.iloc[0]
    st.success(f"👤 你好，{me['name']}（{my_id}）。固定班別：{me['shift']}；資深：{'是' if to_bool(me['senior']) else '否'}；新人：{'是' if to_bool(me['junior']) else '否'}")

    prefs_df = load_prefs(year, month)
    # 員工 ID 必須正規化後才能比對
    my_prefs = prefs_df[prefs_df["nurse_id"].map(normalize_id) == normalize_id(my_id)].copy()

    def to_dateset(df, typ):
        s = set()
        df = df[df["type"]==typ] if "type" in df.columns else pd.DataFrame(columns=["date"])
        if df.empty: return s
        for r in df.itertuples(index=False):
            raw = getattr(r, "date", "")
            if pd.isna(raw) or str(raw).strip()=="": continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year) == int(year) and int(dt.month) == int(month):
                s.add(int(dt.day))
        return s

    must_set = to_dateset(my_prefs, "must")

    st.subheader("⛔ 必休（請選取本月日期）")
    options = list(range(1, nd+1))
    selected_days = st.multiselect(
        "請選擇本月必休日期（可多選）",
        options=options,
        default=sorted(must_set),
        format_func=lambda d: f"{year}-{month:02d}-{d:02d}"
    )
    must_days = set(selected_days)

    all_days = set(range(1, nd+1))
    wish_days_computed = sorted(list(all_days - must_days))
    wish_df_preview = pd.DataFrame({
        "date": [f"{year}-{month:02d}-{d:02d}" for d in wish_days_computed]
    })

    col_u1, col_u2 = st.columns(2)
    with col_u1:
        st.write("你選擇的必休日：")
        must_preview = pd.DataFrame({
            "date": [f"{year}-{month:02d}-{d:02d}" for d in sorted(must_days)]
        })
        st.dataframe(must_preview, use_container_width=True, height=240)
    with col_u2:
        st.write("系統自動產生的想休日（其餘天數）：")
        st.dataframe(wish_df_preview, use_container_width=True, height=240)

    if st.button("💾 儲存我的請休（必休 + 想休自動）"):
        must_new_rows = [{
            "nurse_id": my_id,
            "date": f"{year}-{month:02d}-{d:02d}",
            "type": "must"
        } for d in sorted(must_days)]

        wish_new_rows = [{
            "nurse_id": my_id,
            "date": f"{year}-{month:02d}-{d:02d}",
            "type": "wish"
        } for d in range(1, nd+1) if d not in must_days]

        must_new_df = pd.DataFrame(must_new_rows)
        wish_new_df = pd.DataFrame(wish_new_rows)

        # 篩選掉本人的舊資料，保留其他人的
        others = prefs_df[prefs_df["nurse_id"].map(normalize_id) != normalize_id(my_id)].copy()
        merged = pd.concat([others, must_new_df, wish_new_df], ignore_index=True)
        save_prefs(merged, year, month)
        st.success("✅ 已儲存完成！")

    st.stop()

# ================== 未登入或非 admin ==================
if role != "admin":
    st.info(
        "請先登入。\n"
        "- 員工：自助註冊後，用【員編＋身分證末四碼】登入\n"
        "- 護理長：預設帳密 headnurse / admin123（建議之後修改）"
    )
    st.stop()

# ================== 管理端畫面 ==================
st.success("✅ 以護理長（管理者）身份登入")

# ---- 1) 人員清單 ----
st.subheader("👥 人員清單（員工也可自助註冊）")
users_raw = load_users().copy()

users_view = users_raw.copy()
users_view["senior"] = users_view["senior"].map(to_bool)
users_view["junior"] = users_view["junior"].map(to_bool)

users_view = st.data_editor(
    users_view,
    use_container_width=True,
    num_rows="dynamic",
    height=360,
    column_config={
        "employee_id": st.column_config.TextColumn("員工編號（帳號）"),
        "name":        st.column_config.TextColumn("姓名"),
        "pwd4":        st.column_config.TextColumn("密碼（身分證末四碼）"),
        "shift":       st.column_config.TextColumn("固定班別 D/E/N"),
        "weekly_cap":  st.column_config.TextColumn("每週上限天（可空白）"),
        "senior":      st.column_config.CheckboxColumn("資深"),
        "junior":      st.column_config.CheckboxColumn("新人"),
    },
    key="admin_users"
)

if st.button("💾 儲存人員清單"):
    users_out = users_view.copy()
    users_out["senior"] = users_out["senior"].map(lambda v: "TRUE" if bool(v) else "FALSE")
    users_out["junior"] = users_out["junior"].map(lambda v: "TRUE" if bool(v) else "FALSE")
    save_users(users_out)
    st.success("✅ 已儲存人員清單。")

# ---- 2) 員工請休彙整 ----
st.subheader("📥 員工請休彙整（本月）")
prefs_df = load_prefs(year, month)
st.dataframe(prefs_df, use_container_width=True, height=260)

# ---- 3) 假日清單 ----
st.subheader("📅 假日清單（例假日/國定假日等）")
hol_df = load_holidays(year, month)
hol_df = st.data_editor(
    hol_df,
    use_container_width=True,
    num_rows="dynamic",
    height=180,
    key="admin_holidays"
)
if st.button("💾 儲存假日清單", key="save_hol"):
    save_holidays(hol_df, year, month)
    st.success("✅ 已儲存假日清單。")

holiday_set = set()
for r in hol_df.itertuples(index=False):
    raw = getattr(r, "date", "")
    if pd.isna(raw) or str(raw).strip()=="": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        try:
            holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))
        except ValueError:
             # 處理日期範圍錯誤 (如 2/30)
            st.error(f"日期格式錯誤或無效日期：{raw}")

# ---- 4) 每日加開人力 ----
st.subheader("📈 每日加開人力（單位；加在 min/max 上）")
extra_df = load_extra(year, month)
extra_df = st.data_editor(
    extra_df,
    use_container_width=True,
    num_rows="fixed",
    height=300,
    column_config={
        "day":       st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_extra":   st.column_config.NumberColumn("白班加開", min_value=0, max_value=1000, step=1),
        "E_extra":   st.column_config.NumberColumn("小夜加開", min_value=0, max_value=1000, step=1),
        "N_extra":   st.column_config.NumberColumn("大夜加開", min_value=0, max_value=1000, step=1),
    },
    key="admin_extra"
)
if st.button("💾 儲存加開人力", key="save_extra"):
    save_extra(extra_df, year, month)
    st.success("✅ 已儲存每日加開人力。")

# ---- 5) 每日三班需求（能力單位） ----
st.subheader("📋 每日三班需求（能力單位；可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max,
    e_ratio_min, e_ratio_max,
    n_ratio_min, n_ratio_max,
    extra_df=extra_df
)
# 檢查是否有儲存的 custom demand
demand_key = f"demand_{year}_{month}"
if demand_key not in st.session_state:
    st.session_state[demand_key] = df_demand_auto.copy()

df_demand = st.data_editor(
    st.session_state[demand_key],
    use_container_width=True,
    num_rows="fixed",
    height=380,
    column_config={
        "day":           st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_min_units":   st.column_config.NumberColumn("D_min_units", min_value=0, max_value=1000, step=1),
        "D_max_units":   st.column_config.NumberColumn("D_max_units", min_value=0, max_value=1000, step=1),
        "E_min_units":   st.column_config.NumberColumn("E_min_units", min_value=0, max_value=1000, step=1),
        "E_max_units":   st.column_config.NumberColumn("E_max_units", min_value=0, max_value=1000, step=1),
        "N_min_units":   st.column_config.NumberColumn("N_min_units", min_value=0, max_value=1000, step=1),
        "N_max_units":   st.column_config.NumberColumn("N_max_units", min_value=0, max_value=1000, step=1),
    },
    key="demand_editor"
)
if st.button("💾 儲存調整後的需求", key="save_demand"):
    st.session_state[demand_key] = df_demand.copy()
    st.success("✅ 已儲存調整後的需求。")


# ---- 6) 排班規則 ----
st.subheader("⚙️ 排班規則")
col_r1, col_r2, col_r3 = st.columns(3)
with col_r1:
    allow_cross         = st.checkbox("允許同日跨班平衡（以能力單位）", value=True)
    prefer_off_holiday  = st.checkbox("假日優先排休（能休就自動打 O）", value=True)
    balance_monthly_off = st.checkbox("盡量讓每人 O 天數接近（平衡）", value=True)
with col_r2:
    min_monthly_off     = st.number_input("每人每月最少 O 天數", 0, 31, 8, 1, key="min_off")
    min_work_stretch    = st.number_input("最小連續上班天數（避免上一兩天就休）", 2, 7, 3, 1, key="min_work")
with col_r3:
    TARGET_OFF_DAYS     = st.number_input("目標月休天數 (用於平衡)", 0, 31, 10, 1, key="target_off")
    MAX_WORK_STREAK     = st.number_input("最大連續上班天數", 3, 7, 5, 1, key="max_work")
    MAX_OFF_STREAK      = st.number_input("最大連續休假天數", 1, 5, 2, 1, key="max_off")

# ================== 排班主邏輯：initial ==================
def build_initial_schedule(year, month, users_df, prefs_df, demand_df,
                           d_avg, e_avg, n_avg) -> Tuple[Dict[str, Dict[int, str]], Dict, Dict, List, Dict, Dict, Dict, Dict, Dict]:
    """建立初始排班表，優先處理必休、固定班別、最低人力需求、資深比例與 11 小時休息"""
    nd = days_in_month(year, month)

    tmp = users_df.copy()
    for col in ["employee_id","shift","weekly_cap","senior","junior"]:
        if col not in tmp.columns: tmp[col] = ""
    tmp["employee_id"] = tmp["employee_id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(
        lambda s: s if s in ("D","E","N") else ""
    )
    tmp = tmp[(tmp["employee_id"].astype(str).str.len()>0) & (tmp["shift"].isin(["D","E","N"]))]

    def to_wcap(x):
        try:
            v = int(float(x))
            return v if v >= 0 else None
        except:
            return None

    role_map   = {r.employee_id: r.shift    for r in tmp.itertuples(index=False)}
    wcap_map   = {r.employee_id: to_wcap(r.weekly_cap) for r in tmp.itertuples(index=False)}
    senior_map = {r.employee_id: to_bool(r.senior) for r in tmp.itertuples(index=False)}
    junior_map = {r.employee_id: to_bool(r.junior) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # 偏好 map
    def build_date_map(df, typ):
        m = {nid:set() for nid in id_list}
        if df.empty: return m
        df2 = df[df["type"]==typ] if "type" in df.columns else pd.DataFrame(columns=["nurse_id","date"])
        for r in df2.itertuples(index=False):
            nid = normalize_id(getattr(r,"nurse_id",""))
            raw = getattr(r,"date","")
            if nid not in m: continue
            if pd.isna(raw) or str(raw).strip()=="": continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                m[nid].add(int(dt.day))
        return m

    must_map = build_date_map(prefs_df, "must")
    wish_map = build_date_map(prefs_df, "wish")

    # 每日需求
    demand = {}
    for r in demand_df.itertuples(index=False):
        d = int(r.day)
        demand[d] = {
            "D": (int(r.D_min_units), int(r.D_max_units)),
            "E": (int(r.E_min_units), int(r.E_max_units)),
            "N": (int(r.N_min_units), int(r.N_max_units)),
        }

    sched = {nid: {d:"" for d in range(1, nd+1)} for nid in id_list}
    assigned_days = {nid: 0 for nid in id_list}

    def week_assigned(nid, w):
        """計算某人某週已排班天數 (用於週上限檢查)"""
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, nd+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    def person_units_on(nid, s):
        """計算某人某班的能力單位"""
        return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.0)

    # 1. 先標必休 O（不可被後續邏輯改掉）
    for nid in id_list:
        for d in must_map[nid]:
            if 1 <= d <= nd:
                sched[nid][d] = "O"

    def pick_pool(d, s):
        """選人池：找出所有可排此班的人員，並排序 (優先選想休 O 較少、已排天數較少的人)"""
        wk = week_index(d)
        pool = []
        for nid in id_list:
            # 1. 檢查固定班別
            if role_map[nid] != s: continue
            # 2. 檢查是否已被排班或必休
            if sched[nid][d] != "": continue
            # 3. 檢查 11 小時休息
            if not rest_ok(sched[nid].get(d-1,""), s): continue
            # 4. 檢查週上限
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap: continue

            # 排序依據： (1) 是否在想休名單 (wished: 1=想休, 0=不想休) (2) 已排班天數
            wished = 1 if d in wish_map[nid] else 0
            pool.append((wished, assigned_days[nid], nid))
        
        # 排序：優先選 (1) 不想休 (wished=0) (2) 且已排天數少 的人
        pool.sort() 
        return [nid for (_,_,nid) in pool]

    # 2. 逐日逐班排班
    for d in range(1, nd+1):
        for s in ORDER:
            mn_u, mx_u = demand.get(d,{}).get(s, (0,0))
            assigned = []
            units_sum = 0.0
            senior_cnt = 0 # 白班資深人數計數器

            # 補足 min_units
            while units_sum + 1e-9 < mn_u:
                pool = pick_pool(d, s)
                if not pool: break

                # 確保白班有資深 (至少一個非新人)
                if s == "D" and senior_cnt == 0:
                     non_j = [nid for nid in pool if not junior_map.get(nid, False)]
                     if non_j: pool = non_j
                     else: break # 沒人可排

                # 白班：盡量維持資深人員比例 (ceil(N/3))
                pick_list = pool
                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    if senior_cnt < need_sen and cand_sen:
                         pick_list = cand_sen # 優先選資深來補缺額

                if not pick_list: break

                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if senior_map.get(nid,False): senior_cnt += 1

            # 補足至 max_units (邏輯與上述類似，但不需要嚴格滿足 min_units)
            while units_sum + 1e-9 < mx_u:
                pool = pick_pool(d, s)
                if not pool: break

                if s == "D" and senior_cnt == 0:
                     non_j = [nid for nid in pool if not junior_map.get(nid, False)]
                     if non_j: pool = non_j
                     else: break

                pick_list = pool
                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    if senior_cnt < need_sen and cand_sen:
                         pick_list = cand_sen

                if not pick_list: break

                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if senior_map.get(nid,False): senior_cnt += 1

        # 3. 其餘沒被排到的人 → O（但不覆蓋原本必休 O）
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, senior_map, junior_map, wcap_map, must_map, wish_map

# ================== 各種調整函式 ==================

def get_person_units_fn(junior_map, d_avg, e_avg, n_avg):
    """取得計算能力單位的函式，避免重複傳入參數"""
    return lambda nid, s: per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.0)

def get_actual_units_fn(id_list, get_units, sched):
    """取得計算實際人力單位的函式"""
    return lambda d, s: sum(get_units(nid,s) for nid in id_list if sched[nid][d]==s)

def white_senior_ok_if_remove_fn(d, id_list, sched, senior_map):
    """檢查白班移除某人後資深比例是否仍符合 ceil(N/3)"""
    def check(nid_remove):
        if sched[nid_remove][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid_remove]
        total = len(d_people)
        if total==0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    return check

def white_senior_ok_if_move_fn(d, id_list, sched, senior_map):
    """檢查白班人員移動前後資深比例是否仍符合 ceil(N/3)"""
    def check(nid_move, from_s, to_s):
        if from_s!="D" and to_s!="D": return True
        
        # 模擬移動後的人員清單
        d_people = [x for x in id_list if sched[x][d]=="D"]
        if from_s=="D" and nid_move in d_people: d_people.remove(nid_move)
        if to_s=="D": d_people.append(nid_move)
        
        total = len(d_people)
        if total==0: return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)
    return check


def cross_shift_balance_with_units(year, month, id_list, sched,
                                   demand, junior_map, senior_map,
                                   d_avg, e_avg, n_avg):
    """跨班平衡：將人力從有餘裕的班次移動到人力不足的班次 (以能力單位計算)"""
    nd = days_in_month(year, month)
    get_units = get_person_units_fn(junior_map, d_avg, e_avg, n_avg)
    get_actual = get_actual_units_fn(id_list, get_units, sched)
    check_senior_ok = white_senior_ok_if_move_fn(None, id_list, sched, senior_map)

    for d in range(1, nd+1):
        actual = {s: get_actual(d,s) for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}
        
        changed = True
        while changed:
            changed = False
            # 找出短缺的班次，優先處理短缺最多的
            shortages = [(s, mins[s]-actual[s]) for s in ORDER if actual[s] + 1e-9 < mins[s]]
            if not shortages: break
            shortages.sort(key=lambda x: -x[1])

            for tgt, _need in shortages: # tgt: 目標班別
                for src in ORDER: # src: 來源班別
                    if src == tgt: continue
                    # 來源班次必須有餘裕 (大於 min)
                    if actual[src] - 1e-9 <= mins.get(src,0): continue

                    # 找出可以移動的人員 (優先移動非新人以維持能力)
                    candidates = [nid for nid in id_list if sched[nid][d]==src and not junior_map.get(nid,False)]
                    candidates.sort(key=lambda nid: -get_units(nid, src)) # 優先移動能力值高者

                    for mv in candidates:
                        # 1. 檢查白班資深比例
                        if not check_senior_ok(d, mv, src, tgt): continue
                        
                        # 2. 檢查 11 小時休息限制 (前一日 prev -> tgt, 後一日 tgt -> next)
                        if not (rest_ok(sched[mv].get(d-1,""), tgt) and
                                rest_ok(tgt, sched[mv].get(d+1,""))): continue
                        
                        # 3. 執行移動
                        u_from = get_units(mv, src)
                        u_to   = get_units(mv, tgt)
                        
                        sched[mv][d] = tgt
                        actual[src] -= u_from
                        actual[tgt] += u_to
                        changed = True
                        break # 移動成功，跳出找下一個短缺的班次
                if changed: break # 如果內層迴圈改變了，則外層也跳出重算
    return sched

def prefer_off_on_holidays(year, month, sched, demand_df, id_list,
                           junior_map, senior_map,
                           d_avg, e_avg, n_avg, holiday_set):
    """在假日/週日，盡量將排班人數降至 Min_Units"""
    nd = days_in_month(year, month)
    demand = {int(r.day):{s: (int(getattr(r, f"{s}_min_units")), int(getattr(r, f"{s}_max_units"))) for s in ORDER}
              for r in demand_df.itertuples(index=False)}
    get_units = get_person_units_fn(junior_map, d_avg, e_avg, n_avg)
    get_actual = get_actual_units_fn(id_list, get_units, sched)
    
    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)
    
    check_senior_ok = white_senior_ok_if_remove_fn(None, id_list, sched, senior_map)

    for d in range(1, nd+1):
        if not is_hday(d): continue
        
        for s in ORDER:
            mn, _ = demand.get(d,{}).get(s,(0,0))
            
            changed = True
            while changed:
                changed = False
                cur = get_actual(d, s)
                if cur <= mn + 1e-9: break

                # 找出可被移除的人員 (優先移除能力值低者)
                cands = [nid for nid in id_list if sched[nid][d]==s]
                cands.sort(key=lambda nid: (get_units(nid,s), # 優先能力低者
                                            not junior_map.get(nid,False))) # 其次非新人

                for nid in cands:
                    u = get_units(nid,s)
                    # 1. 檢查移除後是否低於 Min
                    if cur - u + 1e-9 < mn: continue
                    # 2. 檢查白班資深比例
                    if not check_senior_ok(d, nid): continue
                    # 3. 檢查 11 小時休息
                    if not (rest_ok(sched[nid].get(d-1,""), "O") and
                            rest_ok("O", sched[nid].get(d+1,""))): continue
                    
                    # 執行移除
                    sched[nid][d] = "O"
                    changed = True
                    break
    return sched

def enforce_weekly_one_off(year, month, sched, demand_df, id_list,
                           junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set):
    """強制執行每人每週至少排一次休假 (O)"""
    nd = days_in_month(year, month)
    demand = {int(r.day):{s: (int(getattr(r, f"{s}_min_units")), int(getattr(r, f"{s}_max_units"))) for s in ORDER}
              for r in demand_df.itertuples(index=False)}
    get_units = get_person_units_fn(junior_map, d_avg, e_avg, n_avg)
    get_actual = get_actual_units_fn(id_list, get_units, sched)
    check_senior_ok = white_senior_ok_if_remove_fn(None, id_list, sched, senior_map)

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    def week_range(w):
        if w==1: return range(1,8)
        if w==2: return range(8,15)
        if w==3: return range(15,22)
        if w==4: return range(22,29)
        return range(29, nd+1)

    def has_off(nid, w):
        rng = [d for d in week_range(w) if 1 <= d <= nd]
        return any(sched[nid][d] == "O" for d in rng)

    for nid in id_list:
        for w in [1,2,3,4,5]:
            if has_off(nid, w): continue # 本週已有休假，跳過
            
            rng = [d for d in week_range(w) if 1 <= d <= nd]
            if not rng: continue

            # 優先將 假日/週日 轉為 O 假，其次是其他日子
            candidates = sorted(rng, key=lambda d: (0 if is_hday(d) else 1,))
            
            for d in candidates:
                cur_shift = sched[nid][d]
                if cur_shift == "O": continue
                
                mn, _ = demand.get(d,{}).get(cur_shift,(0,0))
                u     = get_units(nid, cur_shift)
                
                # 1. 檢查移除後是否低於 Min
                if get_actual(d, cur_shift) - u + 1e-9 < mn: continue
                # 2. 檢查白班資深比例
                if not check_senior_ok(d, nid): continue
                # 3. 檢查 11 小時休息
                if not (rest_ok(sched[nid].get(d-1,""), "O") and
                        rest_ok("O", sched[nid].get(d+1,""))): continue
                
                # 執行轉 O
                sched[nid][d] = "O"
                break
    return sched

def enforce_min_monthly_off(year, month, sched, demand_df, id_list,
                            junior_map, senior_map,
                            d_avg, e_avg, n_avg,
                            min_off=8, balance=True, holiday_set=None,
                            target_off=10):
    """
    【補完】強制執行最低月休天數 (min_off)
    並可選平衡月休天數 (target_off)
    """
    nd = days_in_month(year, month)
    if holiday_set is None: holiday_set = set()
    target_off = max(min_off, target_off)

    demand = {int(r.day):{s: (int(getattr(r, f"{s}_min_units")), int(getattr(r, f"{s}_max_units"))) for s in ORDER}
              for r in demand_df.itertuples(index=False)}
    get_units = get_person_units_fn(junior_map, d_avg, e_avg, n_avg)
    get_actual = get_actual_units_fn(id_list, get_units, sched)
    check_senior_ok = white_senior_ok_if_remove_fn(None, id_list, sched, senior_map)

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    # 1. 計算每人目前休假天數
    off_counts = {nid: sum(1 for d in range(1, nd + 1) if sched[nid][d] == "O") for nid in id_list}
    
    # 2. 確定要補休的人員清單
    if balance:
        # 平衡模式：先補足 min_off，再從離 target_off 最遠的人開始補
        need_off_list = sorted(id_list, key=lambda nid: off_counts[nid])
        target = target_off
    else:
        # 最低模式：只補足 min_off
        need_off_list = sorted([nid for nid in id_list if off_counts[nid] < min_off],
                               key=lambda nid: off_counts[nid])
        target = min_off

    # 3. 逐一為需補休者找工作日轉 O
    for nid in need_off_list:
        current_target = target if balance else min_off
        
        while off_counts[nid] < current_target:
            # 找出所有工作天，優先將 非假日、非必休、工作日 轉為 O
            work_days = []
            for d in range(1, nd + 1):
                if sched[nid][d] in ORDER: # 確定是工作日
                    work_days.append(d)
            
            # 排序：優先選 (1) 非假日 (2) 非 D 班 (3) 日期較早
            sorted_work_days = sorted(work_days, key=lambda d: (
                1 if is_hday(d) else 0, # 優先非假日 (0: 非假日, 1: 假日)
                0 if sched[nid][d] == "D" else 1, # 優先非 D 班 (D 班可能影響白班資深比例)
                d # 其次日期較早
            ))

            moved = False
            for d in sorted_work_days:
                cur_shift = sched[nid][d]
                mn, _ = demand.get(d,{}).get(cur_shift,(0,0))
                u     = get_units(nid, cur_shift)
                
                # 檢查：移除此工作日後是否打破 Min 需求
                if get_actual(d, cur_shift) - u + 1e-9 < mn: continue
                # 檢查：白班資深比例
                if not check_senior_ok(d, nid): continue
                # 檢查：11 小時休息
                if not (rest_ok(sched[nid].get(d-1,""), "O") and
                        rest_ok("O", sched[nid].get(d+1,""))): continue
                
                # 執行轉 O
                sched[nid][d] = "O"
                off_counts[nid] += 1
                moved = True
                break
            
            if not moved:
                # 找不到任何可以轉 O 的日子，停止對此人的調整
                break
    
    return sched

def enforce_consecutive_streaks(year, month, sched, id_list,
                                max_work=5, max_off=2, min_work=3):
    """強制執行連班/連休限制 (簡化版：僅處理最嚴重的違規，不保證解開所有限制)"""
    nd = days_in_month(year, month)
    
    # 連續上班檢查 (最大連班 MAX_WORK_STREAK)
    for nid in id_list:
        for d in range(1, nd - max_work):
            # 檢查是否有 max_work + 1 天連續上班
            is_over_streak = True
            for i in range(max_work + 1):
                if sched[nid].get(d + i, "O") == "O":
                    is_over_streak = False
                    break
            
            if is_over_streak:
                # 發現超連班 (例如 6 連班)，嘗試將中間某天改為 O
                for change_day in range(d + min_work, d + max_work + 1):
                    # 檢查該天是否為必休 ('O')，這裡理論上不會，因為 build_initial_schedule 已處理
                    if sched[nid][change_day] == "O": continue
                    
                    # 檢查 11 小時限制
                    if rest_ok(sched[nid].get(change_day - 1, "O"), "O") and \
                       rest_ok("O", sched[nid].get(change_day + 1, "O")):
                        
                        # 簡單粗暴地改為 O，沒有檢查 Min_Units，因為這屬於最後優化階段
                        # 且排班器應已在排班時確保 Min_Units，這裡屬於硬性調整
                        sched[nid][change_day] = "O"
                        break # 只改一天即可打破連班

    # 連續休假檢查 (最大連休 MAX_OFF_STREAK)
    for nid in id_list:
        for d in range(1, nd - max_off):
            # 檢查是否有 max_off + 1 天連續休假
            is_over_off_streak = True
            for i in range(max_off + 1):
                if sched[nid].get(d + i, "X") != "O": # X 代表月份外，這裡只檢查月內
                    is_over_off_streak = False
                    break
            
            if is_over_off_streak:
                # 發現超連休 (例如 3 連休)，嘗試將中間某天改為其固定班別
                for change_day in range(d + 1, d + max_off + 1):
                    target_shift = role_map[nid]
                    # 檢查該天是否為必休 (O)，必休不可改
                    if change_day in must_map[nid]: continue 
                    
                    # 檢查 11 小時限制
                    if rest_ok(sched[nid].get(change_day - 1, ""), target_shift) and \
                       rest_ok(target_shift, sched[nid].get(change_day + 1, "")):
                        
                        # 簡單粗暴地改為班別，這裡沒有檢查 Max_Units，屬於硬性調整
                        sched[nid][change_day] = target_shift
                        break # 只改一天即可打破連休
    
    return sched

# ================== 排班執行與結果展示 ==================
st.subheader("🤖 排班執行")
if st.button("🚀 執行排班", type="primary", key="run_schedule"):
    with st.spinner("正在執行初始排班與調整..."):
        # 1. 執行初始排班
        sched, demand_map, role_map, id_list, senior_map, junior_map, wcap_map, must_map, wish_map = \
            build_initial_schedule(year, month, users_raw, prefs_df, df_demand,
                                   d_avg, e_avg, n_avg)

        # 2. 執行調整邏輯
        # a) 跨班平衡 (如果勾選)
        if allow_cross:
             sched = cross_shift_balance_with_units(year, month, id_list, sched,
                                                   demand_map, junior_map, senior_map,
                                                   d_avg, e_avg, n_avg)
        
        # b) 假日優先排休 (如果勾選)
        if prefer_off_holiday:
            sched = prefer_off_on_holidays(year, month, sched, df_demand, id_list,
                                           junior_map, senior_map,
                                           d_avg, e_avg, n_avg, holiday_set)
        
        # c) 每週至少一休 (法規要求)
        sched = enforce_weekly_one_off(year, month, sched, df_demand, id_list,
                                       junior_map, senior_map, d_avg, e_avg, n_avg, holiday_set)
        
        # d) 最低月休與平衡
        sched = enforce_min_monthly_off(year, month, sched, df_demand, id_list,
                                        junior_map, senior_map, d_avg, e_avg, n_avg,
                                        min_off=min_monthly_off, balance=balance_monthly_off, 
                                        holiday_set=holiday_set, target_off=TARGET_OFF_DAYS)
        
        # e) 連班/連休限制
        sched = enforce_consecutive_streaks(year, month, sched, id_list,
                                            max_work=MAX_WORK_STREAK, max_off=MAX_OFF_STREAK, 
                                            min_work=min_work_stretch)

        # 3. 轉換為 DataFrame 儲存和展示
        df_schedule_raw = pd.DataFrame(sched).T.reset_index(names="day")
        df_schedule = df_schedule_raw.set_index("day").T
        df_schedule.index.name = "employee_id"
        
        # 將結果存入 session state 和 CSV
        st.session_state["last_schedule"] = df_schedule.copy()
        save_schedule(df_schedule, year, month)

    st.success("🎉 排班完成！請查看下方結果。")

# ---- 7) 排班結果 ----
st.subheader("📆 排班結果")
# 優先從 session state 載入，其次從 CSV 載入
if "last_schedule" in st.session_state:
    df_schedule = st.session_state["last_schedule"]
else:
    df_schedule = load_schedule(year, month)

if not df_schedule.empty:
    # 重新命名欄位 (1, 2, 3...)
    day_cols = {str(d): f"{d:02d}" for d in range(1, nd + 1)}
    display_df = df_schedule.rename(columns=day_cols).reset_index()
    
    # 加入姓名、固定班別、資深/新人資訊
    users_info = users_raw[["employee_id", "name", "shift", "senior", "junior"]].set_index("employee_id")
    display_df = display_df.join(users_info, on="employee_id")
    display_df = display_df.rename(columns={"employee_id": "ID", "name": "姓名", "shift": "固定班", "senior": "資深", "junior": "新人"})
    
    cols = ["ID", "姓名", "固定班", "資深", "新人"] + [f"{d:02d}" for d in range(1, nd + 1)]

    # 顯示排班表
    st.dataframe(
        display_df[cols],
        use_container_width=True,
        height=min(len(display_df) * 35 + 40, 600), # 自適應高度
        hide_index=True
    )

    # 下載按鈕
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 下載排班表 (.csv)",
        data=csv,
        file_name=f"排班表_{year}_{month:02d}.csv",
        mime="text/csv",
    )
else:
    st.info("請設定好所有參數後，點擊上方的『執行排班』按鈕。")
