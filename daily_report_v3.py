# daily_report_v3.py
# NBA 預測分析專案
# 功能：
# 1. 明日 NBA 賽程
# 2. 大小分模型
# 3. 讓分模型
# 4. 大小分 / 讓分候選排行
# 5. 市場偏向
# 6. 昨日驗證
# 7. 近 7 筆 / 近 30 筆勝率
# 8. HTML 報告

import os
import json
import math
import time
import requests
import pandas as pd
from pathlib import Path
HISTORY_CSV = Path("nba_pick_history.csv")
from datetime import datetime, timedelta, timezone

# =========================
# 信心分數設定
# =========================

MAIN_PICK_MIN_CONFIDENCE = 72  # 主推最低門檻：低於 72% 就不硬推主推


def confidence_to_percent(score):
    """
    把舊版 /50 信心分數轉成 0~100%
    例如：
    49 -> 98%
    "49/50" -> 98%
    "86%" -> 86%
    """
    if score is None:
        return 0

    text = str(score).strip()

    if "/" in text:
        left, right = text.split("/", 1)
        try:
            return round(float(left) / float(right) * 100)
        except Exception:
            return 0

    text = text.replace("%", "")

    try:
        number = float(text)
    except Exception:
        return 0

    if number <= 50:
        return round(number / 50 * 100)

    return round(number)


def format_confidence(score):
    return f"{confidence_to_percent(score)}%"


def is_main_pick_confident(score):
    return confidence_to_percent(score) >= MAIN_PICK_MIN_CONFIDENCE


# =========================
# 基本設定
# =========================
TEAM_SHORT_NAME = {
    "Boston Celtics": "塞爾提克",
    "Philadelphia 76ers": "76人",
    "New York Knicks": "尼克",
    "Atlanta Hawks": "老鷹",
    "San Antonio Spurs": "馬刺",
    "Portland Trail Blazers": "拓荒者",
    "Los Angeles Lakers": "湖人",
    "Golden State Warriors": "勇士",
    "Phoenix Suns": "太陽",
    "Denver Nuggets": "金塊",
    "Milwaukee Bucks": "公鹿",
    "Dallas Mavericks": "獨行俠",
    "Miami Heat": "熱火",
    "Chicago Bulls": "公牛",
    "Cleveland Cavaliers": "騎士",
    "Toronto Raptors": "暴龍",
    "Houston Rockets": "火箭",
    "Utah Jazz": "爵士",
    "Detroit Pistons": "活塞",
    "Washington Wizards": "巫師",
    "Charlotte Hornets": "黃蜂",
    "Orlando Magic": "魔術",
    "Indiana Pacers": "溜馬",
    "Sacramento Kings": "國王",
    "New Orleans Pelicans": "鵜鶘",
    "Oklahoma City Thunder": "雷霆",
    "Minnesota Timberwolves": "灰狼",
    "Brooklyn Nets": "籃網",
    "Los Angeles Clippers": "快艇",
}
TAIWAN_TZ = timezone(timedelta(hours=8))
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"

DATA_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

PREDICTION_LOG_CSV = DATA_DIR / "prediction_log_v3.csv"
ESPN_DAY_CACHE_CSV = DATA_DIR / "espn_day_cache.csv"
RECENT_GAMES_CACHE_CSV = DATA_DIR / "recent_games_cache.csv"
LINE_SNAPSHOT_CSV = DATA_DIR / "line_snapshots.csv"
INJURY_ADJUSTMENTS_CSV = DATA_DIR / "injury_adjustments.csv"
AUTO_INJURY_ADJUSTMENTS_CSV = DATA_DIR / "auto_injury_adjustments.csv"
REPORT_HTML = REPORT_DIR / "daily_report_v3.html"

TODAY_TW = datetime.now(TAIWAN_TZ).date()
TOMORROW_TW = TODAY_TW + timedelta(days=1)
YESTERDAY_TW = TODAY_TW - timedelta(days=1)

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
RECENT_GAMES_CACHE = None
ESPN_DAY_CACHE = {}
# 可選：如果你有 The Odds API key，可以放在 Mac 環境變數
# export THE_ODDS_API_KEY="你的key"
THE_ODDS_API_KEY = os.getenv("THE_ODDS_API_KEY", "").strip()
THE_ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"

NBA_ABBR_FIX = {
    "NY": "NYK",
    "SA": "SAS",
    "GS": "GSW",
    "NO": "NOP",
    "UTAH": "UTA",
}


# =========================
# 小工具
# =========================
def edge_level(edge):
    edge = abs(edge)

    if edge >= 8:
        return "🔥 強烈推薦"
    elif edge >= 5:
        return "✅ 推薦"
    elif edge >= 3:
        return "⚠️ 可考慮"
    else:
        return "❌ 不建議"

def short_name(team):
    return TEAM_SHORT_NAME.get(team, team)

def pick_short_name(pick):
    pick = str(pick)
    for en_name, zh_name in TEAM_SHORT_NAME.items():
        pick = pick.replace(en_name, zh_name)
    return pick   

def tw_now_text():
    return datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if str(value).strip() in ["", "—", "---", "None", "nan", "NaN"]:
            return default

        result = float(value)

        if pd.isna(result):
            return default

        return result

    except Exception:
        return default


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def espn_date(date_obj):
    # ESPN scoreboard 用 YYYYMMDD
    return date_obj.strftime("%Y%m%d")


def request_json(url, params=None, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }
    response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def parse_espn_datetime_to_taiwan(dt_text):
    # ESPN 通常是 UTC ISO，例如 2026-04-26T01:00Z
    if not dt_text:
        return None
    try:
        dt = datetime.fromisoformat(dt_text.replace("Z", "+00:00"))
        return dt.astimezone(TAIWAN_TZ)
    except Exception:
        return None


def normalize_team_name(name):
    if not name:
        return ""
    return str(name).strip()


def team_key(name):
    # 用來比對 odds API 的隊名，避免大小寫與空白問題
    return normalize_team_name(name).lower().replace(".", "").replace(" ", "")
def build_recent_games_cache(today_tw, lookback_days=60):
    """
    一次抓近 lookback_days 天的所有已完賽 NBA 比賽。
    避免每支球隊都重複打 ESPN API。
    """
    rows = []

    today_date = pd.to_datetime(str(today_tw)).date()

    for i in range(1, lookback_days + 1):
        check_date = today_date - timedelta(days=i)

        try:
            games_df = fetch_espn_games_by_taiwan_date(check_date)
        except Exception as e:
            print(f"近況資料抓取失敗：{check_date}，原因：{e}")
            continue

        if games_df is None or games_df.empty:
            continue

        for _, row in games_df.iterrows():
            completed = row.get("completed", False)

            away_score = safe_int(row.get("away_score"), None)
            home_score = safe_int(row.get("home_score"), None)

            if not completed:
                continue

            if away_score is None or home_score is None:
                continue

            if away_score == 0 and home_score == 0:
                continue

            rows.append({
                "台灣日期": str(check_date),
                "客隊": str(row.get("客隊", "")).strip(),
                "主隊": str(row.get("主隊", "")).strip(),
                "away_score": away_score,
                "home_score": home_score,
            })

    cache_df = pd.DataFrame(rows)

    if cache_df.empty:
        return cache_df

    cache_df = cache_df.drop_duplicates(
        subset=["台灣日期", "客隊", "主隊"]
    ).sort_values("台灣日期", ascending=False).reset_index(drop=True)

    return cache_df

def get_recent_games_cache(today_tw):
    global RECENT_GAMES_CACHE

    if RECENT_GAMES_CACHE is not None:
        return RECENT_GAMES_CACHE

    # ===== 先讀永久 cache =====
    cache_df = load_recent_games_cache()

    # ✅ 穩定優先：只要有舊快取，就先使用，避免 ESPN 卡住導致整份報告不更新
    if not cache_df.empty:
        cache_time = datetime.fromtimestamp(
            RECENT_GAMES_CACHE_CSV.stat().st_mtime
        ).date()

        today_date = datetime.now(TAIWAN_TZ).date()

        if cache_time == today_date:
            print("已載入今日 recent_games_cache.csv")
        else:
            print(f"已載入舊 recent_games_cache.csv（{cache_time}），先用舊快取避免 ESPN 卡住")

        RECENT_GAMES_CACHE = cache_df
        print("近況資料筆數：", len(RECENT_GAMES_CACHE))
        return RECENT_GAMES_CACHE

    # ===== 完全沒有 cache，才重新建立 =====
    print("正在建立近10場共用資料快取...")
    RECENT_GAMES_CACHE = build_recent_games_cache(today_tw)

    print("近況資料筆數：", len(RECENT_GAMES_CACHE))

    try:
        RECENT_GAMES_CACHE.to_csv(
            RECENT_GAMES_CACHE_CSV,
            index=False,
            encoding="utf-8-sig"
        )
        print("已儲存 recent_games_cache.csv")
    except Exception as e:
        print("儲存 recent_games_cache.csv 失敗：", e)

    return RECENT_GAMES_CACHE
def calc_recent_10_team_form(team_name, today_tw):
    """
    計算單一球隊近10場狀態：
    使用共用 cache，不再每支球隊重複打 ESPN API。
    """
    recent_games = get_recent_games_cache(today_tw)

    if recent_games is None or recent_games.empty:
        return {
            "games": 0,
            "win_rate": 0.5,
            "avg_score": 0,
            "avg_allowed": 0,
            "avg_margin": 0,
            "form_score": 0,
            "summary": "近10場資料不足",
        }

    rows = []

    for _, row in recent_games.iterrows():
        away_team = str(row.get("客隊", "")).strip()
        home_team = str(row.get("主隊", "")).strip()

        away_score = safe_int(row.get("away_score"), None)
        home_score = safe_int(row.get("home_score"), None)

        if away_score is None or home_score is None:
            continue

        if team_name == away_team:
            team_score = away_score
            opp_score = home_score
        elif team_name == home_team:
            team_score = home_score
            opp_score = away_score
        else:
            continue

        rows.append({
            "team_score": team_score,
            "opp_score": opp_score,
            "win": 1 if team_score > opp_score else 0,
            "margin": team_score - opp_score,
        })

        if len(rows) >= 10:
            break

    if len(rows) == 0:
        return {
            "games": 0,
            "win_rate": 0.5,
            "avg_score": 0,
            "avg_allowed": 0,
            "avg_margin": 0,
            "form_score": 0,
            "summary": "近10場資料不足",
        }

    df = pd.DataFrame(rows)

    games = len(df)
    wins = int(df["win"].sum())
    win_rate = df["win"].mean()
    avg_score = df["team_score"].mean()
    avg_allowed = df["opp_score"].mean()
    avg_margin = df["margin"].mean()

    form_score = 0

    form_score += (win_rate - 0.5) * 8
    form_score += avg_margin * 0.35

    if avg_score >= 120:
        form_score += 1.5
    elif avg_score >= 115:
        form_score += 0.8
    elif avg_score <= 105:
        form_score -= 1.5
    elif avg_score <= 110:
        form_score -= 0.8

    if avg_allowed <= 108:
        form_score += 1.5
    elif avg_allowed <= 112:
        form_score += 0.8
    elif avg_allowed >= 120:
        form_score -= 1.5
    elif avg_allowed >= 116:
        form_score -= 0.8

    form_score = max(-6, min(6, form_score))

    return {
        "games": games,
        "win_rate": win_rate,
        "avg_score": avg_score,
        "avg_allowed": avg_allowed,
        "avg_margin": avg_margin,
        "form_score": form_score,
        "summary": f"近{games}場 {wins}勝{games - wins}敗｜場均得分 {avg_score:.1f}｜場均失分 {avg_allowed:.1f}｜淨勝分 {avg_margin:+.1f}",
    }


def calc_home_away_form_edge(home_team, away_team, today_tw):
    """
    主場隊最近主場表現
    客場隊最近客場表現

    回傳：
    {
        "home_edge": x
    }
    """

    recent_games = get_recent_games_cache(today_tw)

    if recent_games is None or recent_games.empty:
        return {"home_edge": 0}

    # 主隊最近主場
    home_games = recent_games[
        recent_games["主隊"].astype(str) == str(home_team)
    ].head(10)

    # 客隊最近客場
    away_games = recent_games[
        recent_games["客隊"].astype(str) == str(away_team)
    ].head(10)

    home_margin = 0
    away_margin = 0

    if not home_games.empty:
        home_margin = (
            home_games["home_score"] -
            home_games["away_score"]
        ).mean()

    if not away_games.empty:
        away_margin = (
            away_games["away_score"] -
            away_games["home_score"]
        ).mean()

    edge = safe_float(home_margin, 0) - safe_float(away_margin, 0)

    edge = max(-5, min(5, edge))

    return {
        "home_edge": round(edge, 2)
    }


