import os
import requests
import pandas as pd
from dotenv import load_dotenv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TW_TZ = ZoneInfo("Asia/Taipei")

def get_taiwan_today():
    return datetime.now(TW_TZ).date()

def settle_result(row):
    try:
        away_score = row["客隊得分"]
        home_score = row["主隊得分"]
        line = float(row["盤口"])

        if row["玩法"] == "大小分":
            total = away_score + home_score

            if row["方向"] == "大分":
                if total > line:
                    return "過盤"
                elif total < line:
                    return "沒過"
                else:
                    return "走水"

            elif row["方向"] == "小分":
                if total < line:
                    return "過盤"
                elif total > line:
                    return "沒過"
                else:
                    return "走水"

        elif row["玩法"] == "讓分":
            if row["方向"] == "主隊":
                diff = (home_score + line) - away_score
            elif row["方向"] == "客隊":
                diff = (away_score + line) - home_score
            else:
                return "無法判定"

            if diff > 0:
                return "過盤"
            elif diff < 0:
                return "沒過"
            else:
                return "走水"

        return "無法判定"

    except:
        return "無法判定"


print("開始執行昨日驗證")

load_dotenv()
odds_api_key = os.getenv("ODDS_API_KEY")

yesterday = get_taiwan_today() - timedelta(days=1)
yesterday_str = str(yesterday)

print("台灣昨天：", yesterday_str)

# ===== 讀取昨天預測檔 =====
pred_file = f"nba_final_picks_{yesterday_str}.csv"

try:
    pred_df = pd.read_csv(pred_file)
    print(f"已讀取預測檔：{pred_file}")
except FileNotFoundError:
    print(f"找不到預測檔：{pred_file}")
    print("代表昨天可能還沒先存日期版 CSV。")

    existing_files = sorted([
        f for f in os.listdir(".")
        if f.startswith("nba_final_picks_") and f.endswith(".csv")
    ])

    print("\n目前已有的預測檔：")
    if existing_files:
        for f in existing_files:
            print("-", f)
    else:
        print("目前沒有任何日期版預測檔")

    raise SystemExit

# ===== 抓 NBA scores =====
scores_url = "https://api.the-odds-api.com/v4/sports/basketball_nba/scores"
scores_params = {
    "apiKey": odds_api_key,
    "daysFrom": 3,
    "dateFormat": "iso"
}

response = requests.get(scores_url, params=scores_params)

if response.status_code != 200:
    print("抓取比分失敗")
    print("狀態碼：", response.status_code)
    print(response.text)
    raise SystemExit

games = response.json()

result_rows = []

for game in games:
    if not game.get("completed", False):
        continue

    home_team = game.get("home_team")
    away_team = game.get("away_team")
    commence_time = game.get("commence_time")
    scores = game.get("scores")

    if not home_team or not away_team or not commence_time or not scores:
        continue

    game_time_tw = pd.to_datetime(commence_time, utc=True).tz_convert("Asia/Taipei")
    game_date_tw = game_time_tw.date()

    if game_date_tw != yesterday:
        continue

    home_score = None
    away_score = None

    for s in scores:
        if s["name"] == home_team:
            home_score = int(s["score"])
        elif s["name"] == away_team:
            away_score = int(s["score"])

    if home_score is None or away_score is None:
        continue

    result_rows.append({
        "客隊": away_team,
        "主隊": home_team,
        "客隊得分": away_score,
        "主隊得分": home_score
    })

result_df = pd.DataFrame(result_rows)

print("\n昨天實際賽果：")
if result_df.empty:
    print("昨天沒有抓到已完賽比分")
    raise SystemExit
else:
    print(result_df)

# ===== 合併預測與賽果 =====
merged_df = pred_df.merge(result_df, on=["客隊", "主隊"], how="left")

merged_df["驗證結果"] = merged_df.apply(settle_result, axis=1)

# ===== 輸出驗證結果 =====
verify_file = f"nba_yesterday_result_{yesterday_str}.csv"
merged_df.to_csv(verify_file, index=False, encoding="utf-8-sig")

print("\n📊 昨日戰績")
print(merged_df[["客隊", "主隊", "推薦", "玩法", "方向", "盤口", "客隊得分", "主隊得分", "驗證結果"]])

print("\n統計：")
print(merged_df["驗證結果"].value_counts(dropna=False))

total_count = len(merged_df)
win_count = len(merged_df[merged_df["驗證結果"] == "過盤"])

if total_count > 0:
    win_rate = round(win_count / total_count * 100, 2)
    print(f"勝率：{win_rate}%")

print(f"\n已輸出：{verify_file}")