import streamlit as st
import pandas as pd
from datetime import datetime
import calendar
from io import BytesIO

st.set_page_config(page_title="Nurse Roster (No-A, No openpyxl)", layout="wide")

st.title("🩺 護理師排班工具（不含 A 班，無需 openpyxl）")
st.caption("支援 ≥20 人、每日人力需求可自訂、員工可填想休日期；輸出 CSV/Excel 皆可，不需 openpyxl。")

# ========= Helper functions =========
def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]

def is_sunday(y: int, m: int, d: int) -> bool:
    return datetime(y, m, d).weekday() == 6

# ========= Sidebar 基本設定 =========
st.sidebar.header("排班設定")
year = st.sidebar.number_input("年份", 2024, 2100, value=2025, step=1)
month = st.sidebar.number_input("月份", 1, 12, value=11, step=1)
days = days_in_month(year, month)

st.sidebar.subheader("每日需求初值（可於主頁修改）")
default_weekday_need = st.sidebar.number_input("週一至週六 D 人數", 0, 100, 4)
default_sunday_need = st.sidebar.number_input("週日 D 人數", 0, 100, 5)

st.sidebar.subheader("限制條件")
max_off = st.sidebar.number_input("每人每月 O 上限", 0, 31, 8)

st.sidebar.subheader("資料上傳（可選）")
nurses_file = st.sidebar.file_uploader("護理師名單 CSV（欄位：id,name）", type=["csv"])
prefs_file = st.sidebar.file_uploader("想休日期 CSV（欄位：nurse_id,date）", type=["csv"])
demand_file = st.sidebar.file_uploader("每日需求 CSV（欄位：day,D_required）", type=["csv"])

# ========= 名單處理 =========
if nurses_file:
    nurses = pd.read_csv(nurses_file)
else:
    nurses = pd.DataFrame({
        "id": list(range(1, 21)),
        "name": [f"{i}號護理師" for i in range(1, 21)]
    })

# ========= 想休日期處理 =========
if prefs_file:
    prefs = pd.read_csv(prefs_file)
else:
    prefs = pd.DataFrame(columns=["nurse_id", "date"])

st.subheader("員工想休設定")
with st.expander("點此展開/編輯想休日期", expanded=False):
    month_prefix = f"{year}-{month:02d}-"
    display_prefs = prefs[prefs["date"].astype(str).str.startswith(month_prefix)].copy()
    edited = st.data_editor(display_prefs, num_rows="dynamic", use_container_width=True)
    if st.button("✅ 套用想休設定"):
        other = prefs[~prefs["date"].astype(str).str.startswith(month_prefix)]
        prefs = pd.concat([other, edited], ignore_index=True)
        st.success("已更新想休資料。")

pref_map = {int(r.id): set() for r in nurses.itertuples(index=False)}
for r in prefs.itertuples(index=False):
    try:
        dt = pd.to_datetime(r.date)
        if dt.year == year and dt.month == month:
            pref_map.setdefault(int(r.nurse_id), set()).add(int(dt.day))
    except Exception:
        pass

# ========= 每日需求 =========
def seed_demand_df(y: int, m: int, wd_need: int, sun_need: int) -> pd.DataFrame:
    rows = []
    for d in range(1, days_in_month(y, m) + 1):
        need = sun_need if is_sunday(y, m, d) else wd_need
        rows.append({"day": d, "D_required": int(need)})
    return pd.DataFrame(rows)

if demand_file:
    df_demand = pd.read_csv(demand_file)
else:
    df_demand = seed_demand_df(year, month, default_weekday_need, default_sunday_need)

st.subheader("每日人力需求（可修改）")
df_demand = st.data_editor(df_demand, use_container_width=True, height=350)
demand_map = {int(r.day): int(r.D_required) for r in df_demand.itertuples(index=False)}

# ========= 排班邏輯（D/O） =========
schedule = {int(r.id): {d: "" for d in range(1, days + 1)} for r in nurses.itertuples(index=False)}

# 想休日先設 O
for nid in schedule.keys():
    for d in pref_map.get(nid, set()):
        if 1 <= d <= days:
            schedule[nid][d] = "O"

assigned_D = {nid: 0 for nid in schedule.keys()}

# 逐日填入 D
for d in range(1, days + 1):
    req = demand_map.get(d, 0)
    candidates = [nid for nid in schedule.keys() if schedule[nid][d] != "O"]
    candidates.sort(key=lambda nid: (assigned_D[nid], nid))
    chosen = candidates[:req]
    for nid in chosen:
        schedule[nid][d] = "D"
        assigned_D[nid] += 1
    for nid in schedule.keys():
        if schedule[nid][d] == "":
            schedule[nid][d] = "O"

# ========= 統計與可行性檢查 =========
def off_count(nid):
    return sum(1 for d in range(1, days + 1) if schedule[nid][d] == "O")

total_required_D = sum(demand_map.values())
n_staff = len(schedule.keys())
avg_off = (n_staff * days - total_required_D) / n_staff
violations = [(nid, off_count(nid)) for nid in schedule.keys() if off_count(nid) > max_off]

# ========= 結果輸出 =========
id2name = {int(r.id): r.name for r in nurses.itertuples(index=False)}
roster_df = pd.DataFrame([
    {"姓名": id2name[nid], **{str(d): schedule[nid][d] for d in range(1, days + 1)}}
    for nid in schedule.keys()
])
summary_df = pd.DataFrame([
    {"姓名": id2name[nid], "D天數": sum(v == "D" for v in schedule[nid].values()),
     "O天數": sum(v == "O" for v in schedule[nid].values())}
    for nid in schedule.keys()
])

st.subheader(f"📅 {year}-{month:02d} 班表")
st.dataframe(roster_df, use_container_width=True, height=500)

st.subheader("統計摘要")
st.dataframe(summary_df, use_container_width=True, height=300)

st.markdown(f"### 📊 可行性檢視")
st.info(f"本月需 D 班次：**{total_required_D}**；人數：**{n_staff}**；理論平均 O/人：**{avg_off:.2f} 天**。")
if violations:
    st.warning(f"有 {len(violations)} 位人員 O 超過上限（> {max_off} 天）。")
else:
    st.success("目前無 O 上限違規。")

# ========= 下載 =========
csv_bytes = roster_df.to_csv(index=False).encode("utf-8-sig")
st.download_button("⬇️ 下載 CSV", data=csv_bytes, file_name=f"roster_{year}-{month:02d}.csv")

excel_sim = BytesIO()
summary_csv = summary_df.to_csv(index=False)
excel_sim.write(summary_csv.encode("utf-8-sig"))
st.download_button("⬇️ 下載 Excel 模擬檔（實際為 CSV 格式）", data=excel_sim.getvalue(),
                   file_name=f"roster_{year}-{month:02d}_summary.xlsx")

st.markdown("""
---
**使用說明**
1. 側邊欄設定年月與人力預設需求（週日與平日）。
2. 可上傳或在頁面直接編輯每日需求、想休日期、護理師名單。
3. 系統自動生成班表並提供下載。
4. 此版本完全不依賴 openpyxl，可在任何環境執行。
""")
