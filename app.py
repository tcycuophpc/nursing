import os
import streamlit as st
import pandas as pd
from datetime import datetime, date
import calendar
from math import ceil

# ================== 基本設定與資料路徑 ==================
st.set_page_config(page_title="Nurse Roster • 自助註冊版", layout="wide")

# 資料目錄設在目前工作目錄，避免無權限路徑
DATA_DIR = os.path.join(os.getcwd(), "nursing_data")
os.makedirs(DATA_DIR, exist_ok=True)

USERS_CSV = os.path.join(DATA_DIR, "users.csv")                     # 人員清單
PREFS_CSV_TMPL = os.path.join(DATA_DIR, "prefs_{year}_{month}.csv") # 員工請休
HOLIDAYS_CSV_TMPL = os.path.join(DATA_DIR, "holidays_{year}_{month}.csv")  # 例假日
EXTRA_CSV_TMPL = os.path.join(DATA_DIR, "extra_{year}_{month}.csv")        # 加開人力

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
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6  # 週日

def week_index(day: int) -> int:
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
        rest += 24
    return rest >= 11

def normalize_id(x) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()

# ================== 資料存取 ==================
def load_users():
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
    df.to_csv(USERS_CSV, index=False)

def prefs_path(year, month):
    return PREFS_CSV_TMPL.format(year=year, month=f"{month:02d}")