def calc_recent_10_matchup_adjustment(away_team, home_team, today_tw):
    away_form = calc_recent_10_team_form(away_team, today_tw)
    home_form = calc_recent_10_team_form(home_team, today_tw)

    home_form_edge = home_form["form_score"] - away_form["form_score"]

    return {
        "away_form": away_form,
        "home_form": home_form,
        "home_form_edge": home_form_edge,
        "summary": f"{away_team}：{away_form['summary']}｜{home_team}：{home_form['summary']}",
    }


# =========================
# 抓 ESPN 賽程 / 賽果
# =========================
def load_espn_day_cache():
    if not ESPN_DAY_CACHE_CSV.exists():
        return pd.DataFrame()

    try:
        df = pd.read_csv(ESPN_DAY_CACHE_CSV)

        if df.empty:
            return pd.DataFrame()

        return df

    except Exception:
        return pd.DataFrame()

def load_recent_games_cache():
    if not RECENT_GAMES_CACHE_CSV.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(RECENT_GAMES_CACHE_CSV)
    except Exception as e:
        print("讀取 recent_games_cache.csv 失敗：", e)
        return pd.DataFrame()

def save_espn_day_cache(df):
    if df is None or df.empty:
        return

    try:
        df.to_csv(
            ESPN_DAY_CACHE_CSV,
            index=False,
            encoding="utf-8-sig"
        )
    except Exception as e:
        print("儲存 ESPN cache 失敗：", e)
def fetch_espn_games_by_taiwan_date(target_tw_date):
    """抓指定台灣日期的 NBA 比賽。

    注意：ESPN API 是用美國日期查詢，所以為了避免跨時區漏抓，
    會抓 target_tw_date 前後一天，再用台灣開賽日期過濾。
    """
    global ESPN_DAY_CACHE

    cache_key = str(target_tw_date)

    # 1. 先檢查記憶體 cache
    cache_key = str(target_tw_date)

    if cache_key in ESPN_DAY_CACHE:
        return ESPN_DAY_CACHE[cache_key].copy()

    cache_df = load_espn_day_cache()

    if not cache_df.empty and "台灣日期" in cache_df.columns:

        cached_rows = cache_df[
            cache_df["台灣日期"].astype(str) == cache_key
        ].copy()

        if not cached_rows.empty:

            # 今天 / 昨天可能還在更新比分
            # 不要直接使用舊 cache
            if target_tw_date not in [TODAY_TW, YESTERDAY_TW]:

                ESPN_DAY_CACHE[cache_key] = cached_rows.copy()

                return cached_rows

    all_games = []
    check_dates = [
        target_tw_date - timedelta(days=1),
        target_tw_date,
        target_tw_date + timedelta(days=1),
    ]

    for d in check_dates:
        params = {"dates": espn_date(d)}
        try:
            data = request_json(ESPN_SCOREBOARD_URL, params=params)
        except Exception as e:
            print(f"ESPN 抓取失敗：{d}，原因：{e}")
            continue

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            home = None
            away = None

            for c in competitors:
                if c.get("homeAway") == "home":
                    home = c
                elif c.get("homeAway") == "away":
                    away = c

            if not home or not away:
                continue

            start_tw = parse_espn_datetime_to_taiwan(event.get("date"))

            if not start_tw:
                continue

            if start_tw.date() != target_tw_date:
                continue

            status_type = competition.get("status", {}).get("type", {})
            completed = bool(status_type.get("completed", False))
            status_name = status_type.get("description", "")
            state = str(status_type.get("state", "")).lower()

            if completed:
                live_status = "🔴 FINAL"
            elif state == "in":
                live_status = "🟢 LIVE"
            else:
                live_status = "⏳ Scheduled"
            notes = competition.get("notes", [])
            series_info = ""

            if notes:
                note_texts = []
                for note in notes:
                    headline = str(note.get("headline", "")).strip()
                    if headline:
                        note_texts.append(headline)

                series_info = "｜".join(note_texts)

            home_team = home.get("team", {})
            away_team = away.get("team", {})

            home_score = safe_int(home.get("score"), None)
            away_score = safe_int(away.get("score"), None)

            espn_odds = competition.get("odds", [])
            spread = None
            total = None
            odds_details = ""

            if espn_odds:
                first_odds = espn_odds[0]
                spread = safe_float(first_odds.get("spread"), None)
                total = safe_float(first_odds.get("overUnder"), None)
                odds_details = first_odds.get("details", "") or ""

            all_games.append({
                "game_id": event.get("id"),
                "台灣開賽時間": start_tw.strftime("%Y-%m-%d %H:%M"),
                "台灣日期": str(start_tw.date()),
                "客隊": normalize_team_name(away_team.get("displayName")),
                "主隊": normalize_team_name(home_team.get("displayName")),
                "客隊縮寫": away_team.get("abbreviation", ""),
                "主隊縮寫": home_team.get("abbreviation", ""),
                "away_score": away_score,
                "home_score": home_score,
                "completed": completed,
                "status": status_name,
                "live_status": live_status,
                "series_info": series_info,
                "espn_spread": spread,
                "espn_total": total,
                "espn_odds_details": odds_details,
            })

    df = pd.DataFrame(all_games)

    ESPN_DAY_CACHE[cache_key] = df.copy()

    # 4. 有資料才寫入永久 CSV cache
    if not df.empty:
        existing_cache = load_espn_day_cache()

        combined_cache = pd.concat(
            [existing_cache, df],
            ignore_index=True
        )

        combined_cache = combined_cache.drop_duplicates(
            subset=["game_id"],
            keep="last"
        )

        save_espn_day_cache(combined_cache)

    return df


# =========================
# 抓盤口：The Odds API optional
# =========================

