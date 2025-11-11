import streamlit as st
import pandas as pd
import os
from datetime import datetime, date
import calendar
from math import ceil

# =============== 基本設定與資料路徑 ===============
st.set_page_config(page_title="Nurse Roster • Login + Custom Demand + Prefs", layout="wide")
DATA_DIR = "/mnt/data/nursing"
os.makedirs(DATA_DIR, exist_ok=True)

USERS_CSV = os.path.join(DATA_DIR, "users.csv")              # 人員清單/帳密（只存末四碼）
PREFS_CSV_TMPL = os.path.join(DATA_DIR, "prefs_{year}_{month}.csv")  # 個人必休/想休彙整
HOLIDAYS_CSV_TMPL = os.path.join(DATA_DIR, "holidays_{year}_{month}.csv")  # 假日清單（管理端）
EXTRA_CSV_TMPL = os.path.join(DATA_DIR, "extra_{year}_{month}.csv")        # 每日加開

# 預設護理長帳密（可在程式裡改）
ADMIN_USER = "headnurse"
ADMIN_PASS = "admin123"

# =============== 共用工具 ===============
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

def ceil_div(beds, r):
    return 0 if r <= 0 else (beds + r - 1) // r

def load_users():
    if os.path.exists(USERS_CSV):
        df = pd.read_csv(USERS_CSV, dtype=str).fillna("")
    else:
        # 預設幾位同仁，pwd4=9999 只是示例，請管理端更新
        data = []
        for i in range(1, 11):
            data.append({"employee_id": f"N{i:03d}", "name": f"護理{i:02d}",
                         "pwd4": "9999", "shift": ("D" if i<=4 else ("E" if i<=7 else "N")),
                         "weekly_cap": "", "senior": "TRUE" if i in (1,2,5,8) else "FALSE",
                         "junior": "TRUE" if i in (9,10) else "FALSE"})
        df = pd.DataFrame(data)
        df.to_csv(USERS_CSV, index=False)
    # 正規化欄位型別
    for c in ["employee_id","name","pwd4","shift","weekly_cap","senior","junior"]:
        if c not in df.columns: df[c] = ""
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
            if c not in df.columns: df[c] = ""
        return df
    else:
        return pd.DataFrame(columns=["nurse_id","date","type"])

def save_prefs(df, year, month):
    df.to_csv(prefs_path(year, month), index=False)

def holidays_path(year, month):
    return HOLIDAYS_CSV_TMPL.format(year=year, month=f"{month:02d}")

def load_holidays(year, month):
    p = holidays_path(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p, dtype=str).fillna("")
        if "date" not in df.columns: df["date"] = ""
        return df
    return pd.DataFrame(columns=["date"])

def save_holidays(df, year, month):
    df.to_csv(holidays_path(year, month), index=False)

def extra_path(year, month):
    return EXTRA_CSV_TMPL.format(year=year, month=f"{month:02d}")

def load_extra(year, month):
    p = extra_path(year, month)
    if os.path.exists(p):
        df = pd.read_csv(p).fillna(0)
    else:
        nd = days_in_month(year, month)
        df = pd.DataFrame({"day": list(range(1, nd+1)),
                           "D_extra": [0]*nd, "E_extra": [0]*nd, "N_extra": [0]*nd})
    # 確保欄位存在
    for c in ["day","D_extra","E_extra","N_extra"]:
        if c not in df.columns: df[c] = 0
    return df

def save_extra(df, year, month):
    df.to_csv(extra_path(year, month), index=False)