def load_prefs(year, month):
    p = prefs_path(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        for c in ["nurse_id","date","type"]:
            if c not in df.columns:
                df[c] = ""
        return df
    return pd.DataFrame(columns=["nurse_id","date","type"])

def save_prefs(df, year, month):
    df.to_csv(prefs_path(year, month), index=False)

def load_holidays(year, month):
    p = HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        if "date" not in df.columns:
            df["date"] = ""
        return df
    return pd.DataFrame(columns=["date"])

def save_holidays(df, year, month):
    df.to_csv(HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

def load_extra(year, month):
    p = EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}")
    if os.path.exists(p):
        df = pd.read_csv(p).fillna(0)
    else:
        nd = days_in_month(year, month)
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
    df.to_csv(EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}"), index=False)

# ================== 護病比 → 每日需求（能力單位） ==================
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          extra_df=None):
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
    正式人員：1 單位（護病比依你設定）
    新人：護病比固定 1:4，能力 = 4 / 該班別平均護病比（通常 < 1）
    """
    if not is_junior:
        return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0:
        return 1.0
    return jr_ratio / base

# ================== 登入與自助註冊 ==================
def sidebar_auth():
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
            if (users["employee_id"] == rid).any():
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
        row = users[users["employee_id"].astype(str) == acct]
        if row.empty:
            st.sidebar.error("查無此員工。請先在下方『自助註冊』建立帳號。")
            return
        if str(row.iloc[0]["pwd4"]).strip() != str(pwd).strip():
            st.sidebar.error("密碼錯誤（請輸入身分證末四碼）")
            return
        st.session_state["role"] = "user"
        st.sidebar.success(f"已以員工 {acct} 登入")

if "role" not in st.session_state:
    st.session_state["role"] = None

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
    with c1: d_ratio_min = st.number_input("白最少", 1, 200, 6)
    with c2: d_ratio_max = st.number_input("白最多", 1, 200, 7)
    with c3: e_ratio_min = st.number_input("小最少", 1, 200, 10)
    with c4: e_ratio_max = st.number_input("小最多", 1, 200, 12)
    with c5: n_ratio_min = st.number_input("大最少", 1, 200, 15)
    with c6: n_ratio_max = st.number_input("大最多", 1, 200, 16)

d_avg = (d_ratio_min + d_ratio_max) / 2.0
e_avg = (e_ratio_min + e_ratio_max) / 2.0
n_avg = (n_ratio_min + n_ratio_max) / 2.0

role = st.session_state.get("role", None)

# ================== 員工端（必休選取，其餘自動想休） ==================
if role == "user":
    users = load_users()
    me = users[users["employee_id"] == st.session_state["acct"]].iloc[0]
    my_id = me["employee_id"]
    st.success(f"👤 你好，{me['name']}（{my_id}）。固定班別：{me['shift']}；資深：{me['senior']}；新人：{me['junior']}")

    prefs_df = load_prefs(year, month)
    my = prefs_df[prefs_df["nurse_id"] == my_id].copy()

    def to_dateset(df):
        s = set()
        if df.empty:
            return s
        for r in df.itertuples(index=False):
            raw = getattr(r, "date", "")
            if pd.isna(raw) or str(raw).strip()=="":
                continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt):
                continue
            if int(dt.year) == int(year) and int(dt.month) == int(month):
                s.add(int(dt.day))
        return s

    must_set = to_dateset(my[my["type"]=="must"])

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
    wish_days = sorted(list(all_days - must_days))
    wish_df_preview = pd.DataFrame({
        "date": [f"{year}-{month:02d}-{d:02d}" for d in wish_days]
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

        others = prefs_df[prefs_df["nurse_id"] != my_id].copy()
        merged = pd.concat([others, must_new_df, wish_new_df], ignore_index=True)
        save_prefs(merged, year, month)
        st.success("已儲存完成！")

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
users_view["senior"] = users_view["senior"].astype(str).str.upper().isin(["TRUE","1","YES","Y","T"])
users_view["junior"] = users_view["junior"].astype(str).str.upper().isin(["TRUE","1","YES","Y","T"])

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
    st.success("已儲存人員清單。")

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
if st.button("💾 儲存假日清單"):
    save_holidays(hol_df, year, month)
    st.success("已儲存假日清單。")

holiday_set = set()
for r in hol_df.itertuples(index=False):
    raw = getattr(r, "date", "")
    if pd.isna(raw) or str(raw).strip()=="":
        continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt):
        continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

# ---- 4) 每日加開人力 ----
st.subheader("📈 每日加開人力（單位；加在 min/max 上）")
extra_df = load_extra(year, month)
extra_df = st.data_editor(
    extra_df,
    use_container_width=True,
    num_rows="fixed",
    height=300,
    column_config={
        "day":      st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_extra":  st.column_config.NumberColumn("白班加開", min_value=0, max_value=1000, step=1),
        "E_extra":  st.column_config.NumberColumn("小夜加開", min_value=0, max_value=1000, step=1),
        "N_extra":  st.column_config.NumberColumn("大夜加開", min_value=0, max_value=1000, step=1),
    },
    key="admin_extra"
)
if st.button("💾 儲存加開人力"):
    save_extra(extra_df, year, month)
    st.success("已儲存每日加開人力。")

# ---- 5) 每日三班需求（能力單位） ----
st.subheader("📋 每日三班需求（能力單位；可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max,
    e_ratio_min, e_ratio_max,
    n_ratio_min, n_ratio_max,
    extra_df=extra_df
)
df_demand = st.data_editor(
    df_demand_auto,
    use_container_width=True,
    num_rows="fixed",
    height=380,
    column_config={
        "day":          st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_min_units":  st.column_config.NumberColumn("D_min_units", min_value=0, max_value=1000, step=1),
        "D_max_units":  st.column_config.NumberColumn("D_max_units", min_value=0, max_value=1000, step=1),
        "E_min_units":  st.column_config.NumberColumn("E_min_units", min_value=0, max_value=1000, step=1),
        "E_max_units":  st.column_config.NumberColumn("E_max_units", min_value=0, max_value=1000, step=1),
        "N_min_units":  st.column_config.NumberColumn("N_min_units", min_value=0, max_value=1000, step=1),
        "N_max_units":  st.column_config.NumberColumn("N_max_units", min_value=0, max_value=1000, step=1),
    },
    key="demand_editor"
)

# ---- 6) 排班規則 ----
st.subheader("⚙️ 排班規則")
allow_cross         = st.checkbox("允許同日跨班平衡（以能力單位）", value=True)
prefer_off_holiday  = st.checkbox("假日優先排休（能休就自動打 O）", value=True)
min_monthly_off     = st.number_input("每人每月最少 O 天數", 0, 31, 8, 1)
balance_monthly_off = st.checkbox("盡量讓每人 O 天數接近（平衡）", value=True)
min_work_stretch    = st.number_input("最小連續上班天數（避免上一兩天就休）", 2, 7, 3, 1)

# 半月休假基底與連班 / 連休規則
MIN_OFF_BEFORE_15 = 5   # 1–15 至少 5 天休
MIN_OFF_AFTER_15  = 3   # 16–月底至少 3 天休

TARGET_OFF_DAYS = 10    # 目標月休 ≈ 10 天
MAX_WORK_STREAK = 5     # 最大連續上班 5 天（自然不會>6）
MAX_OFF_STREAK  = 2     # 連續休假盡量不超過 2 天

# ================== 排班主邏輯：initial ==================
def build_initial_schedule(year, month, users_df, prefs_df, demand_df,
                           d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)

    tmp = users_df.copy()
    for col in ["employee_id","shift","weekly_cap","senior","junior"]:
        if col not in tmp.columns:
            tmp[col] = ""
    tmp["employee_id"] = tmp["employee_id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(
        lambda s: s if s in ("D","E","N") else ""
    )
    tmp = tmp[(tmp["employee_id"].astype(str).str.len()>0) & (tmp["shift"].isin(["D","E","N"]))]

    def to_bool(x):
        return str(x).strip().upper() in ("TRUE","1","YES","Y","T")

    def to_wcap(x):
        try:
            v = int(float(x))
            return v if v >= 0 else None
        except:
            return None

    role_map   = {r.employee_id: r.shift   for r in tmp.itertuples(index=False)}
    wcap_map   = {r.employee_id: to_wcap(r.weekly_cap) for r in tmp.itertuples(index=False)}
    senior_map = {r.employee_id: to_bool(r.senior) for r in tmp.itertuples(index=False)}
    junior_map = {r.employee_id: to_bool(r.junior) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # 偏好 map
    def build_date_map(df, typ):
        m = {nid:set() for nid in id_list}
        if df.empty:
            return m
        df2 = df[df["type"]==typ] if "type" in df.columns else pd.DataFrame(columns=["nurse_id","date"])
        for r in df2.itertuples(index=False):
            nid = normalize_id(getattr(r,"nurse_id",""))
            raw = getattr(r,"date","")
            if nid not in m:
                continue
            if pd.isna(raw) or str(raw).strip()=="":
                continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt):
                continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                m[nid].add(int(dt.day))
        return m

    must_map = build_date_map(prefs_df, "must")
    wish_map = build_date_map(prefs_df, "wish")

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
        if w==1: rng = range(1,8)
        elif w==2: rng = range(8,15)
        elif w==3: rng = range(15,22)
        elif w==4: rng = range(22,29)
        else: rng = range(29, nd+1)
        return sum(1 for dd in rng if sched[nid][dd] in ("D","E","N"))

    def person_units_on(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    # 先標必休 O（不可被後續邏輯改掉）
    for nid in id_list:
        for d in must_map[nid]:
            if 1 <= d <= nd:
                sched[nid][d] = "O"

    # 選人池
    def pick_pool(d, s):
        wk = week_index(d)
        pool = []
        for nid in id_list:
            if role_map[nid] != s:
                continue
            if sched[nid][d] != "":
                continue
            if not rest_ok(sched[nid].get(d-1,""), s):
                continue
            cap = wcap_map[nid]
            if cap is not None and week_assigned(nid, wk) >= cap:
                continue
            wished = 1 if d in wish_map[nid] else 0
            pool.append((wished, assigned_days[nid], nid))
        pool.sort()
        return [nid for (_,_,nid) in pool]

    # 逐日逐班排班
    for d in range(1, nd+1):
        for s in ORDER:
            mn_u, mx_u = demand.get(d,{}).get(s, (0,0))
            assigned = []
            units_sum = 0.0
            senior_cnt = 0

            # 先達到 min_units
            while units_sum + 1e-9 < mn_u:
                pool = pick_pool(d, s)
                if not pool:
                    break

                # 首位必有資深（避免新人成為唯一）
                if senior_cnt == 0:
                    non_j = [nid for nid in pool if not junior_map.get(nid, False)]
                    if non_j:
                        pool = non_j
                    else:
                        break

                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    pick_list = cand_sen if (senior_cnt < need_sen and cand_sen) else pool
                else:
                    pick_list = pool

                if not pick_list:
                    break

                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if senior_map.get(nid,False):
                    senior_cnt += 1

            # 再往 max_units 補
            while units_sum + 1e-9 < mx_u:
                pool = pick_pool(d, s)
                if not pool:
                    break

                if senior_cnt == 0:
                    non_j = [nid for nid in pool if not junior_map.get(nid, False)]
                    if non_j:
                        pool = non_j
                    else:
                        break

                if s == "D":
                    need_sen = ceil((len(assigned)+1)/3)
                    cand_sen = [nid for nid in pool if senior_map.get(nid,False)]
                    pick_list = cand_sen if (senior_cnt < need_sen and cand_sen) else pool
                else:
                    pick_list = pool

                if not pick_list:
                    break

                nid = pick_list[0]
                sched[nid][d] = s
                assigned_days[nid] += 1
                assigned.append(nid)
                units_sum += person_units_on(nid, s)
                if senior_map.get(nid,False):
                    senior_cnt += 1

        # 其餘沒被排到的人 → O（但不覆蓋原本必休 O）
        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, senior_map, junior_map, wcap_map, must_map, wish_map

# ================== 各種調整函式 ==================
def cross_shift_balance_with_units(year, month, id_list, sched,
                                   demand, role_map, senior_map, junior_map,
                                   d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    for d in range(1, nd+1):
        actual = {s: sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)
                  for s in ORDER}
        mins = {s: demand.get(d,{}).get(s,(0,0))[0] for s in ORDER}

        changed = True
        while changed:
            changed = False
            shortages = [(s, mins[s]-actual[s]) for s in ORDER
                         if actual[s] + 1e-9 < mins[s]]
            if not shortages:
                break
            shortages.sort(key=lambda x: -x[1])

            for tgt, _need in shortages:
                for src in ORDER:
                    if src == tgt:
                        continue
                    if actual[src] - 1e-9 <= mins.get(src,0):
                        continue
                    candidates = [nid for nid in id_list
                                  if sched[nid][d]==src and not junior_map.get(nid,False)]
                    candidates.sort(key=lambda nid: -units_of(nid, src))
                    moved = False

                    for mv in candidates:
                        def senior_ok_after_move(nid_move, from_s, to_s):
                            if from_s!="D" and to_s!="D":
                                return True
                            d_people = [x for x in id_list if sched[x][d]=="D"]
                            if from_s=="D" and nid_move in d_people:
                                d_people.remove(nid_move)
                            if to_s=="D":
                                d_people.append(nid_move)
                            total = len(d_people)
                            if total==0:
                                return True
                            sen = sum(1 for x in d_people if senior_map.get(x,False))
                            return sen >= ceil(total/3)

                        if not senior_ok_after_move(mv, src, tgt):
                            continue
                        if not (rest_ok(sched[mv].get(d-1,""), tgt) and
                                rest_ok(tgt, sched[mv].get(d+1,""))):
                            continue

                        u_from = units_of(mv, src)
                        u_to   = units_of(mv, tgt)
                        sched[mv][d] = tgt
                        actual[src] -= u_from
                        actual[tgt] += u_to
                        changed = True
                        moved = True
                        break
                    if moved:
                        break
    return sched

def prefer_off_on_holidays(year, month, sched, demand_df, id_list,
                           role_map, senior_map, junior_map,
                           d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    for d in range(1, nd+1):
        if not is_hday(d):
            continue
        for s in ("D","E","N"):
            mn, _ = demand.get(d,{}).get(s,(0,0))

            changed = True
            while changed:
                changed = False
                cur = actual_units(d, s)
                if cur <= mn + 1e-9:
                    break

                cands = [nid for nid in id_list if sched[nid][d]==s]
                cands.sort(key=lambda nid: (units_of(nid,s),
                                            not junior_map.get(nid,False)))
                moved = False
                for nid in cands:
                    u = units_of(nid,s)
                    if cur - u + 1e-9 < mn:
                        continue
                    if not white_senior_ok_if_remove(d,nid):
                        continue
                    if not (rest_ok(sched[nid].get(d-1,""), "O") and
                            rest_ok("O", sched[nid].get(d+1,""))):
                        continue
                    sched[nid][d] = "O"
                    changed = True
                    moved = True
                    break
                if not moved:
                    break
    return sched

def enforce_weekly_one_off(year, month, sched, demand_df, id_list,
                           role_map, senior_map, junior_map,
                           d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

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
            rng = [d for d in week_range(w) if 1 <= d <= nd]
            if not rng:
                continue
            if has_off(nid, w):
                continue
            candidates = sorted(rng, key=lambda d: (0 if is_hday(d) else 1,))
            for d in candidates:
                cur = sched[nid][d]
                if cur == "O":
                    break
                mn = demand.get(d,{}).get(cur,(0,0))[0]
                u  = units_of(nid, cur)
                if actual_units(d, cur) - u + 1e-9 < mn:
                    continue
                if not white_senior_ok_if_remove(d, nid):
                    continue
                if not (rest_ok(sched[nid].get(d-1,""), "O") and
                        rest_ok("O", sched[nid].get(d+1,""))):
                    continue
                sched[nid][d] = "O"
                break
    return sched

def enforce_min_monthly_off(year, month, sched, demand_df, id_list,
                            role_map, senior_map, junior_map,
                            d_avg, e_avg, n_avg,
                            min_off=8, balance=True, holiday_set=None,
                            target_off=10):
    nd = days_in_month(year, month)
    if holiday_set is None:
        holiday_set = set()
    if target_off is None:
        target_off = min_off
    target_off = max(min_off, target_off)

    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    def off_count(nid):
        return sum(1 for d in range(1, nd+1) if sched[nid][d]=="O")

    def try_add_one_off(nid):
        if off_count(nid) >= target_off:
            return False
        work_days = [(d, sched[nid][d]) for d in range(1, nd+1)
                     if sched[nid][d] in ("D","E","N")]
        if not work_days:
            return False
        scored = []
        for d, s in work_days:
            mn = demand.get(d,{}).get(s,(0,0))[0]
            u  = units_of(nid, s)
            slack = actual_units(d, s) - mn
            feasible = (slack + 1e-9 >= u) and white_senior_ok_if_remove(d,nid) \
                       and rest_ok(sched[nid].get(d-1,""), "O") \
                       and rest_ok("O", sched[nid].get(d+1,""))
            if feasible:
                scored.append((1 if is_hday(d) else 2, -slack, d))
        if not scored:
            return False
        scored.sort()
        chosen_d = scored[0][2]
        sched[nid][chosen_d] = "O"
        return True

    # 先確保至少 min_off
    changed = True
    while changed:
        changed = False
        needs = sorted([nid for nid in id_list if off_count(nid) < min_off],
                       key=lambda x: off_count(x))
        if not needs:
            break
        for nid in needs:
            if try_add_one_off(nid):
                changed = True
        if not changed:
            break

    if not balance:
        return sched

    # 平衡 O，讓大家接近
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

def enforce_halfmonth_off_base(year, month, sched, demand_df, id_list,
                               role_map, senior_map, junior_map,
                               d_avg, e_avg, n_avg,
                               min_before=5, min_after=3,
                               min_off_total=8, target_off=10,
                               holiday_set=None, must_map=None):
    nd = days_in_month(year, month)
    if holiday_set is None:
        holiday_set = set()
    if must_map is None:
        must_map = {}

    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    def off_count_all(nid):
        return sum(1 for d in range(1, nd+1) if sched[nid][d]=="O")

    def off_count_before(nid):
        return sum(1 for d in range(1, min(15, nd)+1) if sched[nid][d]=="O")

    def off_count_after(nid):
        return sum(1 for d in range(16, nd+1) if sched[nid][d]=="O")

    def try_add_one_off_in_range(nid, start_day, end_day):
        if off_count_all(nid) >= target_off:
            return False
        days = [d for d in range(start_day, end_day+1)
                if 1 <= d <= nd and sched[nid][d] in ("D","E","N")
                and d not in must_map.get(nid, set())]
        if not days:
            return False
        scored = []
        for d in days:
            s = sched[nid][d]
            mn = demand.get(d,{}).get(s,(0,0))[0]
            u  = units_of(nid, s)
            slack = actual_units(d, s) - mn
            feasible = (slack + 1e-9 >= u) and white_senior_ok_if_remove(d,nid) \
                       and rest_ok(sched[nid].get(d-1,""), "O") \
                       and rest_ok("O", sched[nid].get(d+1,""))
            if feasible:
                scored.append((1 if is_hday(d) else 2, -slack, d))
        if not scored:
            return False
        scored.sort()
        chosen_d = scored[0][2]
        sched[nid][chosen_d] = "O"
        return True

    changed = True
    guard = 0
    while changed and guard < 50:
        changed = False
        guard += 1
        for nid in id_list:
            b = off_count_before(nid)
            a = off_count_after(nid)
            total = off_count_all(nid)
            if total < min_off_total:
                need = min_off_total - total
                for _ in range(need):
                    if b < min_before:
                        if try_add_one_off_in_range(nid, 1, min(15, nd)):
                            b += 1
                            total += 1
                            changed = True
                            continue
                    if a < min_after:
                        if try_add_one_off_in_range(nid, 16, nd):
                            a += 1
                            total += 1
                            changed = True
                            continue
                continue

            if b < min_before and off_count_all(nid) < target_off:
                if try_add_one_off_in_range(nid, 1, min(15, nd)):
                    changed = True
                    continue
            if a < min_after and off_count_all(nid) < target_off:
                if try_add_one_off_in_range(nid, 16, nd):
                    changed = True
                    continue
    return sched

def enforce_min_work_stretch(year, month, sched, demand_df, id_list,
                             role_map, senior_map, junior_map,
                             d_avg, e_avg, n_avg, min_stretch=3,
                             holiday_set=None, must_map=None):
    nd = days_in_month(year, month)
    if must_map is None:
        must_map = {}

    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(x,s) for x in id_list if sched[x][d]==s)

    def white_senior_ok_if_add(d, nid):
        if role_map[nid] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D"] + [nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    def work_streak_before(nid, d):
        k = 0
        dd = d-1
        while dd >= 1 and sched[nid][dd] in ("D","E","N"):
            k += 1
            dd -= 1
        return k

    def try_move_off_forward(nid, d):
        if d in must_map.get(nid, set()):
            return False

        s_fixed = role_map[nid]
        if s_fixed not in ("D","E","N"):
            return False
        mn_d, mx_d = demand.get(d,{}).get(s_fixed,(0,0))
        if actual_units(d, s_fixed) + units_of(nid,s_fixed) > mx_d + 1e-9:
            return False
        if not rest_ok(sched[nid].get(d-1,""), s_fixed) or \
           not rest_ok(s_fixed, sched[nid].get(d+1,"")):
            return False
        if not white_senior_ok_if_add(d, nid):
            return False

        for d2 in range(d+1, nd+1):
            s2 = sched[nid][d2]
            if s2 not in ("D","E","N"):
                continue
            mn2, _mx2 = demand.get(d2,{}).get(s2,(0,0))
            if actual_units(d2,s2) - units_of(nid,s2) + 1e-9 < mn2:
                continue
            if not white_senior_ok_if_remove(d2, nid):
                continue
            if not (rest_ok(sched[nid].get(d2-1,""), "O") and
                    rest_ok("O", sched[nid].get(d2+1,""))):
                continue
            sched[nid][d]  = s_fixed
            sched[nid][d2] = "O"
            return True
        return False

    changed = True
    guard = 0
    while changed and guard < 3:
        guard += 1
        changed = False
        for nid in id_list:
            for d in range(1, nd+1):
                if d in must_map.get(nid, set()):
                    continue
                if sched[nid][d] != "O":
                    continue
                if work_streak_before(nid, d) < min_stretch:
                    if try_move_off_forward(nid, d):
                        changed = True
    return sched

def enforce_streak_preferences(year, month, sched, demand_df, id_list,
                               role_map, senior_map, junior_map,
                               d_avg, e_avg, n_avg,
                               max_work_streak=5, max_off_streak=2,
                               min_monthly_off=8,
                               min_before=5, min_after=3,
                               target_off=10,
                               holiday_set=None, must_map=None):
    nd = days_in_month(year, month)
    if holiday_set is None:
        holiday_set = set()
    if must_map is None:
        must_map = {}

    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(x,s) for x in id_list if sched[x][d]==s)

    def off_total(nid):
        return sum(1 for d in range(1, nd+1) if sched[nid][d]=="O")

    def off_before(nid):
        return sum(1 for d in range(1, min(15, nd)+1) if sched[nid][d]=="O")

    def off_after(nid):
        return sum(1 for d in range(16, nd+1) if sched[nid][d]=="O")

    def white_senior_ok_if_add(d, nid):
        if role_map[nid] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D"] + [nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D" and x != nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    # 1) 最大連續上班天數（> max_work_streak 會試圖插 O）
    for nid in id_list:
        d = 1
        while d <= nd:
            if sched[nid][d] not in ("D","E","N"):
                d += 1
                continue
            start = d
            while d+1 <= nd and sched[nid][d+1] in ("D","E","N"):
                d += 1
            end = d
            length = end - start + 1
            if length > max_work_streak:
                for mid in range(start+1, end):
                    if mid in must_map.get(nid,set()):
                        continue
                    s_mid = sched[nid][mid]
                    mn = demand.get(mid,{}).get(s_mid,(0,0))[0]
                    u  = units_of(nid, s_mid)
                    if actual_units(mid, s_mid) - u + 1e-9 < mn:
                        continue
                    if not white_senior_ok_if_remove(mid, nid):
                        continue
                    if off_total(nid) + 1 > target_off + 2:
                        continue
                    if not (rest_ok(sched[nid].get(mid-1,""), "O") and
                            rest_ok("O", sched[nid].get(mid+1,""))):
                        continue
                    sched[nid][mid] = "O"
                    break
            d += 1

    # 2) 限制連續休假天數（> max_off_streak 時嘗試插上班）
    for nid in id_list:
        d = 1
        while d <= nd:
            if sched[nid][d] != "O":
                d += 1
                continue
            start = d
            while d+1 <= nd and sched[nid][d+1] == "O":
                d += 1
            end = d
            length = end - start + 1
            if length > max_off_streak:
                s_fixed = role_map[nid]
                if s_fixed not in ("D","E","N"):
                    d += 1
                    continue

                for mid in range(start+1, end):
                    if mid in must_map.get(nid,set()):
                        continue
                    if mid <= 15:
                        if off_before(nid) - 1 < min_before:
                            continue
                    else:
                        if off_after(nid) - 1 < min_after:
                            continue
                    if off_total(nid) - 1 < min_monthly_off:
                        continue

                    mn, mx = demand.get(mid,{}).get(s_fixed,(0,0))
                    if actual_units(mid, s_fixed) + units_of(nid,s_fixed) > mx + 1e-9:
                        continue
                    if not white_senior_ok_if_add(mid, nid):
                        continue
                    if not (rest_ok(sched[nid].get(mid-1,""), s_fixed) and
                            rest_ok(s_fixed, sched[nid].get(mid+1,""))):
                        continue
                    sched[nid][mid] = s_fixed
                    break
            d += 1

    return sched

# ⭐ 新增：更強制拆掉「超長連續上班」
def hard_break_long_work_streaks(year, month, sched, demand_df, id_list,
                                 role_map, senior_map, junior_map,
                                 d_avg, e_avg, n_avg,
                                 max_work_streak=5,
                                 min_monthly_off=8,
                                 must_map=None):
    """
    更強制版：只要某人連續上班 > max_work_streak，就盡量在中間插 O
    只確保：
      1) 當日能力 >= min_units
      2) 白班資深比例維持 >= 1/3
      3) 這個人月休最後 >= min_monthly_off
    """
    nd = days_in_month(year, month)
    if must_map is None:
        must_map = {}

    demand = {int(r.day): {
                "D": (int(r.D_min_units), int(r.D_max_units)),
                "E": (int(r.E_min_units), int(r.E_max_units)),
                "N": (int(r.N_min_units), int(r.N_max_units)),
              } for r in demand_df.itertuples(index=False)}

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid, False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(x, s) for x in id_list if sched[x][d] == s)

    def off_total(nid):
        return sum(1 for d in range(1, nd + 1) if sched[nid][d] == "O")

    def white_senior_ok_if_to_O(d, nid):
        """把某人從 D 變成 O 時，白班資深比例是否仍 >= 1/3"""
        if sched[nid][d] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people)
        if total == 0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x, False))
        return sen >= ceil(total / 3)

    for nid in id_list:
        d = 1
        while d <= nd:
            if sched[nid][d] not in ("D", "E", "N"):
                d += 1
                continue

            # 找出連續上班區段 [start, end]
            start = d
            while d + 1 <= nd and sched[nid][d + 1] in ("D", "E", "N"):
                d += 1
            end = d
            length = end - start + 1

            if length > max_work_streak:
                cur_off = off_total(nid)

                # 需要插入幾個 O 才會讓每段 <= max_work_streak
                needed_breaks = ceil(length / max_work_streak) - 1

                candidates = list(range(start + 1, end))
                score_list = []
                for day in candidates:
                    if day in must_map.get(nid, set()):
                        continue
                    s_code = sched[nid][day]
                    mn, _mx = demand.get(day, {}).get(s_code, (0, 0))
                    u = units_of(nid, s_code)
                    slack = actual_units(day, s_code) - u - mn
                    score_list.append((slack, day, s_code, u))

                score_list.sort(reverse=True, key=lambda x: x[0])

                used = 0
                for slack, day, s_code, u in score_list:
                    if used >= needed_breaks:
                        break
                    # 改成 O 後不能低於 min_units
                    if slack < -1e-9:
                        continue
                    if not white_senior_ok_if_to_O(day, nid):
                        continue
                    # 改成 O，月休不能 < 最低月休日數
                    if cur_off + 1 < min_monthly_off:
                        pass
                    sched[nid][day] = "O"
                    cur_off += 1
                    used += 1

            d += 1

    return sched

# ⭐ 平滑短上班段（避免 W O W / 上一天休一天）
def smooth_short_work_segments(year, month, sched, demand_df, id_list,
                               role_map, senior_map, junior_map,
                               d_avg, e_avg, n_avg,
                               min_stretch=3,
                               min_monthly_off=8,
                               min_before=5,
                               min_after=3,
                               holiday_set=None,
                               must_map=None):
    nd = days_in_month(year, month)
    if holiday_set is None:
        holiday_set = set()
    if must_map is None:
        must_map = {}

    demand = {int(r.day):{
                "D":(int(r.D_min_units),int(r.D_max_units)),
                "E":(int(r.E_min_units),int(r.E_max_units)),
                "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}

    def units_of(nid, s):
        return per_person_units(junior_map.get(nid,False),
                                s, d_avg, e_avg, n_avg, 4.0)

    def actual_units(d, s):
        return sum(units_of(x,s) for x in id_list if sched[x][d]==s)

    def off_total(nid):
        return sum(1 for d in range(1, nd+1) if sched[nid][d]=="O")

    def off_before(nid):
        return sum(1 for d in range(1, min(15, nd)+1) if sched[nid][d]=="O")

    def off_after(nid):
        return sum(1 for d in range(16, nd+1) if sched[nid][d]=="O")

    def white_senior_ok_if_add(d, nid):
        if role_map[nid] != "D":
            return True
        d_people = [x for x in id_list if sched[x][d]=="D"] + [nid]
        total = len(d_people)
        if total==0:
            return True
        sen = sum(1 for x in d_people if senior_map.get(x,False))
        return sen >= ceil(total/3)

    for nid in id_list:
        d = 1
        while d <= nd:
            if sched[nid][d] not in ("D","E","N"):
                d += 1
                continue
            start = d
            while d+1 <= nd and sched[nid][d+1] in ("D","E","N"):
                d += 1
            end = d
            length = end - start + 1
            # 只處理短段（小於 min_stretch）
            if length < min_stretch:
                extended = True
                while length < min_stretch and extended:
                    extended = False
                    # 左邊
                    ld = start - 1
                    if ld >= 1 and sched[nid][ld] == "O" and ld not in must_map.get(nid,set()):
                        if off_total(nid) - 1 >= min_monthly_off:
                            if (ld <= 15 and off_before(nid) - 1 >= min_before) or \
                               (ld >= 16 and off_after(nid) - 1 >= min_after):
                                s_fixed = role_map[nid]
                                if s_fixed in ("D","E","N"):
                                    mn, mx = demand.get(ld,{}).get(s_fixed,(0,0))
                                    if actual_units(ld, s_fixed) + units_of(nid,s_fixed) <= mx + 1e-9:
                                        if white_senior_ok_if_add(ld, nid):
                                            if rest_ok(sched[nid].get(ld-1,""), s_fixed) and \
                                               rest_ok(s_fixed, sched[nid].get(ld+1,"")):
                                                sched[nid][ld] = s_fixed
                                                start = ld
                                                extended = True
                    # 右邊
                    rd = end + 1
                    if length < min_stretch and rd <= nd and sched[nid][rd] == "O" and rd not in must_map.get(nid,set()):
                        if off_total(nid) - 1 >= min_monthly_off:
                            if (rd <= 15 and off_after(nid) - 1 >= min_after) or \
                               (rd >= 16 and off_after(nid) - 1 >= min_after):
                                s_fixed = role_map[nid]
                                if s_fixed in ("D","E","N"):
                                    mn, mx = demand.get(rd,{}).get(s_fixed,(0,0))
                                    if actual_units(rd, s_fixed) + units_of(nid,s_fixed) <= mx + 1e-9:
                                        if white_senior_ok_if_add(rd, nid):
                                            if rest_ok(sched[nid].get(rd-1,""), s_fixed) and \
                                               rest_ok(s_fixed, sched[nid].get(rd+1,"")):
                                                sched[nid][rd] = s_fixed
                                                end = rd
                                                extended = True
                    length = end - start + 1
            d += 1

    return sched

# ================== 整體排班流程 ==================
def run_schedule(df_demand):
    users_df = load_users()
    prefs_df = load_prefs(year, month)

    (sched, demand_map, role_map, id_list,
     senior_map, junior_map, wcap_map,
     must_map, wish_map) = build_initial_schedule(
        year, month, users_df, prefs_df,
        df_demand, d_avg, e_avg, n_avg
    )

    if allow_cross:
        sched = cross_shift_balance_with_units(
            year, month, id_list, sched,
            demand_map, role_map, senior_map, junior_map,
            d_avg, e_avg, n_avg
        )

    # 假日優先排 O
    hol_df = load_holidays(year, month)
    holiday_set_local = set()
    for r in hol_df.itertuples(index=False):
        raw = getattr(r,"date","")
        if pd.isna(raw) or str(raw).strip()=="":
            continue
        dt = pd.to_datetime(raw, errors="coerce")
        if pd.isna(dt):
            continue
        if int(dt.year)==int(year) and int(dt.month)==int(month):
            holiday_set_local.add(date(int(dt.year), int(dt.month), int(dt.day)))

    if prefer_off_holiday:
        sched = prefer_off_on_holidays(
            year, month, sched, df_demand, id_list,
            role_map, senior_map, junior_map,
            d_avg, e_avg, n_avg, holiday_set_local
        )

    # 每週至少一休
    sched = enforce_weekly_one_off(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg, holiday_set_local
    )

    # 月休 ≥ min_monthly_off，目標 ≈ 10 天
    sched = enforce_min_monthly_off(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        min_off=min_monthly_off,
        balance=balance_monthly_off,
        holiday_set=holiday_set_local,
        target_off=TARGET_OFF_DAYS
    )

    # 半月基底：1–15 至少 5 天，16–月底至少 3 天
    sched = enforce_halfmonth_off_base(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        min_before=MIN_OFF_BEFORE_15,
        min_after=MIN_OFF_AFTER_15,
        min_off_total=min_monthly_off,
        target_off=TARGET_OFF_DAYS,
        holiday_set=holiday_set_local,
        must_map=must_map
    )

    # 避免上一兩天就休（最小連續上班 min_work_stretch）
    sched = enforce_min_work_stretch(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        min_stretch=min_work_stretch,
        holiday_set=holiday_set_local,
        must_map=must_map
    )

    # 最大連班 / 連休偏好
    sched = enforce_streak_preferences(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        max_work_streak=MAX_WORK_STREAK,
        max_off_streak=MAX_OFF_STREAK,
        min_monthly_off=min_monthly_off,
        min_before=MIN_OFF_BEFORE_15,
        min_after=MIN_OFF_AFTER_15,
        target_off=TARGET_OFF_DAYS,
        holiday_set=holiday_set_local,
        must_map=must_map
    )

    # ⭐ 強制拆掉所有 > MAX_WORK_STREAK 的連班（避免 10 連 E）
    sched = hard_break_long_work_streaks(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        max_work_streak=MAX_WORK_STREAK,
        min_monthly_off=min_monthly_off,
        must_map=must_map
    )

    # ⭐ 最後再做一次「平滑短上班段」避免 W O W / 上一天休一天
    sched = smooth_short_work_segments(
        year, month, sched, df_demand, id_list,
        role_map, senior_map, junior_map,
        d_avg, e_avg, n_avg,
        min_stretch=min_work_stretch,
        min_monthly_off=min_monthly_off,
        min_before=MIN_OFF_BEFORE_15,
        min_after=MIN_OFF_AFTER_15,
        holiday_set=holiday_set_local,
        must_map=must_map
    )

    ndays = days_in_month(year, month)

    # 輸出班表
    roster_rows = []
    for nid in id_list:
        row = {
            "id": nid,
            "shift": role_map[nid],
            "senior": senior_map.get(nid,False),
            "junior": junior_map.get(nid,False),
        }
        for d in range(1, ndays+1):
            row[str(d)] = sched[nid][d]
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(
        ["shift","senior","junior","id"]
    ).reset_index(drop=True)

    def count_code(nid, code):
        return sum(1 for d in range(1, ndays+1) if sched[nid][d]==code)

    def is_hday(d):
        return is_sunday(year, month, d) or (date(year,month,d) in holiday_set_local)

    holiday_off = {
        nid: sum(1 for d in range(1, ndays+1)
                 if is_hday(d) and sched[nid][d]=="O")
        for nid in id_list
    }

    summary_df = pd.DataFrame([{
        "id": nid,
        "shift": role_map[nid],
        "senior": senior_map.get(nid,False),
        "junior": junior_map.get(nid,False),
        "D天數": count_code(nid,"D"),
        "E天數": count_code(nid,"E"),
        "N天數": count_code(nid,"N"),
        "O天數": count_code(nid,"O"),
        "本月例假日放假數": holiday_off[nid],
    } for nid in id_list]).sort_values(
        ["shift","senior","junior","id"]
    ).reset_index(drop=True)

    def person_units_on(nid, s):
        return per_person_units(
            junior_map.get(nid,False),
            s, d_avg, e_avg, n_avg, 4.0
        )

    comp_rows = []
    for d in range(1, ndays+1):
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(
                person_units_on(nid,s)
                for nid in id_list
                if sched[nid][d]==s
            )
            if act + 1e-9 < mn:
                status = "🔴 不足"
            elif act <= mx + 1e-9:
                status = "🟢 達標"
            else:
                status = "🟡 超編"
            comp_rows.append({
                "day": d,
                "shift": s,
                "min_units": mn,
                "max_units": mx,
                "actual_units": round(act,2),
                "狀態": status,
            })
    compliance_df = pd.DataFrame(comp_rows)

    return roster_df, summary_df, compliance_df

# ================== 產生班表按鈕 ==================
if st.button("🚀 產生班表（以員工編號為 id）", type="primary"):
    roster_df, summary_df, compliance_df = run_schedule(df_demand)

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("📊 統計摘要（含資深/新人、例假日放假數）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📈 每日達標情況（以能力單位）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

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
        "⬇️ 下載 CSV 達標",
        data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"compliance_{year}-{month:02d}.csv"
    )
else:
    st.info(
        "流程建議：\n"
        "1️⃣ 同仁登入 → 用【選取】填必休（其餘自動變想休）\n"
        "2️⃣ 護理長設定床數、護病比、加開人力、假日\n"
        "3️⃣ 按下『產生班表』即可。\n\n"
        "本版規則：\n"
        "• 每月 1–15 至少休 5 天，16–月底至少休 3 天\n"
        "• 每人每月至少休 8 天，目標約 10 天，盡量平均\n"
        "• 盡量 3–4 天上班為一個週期，最大連續上班 5 天\n"
        "• 連續休假盡量不超過 2 天\n"
        "• 盡量避免『上一天休一天』的短上班段\n"
        "• 跨班別與所有班別銜接皆檢查 11 小時休息\n"
        "• 新人護病比 1:4，排在資深旁邊使用；白班資深至少 1/3。"
    )