def fetch_the_odds_api_lines():
    """如果有 THE_ODDS_API_KEY，抓 spreads / totals。
    沒有 key 時回傳空表，不會讓程式壞掉。
    """
    if not THE_ODDS_API_KEY:
        return pd.DataFrame()

    params = {
        "apiKey": THE_ODDS_API_KEY,
        "regions": "us",
        "markets": "spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    try:
        data = request_json(THE_ODDS_API_URL, params=params)
    except Exception as e:
        print("The Odds API 抓取失敗：", e)
        return pd.DataFrame()

    rows = []
    for game in data:
        commence_tw = parse_espn_datetime_to_taiwan(game.get("commence_time"))
        if not commence_tw:
            continue

        home_team = normalize_team_name(game.get("home_team"))
        away_team = normalize_team_name(game.get("away_team"))

        spreads = []
        totals = []

        for bookmaker in game.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "spreads":
                    for outcome in market.get("outcomes", []):
                        if team_key(outcome.get("name")) == team_key(home_team):
                            point = safe_float(outcome.get("point"), None)
                            if point is not None:
                                spreads.append(point)
                elif market.get("key") == "totals":
                    for outcome in market.get("outcomes", []):
                        point = safe_float(outcome.get("point"), None)
                        if point is not None:
                            totals.append(point)

        home_spread = sum(spreads) / len(spreads) if spreads else None
        total = sum(totals) / len(totals) if totals else None

        rows.append({
            "odds_台灣開賽時間": commence_tw.strftime("%Y-%m-%d %H:%M"),
            "odds_台灣日期": str(commence_tw.date()),
            "客隊": away_team,
            "主隊": home_team,
            "home_spread_api": home_spread,
            "total_api": total,
        })

    return pd.DataFrame(rows)


# =========================
# 合併盤口
# =========================

def merge_lines(games_df):
    if games_df.empty:
        return games_df

    df = games_df.copy()
    odds_df = fetch_the_odds_api_lines()

    if not odds_df.empty:
        df["match_key"] = df["主隊"].apply(team_key) + "_" + df["客隊"].apply(team_key)
        odds_df["match_key"] = odds_df["主隊"].apply(team_key) + "_" + odds_df["客隊"].apply(team_key)
        df = df.merge(
            odds_df[["match_key", "home_spread_api", "total_api"]],
            on="match_key",
            how="left",
        )
    else:
        df["home_spread_api"] = None
        df["total_api"] = None

    # 優先用 The Odds API，沒有就用 ESPN odds
    df["home_spread"] = df["home_spread_api"].combine_first(df["espn_spread"])
    df["away_spread"] = df["home_spread"].apply(lambda x: -x if pd.notna(x) else None)
    df["total"] = df["total_api"].combine_first(df["espn_total"])

    df = df.drop(columns=[c for c in ["match_key"] if c in df.columns])
    return df


# =========================
# 簡易模型：乾淨穩定版
# =========================

def get_team_power_v3_base(team_abbr):
    """簡易隊伍強度基準。

    這不是最終精準模型，而是乾淨版 v3 的穩定底座。
    後續你要做模型優化時，可以再把近期戰績、傷兵、主客場、pace 加進來。
    """
    power_map = {
        "BOS": 8.5, "OKC": 8.5, "DEN": 7.5, "MIN": 7.0, "NYK": 6.8,
        "MIL": 6.5, "CLE": 6.5, "LAC": 6.2, "DAL": 6.0, "PHX": 5.8,
        "LAL": 5.5, "GSW": 5.2, "IND": 5.0, "MIA": 4.8, "ORL": 4.6,
        "PHI": 4.5, "SAC": 4.2, "NOP": 4.0, "HOU": 3.8, "ATL": 3.5,
        "CHI": 2.8, "BKN": 2.5, "TOR": 2.2, "MEM": 2.0, "SAS": 1.8,
        "UTA": 1.5, "POR": 1.0, "CHA": 0.8, "DET": 0.6, "WAS": 0.5,
    }
    return power_map.get(str(team_abbr).upper(), 3.0)


def load_recent_games_for_power():
    """讀取近期比賽資料，給 V4 動態強度使用。失敗時回傳空表。"""
    from pathlib import Path

    candidates = [
        DATA_DIR / "recent_games_cache.csv",
        DATA_DIR / "espn_day_cache.csv",
    ]

    frames = []

    for file_path in candidates:
        try:
            if Path(file_path).exists():
                df = pd.read_csv(file_path)
                if not df.empty:
                    frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    if "game_id" in df.columns:
        df["game_id"] = df["game_id"].astype(str)
        df = df.drop_duplicates(subset=["game_id"], keep="last")

    return df


def calculate_recent_power_adjust(team_abbr, max_games=10):
    """用近期比賽結果計算強度修正。支援縮寫與球隊全名。"""
    df = load_recent_games_for_power()

    if df.empty:
        return 0.0

    abbr_to_name = {
        "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
        "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
        "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
        "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
        "LAC": "LA Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
        "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
        "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
        "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
        "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
        "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
    }

    team_name = abbr_to_name.get(str(team_abbr).strip(), str(team_abbr).strip())

    if "completed" in df.columns:
        completed_text = df["completed"].astype(str).str.lower()
        keep_completed = completed_text.isin(["true", "1", "yes"])
        keep_unknown = df["completed"].isna()
        df = df[keep_completed | keep_unknown].copy()

    df["home_score"] = pd.to_numeric(df.get("home_score"), errors="coerce")
    df["away_score"] = pd.to_numeric(df.get("away_score"), errors="coerce")
    df = df.dropna(subset=["home_score", "away_score"])

    if "台灣開賽時間" in df.columns:
        df["_sort_time"] = pd.to_datetime(df["台灣開賽時間"], errors="coerce")
        df = df.sort_values("_sort_time", ascending=False)
    elif "台灣日期" in df.columns:
        df["_sort_time"] = pd.to_datetime(df["台灣日期"], errors="coerce")
        df = df.sort_values("_sort_time", ascending=False)

    games = []

    for _, row in df.iterrows():
        home_abbr = str(row.get("主隊縮寫", "")).strip()
        away_abbr = str(row.get("客隊縮寫", "")).strip()
        home_name = str(row.get("主隊", "")).strip()
        away_name = str(row.get("客隊", "")).strip()

        home_score = safe_float(row.get("home_score"), None)
        away_score = safe_float(row.get("away_score"), None)

        if home_score is None or away_score is None:
            continue

        is_home = team_abbr == home_abbr or team_name == home_name
        is_away = team_abbr == away_abbr or team_name == away_name

        if is_home:
            margin = home_score - away_score
            scored = home_score
            allowed = away_score
        elif is_away:
            margin = away_score - home_score
            scored = away_score
            allowed = home_score
        else:
            continue

        games.append({
            "margin": margin,
            "scored": scored,
            "allowed": allowed,
            "win": 1 if margin > 0 else 0,
        })

        if len(games) >= max_games:
            break

    if len(games) < 3:
        return 0.0

    gdf = pd.DataFrame(games)

    avg_margin = gdf["margin"].mean()
    win_rate = gdf["win"].mean()
    avg_scored = gdf["scored"].mean()
    avg_allowed = gdf["allowed"].mean()

    margin_part = avg_margin * 0.45
    win_part = (win_rate - 0.5) * 8.0
    offense_defense_part = (avg_scored - avg_allowed) * 0.05

    adjust = margin_part + win_part + offense_defense_part

    return round(max(min(adjust, 5.0), -5.0), 2)


def get_team_power(team_abbr):
    """V4-3：降低固定強度依賴，提高近期資料影響。

    固定強度只保留 25% 當保底參考。
    主要依靠近10場與近30場自動更新，減少人工維護。
    """
    base_power = get_team_power_v3_base(team_abbr)

    short_adjust = calculate_recent_power_adjust(team_abbr, max_games=10)
    medium_adjust = calculate_recent_power_adjust(team_abbr, max_games=30)

    dynamic_power = (short_adjust * 0.65) + (medium_adjust * 0.35)
    dynamic_power = max(min(dynamic_power, 6.0), -6.0)

    final_power = (base_power * 0.25) + dynamic_power

    return round(final_power, 2)





def injury_status_weight(status_text):
    status_text = str(status_text).lower()

    if "out" in status_text:
        return -2.0
    if "doubtful" in status_text:
        return -1.5
    if "questionable" in status_text:
        return -0.8
    if "day-to-day" in status_text or "day to day" in status_text:
        return -0.5

    return 0.0


def fetch_auto_injury_adjustments(target_tw_date):
    """
    自動傷兵抓取入口。
    目前先嘗試從 ESPN scoreboard 的 competitors 裡讀取 injuries。
    如果資料源沒有提供 injuries，就回傳空表，不影響主程式。
    """
    rows = []
    check_dates = [
        target_tw_date - timedelta(days=1),
        target_tw_date,
        target_tw_date + timedelta(days=1),
    ]

    for d in check_dates:
        params = {"dates": espn_date(d)}

        try:
            data = request_json(ESPN_SCOREBOARD_URL, params=params)
        except Exception as e:
            print(f"自動傷兵抓取失敗：{d}，原因：{e}")
            continue

        for event in data.get("events", []):
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            start_tw = parse_espn_datetime_to_taiwan(event.get("date"))
            if not start_tw or start_tw.date() != target_tw_date:
                continue

            for c in competitors:
                team = c.get("team", {})
                team_abbr = str(team.get("abbreviation", "")).strip().upper()
                team_abbr = NBA_ABBR_FIX.get(team_abbr, team_abbr)
                team_name = str(team.get("displayName", "")).strip()

                injuries = c.get("injuries", []) or []

                total_adjust = 0.0
                notes = []

                for injury in injuries:
                    athlete = injury.get("athlete", {}) or {}
                    player_name = str(athlete.get("displayName", "")).strip()
                    status = str(injury.get("status", "")).strip()
                    detail = str(injury.get("details", "")).strip()

                    weight = injury_status_weight(status)

                    if weight != 0:
                        total_adjust += weight

                    note_parts = [x for x in [player_name, status, detail] if x]
                    if note_parts:
                        notes.append(" / ".join(note_parts))

                # 避免單隊自動傷兵扣太誇張，先限制在 -5 到 +0
                total_adjust = max(-5.0, min(0.0, total_adjust))

                rows.append({
                    "team_abbr": team_abbr,
                    "team_name": team_name,
                    "adjust": round(total_adjust, 2),
                    "note": "；".join(notes) if notes else "ESPN 未提供傷兵資料",
                    "source": "espn_scoreboard",
                    "created_at": tw_now_text(),
                })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.drop_duplicates(subset=["team_abbr"], keep="last")
    return df


def save_auto_injury_adjustments(target_tw_date):
    df = fetch_auto_injury_adjustments(target_tw_date)

    if df is None or df.empty:
        print("自動傷兵：目前沒有抓到資料")
        return pd.DataFrame()

    df.to_csv(
        AUTO_INJURY_ADJUSTMENTS_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    print("自動傷兵已儲存：", AUTO_INJURY_ADJUSTMENTS_CSV)
    return df


def get_auto_injury_adjustment(team_abbr):
    """
    自動傷兵修正。
    檔案位置：data/auto_injury_adjustments.csv
    """
    if not AUTO_INJURY_ADJUSTMENTS_CSV.exists():
        return {"adjust": 0.0, "note": "無自動傷兵資料"}

    try:
        df = pd.read_csv(AUTO_INJURY_ADJUSTMENTS_CSV)
    except Exception:
        return {"adjust": 0.0, "note": "自動傷兵檔讀取失敗"}

    required_cols = {"team_abbr", "adjust", "note"}

    if not required_cols.issubset(set(df.columns)):
        return {"adjust": 0.0, "note": "自動傷兵檔欄位不完整"}

    team_abbr = str(team_abbr).strip().upper()

    matched = df[
        df["team_abbr"].astype(str).str.strip().str.upper() == team_abbr
    ]

    if matched.empty:
        return {"adjust": 0.0, "note": "無自動傷兵資料"}

    row = matched.iloc[0]

    return {
        "adjust": safe_float(row.get("adjust"), 0.0),
        "note": str(row.get("note", "")).strip() or "無備註",
    }


def get_combined_injury_adjustment(team_abbr):
    """
    合併手動傷兵 + 自動傷兵。
    手動資料優先，但自動資料也會一起納入。
    """
    manual = get_manual_injury_adjustment(team_abbr)
    auto = get_auto_injury_adjustment(team_abbr)

    manual_adjust = safe_float(manual.get("adjust"), 0.0)
    auto_adjust = safe_float(auto.get("adjust"), 0.0)

    total_adjust = manual_adjust + auto_adjust
    total_adjust = max(-6.0, min(3.0, total_adjust))

    notes = []

    manual_note = str(manual.get("note", "")).strip()
    auto_note = str(auto.get("note", "")).strip()

    if manual_note:
        notes.append("手動：" + manual_note)

    if auto_note:
        notes.append("自動：" + auto_note)

    return {
        "adjust": round(total_adjust, 2),
        "note": "｜".join(notes) if notes else "無傷兵修正",
    }


def get_manual_injury_adjustment(team_abbr):
    """
    手動傷兵修正。
    檔案位置：data/injury_adjustments.csv

    CSV 格式：
    team_abbr,adjust,note
    NYK,-2.5,主力缺陣
    SAS,1.0,主力回歸
    """
    if not INJURY_ADJUSTMENTS_CSV.exists():
        return {"adjust": 0.0, "note": "無手動傷兵修正"}

    try:
        df = pd.read_csv(INJURY_ADJUSTMENTS_CSV)
    except Exception:
        return {"adjust": 0.0, "note": "傷兵修正檔讀取失敗"}

    required_cols = {"team_abbr", "adjust", "note"}

    if not required_cols.issubset(set(df.columns)):
        return {"adjust": 0.0, "note": "傷兵修正檔欄位不完整"}

    team_abbr = str(team_abbr).strip().upper()

    matched = df[
        df["team_abbr"].astype(str).str.strip().str.upper() == team_abbr
    ]

    if matched.empty:
        return {"adjust": 0.0, "note": "無手動傷兵修正"}

    row = matched.iloc[0]

    return {
        "adjust": safe_float(row.get("adjust"), 0.0),
        "note": str(row.get("note", "")).strip() or "無備註",
    }

def save_line_snapshots(predictions):
    """V5-1：保存每次執行時的盤口快照。

    之後用來比較：
    - 晚上盤口
    - 凌晨盤口
    - 早上盤口

    這才是真正的盤口變動資料。
    """
    if predictions is None or predictions.empty:
        return

    keep_cols = [
        "game_id", "台灣開賽時間", "預測目標日期",
        "客隊", "主隊", "客隊縮寫", "主隊縮寫",
        "home_spread", "away_spread", "total",
        "home_spread_api", "total_api",
        "espn_spread", "espn_total",
    ]

    rows = predictions[[c for c in keep_cols if c in predictions.columns]].copy()

    if rows.empty:
        return

    rows["snapshot_time"] = tw_now_text()
    rows["snapshot_date"] = str(TODAY_TW)

    old = pd.DataFrame()

    if LINE_SNAPSHOT_CSV.exists():
        try:
            old = pd.read_csv(LINE_SNAPSHOT_CSV)
        except Exception:
            old = pd.DataFrame()

    combined = pd.concat([old, rows], ignore_index=True)

    # 同一場、同一時間執行，只保留最後一筆，避免手動連按造成重複
    dedupe_cols = [
        c for c in ["game_id", "snapshot_time"]
        if c in combined.columns
    ]

    if dedupe_cols:
        combined = combined.drop_duplicates(subset=dedupe_cols, keep="last")

    combined.to_csv(LINE_SNAPSHOT_CSV, index=False, encoding="utf-8-sig")


def add_line_movement_columns(predictions):
    """V5-2：讀取 line_snapshots.csv，計算盤口變動。

    目前只新增欄位，不影響推薦分數：
    - 開盤主隊讓分
    - 最新主隊讓分
    - 主隊讓分變動
    - 開盤大小分
    - 最新大小分
    - 大小分變動
    - 盤口變動摘要
    """
    if predictions is None or predictions.empty:
        return predictions

    df = predictions.copy()

    default_cols = {
        "開盤主隊讓分": None,
        "最新主隊讓分": None,
        "主隊讓分變動": None,
        "開盤大小分": None,
        "最新大小分": None,
        "大小分變動": None,
        "盤口變動摘要": "盤口快照不足",
    }

    for col, value in default_cols.items():
        if col not in df.columns:
            df[col] = value

    if not LINE_SNAPSHOT_CSV.exists():
        return df

    try:
        snapshots = pd.read_csv(LINE_SNAPSHOT_CSV)
    except Exception:
        return df

    if snapshots.empty or "game_id" not in snapshots.columns or "snapshot_time" not in snapshots.columns:
        return df

    snapshots = snapshots.copy()
    snapshots["game_id"] = snapshots["game_id"].astype(str)
    snapshots["snapshot_dt"] = pd.to_datetime(snapshots["snapshot_time"], errors="coerce")

    snapshots["home_spread"] = pd.to_numeric(snapshots.get("home_spread"), errors="coerce")
    snapshots["total"] = pd.to_numeric(snapshots.get("total"), errors="coerce")

    movement_rows = []

    for game_id, group in snapshots.groupby("game_id"):
        group = group.sort_values("snapshot_dt").copy()

        spread_group = group.dropna(subset=["home_spread"])
        total_group = group.dropna(subset=["total"])

        opening_spread = None
        latest_spread = None
        spread_move = None

        if not spread_group.empty:
            opening_spread = safe_float(spread_group.iloc[0].get("home_spread"), None)
            latest_spread = safe_float(spread_group.iloc[-1].get("home_spread"), None)

            if opening_spread is not None and latest_spread is not None:
                spread_move = round(latest_spread - opening_spread, 2)

        opening_total = None
        latest_total = None
        total_move = None

        if not total_group.empty:
            opening_total = safe_float(total_group.iloc[0].get("total"), None)
            latest_total = safe_float(total_group.iloc[-1].get("total"), None)

            if opening_total is not None and latest_total is not None:
                total_move = round(latest_total - opening_total, 2)

        summary_parts = []

        if opening_spread is not None and latest_spread is not None:
            summary_parts.append(
                f"讓分 {opening_spread:+.1f} → {latest_spread:+.1f}（{spread_move:+.1f}）"
            )

        if opening_total is not None and latest_total is not None:
            summary_parts.append(
                f"大小 {opening_total:.1f} → {latest_total:.1f}（{total_move:+.1f}）"
            )

        summary = "｜".join(summary_parts) if summary_parts else "盤口快照不足"

        movement_rows.append({
            "game_id": str(game_id),
            "開盤主隊讓分": opening_spread,
            "最新主隊讓分": latest_spread,
            "主隊讓分變動": spread_move,
            "開盤大小分": opening_total,
            "最新大小分": latest_total,
            "大小分變動": total_move,
            "盤口變動摘要": summary,
        })

    if not movement_rows:
        return df

    movement_df = pd.DataFrame(movement_rows)

    df["game_id"] = df["game_id"].astype(str)

    df = df.drop(
        columns=[
            "開盤主隊讓分", "最新主隊讓分", "主隊讓分變動",
            "開盤大小分", "最新大小分", "大小分變動", "盤口變動摘要"
        ],
        errors="ignore"
    )

    df = df.merge(movement_df, on="game_id", how="left")

    df["盤口變動摘要"] = df["盤口變動摘要"].fillna("盤口快照不足")

    def spread_move_direction(row):
        move = safe_float(row.get("主隊讓分變動"), None)

        if move is None:
            return "讓分盤口不足"

        if abs(move) < 0.5:
            return "讓分幾乎不動"

        if move > 0:
            return "盤口往主隊受讓方向移動"

        return "盤口往主隊讓更多方向移動"

    def total_move_direction(row):
        move = safe_float(row.get("大小分變動"), None)

        if move is None:
            return "大小分盤口不足"

        if abs(move) < 1.0:
            return "大小分幾乎不動"

        if move > 0:
            return "市場往大分方向移動"

        return "市場往小分方向移動"

    df["讓分盤口方向"] = df.apply(spread_move_direction, axis=1)
    df["大小分盤口方向"] = df.apply(total_move_direction, axis=1)

    return df


def predict_game(row):
    away_team = row.get("客隊", "")
    home_team = row.get("主隊", "")
    away_abbr = row.get("客隊縮寫", "")
    home_abbr = row.get("主隊縮寫", "")

    away_power = get_team_power(away_abbr)
    home_power = get_team_power(home_abbr)

    away_injury = get_combined_injury_adjustment(away_abbr)
    home_injury = get_combined_injury_adjustment(home_abbr)

    away_injury_adjust = safe_float(away_injury.get("adjust"), 0.0)
    home_injury_adjust = safe_float(home_injury.get("adjust"), 0.0)
    injury_edge = home_injury_adjust - away_injury_adjust

    matchup = calc_recent_10_matchup_adjustment(
        away_team,
        home_team,
        TODAY_TW
    )

    away_form = matchup.get("away_form", {})
    home_form = matchup.get("home_form", {})
    home_form_edge = safe_float(matchup.get("home_form_edge"), 0)

    home_away_form = calc_home_away_form_edge(
        home_team,
        away_team,
        TODAY_TW
    )
    home_away_edge = safe_float(home_away_form.get("home_edge"), 0)

    home_advantage = 2.0

    line_move_adjust = 0.0
    spread_move = safe_float(row.get("主隊讓分變動"), None)

    if spread_move is not None:
        if spread_move <= -1.0:
            line_move_adjust = 0.5
        elif spread_move >= 1.0:
            line_move_adjust = -0.5

    predicted_home_margin = (
        home_power
        - away_power
        + home_advantage
        + home_form_edge * 0.45
        + home_away_edge * 0.30
        + injury_edge
        + line_move_adjust
    )

    predicted_home_margin = round(predicted_home_margin, 1)

    home_spread = safe_float(row.get("home_spread"), None)
    total_line = safe_float(row.get("total"), None)

    home_avg_score = safe_float(home_form.get("avg_score"), 0)
    away_avg_score = safe_float(away_form.get("avg_score"), 0)
    home_avg_allowed = safe_float(home_form.get("avg_allowed"), 0)
    away_avg_allowed = safe_float(away_form.get("avg_allowed"), 0)

    if home_avg_score > 0 and away_avg_score > 0:
        offense_base = (home_avg_score + away_avg_score) / 2
    else:
        offense_base = 112

    if home_avg_allowed > 0 and away_avg_allowed > 0:
        defense_base = (home_avg_allowed + away_avg_allowed) / 2
    else:
        defense_base = 112

    predicted_total = (
        offense_base * 0.55
        + defense_base * 0.45
    ) * 2

    if total_line is not None:
        predicted_total = predicted_total * 0.55 + total_line * 0.45

    predicted_total = round(predicted_total, 1)

    if home_spread is None:
        spread_pick = "無盤口"
        spread_edge = 0
        spread_reason = "目前沒有讓分盤口，暫不推薦。"
    else:
        home_cover_edge = predicted_home_margin + home_spread
        spread_edge = round(home_cover_edge, 1)

        if home_cover_edge > 0:
            spread_pick = f"{home_team} {home_spread:+.1f}"
            spread_reason = (
                f"預測主隊分差 {predicted_home_margin:+.1f}，"
                f"盤口 {home_spread:+.1f}，主隊有 {abs(spread_edge):.1f} 分優勢。"
            )
        else:
            away_spread = -home_spread
            spread_pick = f"{away_team} {away_spread:+.1f}"
            spread_reason = (
                f"預測主隊分差 {predicted_home_margin:+.1f}，"
                f"盤口 {home_spread:+.1f}，客隊有 {abs(spread_edge):.1f} 分優勢。"
            )

    if total_line is None:
        total_pick = "無盤口"
        total_edge = 0
        total_reason = "目前沒有大小分盤口，暫不推薦。"
    else:
        total_edge_raw = predicted_total - total_line
        total_edge = round(total_edge_raw, 1)

        if total_edge_raw > 0:
            total_pick = f"大分 {total_line:.1f}"
            total_reason = (
                f"預測總分 {predicted_total:.1f}，"
                f"盤口 {total_line:.1f}，大分有 {abs(total_edge):.1f} 分優勢。"
            )
        else:
            total_pick = f"小分 {total_line:.1f}"
            total_reason = (
                f"預測總分 {predicted_total:.1f}，"
                f"盤口 {total_line:.1f}，小分有 {abs(total_edge):.1f} 分優勢。"
            )

    if abs(spread_edge) > abs(total_edge):
        market_bias = "讓分優勢較明顯"
    elif abs(total_edge) > abs(spread_edge):
        market_bias = "大小分優勢較明顯"
    else:
        market_bias = "市場差異不明顯"

    return pd.Series({
        "客隊強度": away_power,
        "主隊強度": home_power,
        "主隊近10場": home_form.get("summary", ""),
        "客隊近10場": away_form.get("summary", ""),
        "主隊近10場場數": safe_int(home_form.get("games", 0), 0),
        "客隊近10場場數": safe_int(away_form.get("games", 0), 0),
        "主隊近況分數": round(safe_float(home_form.get("form_score"), 0), 2),
        "客隊近況分數": round(safe_float(away_form.get("form_score"), 0), 2),
        "主隊近況優勢": round(home_form_edge, 2),
        "主客場近況優勢": round(home_away_edge, 2),
        "客隊傷兵修正": away_injury_adjust,
        "主隊傷兵修正": home_injury_adjust,
        "傷兵影響": round(injury_edge, 2),
        "盤口分差修正": round(line_move_adjust, 2),
        "客隊傷兵備註": away_injury.get("note", ""),
        "主隊傷兵備註": home_injury.get("note", ""),
        "預測主隊分差": predicted_home_margin,
        "預測總分": predicted_total,
        "讓分推薦": spread_pick,
        "讓分優勢": spread_edge,
        "讓分理由": spread_reason,
        "大小分推薦": total_pick,
        "大小分優勢": total_edge,
        "大小分理由": total_reason,
        "市場偏向": market_bias,
        "市場與預測": f"讓分優勢 {spread_edge:+.1f}｜大小分優勢 {total_edge:+.1f}",
    })

# =========================
# 產生明日預測
# =========================

def build_tomorrow_predictions():
    games = fetch_espn_games_by_taiwan_date(TOMORROW_TW)
    if games.empty:
        return games

    games = merge_lines(games)
    pred_cols = games.apply(predict_game, axis=1)
    df = pd.concat([games, pred_cols], axis=1)

    df["報告日期"] = str(TODAY_TW)
    df["預測目標日期"] = str(TOMORROW_TW)
    df["created_at"] = tw_now_text()

    return df


# =========================
# 昨日驗證
# =========================

def load_prediction_log():
    if not PREDICTION_LOG_CSV.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(PREDICTION_LOG_CSV)
    except Exception:
        return pd.DataFrame()

def save_prediction_log(new_predictions):
    if new_predictions.empty:
        return

    final_recs = build_final_recommendations(new_predictions)

    keep_cols = [
        "game_id", "報告日期", "預測目標日期", "台灣開賽時間", "客隊", "主隊",
        "客隊縮寫", "主隊縮寫", "away_spread", "home_spread", "total",
        "預測主隊分差", "預測總分", "讓分推薦", "讓分優勢", "大小分推薦", "大小分優勢",
        "市場偏向",
        "主客場近況優勢", "傷兵影響", "主隊傷兵修正", "客隊傷兵修正",
        "主隊傷兵備註", "客隊傷兵備註",
        "開盤主隊讓分", "最新主隊讓分", "主隊讓分變動", "讓分盤口方向",
        "開盤大小分", "最新大小分", "大小分變動", "大小分盤口方向",
        "盤口變動摘要",
        "created_at",
        "推薦等級", "推薦類型", "推薦內容", "信心分數", "預測優勢",
        "盤口配合", "優勢絕對值", "優勢級距",
    ]

    rows = []

    if len(final_recs) == 0:
        save_df = pd.DataFrame(columns=keep_cols)
    else:
        for item in final_recs:
            if item.get("推薦等級") == "PASS":
                base = new_predictions.iloc[0]
            else:
                game_text = str(item.get("比賽", ""))

                mask = (
                    new_predictions["台灣開賽時間"].astype(str).eq(str(item.get("台灣開賽時間", "")))
                    & ((new_predictions["客隊"].astype(str) + " vs " + new_predictions["主隊"].astype(str)) == game_text)
                )

                if not mask.any():
                    continue

                base = new_predictions.loc[mask].iloc[0]

            row = {}

            for col in keep_cols:
                row[col] = base.get(col, "")

            row["推薦等級"] = item.get("推薦等級", "")
            row["推薦類型"] = item.get("類型", "")
            row["推薦內容"] = item.get("推薦", "")
            row["信心分數"] = item.get("信心分數", "")
            row["預測優勢"] = item.get("預測優勢", "")
            row["盤口配合"] = item.get("盤口配合", "盤口資料不足")

            edge_value = abs(safe_float(item.get("預測優勢", 0), 0))
            row["優勢絕對值"] = edge_value

            if edge_value >= 6.0:
                row["優勢級距"] = ">=6.0"
            elif edge_value >= 5.0:
                row["優勢級距"] = ">=5.0"
            elif edge_value >= 4.0:
                row["優勢級距"] = ">=4.0"
            elif edge_value >= 2.5:
                row["優勢級距"] = ">=2.5"
            else:
                row["優勢級距"] = "<2.5"

            rows.append(row)

        save_df = pd.DataFrame(rows)

    old = load_prediction_log()

    if not old.empty:
        for col in keep_cols:
            if col not in old.columns:
                old[col] = ""

        old = old[
            ~(
                old["報告日期"].astype(str).eq(str(TODAY_TW))
                & old["預測目標日期"].astype(str).eq(str(TOMORROW_TW))
            )
        ].copy()

        combined = pd.concat([old, save_df], ignore_index=True)
    else:
        combined = save_df

    for col in keep_cols:
        if col not in combined.columns:
            combined[col] = ""

    combined = combined[keep_cols]

    combined.to_csv(PREDICTION_LOG_CSV, index=False, encoding="utf-8-sig")


def verify_yesterday_predictions():
    log = load_prediction_log()
    if log.empty:
        return pd.DataFrame()

    target_log = log[
        (pd.to_datetime(log["報告日期"], errors="coerce").dt.date == YESTERDAY_TW)
        & (pd.to_datetime(log["預測目標日期"], errors="coerce").dt.date == TODAY_TW)
    ].copy()

    if target_log.empty:
        return pd.DataFrame()

    results = fetch_espn_games_by_taiwan_date(TODAY_TW)
    if results.empty:
        return pd.DataFrame()

    results = results[["game_id", "away_score", "home_score", "completed", "status"]].copy()

    target_log["game_id"] = target_log["game_id"].astype(str)
    results["game_id"] = results["game_id"].astype(str)

    merged = target_log.merge(results, on="game_id", how="left")

    verify_rows = []
    for _, row in merged.iterrows():
        if str(row.get("推薦等級", "")) == "PASS" or str(row.get("推薦類型", "")) == "觀望":
            continue

        rec_level = normalize_rec_level(row.get("推薦等級", ""))
        rec_type = str(row.get("推薦類型", ""))
        rec_pick = str(row.get("推薦內容", ""))

        if rec_level not in ["主推", "副推 1", "副推 2"]:
            continue

        if not row.get("completed", False):
            result = "未完賽"
        else:
            away_score = safe_float(row.get("away_score"), None)
            home_score = safe_float(row.get("home_score"), None)

            if away_score is None or home_score is None:
                result = "無法驗證"

            elif rec_type == "大小分":
                total_line = safe_float(row.get("total"), None)

                if total_line is None:
                    result = "無法驗證"
                else:
                    actual_total = away_score + home_score

                    if actual_total == total_line:
                        result = "走水"
                    elif "大分" in rec_pick or "Over" in rec_pick:
                        result = "過" if actual_total > total_line else "沒過"
                    elif "小分" in rec_pick or "Under" in rec_pick:
                        result = "過" if actual_total < total_line else "沒過"
                    else:
                        result = "無法驗證"

            elif rec_type == "讓分":
                home_spread = safe_float(row.get("home_spread"), None)

                if home_spread is None:
                    result = "無法驗證"
                else:
                    actual_home_margin = home_score - away_score
                    home_cover_value = actual_home_margin + home_spread

                    if home_cover_value == 0:
                        result = "走水"
                    elif str(row.get("主隊", "")) in rec_pick:
                        result = "過" if home_cover_value > 0 else "沒過"
                    elif str(row.get("客隊", "")) in rec_pick:
                        result = "過" if home_cover_value < 0 else "沒過"
                    else:
                        result = "無法驗證"

            else:
                result = "無法驗證"

        verify_rows.append({
            "台灣開賽時間": row.get("台灣開賽時間"),
            "客隊": short_name(row.get("客隊")),
            "主隊": short_name(row.get("主隊")),
            "比分": f"{safe_int(row.get('away_score'), '-')}-{safe_int(row.get('home_score'), '-')}",
            "推薦等級": rec_level,
            "推薦類型": rec_type,
            "推薦內容": rec_pick,
            "結果": result_display(result),
        })

    verify_df = pd.DataFrame(verify_rows)

    if not verify_df.empty and "推薦等級" in verify_df.columns:
        verify_df["排序"] = verify_df["推薦等級"].apply(rec_level_order)
        verify_df = verify_df.sort_values("排序").drop(columns=["排序"])

    return verify_df

# =========================
# 近 7 筆 / 近 30 筆勝率
# =========================

def calculate_win_rates():
    log = load_prediction_log()

    output = {
        "main_7": "0/0（0.0%）",
        "main_30": "0/0（0.0%）",
        "top3_7": "0/0（0.0%）",
        "top3_30": "0/0（0.0%）",
        "overall_7": "0/0（0.0%）",
        "overall_30": "0/0（0.0%）",
        "overall_all": "無資料",
    }

    if log.empty:
        return output

    today = TODAY_TW
    all_verified = []

    for d in log["預測目標日期"].dropna().unique():
        try:
            d = pd.to_datetime(d).date()

            if d > today:
                continue

            verified = verify_predictions_for_date(d)

            if not verified.empty:
                verified["預測日期"] = str(d)
                all_verified.append(verified)

        except Exception:
            continue

    if not all_verified:
        return output

    all_df = pd.concat(all_verified, ignore_index=True)

    if "推薦等級" in all_df.columns:
        all_df = all_df[
            all_df["推薦等級"]
            .fillna("")
            .astype(str)
            .str.contains("主推|副推", na=False)
        ].copy()

    if "結果" in all_df.columns:
        all_df = all_df[
            all_df["結果"]
            .fillna("")
            .astype(str)
            .str.contains("過|沒過", na=False)
        ].copy()

    if "推薦等級" in all_df.columns:
        all_df = all_df[
            ~all_df["推薦等級"]
            .fillna("")
            .astype(str)
            .str.contains("PASS|觀望", na=False)
        ].copy()

    if all_df.empty:
        return output

    # 日期舊到新排序，再取最後 N 筆
    if "預測日期" in all_df.columns:
        all_df["排序日期"] = pd.to_datetime(all_df["預測日期"], errors="coerce")
        all_df = all_df.sort_values("排序日期").drop(columns=["排序日期"])

    for label, count in {"7": 7, "30": 30}.items():
        recent_df = all_df.tail(count).copy()

        output[f"main_{label}"] = format_final_pick_rate(
            recent_df,
            only_main=True
        )

        output[f"top3_{label}"] = format_final_pick_rate(
            recent_df,
            only_main=False
        )

        output[f"overall_{label}"] = format_final_pick_rate(
            recent_df,
            only_main=False
        )

    output["overall_all"] = format_final_pick_rate(
        all_df,
        only_main=False
    )

    return output

def calculate_edge_bucket_rates():
    """V4-13：回測不同預測優勢級距的勝率。

    目的：
    - 不靠感覺調整門檻。
    - 用歷史紀錄觀察：優勢越大，勝率是否真的越高。
    - 先只印在終端機，不影響推薦邏輯。
    """
    log = load_prediction_log()

    if log.empty or "預測優勢" not in log.columns:
        return pd.DataFrame()

    today = TODAY_TW
    verified_list = []

    for d in log["預測目標日期"].dropna().unique():
        try:
            check_date = pd.to_datetime(d).date()

            if check_date > today:
                continue

            verified = verify_predictions_for_date(check_date)

            if not verified.empty:
                verified["預測目標日期"] = str(check_date)
                verified_list.append(verified)

        except Exception:
            continue

    if not verified_list:
        return pd.DataFrame()

    verified_df = pd.concat(verified_list, ignore_index=True)

    base = log.copy()
    base["預測目標日期"] = base["預測目標日期"].astype(str)

    merged = base.merge(
        verified_df[["預測目標日期", "推薦等級", "推薦類型", "推薦內容", "推薦結果"]],
        on=["預測目標日期", "推薦等級", "推薦類型", "推薦內容"],
        how="left"
    )

    merged["預測優勢數值"] = pd.to_numeric(merged["預測優勢"], errors="coerce").abs()

    merged = merged[
        merged["推薦結果"].astype(str).isin(["過", "沒過"])
    ].copy()

    if merged.empty:
        return pd.DataFrame()

    buckets = [
        ("優勢 >= 2.5", 2.5),
        ("優勢 >= 4.0", 4.0),
        ("優勢 >= 5.0", 5.0),
        ("優勢 >= 6.0", 6.0),
    ]

    rows = []

    for label, threshold in buckets:
        temp = merged[merged["預測優勢數值"] >= threshold].copy()

        total = len(temp)
        win = temp["推薦結果"].astype(str).eq("過").sum()
        lose = temp["推薦結果"].astype(str).eq("沒過").sum()

        if total == 0:
            rate_text = "無資料"
        else:
            rate_text = f"{win}/{total}（{win / total * 100:.1f}%）"

        rows.append({
            "優勢門檻": label,
            "過": win,
            "沒過": lose,
            "總數": total,
            "勝率": rate_text,
        })

    return pd.DataFrame(rows)



def calculate_confidence_threshold_rates():
    """回測不同信心分數門檻的勝率。

    目的：
    - 找出主推門檻 72% 是否合理。
    - 不先改推薦邏輯，只先印出統計結果。
    """
    log = load_prediction_log()

    if log.empty or "信心分數" not in log.columns:
        return pd.DataFrame()

    today = TODAY_TW
    verified_list = []

    for d in log["預測目標日期"].dropna().unique():
        try:
            check_date = pd.to_datetime(d).date()

            if check_date > today:
                continue

            verified = verify_predictions_for_date(check_date)

            if not verified.empty:
                verified["預測目標日期"] = str(check_date)
                verified_list.append(verified)

        except Exception:
            continue

    if not verified_list:
        return pd.DataFrame()

    verified_df = pd.concat(verified_list, ignore_index=True)

    base = log.copy()
    base["預測目標日期"] = base["預測目標日期"].astype(str)

    merged = base.merge(
        verified_df[["預測目標日期", "推薦等級", "推薦類型", "推薦內容", "推薦結果"]],
        on=["預測目標日期", "推薦等級", "推薦類型", "推薦內容"],
        how="left"
    )

    merged["信心分數數值"] = merged["信心分數"].apply(confidence_to_percent)

    merged = merged[
        merged["推薦結果"].astype(str).isin(["過", "沒過"])
    ].copy()

    if merged.empty:
        return pd.DataFrame()

    thresholds = [58, 61, 64, 68, 70, 72, 73, 75]

    rows = []

    for threshold in thresholds:
        temp = merged[merged["信心分數數值"] >= threshold].copy()

        total = len(temp)
        win = temp["推薦結果"].astype(str).eq("過").sum()
        lose = temp["推薦結果"].astype(str).eq("沒過").sum()

        if total == 0:
            rate_text = "無資料"
        else:
            rate_text = f"{win}/{total}（{win / total * 100:.1f}%）"

        rows.append({
            "信心門檻": f">= {threshold}%",
            "過": win,
            "沒過": lose,
            "總數": total,
            "勝率": rate_text,
        })

    return pd.DataFrame(rows)


def calculate_line_alignment_rates():
    """V5-6：回測順盤 / 逆盤 / 中性推薦勝率。

    只印在終端機，不改推薦分數。
    """
    log = load_prediction_log()

    if log.empty or "盤口配合" not in log.columns:
        return pd.DataFrame()

    today = TODAY_TW
    verified_list = []

    for d in log["預測目標日期"].dropna().unique():
        try:
            check_date = pd.to_datetime(d).date()

            if check_date > today:
                continue

            verified = verify_predictions_for_date(check_date)

            if not verified.empty:
                verified["預測目標日期"] = str(check_date)
                verified_list.append(verified)

        except Exception:
            continue

    if not verified_list:
        return pd.DataFrame()

    verified_df = pd.concat(verified_list, ignore_index=True)

    base = log.copy()
    base["預測目標日期"] = base["預測目標日期"].astype(str)

    merged = base.merge(
        verified_df[["預測目標日期", "推薦等級", "推薦類型", "推薦內容", "推薦結果"]],
        on=["預測目標日期", "推薦等級", "推薦類型", "推薦內容"],
        how="left"
    )

    merged = merged[
        merged["推薦結果"].astype(str).isin(["過", "沒過"])
    ].copy()

    if merged.empty:
        return pd.DataFrame()

    rows = []

    for status in ["順盤", "逆盤", "盤口中性", "盤口資料不足"]:
        temp = merged[merged["盤口配合"].astype(str).eq(status)].copy()

        total = len(temp)
        win = temp["推薦結果"].astype(str).eq("過").sum()
        lose = temp["推薦結果"].astype(str).eq("沒過").sum()

        if total == 0:
            rate_text = "無資料"
        else:
            rate_text = f"{win}/{total}（{win / total * 100:.1f}%）"

        rows.append({
            "盤口配合": status,
            "過": win,
            "沒過": lose,
            "總數": total,
            "勝率": rate_text,
        })

    return pd.DataFrame(rows)

def build_summary_text(win_rates):
    try:
        main = win_rates.get("main_7", "0/0（0.0%）")
        top3 = win_rates.get("top3_7", "0/0（0.0%）")
        overall = win_rates.get("overall_7", "0/0（0.0%）")

        # 只取百分比那段（括號內）
        def extract_pct(text):
            if "（" in text and "）" in text:
                return text.split("（")[1].replace("）", "")
            return "0.0%"

        main_pct = extract_pct(main)
        top3_pct = extract_pct(top3)
        overall_pct = extract_pct(overall)

        return f"昨日表現：主推{main_pct}｜Top3 {top3_pct}｜整體{overall_pct}"

    except:
        return "昨日表現：無資料"

def verify_predictions_for_date(target_date):
    log = load_prediction_log()
    if log.empty:
        return pd.DataFrame()

    target_log = log[log["預測目標日期"].astype(str) == str(target_date)].copy()
    if target_log.empty:
        return pd.DataFrame()

    results = fetch_espn_games_by_taiwan_date(target_date)
    if results.empty:
        return pd.DataFrame()

    results = results[["game_id", "away_score", "home_score", "completed"]].copy()

    target_log["game_id"] = target_log["game_id"].astype(str)
    results["game_id"] = results["game_id"].astype(str)

    merged = target_log.merge(results, on="game_id", how="left")

    final_rows = []

    level_map = {
        "主推": "🔥 主推",
        "副推 1": "🥈 副推 1",
        "副推 2": "🥉 副推 2",
        "🔥 主推": "🔥 主推",
        "🥈 副推 1": "🥈 副推 1",
        "🥉 副推 2": "🥉 副推 2",
    }

    for _, row in merged.iterrows():
        if not row.get("completed", False):
            continue

        rec_level = level_map.get(str(row.get("推薦等級", "")), str(row.get("推薦等級", "")))
        rec_type = str(row.get("推薦類型", ""))
        rec_pick = str(row.get("推薦內容", ""))

        if rec_level not in ["主推", "副推 1", "副推 2", "🔥 主推", "🥈 副推 1", "🥉 副推 2"]:
            continue
        away_score = safe_float(row.get("away_score"), None)
        home_score = safe_float(row.get("home_score"), None)

        if away_score is None or home_score is None:
            result = "無法驗證"

        elif rec_type == "大小分":
            total_line = safe_float(row.get("total"), None)

            if total_line is None:
                result = "無法驗證"
            else:
                actual_total = away_score + home_score

                if actual_total == total_line:
                    result = "走水"
                elif "大分" in rec_pick or "Over" in rec_pick:
                    result = "過" if actual_total > total_line else "沒過"
                elif "小分" in rec_pick or "Under" in rec_pick:
                    result = "過" if actual_total < total_line else "沒過"
                else:
                    result = "無法驗證"

        elif rec_type == "讓分":
            home_spread = safe_float(row.get("home_spread"), None)

            if home_spread is None:
                result = "無法驗證"
            else:
                actual_home_margin = home_score - away_score
                home_cover_value = actual_home_margin + home_spread

                if home_cover_value == 0:
                    result = "走水"
                elif str(row.get("主隊", "")) in rec_pick:
                    result = "過" if home_cover_value > 0 else "沒過"
                elif str(row.get("客隊", "")) in rec_pick:
                    result = "過" if home_cover_value < 0 else "沒過"
                else:
                    result = "無法驗證"

        else:
            result = "無法驗證"

        final_rows.append({
            "date": str(target_date),
            "推薦等級": rec_level,
            "推薦類型": rec_type,
            "推薦內容": rec_pick,
            "推薦結果": result,
            "結果": result_display(result),
        })

    return pd.DataFrame(final_rows)


def format_final_pick_rate(df, only_main=True):
    if df is None or df.empty:
        return "0/0（0.0%）"

    temp = df.copy()

    # 只保留主推 / 副推資料
    temp = temp[temp["推薦等級"].notna()]
    temp = temp[temp["推薦結果"].notna()]

    # 只算已完賽：過、沒過、走水
    temp = temp[temp["推薦結果"].astype(str).str.contains("過|沒過|走水", na=False)]

    if only_main:
        temp = temp[temp["推薦等級"].astype(str).str.contains("主推", na=False)]
    else:
        temp = temp[temp["推薦等級"].astype(str).str.contains("主推|副推", na=False)]

    total = len(temp)
    win = temp["推薦結果"].astype(str).eq("過").sum()

    if total == 0:
        return "0/0（0.0%）"

    rate = win / total * 100
    return f"{win}/{total}（{rate:.1f}%）"

def format_rate(df, col):
    if df.empty or col not in df.columns:
        return "無資料"
    valid = df[df[col].isin(["過", "沒過"])]
    if valid.empty:
        return "無資料"
    wins = (valid[col] == "過").sum()
    total = len(valid)
    rate = wins / total * 100
    return f"{wins}/{total}（{rate:.1f}%）"


# =========================
# HTML 報告
# =========================

def df_to_html_table(df, columns=None):
    if df is None or df.empty:
        return "<p class='empty'>目前沒有資料。</p>"

    show_df = df.copy()
    if columns:
        show_df = show_df[[c for c in columns if c in show_df.columns]]

    return show_df.to_html(index=False, escape=False, classes="data-table")


def confidence_score(edge):
    """V4-8：更保守的信心分數。

    核心原則：
    - 小優勢不要假裝很有把握。
    - 只有明顯優勢才給到 64% 以上。
    - 70% 以上保留給非常強的選項。
    """
    edge = abs(safe_float(edge, 0))

    if edge < 1.0:
        return 52
    if edge < 1.8:
        return 55
    if edge < 2.5:
        return 58
    if edge < 3.5:
        return 61
    if edge < 4.5:
        return 64
    if edge < 6.0:
        return 68
    if edge < 7.5:
        return 70
    if edge < 9.0:
        return 73

    return 75


def confidence_label(score):
    score = confidence_to_percent(score)

    if score >= 70:
        return "高信心"
    if score >= 64:
        return "可考慮"
    if score >= 58:
        return "低信心"
    return "不推薦"

def final_level_display(level):
    level = str(level)

    if level == "主推":
        return "🔥 主推"
    elif level == "副推 1":
        return "🥈 副推"
    elif level == "副推 2":
        return "🥉 副推"
    else:
        return "—"

def normalize_rec_level(level):
    level = str(level).strip()

    if level in ["主推", "🔥 主推"]:
        return "主推"
    elif level in ["副推 1", "副推1", "🥈 副推 1", "🥈 副推"]:
        return "副推 1"
    elif level in ["副推 2", "副推2", "🥉 副推 2", "🥉 副推"]:
        return "副推 2"
    else:
        return level


def rec_level_order(level):
    level = normalize_rec_level(level)

    if level == "主推":
        return 1
    elif level == "副推 1":
        return 2
    elif level == "副推 2":
        return 3
    else:
        return 99
def result_display(result):
    result = str(result)

    if result == "過":
        return "<span class='result-win'>✅ 過</span>"
    elif result == "沒過":
        return "<span class='result-lose'>❌ 沒過</span>"
    elif result == "走水":
        return "<span class='result-push'>➖ 走水</span>"
    elif result == "未完賽":
        return "<span class='result-pending'>⏳ 未完賽</span>"
    else:
        return "<span class='result-pending'>—</span>"
def clean_yesterday_verify(df):
    """
    清理昨日驗證：
    1. 只保留主推 / 副推
    2. 如果同一個推薦等級重複，只保留最後一筆
    3. 排序成 主推 → 副推1 → 副推2
    """
    if df is None or df.empty:
        return df

    temp = df.copy()

    if "推薦等級" not in temp.columns:
        return temp

    temp = temp[temp["推薦等級"].astype(str).str.contains("主推|副推", na=False)]

    def rank_key(text):
        text = str(text)
        if "主推" in text:
            return "主推"
        elif "副推 1" in text or "副推1" in text or "🥈 副推" in text:
            return "副推1"
        elif "副推 2" in text or "副推2" in text or "🥉 副推" in text:
            return "副推2"
        return text

    def rank_order(text):
        text = str(text)
        if "主推" in text:
            return 1
        elif "副推1" in text:
            return 2
        elif "副推2" in text:
            return 3
        return 99

    temp["rank_key"] = temp["推薦等級"].apply(rank_key)

    # ✅ 同一個推薦等級重複時，保留最後一筆
    temp = temp.drop_duplicates(subset=["rank_key"], keep="last")

    temp["rank_order"] = temp["rank_key"].apply(rank_order)
    temp = temp.sort_values("rank_order")

    temp = temp.drop(columns=["rank_key", "rank_order"], errors="ignore")

    return temp
def yesterday_top3_summary_html(yesterday_verify):
    if yesterday_verify is None or yesterday_verify.empty:
        return "<div class='verify-summary'>昨日 Top3：目前沒有可驗證資料</div>"

    df = yesterday_verify.copy()

    if "推薦等級" not in df.columns or "結果" not in df.columns:
        return "<div class='verify-summary'>昨日 Top3：目前沒有可驗證資料</div>"

    # 只保留新版 Top3 推薦
    df = df[df["推薦等級"].astype(str).isin(["主推", "副推 1", "副推 2", "🔥 主推", "🥈 副推 1", "🥉 副推 2"])]

    if df.empty:
        return "<div class='verify-summary'>昨日 Top3：目前沒有 Top3 驗證資料</div>"

    result_text = df["結果"].astype(str)

    win_count = result_text.str.contains("✅ 過|過盤", na=False).sum()
    lose_count = result_text.str.contains("❌ 沒過|沒過", na=False).sum()
    valid_total = win_count + lose_count

    main_df = df[df["推薦等級"].astype(str).str.contains("主推", na=False)]
    main_result_text = main_df["結果"].astype(str) if not main_df.empty else pd.Series(dtype=str)

    main_win = main_result_text.str.contains("✅ 過|過盤", na=False).sum()
    main_lose = main_result_text.str.contains("❌ 沒過|沒過", na=False).sum()
    main_total = main_win + main_lose

    if valid_total == 0:
        return "<div class='verify-summary'>昨日 Top3：目前尚無已完賽結果｜主推：尚無結果</div>"

    top3_rate = win_count / valid_total * 100

    if main_total > 0:
        main_rate = main_win / main_total * 100
        main_text = f"主推：{main_win} 過 {main_lose} 沒過（{main_rate:.1f}%）"
    else:
        main_text = "主推：尚無結果"

    return f"<div class='verify-summary'>昨日 Top3：{win_count} 過 {lose_count} 沒過（{top3_rate:.1f}%）｜{main_text}</div>"



def line_alignment_status(row, rec_type, pick):
    """V5-5：判斷推薦是否順著盤口變動。

    只做標記，不加減分。
    """
    rec_type = str(rec_type)
    pick = str(pick)

    if rec_type == "讓分":
        move = safe_float(row.get("主隊讓分變動"), None)

        if move is None:
            return "盤口資料不足"

        if abs(move) < 0.5:
            return "盤口中性"

        home_team = str(row.get("主隊", ""))
        away_team = str(row.get("客隊", ""))

        # home_spread 變小，例如 -3 → -5，代表市場往主隊方向
        if move < 0:
            if home_team in pick:
                return "順盤"
            if away_team in pick:
                return "逆盤"

        # home_spread 變大，例如 -5 → -3 或 +2 → +4，代表市場往客隊方向
        if move > 0:
            if away_team in pick:
                return "順盤"
            if home_team in pick:
                return "逆盤"

        return "盤口中性"

    if rec_type == "大小分":
        move = safe_float(row.get("大小分變動"), None)

        if move is None:
            return "盤口資料不足"

        if abs(move) < 1.0:
            return "盤口中性"

        if move > 0 and "大分" in pick:
            return "順盤"

        if move < 0 and "小分" in pick:
            return "順盤"

        if move > 0 and "小分" in pick:
            return "逆盤"

        if move < 0 and "大分" in pick:
            return "逆盤"

        return "盤口中性"

    return "盤口資料不足"


def adjust_confidence_by_line(score, alignment_status):
    """
    V4-4：盤口變動納入信心分數。
    順盤：小幅加分
    逆盤：扣分
    中性 / 資料不足：不動
    """
    score = safe_float(score, 0)
    alignment_status = str(alignment_status)

    if alignment_status == "順盤":
        score += 2
    elif alignment_status == "逆盤":
        score -= 3

    return int(max(0, min(100, score)))

def build_final_recommendations(predictions):
    """建立最終推薦。

    V4-11：
    - 信心分數仍然由 confidence_score(edge) 決定。
    - 不重做前面分數邏輯。
    - 排序時先看信心分數，再看實際預測優勢大小。
    - 避免同一個信心區間內，較小優勢的場次排在較大優勢前面。
    """

    if predictions is None or predictions.empty:
        return []

    MAIN_MIN_SCORE = MAIN_PICK_MIN_CONFIDENCE
    SECONDARY_MIN_SCORE = 64

    items = []

    for _, row in predictions.iterrows():

        if str(row.get("大小分推薦", "")) != "無盤口":
            edge = row.get("大小分優勢", 0)

            if pd.isna(edge):
                edge = 0

            edge_value = abs(safe_float(edge, 0))

            if edge_value < 2.5:
                continue

            score = confidence_score(edge)
            alignment = line_alignment_status(row, "大小分", row.get("大小分推薦", ""))
            score = adjust_confidence_by_line(score, alignment)

            sample_games = min(
                safe_int(row.get("主隊近10場場數", 0), 0),
                safe_int(row.get("客隊近10場場數", 0), 0)
            )

            if sample_games < 5:
                score -= 8
            elif sample_games < 8:
                score -= 4

            score = int(max(0, min(100, score)))

            items.append({
                "推薦等級": "",
                "類型": "大小分",
                "game_id": row.get("game_id", ""),
                "台灣開賽時間": row.get("台灣開賽時間", ""),
                "比賽": f"{row.get('客隊', '')} vs {row.get('主隊', '')}",
                "推薦": row.get("大小分推薦", ""),
                "信心分數": f"{score}%",
                "信心分數數值": score,
                "預測優勢": edge,
                "排序優勢": edge_value,
                "盤口配合": alignment,
                "理由": row.get("大小分理由", ""),
            })

        if str(row.get("讓分推薦", "")) != "無盤口":
            edge = row.get("讓分優勢", 0)

            if pd.isna(edge):
                edge = 0

            edge_value = abs(safe_float(edge, 0))

            if edge_value < 2.5:
                continue

            score = confidence_score(edge)
            alignment = line_alignment_status(row, "讓分", row.get("讓分推薦", ""))
            score = adjust_confidence_by_line(score, alignment)

            sample_games = min(
                safe_int(row.get("主隊近10場場數", 0), 0),
                safe_int(row.get("客隊近10場場數", 0), 0)
            )

            if sample_games < 5:
                score -= 8
            elif sample_games < 8:
                score -= 4

            score = int(max(0, min(100, score)))

            items.append({
                "推薦等級": "",
                "類型": "讓分",
                "game_id": row.get("game_id", ""),
                "台灣開賽時間": row.get("台灣開賽時間", ""),
                "比賽": f"{row.get('客隊', '')} vs {row.get('主隊', '')}",
                "推薦": row.get("讓分推薦", ""),
                "信心分數": f"{score}%",
                "信心分數數值": score,
                "預測優勢": edge,
                "排序優勢": edge_value,
                "盤口配合": alignment,
                "理由": row.get("讓分理由", ""),
            })

    if len(items) == 0:
        return []

    items = sorted(
        items,
        key=lambda x: (
            x.get("信心分數數值", 0),
            x.get("排序優勢", 0)
        ),
        reverse=True
    )

    if items[0].get("信心分數數值", 0) < MAIN_MIN_SCORE:
        return [{
            "推薦等級": "PASS",
            "類型": "觀望",
            "台灣開賽時間": "",
            "比賽": "今日全部場次",
            "推薦": "今日沒有達到主推門檻的選項",
            "信心分數": f"{items[0].get('信心分數數值', 0)}%",
            "信心分數數值": items[0].get("信心分數數值", 0),
            "預測優勢": items[0].get("預測優勢", 0),
            "理由": "最高分仍低於主推門檻，依照 V4 規則選擇不硬推，避免為了湊推薦而降低勝率。",
        }]

    final_items = []

    main_item = items[0]
    main_item["推薦等級"] = "主推"
    final_items.append(main_item)

    secondary_items = [
        item for item in items[1:]
        if item.get("信心分數數值", 0) >= SECONDARY_MIN_SCORE
    ]

    for i, item in enumerate(secondary_items[:2], start=1):
        item["推薦等級"] = f"副推 {i}"
        final_items.append(item)

    return final_items

def final_recommendations_html(predictions):
    recs = build_final_recommendations(predictions)

    if not recs:
        return "<p class='empty'>目前沒有足夠盤口產生最終推薦。</p>"

    html_parts = []

    for item in recs:
        if item.get("推薦等級") == "PASS":
            html_parts.append(f"""
            <div class="final-card">
                <div class="final-level">今日觀望｜不硬推</div>
                <div class="final-game">{item['比賽']}</div>
                <div class="final-pick">{item['推薦']}</div>
                <div class="final-score">
                    最高信心：{item['信心分數']}｜
                    優勢：{item['預測優勢']} 分｜
                    {confidence_label(item.get('信心分數數值', 0))}
                </div>
                <div class="final-reason">
                    {item['理由']}
                </div>
            </div>
            """)
            continue

        main_class = " final-main" if item["推薦等級"] == "主推" else ""

        away_name = item["比賽"].split(" vs ")[0]
        home_name = item["比賽"].split(" vs ")[1]

        html_parts.append(f"""
        <div class="final-card{main_class}">
            <div class="final-level">{final_level_display(item['推薦等級'])}｜{item['類型']}</div>
            <div class="final-game">{short_name(away_name)} vs {short_name(home_name)}</div>
            <div class="final-pick">{pick_short_name(item['推薦'])}</div>
            <div class="final-score">
                信心：{item['信心分數']}｜
                優勢：{item['預測優勢']} 分｜
                盤口：{item.get('盤口配合', '盤口資料不足')}｜
                {confidence_label(item.get('信心分數數值', 0))}
            </div>

            <div class="final-reason">
                {item['理由']}
            </div>
        </div>
        """)

    return "\n".join(html_parts)


def pick_badge_text(row, pick_type):
    if pick_type == "total":
        pick = str(row.get("大小分推薦", "無推薦"))
        edge = safe_float(row.get("大小分優勢"), 0)
    else:
        pick = str(row.get("讓分推薦", "無推薦"))
        edge = safe_float(row.get("讓分優勢"), 0)

    if "無盤口" in pick:
        return "暫不推薦"

    score = confidence_score(edge)
    return f"{score}%（{confidence_label(score)}）"


def simple_game_cards(predictions):
    if predictions is None or predictions.empty:
        return "<p class='empty'>明日沒有抓到 NBA 比賽，或資料源暫時沒有回傳。</p>"

    cards = []
    for _, row in predictions.iterrows():
        total_badge = pick_badge_text(row, "total")
        spread_badge = pick_badge_text(row, "spread")

        cards.append(f"""
        <div class="game-card">
            <div class="game-time">{row.get('台灣開賽時間', '')}</div>
            <div class="market-line">
                狀態：
                {row.get('live_status', '')}
            </div>
            <div class="market-line">
                賽事資訊：
                {row.get('series_info', '')}
            </div>
            <div class="matchup">{short_name(row.get('客隊'))} <span>vs</span> {short_name(row.get('主隊'))}</div>
            <div class="pick-grid">
                <div class="pick-box main-pick">
                    <div class="pick-label">大小分推薦</div>
                    <div class="pick-value">{row.get('大小分推薦', '無推薦')}</div>
                    <div class="pick-note">{total_badge}｜優勢 {row.get('大小分優勢', 0)} 分</div>
                </div>
                <div class="pick-box">
                    <div class="pick-label">讓分推薦</div>
                    <div class="pick-value">{pick_short_name(row.get('讓分推薦', '無推薦'))}</div>
                    <div class="pick-note">{spread_badge}｜優勢 {row.get('讓分優勢', 0)} 分｜{edge_level(row.get('讓分優勢', 0))}</div>
                </div>
            </div>
            <div class="detail-line">
                預測總分：{row.get('預測總分', '')}｜
                預測主隊分差：{row.get('預測主隊分差', '')}
            </div>

            <div class="market-line">
                主隊近10場：
                {row.get('主隊近10場', '')}
            </div>

            <div class="market-line">
                客隊近10場：
                {row.get('客隊近10場', '')}
            </div>

            <div class="market-line">
                主隊近況分數：
                {row.get('主隊近況分數', '')}｜
                客隊近況分數：
                {row.get('客隊近況分數', '')}
            </div>

            <div class="market-line">
                主客場近況優勢：
                {row.get('主客場近況優勢', 0)}
            </div>

            <div class="market-line">
                傷兵修正：
                主隊 {row.get('主隊傷兵修正', 0)}｜
                客隊 {row.get('客隊傷兵修正', 0)}｜
                影響 {row.get('傷兵影響', 0)}
            </div>

            <div class="market-line">
                傷兵備註：
                主隊 {row.get('主隊傷兵備註', '')}｜
                客隊 {row.get('客隊傷兵備註', '')}
            </div>

            <div class="market-line">
                市場判斷：
                {row.get('市場偏向', '')}
            </div>

            <div class="market-line">
                市場與預測：
                {row.get('市場與預測', '')}
            </div>
        </div>
        """)

    return "\n".join(cards)


def build_top3_cards(df, type_name):
    if df is None or df.empty:
        return "<p class='empty'>沒有推薦。</p>"

    cards = []
    for _, r in df.head(3).iterrows():
        rank = len(cards) + 1
        pick = r.get(f"{type_name}推薦", "")
        edge = r.get(f"{type_name}優勢", 0)
        score = confidence_score(edge)
        cards.append(f"""
        <div class="top3-card">
            <div class="top3-rank">#{rank} {type_name}</div>

            <div class="top3-game">
                {short_name(r.get('客隊'))} vs {short_name(r.get('主隊'))}
            </div>

            <div class="top3-pick">{pick_short_name(pick)}</div>

            <div class="top3-info">
                信心分數 {score}%｜
                優勢 {edge} 分｜
                {edge_level(edge)}
            </div>
        </div>
        """)
    return "\n".join(cards)

def generate_html_report(predictions, yesterday_verify, win_rates):
    summary_text = build_summary_text(win_rates)

    # ===== 歷史紀錄 =====
    history_html = "<p class='empty'>目前沒有歷史紀錄。</p>"

    if PREDICTION_LOG_CSV.exists():
        history_df = load_prediction_log()

        if len(history_df) > 0:
            history_show_df = history_df.tail(30).copy()

            # 只保留新版 Top3：主推 / 副推
            if "推薦等級" in history_show_df.columns:
                history_show_df = history_show_df[
                    history_show_df["推薦等級"]
                    .fillna("")
                    .astype(str)
                    .str.contains("主推|副推", na=False)
                ].copy()
            else:
                history_show_df = pd.DataFrame()

            # 排除今天與未來，只留已可驗證資料
            if "預測目標日期" in history_show_df.columns:
                history_show_df["日期檢查"] = pd.to_datetime(history_show_df["預測目標日期"], errors="coerce").dt.date
                history_show_df = history_show_df[history_show_df["日期檢查"] <= TODAY_TW].copy()
                history_show_df = history_show_df.drop(columns=["日期檢查"])

            # 最新在上
            if "預測目標日期" in history_show_df.columns:
                history_show_df["排序日期"] = pd.to_datetime(history_show_df["預測目標日期"], errors="coerce")
                history_show_df = history_show_df.sort_values("排序日期", ascending=False).copy()
                history_show_df = history_show_df.drop(columns=["排序日期"])

            if len(history_show_df) > 0:
                # 日期
                if "預測目標日期" in history_show_df.columns:
                    history_show_df["日期"] = history_show_df["預測目標日期"]
                else:
                    history_show_df["日期"] = "—"

                # 隊伍
                if "客隊" in history_show_df.columns and "主隊" in history_show_df.columns:
                    history_show_df["隊伍"] = (
                        history_show_df["客隊"].apply(short_name)
                        + " vs "
                        + history_show_df["主隊"].apply(short_name)
                    )
                else:
                    history_show_df["隊伍"] = "—"

                # 抓比分
                results_list = []
                if "預測目標日期" in history_show_df.columns:
                    for d in history_show_df["預測目標日期"].dropna().unique():
                        try:
                            d = pd.to_datetime(d).date()
                            res = fetch_espn_games_by_taiwan_date(d)
                            if not res.empty:
                                results_list.append(res)
                        except Exception:
                            continue

                if results_list and "game_id" in history_show_df.columns:
                    results_df = pd.concat(results_list, ignore_index=True)

                    if "game_id" in results_df.columns:
                        results_df["game_id"] = results_df["game_id"].astype(str)
                        history_show_df["game_id"] = history_show_df["game_id"].astype(str)

                        keep_cols = [c for c in ["game_id", "away_score", "home_score", "completed"] if c in results_df.columns]

                        history_show_df = history_show_df.merge(
                            results_df[keep_cols],
                            on="game_id",
                            how="left"
                        )

                # 比分
                if "away_score" in history_show_df.columns and "home_score" in history_show_df.columns:
                    history_show_df["比賽分數"] = (
                        history_show_df["away_score"].fillna("-").astype(str)
                        + "-"
                        + history_show_df["home_score"].fillna("-").astype(str)
                    )
                else:
                    history_show_df["比賽分數"] = "—"

            history_show_df["預測"] = (
                history_show_df["推薦等級"].apply(final_level_display).astype(str)
                + "｜"
                + history_show_df["推薦內容"].apply(pick_short_name).astype(str)
            )
            history_show_df["rank_sort"] = history_show_df["推薦等級"].apply(rec_level_order)

            history_show_df = history_show_df.sort_values(
                ["預測目標日期", "rank_sort"],
                ascending=[False, True]
            )

            history_show_df = history_show_df.drop(
                columns=["rank_sort"]
            )

            # 預測結果（新版：一筆推薦一列）
            def calc_history_result(row):
                away = safe_float(row.get("away_score"), None)
                home = safe_float(row.get("home_score"), None)

                if not row.get("completed", False):
                    return "—"

                if away is None or home is None:
                    return "—"

                if away == 0 and home == 0:
                    return "—"

                rec_type = str(row.get("推薦類型", ""))
                rec_pick = str(row.get("推薦內容", ""))

                # 大小分
                if rec_type == "大小分":
                    total_line = safe_float(row.get("total"), None)

                    if total_line is None:
                        return "—"

                    actual_total = away + home

                    if actual_total == total_line:
                        return "➖ 走水"

                    if "大分" in rec_pick:
                        return "✅ 過" if actual_total > total_line else "❌ 沒過"

                    if "小分" in rec_pick:
                        return "✅ 過" if actual_total < total_line else "❌ 沒過"

                    return "—"

                # 讓分
                if rec_type == "讓分":
                    home_spread = safe_float(row.get("home_spread"), None)

                    if home_spread is None:
                        return "—"

                    actual_home_margin = home - away
                    home_cover_value = actual_home_margin + home_spread

                    if home_cover_value == 0:
                        return "➖ 走水"

                    if str(row.get("主隊", "")) in rec_pick:
                        return "✅ 過" if home_cover_value > 0 else "❌ 沒過"

                    if str(row.get("客隊", "")) in rec_pick:
                        return "✅ 過" if home_cover_value < 0 else "❌ 沒過"

                    return "—"

                return "—"

            history_show_df["預測結果"] = history_show_df.apply(
                calc_history_result,
                axis=1
            )

            # 空資料保護：清掉舊紀錄後，可能暫時沒有可顯示的歷史資料
            for col in ["日期", "隊伍", "比賽分數", "預測", "預測結果"]:
                if col not in history_show_df.columns:
                    history_show_df[col] = "—"

            history_show_df = history_show_df[[
                "日期",
                "隊伍",
                "比賽分數",
                "預測",
                "預測結果"
            ]]

            history_html = history_show_df.to_html(
                index=False,
                escape=False,
                classes="data-table"
            )

        else:
            history_html = "<p class='empty'>目前沒有歷史紀錄。</p>"
    else:
        history_html = "<p class='empty'>找不到 prediction_log_v3.csv。</p>"

    # ===== PASS 歷史紀錄 =====
    pass_history_html = "<p class='empty'>目前沒有 PASS 紀錄。</p>"

    if PREDICTION_LOG_CSV.exists():
        pass_df = load_prediction_log()

        if not pass_df.empty and "推薦等級" in pass_df.columns:
            pass_df = pass_df[
                pass_df["推薦等級"].fillna("").astype(str).eq("PASS")
            ].copy()

            if not pass_df.empty:
                if "預測目標日期" in pass_df.columns:
                    pass_df["排序日期"] = pd.to_datetime(pass_df["預測目標日期"], errors="coerce")
                    pass_df = pass_df.sort_values("排序日期", ascending=False).drop(columns=["排序日期"])

                for col in ["預測目標日期", "推薦內容", "信心分數", "預測優勢", "盤口配合", "優勢級距"]:
                    if col not in pass_df.columns:
                        pass_df[col] = "—"

                pass_df = pass_df[[
                    "預測目標日期",
                    "推薦內容",
                    "信心分數",
                    "預測優勢",
                    "盤口配合",
                    "優勢級距",
                ]].head(30)

                pass_df = pass_df.rename(columns={
                    "預測目標日期": "日期",
                    "推薦內容": "內容",
                })

                pass_history_html = pass_df.to_html(
                    index=False,
                    escape=False,
                    classes="data-table"
                )

    # ===== 明日預測 =====
    if predictions.empty:
        top_total_html = "<p class='empty'>沒有大小分候選。</p>"
        top_spread_html = "<p class='empty'>沒有讓分候選。</p>"
        all_games_html = "<p class='empty'>明日沒有抓到 NBA 比賽。</p>"
    else:
        total_rank = predictions[predictions["大小分推薦"] != "無盤口"].sort_values("大小分優勢", ascending=False).copy()
        spread_rank = predictions[predictions["讓分推薦"] != "無盤口"].sort_values("讓分優勢", ascending=False).copy()

        top_total_html = build_top3_cards(total_rank, "大小分")
        top_spread_html = build_top3_cards(spread_rank, "讓分")
        all_games_html = simple_game_cards(predictions)

    # ===== 昨日驗證 =====
    if not yesterday_verify.empty:
        y_df = yesterday_verify.copy()

        y_df["日期"] = y_df["台灣開賽時間"].astype(str).str[:10]
        y_df["預測"] = (
            y_df["推薦等級"].apply(final_level_display).astype(str)
            + "｜"
            + y_df["推薦內容"].apply(pick_short_name).astype(str)
        )

        y_df["預測結果"] = y_df["結果"]

        y_df["隊伍"] = (
            y_df["客隊"].astype(str)
            + " vs "
            + y_df["主隊"].astype(str)
        )
        y_df = y_df[[
            "日期",
            "隊伍",
            "比分",
            "預測",
            "預測結果"
        ]]

        yesterday_table = y_df.to_html(
            index=False,
            escape=False,
            classes="data-table"
        )
    else:
        yesterday_table = "<p class='empty'>昨日沒有資料</p>"

    html = f"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<title>NBA 預測分析報告 v3</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", Arial, sans-serif;
    background: #f5f2ec;
    color: #2b2b2b;
    margin: 0;
    padding: 22px;
}}