# 需求（以床數與護病比「區間」換算『單位』需求）+ 套用「每日加開」
def seed_demand_from_beds(y, m, total_beds,
                          d_ratio_min=6, d_ratio_max=7,
                          e_ratio_min=10, e_ratio_max=12,
                          n_ratio_min=15, n_ratio_max=16,
                          extra_df=None):
    rows = []
    nd = days_in_month(y, m)
    ext = extra_df if extra_df is not None else pd.DataFrame(columns=["day","D_extra","E_extra","N_extra"])
    ext = ext.set_index("day") if "day" in ext.columns else pd.DataFrame()
    for d in range(1, nd + 1):
        D_min = ceil(total_beds / d_ratio_max) if d_ratio_max>0 else 0
        D_max = ceil(total_beds / d_ratio_min) if d_ratio_min>0 else D_min
        E_min = ceil(total_beds / e_ratio_max) if e_ratio_max>0 else 0
        E_max = ceil(total_beds / e_ratio_min) if e_ratio_min>0 else E_min
        N_min = ceil(total_beds / n_ratio_max) if n_ratio_max>0 else 0
        N_max = ceil(total_beds / n_ratio_min) if n_ratio_min>0 else N_min
        # 加開單位（對 min/max 都加）
        d_ex = int(ext.at[d,"D_extra"]) if d in ext.index else 0
        e_ex = int(ext.at[d,"E_extra"]) if d in ext.index else 0
        n_ex = int(ext.at[d,"N_extra"]) if d in ext.index else 0
        rows.append({
            "day": d,
            "D_min_units": int(D_min + d_ex), "D_max_units": int(D_max + d_ex),
            "E_min_units": int(E_min + e_ex), "E_max_units": int(E_max + e_ex),
            "N_min_units": int(N_min + n_ex), "N_max_units": int(N_max + n_ex),
        })
    return pd.DataFrame(rows)

# 新人能力：一般=1.0；新人= (新人平均護病比 / 班別平均護病比)；固定用 4.5 當新人平均
def per_person_units(is_junior: bool, shift_code: str, d_avg: float, e_avg: float, n_avg: float, jr_avg: float = 4.5):
    if not is_junior:
        return 1.0
    base = {"D": d_avg, "E": e_avg, "N": n_avg}.get(shift_code, d_avg)
    if base <= 0: return 1.0
    return max(0.1, jr_avg / base)

# =============== 登入區 ===============
def login_block():
    st.sidebar.subheader("登入")
    acct = st.sidebar.text_input("帳號（員工編號／護理長）", value=st.session_state.get("acct",""))
    pwd  = st.sidebar.text_input("密碼（員工：身分證末四碼）", type="password", value=st.session_state.get("pwd",""))
    do_login = st.sidebar.button("登入")
    if do_login:
        st.session_state["acct"] = acct
        st.session_state["pwd"] = pwd
        # 管理者
        if acct == ADMIN_USER and pwd == ADMIN_PASS:
            st.session_state["role"] = "admin"
            st.sidebar.success("已以管理者登入")
            return
        # 員工驗證
        users = load_users()
        row = users[users["employee_id"].astype(str)==acct]
        if row.empty:
            st.sidebar.error("查無此員工編號")
            return
        if str(row.iloc[0]["pwd4"]).strip() != str(pwd).strip():
            st.sidebar.error("密碼錯誤（請輸入身分證末四碼）")
            return
        st.session_state["role"] = "user"
        st.sidebar.success(f"已以員工 {acct} 登入")

if "role" not in st.session_state:
    st.session_state["role"] = None
login_block()

# =============== 共用年月與需求參數 ===============
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
    st.caption("護病比（區間；不用假日係數）")
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

# =============== 角色切換畫面 ===============
role = st.session_state.get("role", None)

