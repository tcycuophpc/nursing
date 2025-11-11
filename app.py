import streamlit as st
import pandas as pd
from datetime import datetime
import calendar

st.set_page_config(page_title="Nurse Roster (ID + MustWork/MustOff)", layout="wide")

st.title("🩺 護理師排班工具（ID｜不含 A 班｜必上/必休｜每日達標檢視）")
st.caption("D=出勤、O=休假；依你輸入/上傳的 ID 自動辨識人數，支援必上/必休設定與每日人力達標檢視。")

# ========= Helpers =========
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6

def seed_demand_df(y, m, wd_need, sun_need):
    rows = []
    for d in range(1, days_in_month(y, m) + 1):
        need = sun_need if is_sunday(y, m, d) else wd_need
        rows.append({"day": d, "D_required": int(need)})
    return pd.DataFrame(rows, columns=["day", "D_required"])

def parse_id_list(text: str):
    if not text:
        return []
    tokens = [t.strip() for t in text.replace("\n", " ").replace(",", " ").split(" ") if t.strip()]
    ids = []
    for t in tokens:
        try:
            ids.append(int(t))
        except:
            pass
    return sorted(list(set(ids)))

def build_schedule(year, month, id_list, prefs_df, demand_df, must_work_df, must_off_df, max_off):
    days = days_in_month(year, month)

    # Preferences map
    pref_map = {nid: set() for nid in id_list}
    for r in prefs_df.itertuples(index=False):
        try:
            dt = pd.to_datetime(r.date); nid = int(r.nurse_id)
            if nid in pref_map and dt.year == year and dt.month == month:
                pref_map[nid].add(int(dt.day))
        except: 
            pass

    # Must work / must off maps
    mustW = {nid: set() for nid in id_list}
    mustO = {nid: set() for nid in id_list}
    for r in must_work_df.itertuples(index=False):
        try:
            dt = pd.to_datetime(r.date); nid = int(r.nurse_id)
            if nid in mustW and dt.year == year and dt.month == month:
                mustW[nid].add(int(dt.day))
        except: 
            pass
    for r in must_off_df.itertuples(index=False):
        try:
            dt = pd.to_datetime(r.date); nid = int(r.nurse_id)
            if nid in mustO and dt.year == year and dt.month == month:
                mustO[nid].add(int(dt.day))
        except: 
            pass

    demand_map = {int(r.day): int(r.D_required) for r in demand_df.itertuples(index=False)}

    # Init schedule
    schedule = {nid: {d: "" for d in range(1, days + 1)} for nid in id_list}

    # Apply must_off first
    for nid in id_list:
        for d in mustO[nid]:
            if 1 <= d <= days:
                schedule[nid][d] = "O"

    # Apply preferences (soft O)
    for nid in id_list:
        for d in pref_map[nid]:
            if 1 <= d <= days and schedule[nid][d] == "":
                schedule[nid][d] = "O"

    # Per-day assignment (respect must_work as hard D)
    assigned_D = {nid: 0 for nid in id_list}
    daily_info = []  # for compliance table
    for d in range(1, days + 1):
        req = max(0, int(demand_map.get(d, 0)))
        # 1) must-work
        mw_today = [nid for nid in id_list if d in mustW[nid]]
        for nid in mw_today:
            schedule[nid][d] = "D"
        for nid in mw_today:
            assigned_D[nid] += 1
        cur = len(mw_today)

        # 2) fill remaining fairly
        if cur < req:
            candidates = [nid for nid in id_list if schedule[nid][d] not in ("O", "D")]
            candidates.sort(key=lambda k: (assigned_D[k], k))
            need_more = req - cur
            chosen = candidates[:need_more]
            for nid in chosen:
                schedule[nid][d] = "D"
                assigned_D[nid] += 1

        # 3) blanks -> O
        for nid in id_list:
            if schedule[nid][d] == "":
                schedule[nid][d] = "O"

        # compliance
        actual = sum(1 for nid in id_list if schedule[nid][d] == "D")
        delta = actual - req
        status = "🟢 達標" if actual == req else ("🟡 超編(+{})".format(delta) if delta > 0 else "🔴 不足({})".format(delta))
        daily_info.append({"day": d, "D_required": req, "D_actual": actual, "差額": delta, "狀態": status})

    # Feasibility (O cap)
    def off_count(nid):
        return sum(1 for k in range(1, days + 1) if schedule[nid][k] == "O")

    total_required_D = sum(max(0, int(demand_map.get(d, 0))) for d in range(1, days + 1))
    n_staff = len(id_list)
    avg_off = (n_staff * days - total_required_D) / n_staff if n_staff else 0
    violations = [(nid, off_count(nid)) for nid in id_list if off_count(nid) > max_off]

    # DataFrames
    roster_rows = []
    for nid in id_list:
        row = {"id": nid}
        row.update({str(d): schedule[nid][d] for d in range(1, days + 1)})
        roster_rows.append(row)
    roster_df = pd.DataFrame(roster_rows).sort_values("id").reset_index(drop=True)

    summary_rows = []
    for nid in id_list:
        summary_rows.append({
            "id": nid,
            "D天數": sum(1 for d in range(1, days + 1) if schedule[nid][d] == "D"),
            "O天數": sum(1 for d in range(1, days + 1) if schedule[nid][d] == "O"),
        })
    summary_df = pd.DataFrame(summary_rows).sort_values("id").reset_index(drop=True)

    compliance_df = pd.DataFrame(daily_info)

    return roster_df, summary_df, compliance_df, total_required_D, n_staff, avg_off, violations