.container {{
    max-width: 1100px;
    margin: 0 auto;
}}

h1 {{
    margin: 0;
    font-size: 30px;
}}

h2 {{
    margin: 26px 0 12px;
    font-size: 22px;
}}

.subtitle {{
    color: #777;
    margin-top: 8px;
}}

.verify-summary {{
    background: #efe3d1;
    border: 1px solid #c7a46b;
    border-radius: 14px;
    padding: 14px 16px;
    margin: 12px 0;
    font-size: 18px;
    font-weight: 900;
}}

.cards {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
}}

.card,
.section,
.game-card,
.final-card {{
    background: #fffdf8;
    border: 1px solid #eadfce;
    border-radius: 16px;
    box-shadow: 0 4px 14px rgba(90, 65, 30, 0.08);
}}

.card {{
    padding: 15px;
}}

.card .label {{
    color: #7c6f5f;
    font-size: 14px;
}}

.card .value {{
    font-size: 22px;
    font-weight: 800;
    margin-top: 5px;
}}

.section {{
    padding: 16px;
    margin-bottom: 18px;
    overflow-x: auto;
}}

.game-card {{
    padding: 18px;
    margin-bottom: 14px;
}}

.game-time {{
    color: #8a7a66;
    font-size: 14px;
}}

.matchup {{
    font-size: 22px;
    font-weight: 800;
    margin: 6px 0 14px;
}}