# ---------- 員工畫面：填寫自己的必休/想休 ----------
if role == "user":
    users = load_users()
    me = users[users["employee_id"]==st.session_state["acct"]].iloc[0]
    st.success(f"👤 你好，{me['name']}（{me['employee_id']}），固定班別：{me['shift']}；資深：{me['senior']}；新人：{me['junior']}")
    # 載入本月偏好
    prefs_df = load_prefs(year, month)
    my = prefs_df[prefs_df["nurse_id"]==me["employee_id"]].copy()

    st.subheader("⛔ 必休（填日期：YYYY-MM-DD）")
    must_df = my[my["type"]=="must"].drop(columns=["type"]).rename(columns={"nurse_id":"id"}).reset_index(drop=True)
    must_df = st.data_editor(must_df, use_container_width=True, num_rows="dynamic", height=220, key="u_must")
    st.caption("說明：只要你填的必休會被當作『硬性 O』。")

    st.subheader("📝 想休（填日期：YYYY-MM-DD）")
    wish_df = my[my["type"]=="wish"].drop(columns=["type"]).rename(columns={"nurse_id":"id"}).reset_index(drop=True)
    wish_df = st.data_editor(wish_df, use_container_width=True, num_rows="dynamic", height=220, key="u_wish")

    if st.button("💾 儲存我的請休"):
        # 重新合併到 prefs
        def norm_dates(df):
            rows=[]
            for r in df.itertuples(index=False):
                _id = normalize_id(getattr(r,"id",""))
                raw = getattr(r,"date","")
                if _id=="" or pd.isna(raw) or str(raw).strip()=="": continue
                dt = pd.to_datetime(raw, errors="coerce")
                if pd.isna(dt): continue
                if int(dt.year)==int(year) and int(dt.month)==int(month):
                    rows.append({"nurse_id": _id, "date": f"{int(dt.year)}-{int(dt.month):02d}-{int(dt.day):02d}"})
            return pd.DataFrame(rows)

        must_new = norm_dates(must_df)
        must_new["type"] = "must"
        wish_new = norm_dates(wish_df)
        wish_new["type"] = "wish"

        others = prefs_df[prefs_df["nurse_id"]!=me["employee_id"]].copy()
        merged = pd.concat([others, must_new, wish_new], ignore_index=True)
        save_prefs(merged, year, month)
        st.success("已儲存。護理長在後台會看到你的請休，並用你的員工編號作為排班 id。")

    st.stop()

# ---------- 管理端畫面：管理人員/需求/假日/產生班表 ----------
if role != "admin":
    st.info("請先以『護理長帳號』或『員工編號＋身分證末四碼』登入。護理長預設帳密：headnurse / admin123（請修改程式內預設值）。")
    st.stop()

st.success("✅ 以護理長（管理者）身份登入")

# 1) 人員清單（帳密與角色）
st.subheader("👥 人員清單（帳密與屬性）")
users = load_users()
users = st.data_editor(
    users, use_container_width=True, num_rows="dynamic", height=360,
    column_config={
        "employee_id": st.column_config.TextColumn("員工編號（登入帳號）"),
        "name": st.column_config.TextColumn("姓名"),
        "pwd4": st.column_config.TextColumn("密碼（身分證末四碼）"),
        "shift": st.column_config.TextColumn("固定班別 D/E/N"),
        "weekly_cap": st.column_config.TextColumn("每週上限天（可空白）"),
        "senior": st.column_config.TextColumn("資深 TRUE/FALSE"),
        "junior": st.column_config.TextColumn("新人 TRUE/FALSE"),
    }, key="admin_users"
)
if st.button("💾 儲存人員清單"):
    save_users(users)
    st.success("已儲存人員清單。")

# 2) 員工請休彙整與編修
st.subheader("📥 員工請休彙整（本月）")
prefs_df = load_prefs(year, month)
st.dataframe(prefs_df, use_container_width=True, height=260)
st.caption("來源：員工端自行填寫。你可匯出備份或直接編修 CSV 檔案於伺服器目錄。")

# 3) 假日清單（影響『假日優先 O』與統計『本月例假日放假數』）
st.subheader("📅 假日清單（僅供排休偏好與統計；不再有假日係數）")
hol_df = load_holidays(year, month)
hol_df = st.data_editor(hol_df, use_container_width=True, num_rows="dynamic", height=180, key="admin_holidays")
if st.button("💾 儲存假日清單"):
    save_holidays(hol_df, year, month)
    st.success("已儲存假日清單。")

holiday_set = set()
for r in hol_df.itertuples(index=False):
    raw = getattr(r,"date","")
    if pd.isna(raw) or str(raw).strip()=="": continue
    dt = pd.to_datetime(raw, errors="coerce")
    if pd.isna(dt): continue
    if int(dt.year)==int(year) and int(dt.month)==int(month):
        holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))

# 4) 每日加開人力（客製化）
st.subheader("📈 每日加開人力（單位；會直接加在 min/max 上）")
extra_df = load_extra(year, month)
extra_df = st.data_editor(
    extra_df, use_container_width=True, num_rows="fixed", height=300,
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=nd, step=1),
        "D_extra": st.column_config.NumberColumn("白班加開", min_value=0, max_value=1000, step=1),
        "E_extra": st.column_config.NumberColumn("小夜加開", min_value=0, max_value=1000, step=1),
        "N_extra": st.column_config.NumberColumn("大夜加開", min_value=0, max_value=1000, step=1),
    }, key="admin_extra"
)
if st.button("💾 儲存加開人力"):
    save_extra(extra_df, year, month)
    st.success("已儲存每日加開人力。")