# ========= Sidebar =========
with st.sidebar:
    st.header("排班設定")
    year = st.number_input("年份", 2024, 2100, value=2025, step=1)
    month = st.number_input("月份", 1, 12, value=11, step=1)
    days = days_in_month(year, month)

    st.subheader("每日需求預填（之後可在表格調整）")
    default_wd = st.number_input("週一至週六 D 人數", 0, 200, 4)
    default_sun = st.number_input("週日 D 人數", 0, 200, 5)

    st.subheader("限制條件（檢視用）")
    max_off = st.number_input("每人每月 O 上限", 0, 31, 8)

    st.subheader("資料上傳（可選）")
    nurses_file = st.file_uploader("護理師名單 CSV（欄位：id,name，可留空）", type=["csv"])
    prefs_file = st.file_uploader("想休假 CSV（欄位：nurse_id,date）", type=["csv"])
    demand_file = st.file_uploader("每日需求 CSV（欄位：day,D_required 或 date,D_required）", type=["csv"])
    must_work_file = st.file_uploader("必上 CSV（欄位：nurse_id,date）", type=["csv"])
    must_off_file  = st.file_uploader("必休 CSV（欄位：nurse_id,date）", type=["csv"])

# ========= ID 來源設定 =========
st.subheader("🆔 護理師 ID 清單（可直接貼上）")
id_text = st.text_area("輸入 ID（逗號/空白/換行分隔；例：101 102 103 或 101,102,103）", value="", height=90)

# 名單、偏好、必上/必休
if nurses_file:
    nurses_df = pd.read_csv(nurses_file)
    uploaded_ids = [int(x) for x in pd.Series(nurses_df["id"]).dropna().unique().tolist()]
else:
    nurses_df = pd.DataFrame(columns=["id", "name"])
    uploaded_ids = []

if prefs_file:
    prefs_df = pd.read_csv(prefs_file)
else:
    prefs_df = pd.DataFrame(columns=["nurse_id", "date"])

if must_work_file:
    must_work_df = pd.read_csv(must_work_file)
else:
    must_work_df = pd.DataFrame(columns=["nurse_id", "date"])

if must_off_file:
    must_off_df = pd.read_csv(must_off_file)
else:
    must_off_df = pd.DataFrame(columns=["nurse_id", "date"])

# 需求
if demand_file:
    raw = pd.read_csv(demand_file)
    if "day" in raw.columns and "D_required" in raw.columns:
        demand_df = raw[["day", "D_required"]].copy()
    elif "date" in raw.columns and "D_required" in raw.columns:
        tmp = raw.copy(); tmp["day"] = pd.to_datetime(tmp["date"]).dt.day
        demand_df = tmp[["day", "D_required"]].copy()
    else:
        st.error("每日需求 CSV 欄位需為 'day,D_required' 或 'date,D_required'")
        st.stop()
else:
    demand_df = seed_demand_df(year, month, default_wd, default_sun)