.pick-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 12px;
}}

.pick-box {{
    background: #f8f1e7;
    border-radius: 14px;
    padding: 14px;
}}

.main-pick {{
    background: #efe3d1;
}}

.pick-label {{
    color: #7b6b59;
    font-size: 14px;
    margin-bottom: 5px;
}}

.pick-value {{
    font-size: 20px;
    font-weight: 800;
}}

.pick-note,
.detail-line,
.market-line {{
    color: #6c6257;
    font-size: 14px;
    margin-top: 8px;
    line-height: 1.5;
}}

.final-wrap {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 14px;
    margin-bottom: 18px;
}}

.final-card {{
    padding: 16px;
}}

.final-main {{
    background: #efe3d1;
    border: 2px solid #c7a46b;
}}

.final-level {{
    color: #7b6b59;
    font-size: 14px;
    font-weight: 800;
}}

.final-game {{
    font-size: 17px;
    font-weight: 800;
    margin-top: 8px;
}}

.final-pick {{
    font-size: 23px;
    font-weight: 900;
    margin-top: 8px;
}}

.final-score,
.final-reason {{
    color: #6c6257;
    font-size: 14px;
    margin-top: 8px;
    line-height: 1.5;
}}

.top3-card {{
    background: #f8f1e7;
    border-radius: 14px;
    padding: 14px;
    margin-bottom: 10px;
}}