# 5) 產生每日需求表（可再微調）
st.subheader("📋 每日三班需求（單位；自動 + 加開，可再微調）")
df_demand_auto = seed_demand_from_beds(
    year, month, total_beds,
    d_ratio_min, d_ratio_max, e_ratio_min, e_ratio_max, n_ratio_min, n_ratio_max,
    extra_df=extra_df
)
df_demand = st.data_editor(
    df_demand_auto, use_container_width=True, num_rows="fixed", height=380,
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

# 6) 排班規則（同前版）
st.subheader("⚙️ 排班規則")
allow_cross = st.checkbox("允許同日跨班平衡（以單位計）", value=True)
prefer_off_holiday = st.checkbox("假日優先排休（能休就自動打 O）", value=True)
min_monthly_off = st.number_input("每人每月最少 O 天數", min_value=0, max_value=31, value=8, step=1)
balance_monthly_off = st.checkbox("盡量讓每人 O 天數接近（平衡）", value=True)

# =============== 排班核心（沿用你前版規則） ===============
def build_initial_schedule(year, month, users_df, prefs_df, demand_df, d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)

    tmp = users_df.copy()
    for col in ["employee_id","shift","weekly_cap","senior","junior"]:
        if col not in tmp.columns: tmp[col] = ""
    tmp["employee_id"] = tmp["employee_id"].map(normalize_id)
    tmp["shift"] = tmp["shift"].astype(str).str.upper().map(lambda s: s if s in ("D","E","N") else "")
    tmp = tmp[(tmp["employee_id"].astype(str).str.len()>0) & (tmp["shift"].isin(["D","E","N"]))]

    def to_bool(x): return str(x).strip().upper() in ("TRUE","1","YES","Y","T")
    def to_wcap(x):
        try:
            v = int(float(x)); return v if v>=0 else None
        except: return None

    role_map   = {r.employee_id: r.shift for r in tmp.itertuples(index=False)}
    wcap_map   = {r.employee_id: to_wcap(r.weekly_cap) for r in tmp.itertuples(index=False)}
    senior_map = {r.employee_id: to_bool(r.senior) for r in tmp.itertuples(index=False)}
    junior_map = {r.employee_id: to_bool(r.junior) for r in tmp.itertuples(index=False)}
    id_list    = sorted(role_map.keys(), key=lambda s: s)

    # 必休/想休 map（員工端已填）
    def build_date_map(df, typ):
        m = {nid:set() for nid in id_list}
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

        for nid in id_list:
            if sched[nid][d] == "":
                sched[nid][d] = "O"

    return sched, demand, role_map, id_list, senior_map, junior_map

def cross_shift_balance_with_units(year, month, id_list, sched, demand, role_map, senior_map, junior_map, d_avg, e_avg, n_avg):
    nd = days_in_month(year, month)
    def units_of(nid, s): return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
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
                    candidates.sort(key=lambda nid: -units_of(nid, src))
                    moved = False
                    for mv in candidates:
                        def senior_ok_after_move(nid_move, from_s, to_s):
                            if from_s!="D" and to_s!="D": return True
                            d_people = [x for x in id_list if sched[x][d]=="D"]
                            if from_s=="D" and nid_move in d_people: d_people.remove(nid_move)
                            if to_s=="D": d_people.append(nid_move)
                            total = len(d_people)
                            if total==0: return True
                            sen = sum(1 for x in d_people if senior_map.get(x,False))
                            return sen >= ceil(total/3)
                        if not senior_ok_after_move(mv, src, tgt): continue
                        if not (rest_ok(sched[mv].get(d-1,""), tgt) and rest_ok(tgt, sched[mv].get(d+1,""))): continue
                        u_from = units_of(mv, src); u_to = units_of(mv, tgt)
                        sched[mv][d] = tgt
                        actual[src] -= u_from; actual[tgt] += u_to
                        changed = True; moved = True
                        break
                    if moved: break
    return sched

def prefer_off_on_holidays(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {int(r.day):{"D":(int(r.D_min_units),int(r.D_max_units)),
                          "E":(int(r.E_min_units),int(r.E_max_units)),
                          "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid, False), s, d_avg, e_avg, n_avg, 4.5)
    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people); 
        if total == 0: return True
        sen = sum(1 for x in d_people if senior_map.get(x, False))
        return sen >= ceil(total / 3)
    for d in range(1, nd+1):
        if not is_hday(d): continue
        for s in ("D","E","N"):
            mn,_ = demand.get(d,{}).get(s,(0,0))
            def actual_units(): return sum(units_of(nid,s) for nid in id_list if sched[nid][d]==s)
            changed=True
            while changed:
                changed=False
                cur = actual_units()
                if cur <= mn + 1e-9: break
                cands = [nid for nid in id_list if sched[nid][d]==s]
                cands.sort(key=lambda nid: (units_of(nid,s), not junior_map.get(nid,False)))
                moved=False
                for nid in cands:
                    u = units_of(nid,s)
                    if cur - u + 1e-9 < mn: continue
                    if not white_senior_ok_if_remove(d,nid): continue
                    if not (rest_ok(sched[nid].get(d-1,""), "O") and rest_ok("O", sched[nid].get(d+1,""))): continue
                    sched[nid][d] = "O"
                    changed=True; moved=True
                    break
                if not moved: break
    return sched

def enforce_weekly_one_off(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set):
    nd = days_in_month(year, month)
    demand = {int(r.day):{"D":(int(r.D_min_units),int(r.D_max_units)),
                          "E":(int(r.E_min_units),int(r.E_max_units)),
                          "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    def actual_units(d, s): return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)
    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people); 
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
            if any(sched[nid][d] == "O" for d in rng): continue
            candidates = sorted(rng, key=lambda d: (0 if is_hday(d) else 1,))
            for d in candidates:
                cur = sched[nid][d]
                if cur == "O": break
                mn = demand.get(d,{}).get(cur,(0,0))[0]
                u  = units_of(nid, cur)
                if actual_units(d, cur) - u + 1e-9 < mn: continue
                if not white_senior_ok_if_remove(d, nid): continue
                if not (rest_ok(sched[nid].get(d-1,""), "O") and rest_ok("O", sched[nid].get(d+1,""))): continue
                sched[nid][d] = "O"
                break
    return sched

def enforce_min_monthly_off(year, month, sched, demand_df, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, min_off=8, balance=True, holiday_set=None):
    nd = days_in_month(year, month)
    if holiday_set is None: holiday_set = set()
    demand = {int(r.day):{"D":(int(r.D_min_units),int(r.D_max_units)),
                          "E":(int(r.E_min_units),int(r.E_max_units)),
                          "N":(int(r.N_min_units),int(r.N_max_units))}
              for r in demand_df.itertuples(index=False)}
    def is_hday(d): return is_sunday(year, month, d) or (date(year, month, d) in holiday_set)
    def units_of(nid, s): return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    def actual_units(d, s): return sum(units_of(nid, s) for nid in id_list if sched[nid][d] == s)
    def white_senior_ok_if_remove(d, nid):
        if sched[nid][d] != "D": return True
        d_people = [x for x in id_list if sched[x][d] == "D" and x != nid]
        total = len(d_people); 
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
            u  = units_of(nid, s)
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

    changed = True
    while changed:
        changed = False
        needs = sorted([nid for nid in id_list if off_count(nid) < min_off],
                       key=lambda x: off_count(x))
        if not needs: break
        for nid in needs:
            if try_add_one_off(nid):
                changed = True
        if not changed: break

    if not balance: return sched

    def off_span():
        cnts = [off_count(n) for n in id_list]
        return (max(cnts) if cnts else 0) - (min(cnts) if cnts else 0)

    guard = 0
    while off_span() > 1 and guard < 200:
        guard += 1
        nid_low = min(id_list, key=lambda x: off_count(x))
        if not try_add_one_off(nid_low): break
    return sched

# =============== 執行與輸出 ===============
def run_schedule():
    users_df = load_users()
    prefs_df = load_prefs(year, month)

    sched, demand_map, role_map, id_list, senior_map, junior_map = build_initial_schedule(
        year, month, users_df, prefs_df, df_demand, d_avg, e_avg, n_avg
    )

    if allow_cross:
        sched = cross_shift_balance_with_units(year, month, id_list, sched, demand_map, role_map, senior_map, junior_map, d_avg, e_avg, n_avg)

    if prefer_off_holiday:
        # 假日清單
        hol_df = load_holidays(year, month)
        holiday_set = set()
        for r in hol_df.itertuples(index=False):
            raw = getattr(r,"date","")
            if pd.isna(raw) or str(raw).strip()=="": continue
            dt = pd.to_datetime(raw, errors="coerce")
            if pd.isna(dt): continue
            if int(dt.year)==int(year) and int(dt.month)==int(month):
                holiday_set.add(date(int(dt.year), int(dt.month), int(dt.day)))
        sched = prefer_off_on_holidays(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set)
    else:
        holiday_set = set()

    sched = enforce_weekly_one_off(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, holiday_set)
    sched = enforce_min_monthly_off(year, month, sched, df_demand, id_list, role_map, senior_map, junior_map, d_avg, e_avg, n_avg, min_off=min_monthly_off, balance=balance_monthly_off, holiday_set=holiday_set)

    ndays = days_in_month(year, month)
    # 班表表格
    roster_rows = []
    for nid in id_list:
        row = {"id": nid, "shift": role_map[nid], "senior": senior_map.get(nid,False), "junior": junior_map.get(nid,False)}
        row.update({str(d): sched[nid][d] for d in range(1, ndays+1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values(["shift","senior","junior","id"]).reset_index(drop=True)

    # 統計與檢核
    def count_code(nid, code): return sum(1 for d in range(1, ndays+1) if sched[nid][d] == code)
    def is_hday(d): return is_sunday(year, month, d) or (date(year,month,d) in holiday_set)
    holiday_off = {nid: sum(1 for d in range(1, ndays+1) if is_hday(d) and sched[nid][d]=="O") for nid in id_list}
    summary_df = pd.DataFrame([{
        "id": nid, "shift": role_map[nid], "senior": senior_map.get(nid,False), "junior": junior_map.get(nid,False),
        "D天數": count_code(nid,"D"), "E天數": count_code(nid,"E"), "N天數": count_code(nid,"N"), "O天數": count_code(nid,"O"),
        "本月例假日放假數": holiday_off[nid]
    } for nid in id_list]).sort_values(["shift","senior","junior","id"]).reset_index(drop=True)

    def person_units_on(nid, s):  return per_person_units(junior_map.get(nid,False), s, d_avg, e_avg, n_avg, 4.5)
    comp_rows = []
    for d in range(1, ndays+1):
        for s in ORDER:
            mn, mx = demand_map.get(d,{}).get(s,(0,0))
            act = sum(person_units_on(nid,s) for nid in id_list if sched[nid][d]==s)
            status = "🟢 達標" if (act + 1e-9 >= mn and act <= mx + 1e-9) else ("🔴 不足" if act < mn - 1e-9 else "🟡 超編")
            comp_rows.append({"day": d, "shift": s, "min_units": mn, "max_units": mx, "actual_units": round(act,2), "狀態": status})
    compliance_df = pd.DataFrame(comp_rows)

    return roster_df, summary_df, compliance_df

# 產出按鈕（管理端）
if st.button("🚀 產生班表（以員工編號為 id）", type="primary"):
    roster_df, summary_df, compliance_df = run_schedule()

    st.subheader(f"📅 班表（{year}-{month:02d}）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要（含 senior/junior、例假日放假數）")
    st.dataframe(summary_df, use_container_width=True, height=360)

    st.subheader("📊 每日達標（以能力單位）")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    # 下載（單行檔名避免 f-string 斷行）
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"roster_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"summary_{year}-{month:02d}.csv")
    st.download_button("⬇️ 下載 CSV 達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"), file_name=f"compliance_{year}-{month:02d}.csv")
else:
    st.info("提示：你可先更新『人員清單』與『每日加開人力』，員工用個人帳號登入後會把必休/想休寫入本月偏好，最後再由護理長產生班表。")