# 整合 ID：手動 + 名單 + 想休 + 必上 + 必休
ids_manual = parse_id_list(id_text)
ids_from_prefs = [int(x) for x in pd.Series(prefs_df["nurse_id"]).dropna().unique().tolist()] if "nurse_id" in prefs_df.columns else []
ids_from_mw = [int(x) for x in pd.Series(must_work_df["nurse_id"]).dropna().unique().tolist()] if "nurse_id" in must_work_df.columns else []
ids_from_mo = [int(x) for x in pd.Series(must_off_df["nurse_id"]).dropna().unique().tolist()] if "nurse_id" in must_off_df.columns else []

id_list = sorted(list(set(ids_manual) | set(uploaded_ids) | set(ids_from_prefs) | set(ids_from_mw) | set(ids_from_mo)))
if len(id_list) == 0:
    id_list = list(range(1, 21))  # fallback 範例

st.info(f"將以 **{len(id_list)} 位**護理師進行排班。ID：{', '.join(map(str, id_list[:50]))}{' ...' if len(id_list)>50 else ''}")

# ========= 可編輯表格：每日需求 / 想休 / 必上 / 必休 =========
st.subheader("📋 每日人力需求（可編輯）")
demand_df = demand_df.sort_values("day").reset_index(drop=True)
demand_df["day"] = demand_df["day"].astype(int)
demand_df["D_required"] = demand_df["D_required"].astype(int)
demand_df = st.data_editor(
    demand_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "day": st.column_config.NumberColumn("day", min_value=1, max_value=days, step=1),
        "D_required": st.column_config.NumberColumn("D_required", min_value=0, max_value=200, step=1),
    },
    height=320
)

st.subheader("📝 員工想休（本月）")
month_prefix = f"{year}-{month:02d}-"
show_prefs = prefs_df[prefs_df["date"].astype(str).str.startswith(month_prefix)].copy()
prefs_edit = st.data_editor(show_prefs, num_rows="dynamic", use_container_width=True, height=260, key="prefs_edit")

st.subheader("✅ 必上（硬性出勤）")
mw_show = must_work_df[must_work_df["date"].astype(str).str.startswith(month_prefix)] if "date" in must_work_df.columns else must_work_df
mw_edit = st.data_editor(mw_show, num_rows="dynamic", use_container_width=True, height=200, key="mw_edit")

st.subheader("⛔ 必休（硬性休假）")
mo_show = must_off_df[must_off_df["date"].astype(str).str.startswith(month_prefix)] if "date" in must_off_df.columns else must_off_df
mo_edit = st.data_editor(mo_show, num_rows="dynamic", use_container_width=True, height=200, key="mo_edit")

# ========= 產生班表 =========
if st.button("🚀 產生班表"):
    roster_df, summary_df, compliance_df, total_required_D, n_staff, avg_off, violations = build_schedule(
        year, month, id_list, prefs_edit, demand_df, mw_edit, mo_edit, max_off
    )

    st.subheader(f"📅 {year}-{month:02d} 班表（ID）")
    st.dataframe(roster_df, use_container_width=True, height=520)

    st.subheader("統計摘要")
    st.dataframe(summary_df, use_container_width=True, height=320)

    st.subheader("📊 每日人力達標檢視")
    st.dataframe(compliance_df, use_container_width=True, height=360)

    st.markdown("### 可行性檢視")
    st.info(f"本月需 D 班次：**{total_required_D}**；參與人數：**{n_staff}**；理論平均 O/人：**{avg_off:.2f} 天**。")
    if violations:
        st.warning(f"有 {len(violations)} 位 O 超過上限（> {max_off} 天）。")
    else:
        st.success("目前無 O 上限違規。")

    # Downloads
    st.download_button("⬇️ 下載 CSV 班表", data=roster_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"roster_{year}-{month:02d}_by_id.csv")
    st.download_button("⬇️ 下載 CSV 統計", data=summary_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"summary_{year}-{month:02d}_by_id.csv")
    st.download_button("⬇️ 下載 CSV 每日達標", data=compliance_df.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"compliance_{year}-{month:02d}.csv")
else:
    st.info("請確認：ID、每日需求、想休/必上/必休 → 然後按「產生班表」。")

st.markdown("""
---
**說明**
- 系統整合 ID 來源：手動輸入、名單檔、想休檔、必上/必休檔（聯集）。  
- 「必上」會先填 D，再補足當日需求；「必休」會先鎖 O。  
- 「每日人力達標檢視」：🟢達標、🟡超編、🔴不足。  
- 僅 D/O，無 A 班與 E/N 細班；若人力遠大於需求，理論平均 O 會高，可能超過你的 O 上限。
""")