.top3-rank {{
    font-size: 13px;
    color: #7b6b59;
    font-weight: 800;
}}

.top3-game {{
    font-size: 16px;
    font-weight: 800;
    margin-top: 6px;
}}

.top3-pick {{
    font-size: 19px;
    font-weight: 900;
    margin-top: 6px;
}}

.top3-info {{
    font-size: 13px;
    color: #6c6257;
    margin-top: 6px;
}}

.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
    background: #fffdf8;
}}

.data-table th {{
    background: #4a3525;
    color: white;
    padding: 10px;
    white-space: nowrap;
}}

.data-table td {{
    border-bottom: 1px solid #eee3d3;
    padding: 10px;
    vertical-align: top;
}}

.data-table tr:nth-child(even) td {{
    background: #faf5ed;
}}

.history-box {{
    margin: 20px 0;
    background: #fffdf8;
    border: 1px solid #eadfce;
    border-radius: 16px;
    padding: 16px;
}}

.history-box summary {{
    font-size: 18px;
    font-weight: 800;
    cursor: pointer;
}}

.empty {{
    color: #8a7a66;
}}

.note {{
    color: #6c6257;
    font-size: 14px;
    line-height: 1.7;
}}

.result-win {{
    color: #15803d;
    font-weight: 900;
}}

.result-loss,
.result-lose {{
    color: #b91c1c;
    font-weight: 900;
}}

.result-push {{
    color: #92400e;
    font-weight: 900;
}}

.result-pending {{
    color: #6b7280;
    font-weight: 800;
}}

@media (max-width: 600px) {{
    body {{
        padding: 12px;
    }}

    h1 {{
        font-size: 24px;
    }}

    h2 {{
        font-size: 19px;
    }}

    .section {{
        padding: 10px;
    }}

    .data-table {{
        font-size: 12px;
    }}

    .data-table th,
    .data-table td {{
        padding: 6px;
    }}

    .matchup {{
        font-size: 19px;
    }}
}}
</style>
</head>

<body>
<div class="container">
    <h1>NBA 明日預測報告</h1>
    <div class="subtitle">產生時間：{tw_now_text()}｜預測日期：{TOMORROW_TW}</div>

    <h2>明日推薦</h2>

    <div class="final-wrap">
        {final_recommendations_html(predictions)}
    </div>

    <h2>昨日驗證</h2>
    {yesterday_top3_summary_html(yesterday_verify)}

    <div class="section">
        {yesterday_table}
    </div>

    <div class="cards">
        <div class="card">
            <div class="label">主推（近 7 筆）</div>
            <div class="value">{win_rates.get('main_7')}</div>
        </div>

        <div class="card">
            <div class="label">主推（近 30 筆）</div>
            <div class="value">{win_rates.get('main_30')}</div>
        </div>

        <div class="card">
            <div class="label">Top3（近 7 筆）</div>
            <div class="value">{win_rates.get('top3_7')}</div>
        </div>

        <div class="card">
            <div class="label">Top3（近 30 筆）</div>
            <div class="value">{win_rates.get('top3_30')}</div>
        </div>

        <div class="card">
            <div class="label">總勝率（全部累積）</div>
            <div class="value">{win_rates.get('overall_all', '無資料')}</div>
        </div>
    </div>

    <details class="history-box">
        <summary>📂 查看歷史推薦紀錄（最近 30 筆）</summary>
        <div class="section" style="margin-top:14px;">
            {history_html}
        </div>
    </details>

    <details class="history-box">
        <summary>🟡 查看 PASS 觀望紀錄（最近 30 筆）</summary>
        <div class="section" style="margin-top:14px;">
            {pass_history_html}
        </div>
    </details>

    <h2>大小分 Top 3</h2>
    <div class="section">
        {top_total_html}
    </div>

    <h2>讓分 Top 3</h2>
    <div class="section">
        {top_spread_html}
    </div>

    <h2>每場預測</h2>
    <div>
        {all_games_html}
    </div>
</div>
</body>
</html>
"""

    REPORT_HTML.write_text(html, encoding="utf-8")

def main():
    total_start = time.time()

    print("程式開始執行：daily_report_v3.py")
    print("台灣現在：", tw_now_text())
    print("台灣今天：", TODAY_TW)
    print("預測明天：", TOMORROW_TW)
    print("驗證昨天：", YESTERDAY_TW)

    # ===== 自動傷兵 =====
    print("\n正在抓取自動傷兵資料...")
    save_auto_injury_adjustments(TOMORROW_TW)

    # ===== 明日預測 =====
    t1 = time.time()

    print("\n正在建立明日預測...")
    predictions = build_tomorrow_predictions()

    print("明日比賽場次：", len(predictions))

    save_line_snapshots(predictions)
    predictions = add_line_movement_columns(predictions)
    print("盤口快照已更新，盤口變動已計算")

    print("明日預測耗時：", round(time.time() - t1, 2), "秒")

    if not predictions.empty:
        show_cols = [
            "台灣開賽時間", "客隊", "主隊",
            "away_spread", "home_spread", "total",
            "預測總分", "大小分推薦", "大小分優勢",
            "讓分推薦", "讓分優勢",
            "盤口變動摘要", "讓分盤口方向", "大小分盤口方向",
        ]

        print("\n======== 明日預測 ========")
        print(predictions[[c for c in show_cols if c in predictions.columns]])

    # ===== 儲存紀錄 =====
    t2 = time.time()

    print("\n正在儲存預測紀錄...")
    save_prediction_log(predictions)

    print("儲存紀錄耗時：", round(time.time() - t2, 2), "秒")

    # ===== 昨日驗證 =====
    t3 = time.time()

    print("\n正在驗證昨日預測...")
    yesterday_verify = verify_yesterday_predictions()
    yesterday_verify = clean_yesterday_verify(yesterday_verify)

    print("昨日驗證耗時：", round(time.time() - t3, 2), "秒")

    if yesterday_verify.empty:
        print("昨日驗證：目前沒有可驗證資料。")
    else:
        print("\n======== 昨日驗證 ========")
        print(yesterday_verify)

    # ===== 勝率 =====
    t4 = time.time()

    print("\n正在計算近 7 筆 / 近 30 筆勝率...")
    win_rates = calculate_win_rates()

    print("勝率統計：", win_rates)

    edge_bucket_rates = calculate_edge_bucket_rates()
    if edge_bucket_rates.empty:
        print("優勢級距勝率：目前無足夠資料")
    else:
        print("\n======== 優勢級距勝率回測 ========")
        print(edge_bucket_rates.to_string(index=False))

    confidence_threshold_rates = calculate_confidence_threshold_rates()
    if confidence_threshold_rates.empty:
        print("信心分數門檻勝率：目前無足夠資料")
    else:
        print("\n======== 信心分數門檻勝率回測 ========")
        print(confidence_threshold_rates.to_string(index=False))

    line_alignment_rates = calculate_line_alignment_rates()
    if line_alignment_rates.empty:
        print("順盤 / 逆盤勝率：目前無足夠資料")
    else:
        print("\n======== 順盤 / 逆盤勝率回測 ========")
        print(line_alignment_rates.to_string(index=False))

    print("勝率計算耗時：", round(time.time() - t4, 2), "秒")

    # ===== HTML =====
    t5 = time.time()

    print("\n正在產生 HTML 報告...")
    generate_html_report(predictions, yesterday_verify, win_rates)

    print("HTML 報告已產生：", REPORT_HTML)
    print("HTML 產生耗時：", round(time.time() - t5, 2), "秒")

    print("\n完成。")
    print("總耗時：", round(time.time() - total_start, 2), "秒")


if __name__ == "__main__":
    main()
