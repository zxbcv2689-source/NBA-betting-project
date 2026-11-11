# daily_report_v3.py
# NBA 預測分析專案
# NBA_CROSS_SEASON_FIX_TESTED_20261108
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

_ORIGINAL_PD_READ_CSV = pd.read_csv

def _read_csv_without_bom(*args, **kwargs):
    df = _ORIGINAL_PD_READ_CSV(*args, **kwargs)
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    return df

pd.read_csv = _read_csv_without_bom

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
    "Memphis Grizzlies": "灰熊",
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
    """每日更新近況，避免 Git checkout 修改時間讓上一季快取永久有效。

    舊快取第一次先全量重建；之後每日只增量更新最近幾天，
    ESPN 暫時不可用時沿用當季舊資料，不偽稱已更新成功。
    """
    global RECENT_GAMES_CACHE
    if RECENT_GAMES_CACHE is not None:
        return RECENT_GAMES_CACHE

    today_date = pd.to_datetime(today_tw).date()
    current_season = stable_season_year(today_date)
    cache_df = load_recent_games_cache()
    base = pd.DataFrame()
    built_on = None

    if not cache_df.empty and "台灣日期" in cache_df.columns:
        base = cache_df.copy()
        dates = pd.to_datetime(base["台灣日期"], errors="coerce")
        keep = (
            dates.notna()
            & (dates.dt.date >= today_date - timedelta(days=60))
            & dates.dt.date.apply(lambda d: stable_season_year(d) == current_season if pd.notna(d) else False)
        )
        base = base.loc[keep].copy()
        if "_cache_built_on" in cache_df.columns:
            dates_built = pd.to_datetime(cache_df["_cache_built_on"], errors="coerce").dropna()
            if not dates_built.empty:
                built_on = dates_built.max().date()

    if not base.empty and built_on == today_date:
        print("已載入今日當季 recent_games_cache.csv")
        RECENT_GAMES_CACHE = base
        return RECENT_GAMES_CACHE

    # 沒有當季快取、沒有可信建立日期、或長期未執行時需完整補抓。
    if base.empty or built_on is None or (today_date - built_on).days > 7 or built_on > today_date:
        print("當季近期戰績快取需重建，重新抓取近60天資料...")
        fresh = build_recent_games_cache(today_date, lookback_days=60)
    else:
        days = max(2, min(7, (today_date - built_on).days + 2))
        print(f"更新近況快取：補抓最近 {days} 天")
        fresh = build_recent_games_cache(today_date, lookback_days=days)

    if fresh is None or fresh.empty:
        if not base.empty:
            print("近期比賽未抓到新賽果，沿用當季既有快取並標示未確認更新")
            RECENT_GAMES_CACHE = base
            return RECENT_GAMES_CACHE
        RECENT_GAMES_CACHE = pd.DataFrame()
        return RECENT_GAMES_CACHE

    frames = [fresh] if base.empty else [base, fresh]
    result = pd.concat(frames, ignore_index=True)
    result = result.drop_duplicates(subset=["台灣日期", "客隊", "主隊"], keep="last")
    result = result.sort_values("台灣日期", ascending=False).reset_index(drop=True)
    result["_cache_built_on"] = str(today_date)
    RECENT_GAMES_CACHE = result

    try:
        result.to_csv(RECENT_GAMES_CACHE_CSV, index=False, encoding="utf-8-sig")
        print("已儲存當季近況快取：", len(result), "筆")
    except Exception as error:
        print("儲存近期快取失敗：", error)
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
            if target_tw_date not in [TODAY_TW, YESTERDAY_TW, TOMORROW_TW, TODAY_TW - timedelta(days=2)]:
                # 過去的賽程如果當時尚未完賽，不能永久沿用那份舊快取。
                complete = (
                    cached_rows["completed"].fillna(False).astype(str).str.lower().isin(("true", "1", "yes")).all()
                    if "completed" in cached_rows.columns else False
                )
                if complete:
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

    # 只使用當季賽事計算近期動態強度，避免去年季後賽被當成新球季近況。
    date_col = "台灣日期" if "台灣日期" in df.columns else "台灣開賽時間"
    if date_col in df.columns:
        dates = pd.to_datetime(df[date_col], errors="coerce")
        df = df[dates.apply(lambda d: pd.notna(d) and stable_season_year(d) == stable_season_year(TODAY_TW))].copy()
        if df.empty:
            return 0.0

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
    if games is None or games.empty:
        return pd.DataFrame() if games is None else games

    # 午夜後的「今日賽程」只計算尚未開賽的場次，絕不事後產生預測。
    if TOMORROW_TW == TODAY_TW:
        now_text = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d %H:%M")
        games = games[games["台灣開賽時間"].astype(str) > now_text].copy()
        if games.empty:
            return games

    games = merge_lines(games)
    get_recent_games_cache(TODAY_TW)  # 先更新再計算固定強度以外的動態調整
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


def parse_confidence_percent(value):
    """
    統一把信心分數轉成百分比：
    - 68% -> 68
    - 68 -> 68
    - 68/100 -> 68
    - 34/50 -> 68
    """
    try:
        if value is None:
            return None
        text = str(value).strip().replace("％", "%")
        if not text or text.lower() == "nan":
            return None
        if "/" in text:
            a, b = text.replace("%", "").split("/", 1)
            return float(a) / float(b) * 100
        return float(text.replace("%", ""))
    except Exception:
        return None


def get_recommendation_status(confidence):
    pct = parse_confidence_percent(confidence)
    if pct is None:
        return "推薦"
    return "不推薦" if pct < 70 else "推薦"


def is_effective_pick(row):
    """
    勝率統計用：以「筆數」往前抓滿。
    排除無法驗證、未賽、空值，但不排除不推薦。
    """
    result = str(row.get("預測結果", "")).strip()
    pick = str(row.get("推薦內容", "")).strip()
    if not pick or pick.lower() == "nan":
        return False
    if "PASS" in pick.upper() and "原候選" not in pick:
        return False
    return result in ["過", "沒過", "走水"]


def save_prediction_log(new_predictions):
    if new_predictions.empty:


        return

    # 每個目標日期最多儲存一次 Top3；隔天午夜後保留前一天的賽前紀錄。
    previous = load_prediction_log()
    if not previous.empty and "預測目標日期" in previous.columns:
        saved = previous[previous["預測目標日期"].astype(str).eq(str(TOMORROW_TW))]
        if not saved.empty:
            print(f"{TOMORROW_TW} 原始賽前預測已存 {len(saved)} 筆，保留、不重排、不覆蓋")
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
        "推薦等級", "推薦狀態", "推薦類型", "推薦內容", "信心分數", "預測優勢",
        "盤口配合", "優勢絕對值", "優勢級距",
    ]

    rows = []

    for item in final_recs:
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
        row["推薦狀態"] = item.get("推薦狀態", "推薦")
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

        existing_same_day = old[
            old["報告日期"].astype(str).eq(str(TODAY_TW))
            & old["預測目標日期"].astype(str).eq(str(TOMORROW_TW))
        ].copy()

        if not existing_same_day.empty:
            has_legacy_pass = (
                existing_same_day["推薦等級"].fillna("").astype(str).eq("PASS").any()
                or existing_same_day["推薦類型"].fillna("").astype(str).eq("觀望").any()
                or existing_same_day["推薦狀態"].fillna("").astype(str).eq("").all()
            )

            if has_legacy_pass:
                print("偵測到今日舊版 PASS / 舊欄位紀錄，改用新版 Top3 + 不推薦資料重建今日紀錄。")
                keep_old = old[
                    ~(
                        old["報告日期"].astype(str).eq(str(TODAY_TW))
                        & old["預測目標日期"].astype(str).eq(str(TOMORROW_TW))
                    )
                ].copy()
                combined = pd.concat([keep_old, save_df], ignore_index=True)
            else:
                print("今日預測紀錄已存在，保留原始紀錄，不覆蓋 prediction_log_v3.csv")
                combined = old.copy()
        else:
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

    if "推薦狀態" in target_log.columns:
        target_log = target_log[
            target_log["推薦狀態"].fillna("推薦").astype(str).eq("推薦")
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
        rec_level = normalize_rec_level(row.get("推薦等級", ""))
        rec_type = str(row.get("推薦類型", "")).strip()
        rec_pick = str(row.get("推薦內容", "")).strip()

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
    output = {
        "main_7": "0/0（0.0%）",
        "main_30": "0/0（0.0%）",
        "top3_7": "0/0（0.0%）",
        "top3_30": "0/0（0.0%）",
        "overall_7": "0/0（0.0%）",
        "overall_30": "0/0（0.0%）",
        "overall_all": "無資料",
        "year_current": "0/0（0.0%）",
        "season_current": "0/0（0.0%）",
        "finals_current": "0/0（0.0%）",
        "season_label": stable_season_label(TODAY_TW),
        "year_label": str(TODAY_TW.year),
    }

    all_df = stable_build_verified_rows(include_no_recommend=False)

    if all_df is None or all_df.empty:
        return output

    all_df = all_df[
        all_df["推薦結果"].astype(str).isin(["過", "沒過", "走水"])
    ].copy()

    if all_df.empty:
        return output

    all_df["_date"] = pd.to_datetime(all_df["預測目標日期"], errors="coerce")
    all_df = all_df.sort_values("_date").drop(columns=["_date"], errors="ignore")

    current_df = stable_filter_current_season(all_df)
    for label, count in {"7": 7, "30": 30}.items():
        main_recent_df = current_df[
            current_df["推薦等級"].astype(str).str.contains("主推", na=False)
        ].tail(count).copy()

        top3_recent_df = current_df[
            current_df["推薦等級"].astype(str).str.contains("主推|副推", na=False)
        ].tail(count).copy()

        output[f"main_{label}"] = stable_rate_text(main_recent_df)
        output[f"top3_{label}"] = stable_rate_text(top3_recent_df)
        output[f"overall_{label}"] = stable_rate_text(top3_recent_df)

    output["overall_all"] = stable_rate_text(all_df)
    output["year_current"] = stable_rate_text(stable_filter_current_year(all_df))
    output["season_current"] = stable_rate_text(stable_filter_current_season(all_df))
    output["finals_current"] = stable_rate_text(stable_filter_finals(all_df))

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
# 穩定版報告補強：年度 / 本季 / NBA Finals / 缺漏檢查
# =========================

def stable_season_label(date_value=None):
    """
    顯示用賽季名稱：
    - 2026 年 1～9 月：2026 賽季
    - 2026 年 10～12 月：2027 賽季
    也就是用「總冠軍產生年份 / 賽季結束年份」作為頁面顯示。
    """
    try:
        d = pd.to_datetime(date_value if date_value is not None else TODAY_TW).date()
    except Exception:
        d = TODAY_TW

    season_year = d.year + 1 if d.month >= 10 else d.year
    return f"{season_year} 賽季"
def stable_short_finals_game_label(text):
    text = str(text).strip()

    for n in range(1, 8):
        if f"Game {n}" in text:
            return f"G{n}"

    return text or "Finals"


def stable_score_text(row):
    away = safe_float(row.get("away_score"), None)
    home = safe_float(row.get("home_score"), None)

    if away is None or home is None:
        return "—"

    return f"{safe_int(away)}-{safe_int(home)}"


def stable_parse_original_pick(rec_type, rec_pick):
    rec_type = str(rec_type).strip()
    pick = str(rec_pick).strip()

    if "原最佳候選：" in pick:
        pick = pick.split("原最佳候選：", 1)[1].strip()
    elif "原候選：" in pick:
        pick = pick.split("原候選：", 1)[1].strip()

    if rec_type in ["", "觀望", "PASS"]:
        if any(k in pick for k in ["大分", "小分", "Over", "Under"]):
            rec_type = "大小分"
        else:
            rec_type = "讓分"

    return rec_type, pick


def stable_calc_pick_result(row, allow_original_pick=False):
    away_score = safe_float(row.get("away_score"), None)
    home_score = safe_float(row.get("home_score"), None)

    if away_score is None or home_score is None:
        return "無法驗證"

    if not row.get("completed", False):
        return "未完賽"

    rec_type = str(row.get("推薦類型", "")).strip()
    rec_pick = str(row.get("推薦內容", "")).strip()

    if allow_original_pick:
        rec_type, rec_pick = stable_parse_original_pick(rec_type, rec_pick)

    if rec_type == "大小分":
        total_line = safe_float(row.get("total"), None)

        if total_line is None:
            return "無法驗證"

        actual_total = away_score + home_score

        if actual_total == total_line:
            return "走水"

        if "大分" in rec_pick or "Over" in rec_pick:
            return "過" if actual_total > total_line else "沒過"

        if "小分" in rec_pick or "Under" in rec_pick:
            return "過" if actual_total < total_line else "沒過"

        return "無法驗證"

    if rec_type == "讓分":
        home_spread = safe_float(row.get("home_spread"), None)

        if home_spread is None:
            return "無法驗證"

        actual_home_margin = home_score - away_score
        home_cover_value = actual_home_margin + home_spread

        if home_cover_value == 0:
            return "走水"

        if str(row.get("主隊", "")) in rec_pick:
            return "過" if home_cover_value > 0 else "沒過"

        if str(row.get("客隊", "")) in rec_pick:
            return "過" if home_cover_value < 0 else "沒過"

        return "無法驗證"

    return "無法驗證"


def stable_refresh_finals_games():
    """只在當季 6 月更新 Finals 資料；其他月份直接讀歷史 ESPN cache。"""
    finals_year = stable_season_year(TODAY_TW)
    frames = []
    if TODAY_TW.year == finals_year and TODAY_TW.month == 6:
        current = datetime(finals_year, 6, 1).date()
        while current <= TODAY_TW:
            try:
                df = fetch_espn_games_by_taiwan_date(current)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as error:
                print("Finals 資料補抓失敗：", current, error)
            current += timedelta(days=1)

    cache_df = load_espn_day_cache()
    if cache_df is not None and not cache_df.empty:
        frames.append(cache_df)
    if not frames:
        return pd.DataFrame()
    games = pd.concat(frames, ignore_index=True)
    if "game_id" in games.columns:
        games["game_id"] = games["game_id"].astype(str)
        games = games.drop_duplicates(subset=["game_id"], keep="first")
    return games


def stable_get_finals_games():
    games = stable_refresh_finals_games()
    if games is None or games.empty or "series_info" not in games.columns:
        return pd.DataFrame()

    temp = games.copy()
    date_col = "台灣日期" if "台灣日期" in temp.columns else "台灣開賽時間"
    if date_col not in temp.columns:
        return pd.DataFrame()
    dates = pd.to_datetime(temp[date_col], errors="coerce")
    season_year = stable_season_year(TODAY_TW)
    in_final_month = (dates.dt.year == season_year) & (dates.dt.month == 6)
    marked_finals = temp["series_info"].fillna("").astype(str).str.contains("NBA Finals", case=False, regex=False)
    temp = temp.loc[in_final_month & marked_finals].copy()
    if "台灣開賽時間" in temp.columns and not temp.empty:
        temp["_sort"] = pd.to_datetime(temp["台灣開賽時間"], errors="coerce")
        temp = temp.sort_values("_sort").drop(columns=["_sort"])
    return temp


def stable_build_verified_rows(include_no_recommend=False):
    log = load_prediction_log()

    if log.empty:
        return pd.DataFrame()

    rows = []

    for d in log["預測目標日期"].dropna().unique():
        try:
            check_date = pd.to_datetime(d).date()

            if check_date > TODAY_TW:
                continue

            results = fetch_espn_games_by_taiwan_date(check_date)

            if results is None or results.empty:
                continue

            keep_cols = [
                c for c in [
                    "game_id", "away_score", "home_score", "completed",
                    "status", "series_info"
                ]
                if c in results.columns
            ]

            day_log = log[log["預測目標日期"].astype(str).eq(str(check_date))].copy()

            if day_log.empty:
                continue

            day_log["game_id"] = day_log["game_id"].astype(str)
            results["game_id"] = results["game_id"].astype(str)

            merged = day_log.merge(results[keep_cols], on="game_id", how="left")

            for _, row in merged.iterrows():
                rec_status = str(row.get("推薦狀態", "推薦")).strip() or "推薦"
                rec_level = normalize_rec_level(row.get("推薦等級", ""))
                rec_type = str(row.get("推薦類型", "")).strip()
                rec_pick = str(row.get("推薦內容", "")).strip()

                if not include_no_recommend and rec_status != "推薦":
                    continue

                if rec_level not in ["主推", "副推 1", "副推 2"]:
                    continue

                if str(row.get("推薦等級", "")).upper() == "PASS" or rec_type == "觀望":
                    if not include_no_recommend:
                        continue

                result = stable_calc_pick_result(
                    row,
                    allow_original_pick=(rec_status == "不推薦")
                )

                if result not in ["過", "沒過", "走水"]:
                    continue

                rows.append({
                    "預測目標日期": str(check_date),
                    "推薦等級": rec_level,
                    "推薦狀態": rec_status,
                    "推薦類型": rec_type,
                    "推薦內容": rec_pick,
                    "推薦結果": result,
                    "series_info": str(row.get("series_info", "")),
                    "game_id": str(row.get("game_id", "")),
                })

        except Exception as e:
            print("穩定版勝率略過日期：", d, e)
            continue

    return pd.DataFrame(rows)


def stable_rate_text(df):
    if df is None or df.empty:
        return "0/0（0.0%）"

    temp = df.copy()
    temp = temp[temp["推薦結果"].astype(str).isin(["過", "沒過", "走水"])].copy()

    total = len(temp)
    win = int(temp["推薦結果"].astype(str).eq("過").sum())

    if total == 0:
        return "0/0（0.0%）"

    return f"{win}/{total}（{win / total * 100:.1f}%）"


def stable_filter_current_season(df):
    if df is None or df.empty or "預測目標日期" not in df.columns:
        return pd.DataFrame()

    season = stable_season_label(TODAY_TW)
    temp = df.copy()
    temp["_season"] = temp["預測目標日期"].apply(stable_season_label)

    return temp[temp["_season"].eq(season)].drop(columns=["_season"], errors="ignore")


def stable_filter_current_year(df):
    if df is None or df.empty or "預測目標日期" not in df.columns:
        return pd.DataFrame()

    temp = df.copy()
    temp["_year"] = pd.to_datetime(temp["預測目標日期"], errors="coerce").dt.year

    return temp[temp["_year"].eq(TODAY_TW.year)].drop(columns=["_year"], errors="ignore")


def stable_filter_finals(df):
    if df is None or df.empty:
        return pd.DataFrame()

    if "series_info" not in df.columns:
        return pd.DataFrame()

    current = stable_filter_current_season(df)
    if current.empty:
        return current
    return current[
        current["series_info"].fillna("").astype(str).str.contains("NBA Finals", case=False, na=False)
    ].copy()


def build_data_health_html():
    log = load_prediction_log()
    cache = load_espn_day_cache()
    # 年度封存的資料狀態只統計該賽季，完整 CSV 仍另外保存。
    if log is not None and not log.empty and "預測目標日期" in log.columns:
        log = log[log["預測目標日期"].apply(stable_season_year).eq(stable_season_year(TODAY_TW))].copy()
    if cache is not None and not cache.empty and "台灣日期" in cache.columns:
        cache = cache[cache["台灣日期"].apply(stable_season_year).eq(stable_season_year(TODAY_TW))].copy()
    finals_games = stable_get_finals_games()

    rows = []

    rows.append({
        "項目": "正式報告入口",
        "狀態": "reports/daily_report_v3.html",
        "備註": "以這個檔案為唯一正式報告"
    })

    if log is None or log.empty:
        rows.append({
            "項目": "預測紀錄 CSV",
            "狀態": "無資料",
            "備註": "找不到可統計的 prediction_log_v3.csv"
        })
    else:
        latest_log_date = "—"
        if "預測目標日期" in log.columns:
            latest_log_date = str(log["預測目標日期"].dropna().astype(str).max())

        rows.append({
            "項目": "預測紀錄 CSV",
            "狀態": f"{len(log)} 筆",
            "備註": f"最新預測目標日期：{latest_log_date}"
        })

    if cache is None or cache.empty:
        rows.append({
            "項目": "ESPN 賽果快取",
            "狀態": "無資料",
            "備註": "目前沒有 espn_day_cache.csv 可檢查"
        })
    else:
        latest_cache_date = "—"
        if "台灣日期" in cache.columns:
            latest_cache_date = str(cache["台灣日期"].dropna().astype(str).max())

        rows.append({
            "項目": "ESPN 賽果快取",
            "狀態": f"{len(cache)} 筆",
            "備註": f"最新賽果日期：{latest_cache_date}"
        })

    missing_notes = []

    if finals_games is not None and not finals_games.empty and log is not None and not log.empty:
        log_game_ids = set(log["game_id"].astype(str)) if "game_id" in log.columns else set()

        for _, game in finals_games.iterrows():
            if not bool(game.get("completed", False)):
                continue

            game_id = str(game.get("game_id", ""))
            if game_id and game_id not in log_game_ids:
                date_text = str(game.get("台灣日期", "")) or str(game.get("台灣開賽時間", ""))[:10]
                matchup = f"{short_name(game.get('客隊'))} vs {short_name(game.get('主隊'))}"
                score = stable_score_text(game)
                game_label = stable_short_finals_game_label(game.get("series_info", ""))
                missing_notes.append(f"{date_text}｜{game_label}｜{matchup}｜{score}｜有賽果但沒有預測紀錄")

    if missing_notes:
        display_notes = missing_notes[:10]
        if len(missing_notes) > 10:
            display_notes.append(f"另外還有 {len(missing_notes) - 10} 場未顯示，請檢查 CSV 或 ESPN cache。")

        rows.append({
            "項目": "漏跑 / 未存預測",
            "狀態": f"{len(missing_notes)} 場",
            "備註": "<br>".join(display_notes)
        })
    else:
        rows.append({
            "項目": "漏跑 / 未存預測",
            "狀態": "目前未偵測到 Finals 缺漏",
            "備註": "以目前 ESPN cache 與 prediction_log_v3.csv 比對"
        })

    return pd.DataFrame(rows).to_html(index=False, escape=False, classes="data-table")
def build_finals_report_html():
    finals_games = stable_get_finals_games()
    log = load_prediction_log()

    if finals_games is None or finals_games.empty:
        return "<p class='empty'>目前沒有抓到 NBA Finals 賽程 / 賽果。</p>"

    # ===== 總冠軍 / 系列賽摘要 =====
    wins = {}

    completed_games = finals_games[
        finals_games.get("completed", pd.Series(dtype=bool)).astype(bool)
    ].copy() if "completed" in finals_games.columns else pd.DataFrame()

    if completed_games is not None and not completed_games.empty:
        for _, game in completed_games.iterrows():
            away_team = str(game.get("客隊", "")).strip()
            home_team = str(game.get("主隊", "")).strip()
            away_score = safe_float(game.get("away_score"), None)
            home_score = safe_float(game.get("home_score"), None)

            if not away_team or not home_team or away_score is None or home_score is None:
                continue

            wins.setdefault(away_team, 0)
            wins.setdefault(home_team, 0)

            if away_score > home_score:
                wins[away_team] += 1
            elif home_score > away_score:
                wins[home_team] += 1

    champion_html = ""

    if wins:
        sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
        leader, leader_wins = sorted_wins[0]
        opponent, opponent_wins = sorted_wins[1] if len(sorted_wins) > 1 else ("", 0)

        if leader_wins >= 4:
            champion_html = f"""
            <div class="verify-summary">
                🏆 總冠軍：{short_name(leader)}｜
                系列賽：{short_name(leader)} {leader_wins}-{opponent_wins} {short_name(opponent)}
            </div>
            """
        else:
            champion_html = f"""
            <div class="verify-summary">
                Finals 系列賽目前：{short_name(leader)} {leader_wins}-{opponent_wins} {short_name(opponent)}
            </div>
            """

    rows = []

    for _, game in finals_games.iterrows():
        game_id = str(game.get("game_id", ""))
        date_text = str(game.get("台灣日期", "")) or str(game.get("台灣開賽時間", ""))[:10]
        matchup = f"{short_name(game.get('客隊'))} vs {short_name(game.get('主隊'))}"
        score = stable_score_text(game)
        series_info = stable_short_finals_game_label(game.get("series_info", ""))

        game_log = pd.DataFrame()
        if log is not None and not log.empty and "game_id" in log.columns:
            game_log = log[log["game_id"].astype(str).eq(game_id)].copy()

        if game_log.empty:
            rows.append({
                "日期": date_text,
                "場次": series_info,
                "隊伍": matchup,
                "比分": score,
                "預測狀態": "未存預測",
                "預測": "—",
                "預測結果": "不列入勝率",
            })
            continue

        for _, pick_row in game_log.iterrows():
            merged_row = pick_row.copy()
            for col in ["away_score", "home_score", "completed", "series_info"]:
                merged_row[col] = game.get(col, "")

            rec_status = str(pick_row.get("推薦狀態", "推薦")).strip() or "推薦"
            rec_type, rec_pick = stable_parse_original_pick(
                pick_row.get("推薦類型", ""),
                pick_row.get("推薦內容", "")
            )

            raw_result = stable_calc_pick_result(
                merged_row,
                allow_original_pick=(rec_status == "不推薦")
            )

            if rec_status == "不推薦":
                if raw_result == "沒過":
                    display_result = "✅ 避開成功"
                elif raw_result == "過":
                    display_result = "❌ 避開失敗"
                elif raw_result == "走水":
                    display_result = "➖ 走水"
                else:
                    display_result = "—"
                status_text = "不推薦"
            else:
                display_result = result_display(raw_result)
                status_text = "推薦"

            rows.append({
                "日期": date_text,
                "場次": series_info,
                "隊伍": matchup,
                "比分": score,
                "預測狀態": status_text,
                "預測": f"{final_level_display(pick_row.get('推薦等級', ''))}｜{pick_short_name(rec_pick)}",
                "預測結果": display_result,
            })

    table_html = pd.DataFrame(rows).to_html(index=False, escape=False, classes="data-table")

    verified = stable_build_verified_rows(include_no_recommend=False)
    finals_verified = stable_filter_finals(verified)

    summary = f"""
    <p class="empty">
        NBA Finals 推薦勝率：{stable_rate_text(finals_verified)}｜
        未存預測的 Finals 場次會顯示在表格，但不列入勝率。
    </p>
    """

    return champion_html + summary + table_html
def stable_season_year(date_value=None):
    try:
        d = pd.to_datetime(date_value if date_value is not None else TODAY_TW).date()
    except Exception:
        d = TODAY_TW

    return d.year + 1 if d.month >= 10 else d.year


def stable_get_champion_info():
    finals_games = stable_get_finals_games()

    if finals_games is None or finals_games.empty:
        return {
            "champion": "—",
            "opponent": "—",
            "champion_wins": 0,
            "opponent_wins": 0,
            "series_text": "尚無 Finals 資料",
            "missing_finals": 0,
        }

    log = load_prediction_log()
    log_game_ids = set()

    if log is not None and not log.empty and "game_id" in log.columns:
        log_game_ids = set(log["game_id"].astype(str))

    wins = {}
    missing_finals = 0

    for _, game in finals_games.iterrows():
        completed = bool(game.get("completed", False))
        game_id = str(game.get("game_id", ""))

        if completed and game_id and game_id not in log_game_ids:
            missing_finals += 1

        if not completed:
            continue

        away_team = str(game.get("客隊", "")).strip()
        home_team = str(game.get("主隊", "")).strip()
        away_score = safe_float(game.get("away_score"), None)
        home_score = safe_float(game.get("home_score"), None)

        if not away_team or not home_team or away_score is None or home_score is None:
            continue

        wins.setdefault(away_team, 0)
        wins.setdefault(home_team, 0)

        if away_score > home_score:
            wins[away_team] += 1
        elif home_score > away_score:
            wins[home_team] += 1

    if not wins:
        return {
            "champion": "—",
            "opponent": "—",
            "champion_wins": 0,
            "opponent_wins": 0,
            "series_text": "尚無已完賽 Finals 資料",
            "missing_finals": missing_finals,
        }

    sorted_wins = sorted(wins.items(), key=lambda x: x[1], reverse=True)
    leader, leader_wins = sorted_wins[0]
    opponent, opponent_wins = sorted_wins[1] if len(sorted_wins) > 1 else ("", 0)

    if leader_wins >= 4:
        champion = short_name(leader)
        series_text = f"{short_name(leader)} {leader_wins}-{opponent_wins} {short_name(opponent)}"
    else:
        champion = "尚未產生"
        series_text = f"{short_name(leader)} {leader_wins}-{opponent_wins} {short_name(opponent)}"

    return {
        "champion": champion,
        "opponent": short_name(opponent),
        "champion_wins": leader_wins,
        "opponent_wins": opponent_wins,
        "series_text": series_text,
        "missing_finals": missing_finals,
    }


def stable_build_no_recommend_rows():
    log = load_prediction_log()

    if log is None or log.empty:
        return pd.DataFrame()

    if "推薦狀態" not in log.columns:
        log["推薦狀態"] = ""

    no_df = log[
        log["推薦狀態"].fillna("").astype(str).eq("不推薦")
        | log["推薦等級"].fillna("").astype(str).eq("PASS")
        | log["推薦類型"].fillna("").astype(str).eq("觀望")
    ].copy()

    if no_df.empty:
        return pd.DataFrame()

    rows = []

    for d in no_df["預測目標日期"].dropna().unique():
        try:
            check_date = pd.to_datetime(d).date()

            if check_date > TODAY_TW:
                continue

            results = fetch_espn_games_by_taiwan_date(check_date)

            if results is None or results.empty:
                continue

            keep_cols = [
                c for c in [
                    "game_id",
                    "away_score",
                    "home_score",
                    "completed",
                    "series_info"
                ]
                if c in results.columns
            ]

            day_log = no_df[no_df["預測目標日期"].astype(str).eq(str(check_date))].copy()

            day_log["game_id"] = day_log["game_id"].astype(str)
            results["game_id"] = results["game_id"].astype(str)

            merged = day_log.merge(results[keep_cols], on="game_id", how="left")

            for _, row in merged.iterrows():
                raw = stable_calc_pick_result(row, allow_original_pick=True)

                if raw == "沒過":
                    avoid_result = "避開成功"
                elif raw == "過":
                    avoid_result = "避開失敗"
                elif raw == "走水":
                    avoid_result = "走水"
                else:
                    avoid_result = "無法驗證"

                if avoid_result not in ["避開成功", "避開失敗", "走水"]:
                    continue

                rows.append({
                    "預測目標日期": str(check_date),
                    "推薦內容": row.get("推薦內容", ""),
                    "避開結果": avoid_result,
                    "series_info": str(row.get("series_info", "")),
                    "game_id": str(row.get("game_id", "")),
                })

        except Exception as e:
            print("不推薦避開率略過日期：", d, e)
            continue

    return pd.DataFrame(rows)


def stable_avoid_rate_text(df):
    if df is None or df.empty:
        return "0/0（0.0%）"

    temp = df[
        df["避開結果"].astype(str).isin(["避開成功", "避開失敗"])
    ].copy()

    total = len(temp)

    if total == 0:
        return "0/0（0.0%）"

    success = int(temp["避開結果"].astype(str).eq("避開成功").sum())
    return f"{success}/{total}（{success / total * 100:.1f}%）"


def stable_filter_no_recommend_finals(df):
    if df is None or df.empty or "series_info" not in df.columns:
        return pd.DataFrame()

    current = stable_filter_current_season(df)
    if current.empty:
        return current
    return current[
        current["series_info"].fillna("").astype(str).str.contains("NBA Finals", case=False, na=False)
    ].copy()


def stable_upsert_season_summary(summary_row):
    summary_path = DATA_DIR / "season_summary.csv"

    new_df = pd.DataFrame([summary_row])

    if summary_path.exists():
        try:
            old = pd.read_csv(summary_path)
        except Exception:
            old = pd.DataFrame()
    else:
        old = pd.DataFrame()

    if old.empty:
        combined = new_df
    else:
        if "賽季年份" not in old.columns:
            old["賽季年份"] = ""
        combined = old[old["賽季年份"].astype(str) != str(summary_row["賽季年份"])].copy()
        combined = pd.concat([combined, new_df], ignore_index=True)

    combined["賽季年份"] = combined["賽季年份"].astype(str)
    combined = combined.sort_values("賽季年份", ascending=False)

    combined.to_csv(summary_path, index=False, encoding="utf-8-sig")


def build_season_dashboard_html(win_rates):
    season_year = stable_season_year(TODAY_TW)
    season_label = stable_season_label(TODAY_TW)
    champion = stable_get_champion_info()

    verified = stable_build_verified_rows(include_no_recommend=False)
    season_verified = stable_filter_current_season(verified)
    finals_verified = stable_filter_finals(verified)

    no_rows = stable_build_no_recommend_rows()
    season_no_rows = stable_filter_current_season(no_rows)
    finals_no_rows = stable_filter_no_recommend_finals(no_rows)

    main_df = pd.DataFrame()
    if season_verified is not None and not season_verified.empty:
        main_df = season_verified[
            season_verified["推薦等級"].astype(str).str.contains("主推", na=False)
        ].copy()

    season_main_rate = stable_rate_text(main_df)
    season_top3_rate = stable_rate_text(season_verified)
    season_total_rate = stable_rate_text(season_verified)
    finals_rate = stable_rate_text(finals_verified)
    season_avoid_rate = stable_avoid_rate_text(season_no_rows)
    finals_avoid_rate = stable_avoid_rate_text(finals_no_rows)

    if int(champion.get("champion_wins", 0) or 0) >= 4:
        stable_upsert_season_summary({
            "賽季年份": season_year,
        "賽季": season_label,
        "總冠軍": champion.get("champion", "—"),
        "總冠軍賽": champion.get("series_text", "—"),
        "主推勝率": season_main_rate,
        "Top3勝率": season_top3_rate,
        "年度總勝率": season_total_rate,
        "Finals勝率": finals_rate,
        "不推薦避開率": season_avoid_rate,
        "Finals避開率": finals_avoid_rate,
        "未存預測場次": champion.get("missing_finals", 0),
        "最後更新": tw_now_text(),
    })

    missing_text = ""
    missing_count = int(champion.get("missing_finals", 0) or 0)

    if missing_count > 0:
        missing_text = f"<p class='empty'>資料提醒：Finals 有 {missing_count} 場未存預測，不列入勝率。</p>"

    html = f"""
    <div class="verify-summary">
        🏆 {season_label}｜總冠軍：{champion.get('champion', '—')}｜
        總冠軍賽：{champion.get('series_text', '—')}
    </div>

    <div class="cards">
        <div class="card">
            <div class="label">主推勝率</div>
            <div class="value">{season_main_rate}</div>
        </div>

        <div class="card">
            <div class="label">Top3 勝率</div>
            <div class="value">{season_top3_rate}</div>
        </div>

        <div class="card">
            <div class="label">{season_label} 總勝率</div>
            <div class="value">{season_total_rate}</div>
        </div>

        <div class="card">
            <div class="label">不推薦避開率</div>
            <div class="value">{season_avoid_rate}</div>
        </div>

        <div class="card">
            <div class="label">Finals 推薦勝率</div>
            <div class="value">{finals_rate}</div>
        </div>

        <div class="card">
            <div class="label">Finals 避開率</div>
            <div class="value">{finals_avoid_rate}</div>
        </div>
    </div>

    {missing_text}
    """

    return html


def build_season_archive_html():
    summary_path = DATA_DIR / "season_summary.csv"

    if not summary_path.exists():
        return "<p class='empty'>目前沒有歷年摘要。</p>"

    try:
        df = pd.read_csv(summary_path)
    except Exception:
        return "<p class='empty'>歷年摘要讀取失敗。</p>"

    if df.empty:
        return "<p class='empty'>目前沒有歷年摘要。</p>"

    show_cols = [
        "賽季",
        "總冠軍",
        "總冠軍賽",
        "主推勝率",
        "Top3勝率",
        "年度總勝率",
        "Finals勝率",
        "不推薦避開率",
        "未存預測場次",
    ]

    show_cols = [c for c in show_cols if c in df.columns]

    return df[show_cols].to_html(index=False, escape=False, classes="data-table")

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
        return "不推薦"
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

    新規則：
    - 先從所有候選中依信心分數排序。
    - 永遠保留 Top3 版位：主推 / 副推 1 / 副推 2。
    - 信心分數 >= 70%：推薦。
    - 信心分數 < 70%：不推薦。
    - 不再產生「整天 PASS」資料。
    """

    if predictions is None or predictions.empty:
        return []

    RECOMMEND_MIN_SCORE = 70
    MIN_EDGE_TO_ENTER_CANDIDATE = 0.0  # 所有有盤口候選均參與信心排名

    items = []

    for _, row in predictions.iterrows():

        if str(row.get("大小分推薦", "")) != "無盤口":
            edge = row.get("大小分優勢", 0)

            if pd.isna(edge):
                edge = 0

            edge_value = abs(safe_float(edge, 0))

            if edge_value >= MIN_EDGE_TO_ENTER_CANDIDATE:
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
                    "推薦狀態": "推薦" if score >= RECOMMEND_MIN_SCORE else "不推薦",
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

            if edge_value >= MIN_EDGE_TO_ENTER_CANDIDATE:
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
                    "推薦狀態": "推薦" if score >= RECOMMEND_MIN_SCORE else "不推薦",
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

    final_items = []
    levels = ["主推", "副推 1", "副推 2"]

    for level, item in zip(levels, items[:3]):
        item = item.copy()
        item["推薦等級"] = level
        item["推薦狀態"] = "推薦" if item.get("信心分數數值", 0) >= RECOMMEND_MIN_SCORE else "不推薦"
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
                狀態：{item.get('推薦狀態', '推薦')}｜
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

def nba_archive_detail_fragment(page):
    """擷取封存 HTML 的外層 season-details-body div，保留內部完整內容。"""
    import re
    pattern = re.compile(r"</?div\b[^>]*>", re.IGNORECASE)
    anchor = re.search(r'<div\s+class=["\']season-details-body["\']\s*>', page, re.IGNORECASE)
    if not anchor:
        return ""
    depth = 0
    for token in pattern.finditer(page, anchor.start()):
        if token.group().lower().startswith("</div"):
            depth -= 1
            if depth == 0:
                return page[anchor.start():token.end()]
        else:
            depth += 1
    return ""


def nba_completed_seasons_html():
    """只列已完成的年度封存，最多 10 年，並嵌入已凍結的年度細節。"""
    from html import escape
    file = DATA_DIR / "season_summary.csv"
    if not file.exists():
        return ""
    try:
        df = pd.read_csv(file)
    except Exception as error:
        print("歷年封存讀取失敗：", error)
        return ""
    if df.empty or "賽季年份" not in df.columns:
        return ""
    df["_year"] = pd.to_numeric(df["賽季年份"], errors="coerce")
    df = df.dropna(subset=["_year"]).sort_values("_year", ascending=False)
    df = df.drop_duplicates("_year").head(10)

    blocks = []
    for _, row in df.iterrows():
        year = int(row["_year"])
        if year > stable_season_year(TODAY_TW):
            continue
        champion = str(row.get("總冠軍", "")).strip()
        if not champion or champion in ("—", "尚未產生", "nan"):
            continue
        def text(name):
            value = row.get(name, "—")
            return escape(str(value) if pd.notna(value) else "—")
        archive_file = REPORT_DIR / "archives" / f"{year}.html"
        details = "<div class='season-details-body'><p class='empty'>本賽季詳細紀錄尚未封存；年度勝率已保留。</p></div>"
        if archive_file.exists():
            raw_detail = archive_file.read_text(encoding="utf-8")
            extracted = nba_archive_detail_fragment(raw_detail)
            if extracted:
                details = extracted
        blocks.append(f"""
        <details class="season-details">
          <summary>▶ {year} 賽季｜總冠軍：{text('總冠軍')}｜
          總冠軍賽：{text('總冠軍賽')}｜Top3：{text('Top3勝率')}｜
          總勝率：{text('年度總勝率')}</summary>
          {details}
        </details>""")
    return "\n".join(blocks)


def nba_has_completed_current_season():
    year = stable_season_year(TODAY_TW)
    if TODAY_TW.month in (7, 8, 9):
        return True
    path = DATA_DIR / "season_summary.csv"
    if not path.exists():
        return False
    try:
        data = pd.read_csv(path)
    except Exception:
        return False
    if data.empty or "賽季年份" not in data.columns or "總冠軍" not in data.columns:
        return False
    match = data[pd.to_numeric(data["賽季年份"], errors="coerce").eq(year)]
    return any(str(x).strip() not in ("", "—", "尚未產生", "nan") for x in match["總冠軍"])



# NBA_2027_LIVE_VISIBILITY_FIX_20261111

def nba_current_season_sections(avoid_rate="尚無可驗證紀錄"):
    """以已儲存預測與實際比分，展示當季最近驗證與兩類歷史。

    不改寫 prediction_log，不重算預測，也不把舊預測標示成明日推薦。
    過／沒過及避開率沿用 stable_calc_pick_result 的原本規則。
    """
    from html import escape

    log = load_prediction_log()
    if log is None or log.empty or "預測目標日期" not in log.columns:
        return ("<h2>最近驗證結果</h2><div class='section'><p class='empty'>本季尚未存有預測紀錄。</p></div>",
                "<details class='history-box' id='season-history'><summary>📂 本季推薦／不推薦紀錄（尚無資料）</summary><div class='section'><p class='empty'>目前沒有紀錄。</p></div></details>")

    current_year = stable_season_year(TODAY_TW)
    log = log[log["預測目標日期"].apply(stable_season_year).eq(current_year)].copy()
    if log.empty:
        return ("<h2>最近驗證結果</h2><div class='section'><p class='empty'>本季尚無可驗證的紀錄。</p></div>",
                "<details class='history-box' id='season-history'><summary>📂 本季推薦／不推薦紀錄（尚無資料）</summary><div class='section'><p class='empty'>目前沒有紀錄。</p></div></details>")

    log["_date"] = pd.to_datetime(log["預測目標日期"], errors="coerce").dt.date
    log = log[log["_date"].notna()].copy()
    if log.empty:
        return ("<h2>最近驗證結果</h2><div class='section'><p class='empty'>預測紀錄日期無法解析。</p></div>", "")

    # 最近日期在前；同一日期照原本 CSV 順序排列，最多保存十年的歷史交給年度封存。
    log = log.sort_values("_date", ascending=False, kind="stable")
    no_rec = log.get("推薦狀態", pd.Series("推薦", index=log.index)).fillna("推薦").astype(str).eq("不推薦")
    if "推薦等級" in log.columns:
        no_rec = no_rec | log["推薦等級"].fillna("").astype(str).str.upper().eq("PASS")
    if "推薦類型" in log.columns:
        no_rec = no_rec | log["推薦類型"].fillna("").astype(str).eq("觀望")
    # 兩種紀錄各取最近 30 筆，避免多年累積後逐一抓取大量歷史日期。
    display_log = pd.concat([log.loc[~no_rec].head(30), log.loc[no_rec].head(30)])
    display_log = display_log.sort_values("_date", ascending=False, kind="stable")
    game_results = {}
    for day in display_log["_date"].unique():
        if day > TODAY_TW:
            continue
        try:
            games = fetch_espn_games_by_taiwan_date(day)
            if games is None or games.empty or "game_id" not in games.columns:
                continue
            for _, g in games.iterrows():
                game_results[(day, str(g.get("game_id", "")))] = g
        except Exception as error:
            print("當季紀錄驗證暫時失敗：", day, error)

    records = []
    for _, row in display_log.iterrows():
        day = row["_date"]
        gid = str(row.get("game_id", ""))
        game = game_results.get((day, gid))
        status = str(row.get("推薦狀態", "推薦")).strip()
        is_avoid = (status == "不推薦" or str(row.get("推薦等級", "")).upper() == "PASS"
                    or str(row.get("推薦類型", "")) == "觀望")
        level = normalize_rec_level(row.get("推薦等級", ""))
        result = "待開賽／驗證" if day >= TODAY_TW else "尚未取得賽果"
        score = "—"
        if game is not None:
            away = safe_float(game.get("away_score"), None)
            home = safe_float(game.get("home_score"), None)
            # ESPN cache 有時將布林值讀成字串；不能把 'False' 當成已完賽。
            completed = str(game.get("completed", False)).strip().lower() in ("true", "1", "yes")
            if completed:
                if away is not None and home is not None:
                    score = f"{safe_int(away)}-{safe_int(home)}"
                merged = row.to_dict()
                merged.update({"completed": True, "away_score": away, "home_score": home})
                result = stable_calc_pick_result(merged, allow_original_pick=is_avoid)
            else:
                result = "未完賽"
        verified = result in ("過", "沒過", "走水")
        if is_avoid:
            # 未推薦的原候選不是反向推薦；只回報該候選的賽後結果。
            display_result = {"沒過": "✅ 原候選沒過", "過": "❌ 原候選過", "走水": "➖ 走水"}.get(result, result)
        else:
            display_result = {"過": "✅ 過", "沒過": "❌ 沒過", "走水": "➖ 走水"}.get(result, result)

        type_text, pick = stable_parse_original_pick(row.get("推薦類型", ""), row.get("推薦內容", ""))
        if not pick or pick.lower() == "nan":
            pick = "—"
        records.append({
            "日期": str(day),
            "隊伍": f"{short_name(row.get('客隊', ''))} vs {short_name(row.get('主隊', ''))}",
            "比分": score,
            "等級": level if level in ("主推", "副推 1", "副推 2") else "—",
            "類型": type_text,
            "預測": pick_short_name(pick),
            "信心": str(row.get("信心分數", "—")),
            "狀態": "不推薦" if is_avoid else "推薦",
            "結果": display_result,
            "_verified": verified,
            "_avoid": is_avoid,
        })

    def table(rows, columns=None):
        if not rows:
            return "<p class='empty'>目前沒有資料。</p>"
        default = ["日期", "隊伍", "比分", "等級", "類型", "預測", "信心", "狀態", "結果"]
        cols = columns if columns is not None else default
        df = pd.DataFrame(rows)
        return df[cols].to_html(index=False, escape=True, classes="data-table")

    # 將真正推薦的命中表現、未推薦候選的賽後追蹤徹底分開。
    # 沿用同一批已儲存的賽前預測及既有 stable_calc_pick_result，不重算預測。
    recommended = [r for r in records if not r["_avoid"]]
    avoided = [r for r in records if r["_avoid"]]
    recommended_verified = [r for r in recommended if r["_verified"]]
    avoided_verified = [r for r in avoided if r["_verified"]]
    rec_cols = ["日期", "隊伍", "等級", "預測", "比分", "結果"]
    avoid_cols = ["日期", "隊伍", "等級", "預測", "信心", "比分", "結果"]
    recent_html = (
        "<section class='nba-recommended-results'>"
        "<h2>最近推薦驗證（近 7 筆）</h2>"
        "<p class='nba-muted-caption'>僅列賽前標記「推薦」且已完賽可驗證的紀錄，不含未推薦候選。</p>"
        "<div class='section'>" + table(recommended_verified[:7], rec_cols) + "</div></section>"
        "<section class='nba-avoidance-results'>"
        "<h2>未推薦候選追蹤（近 7 筆）</h2>"
        "<p class='nba-muted-caption'>與正式推薦的勝率完全分開。原候選沒過＝避開了該候選；不是賽前推薦相反方向。</p>"
        "<div class='nba-avoid-meta'>本季原候選沒過比例：" + escape(str(avoid_rate)) + " <small>（只計算已驗證的不推薦候選）</small></div>"
        "<div class='section'>" + table(avoided_verified[:7], avoid_cols) + "</div></section>"
    )

    latest_saved = records[0]["日期"] if records else "—"
    season = escape(stable_season_label(TODAY_TW))
    history_html = f"""
    <h2>當季歷史紀錄</h2>
    <details class='history-box' id='season-history'>
      <summary>📂 展開 {season} 當季預測紀錄（最新：{escape(latest_saved)}）</summary>
      <div class='section'>
        <p class='empty'>以下是當季已儲存的預測，不等於今天或明天的即時推薦。未完賽及缺少賽果的項目會明確標示。</p>
        <details class='history-box'><summary>✅ 已推薦紀錄（最近 30 筆，共 {len(recommended)} 筆）</summary>
          <div class='section'>{table(recommended[:30])}</div>
        </details>
        <details class='history-box'><summary>🟡 不推薦／PASS 紀錄（最近 30 筆，共 {len(avoided)} 筆）</summary>
          <div class='section'>{table(avoided[:30])}</div>
        </details>
      </div>
    </details>
    """
    return recent_html, history_html


def nba_schedule_without_picks_html(predictions):
    """有賽程但候選沒有達推薦門檻時，展示賽程而不捏造推薦。"""
    if predictions is None or predictions.empty:
        return "<div class='section'><p class='empty'>目前尚未取得明日賽程；可能沒有比賽，也可能是資料來源尚未更新。</p></div>"
    lines_ready = 0
    rows = []
    for _, row in predictions.iterrows():
        spread = safe_float(row.get("home_spread"), None)
        total = safe_float(row.get("total"), None)
        if spread is not None or total is not None:
            lines_ready += 1
        rows.append({
            "開賽時間": str(row.get("台灣開賽時間", "")),
            "對戰": f"{short_name(row.get('客隊', ''))} vs {short_name(row.get('主隊', ''))}",
            "主隊讓分盤": f"{spread:+.1f}" if spread is not None else "尚無盤口",
            "大小分盤": f"{total:.1f}" if total is not None else "尚無盤口",
            "參考預估（非推薦）": (
                f"主隊分差 {safe_float(row.get('預測主隊分差')):+.1f}／總分 {safe_float(row.get('預測總分')):.1f}"
                if safe_float(row.get("預測主隊分差")) is not None
                and safe_float(row.get("預測總分")) is not None else "—"
            ),
        })
    message = ("盤口尚未齊全" if not lines_ready else "盤口資料或預測優勢尚未達推薦門檻")
    df = pd.DataFrame(rows)
    return (f"<div class='section'><p class='empty'>已取得 {len(rows)} 場明日賽程，但{message}，暫無可列入 Top3 的推薦。"
            "稍後更新有可能出現新推薦；不會用舊預測冒充明日預測。</p>"
            + df.to_html(index=False, escape=True, classes="data-table") + "</div>")

def generate_html_report(predictions, yesterday_verify, win_rates):
    summary_text = build_summary_text(win_rates)
    season_dashboard_html = build_season_dashboard_html(win_rates)
    health_html = build_data_health_html()
    finals_html = build_finals_report_html()

    # ===== 歷史紀錄 =====
    history_html = "<p class='empty'>目前沒有歷史紀錄。</p>"

    if PREDICTION_LOG_CSV.exists():
        history_df = load_prediction_log()
        if not history_df.empty and "預測目標日期" in history_df.columns:
            history_df = history_df[
                history_df["預測目標日期"].apply(stable_season_year).eq(stable_season_year(TODAY_TW))
            ].copy()

        if len(history_df) > 0:
            # 只保留一般歷史推薦：主推 / 副推
            # 注意：必須先篩掉 PASS，再取最近 30 筆，避免 PASS 混入或佔掉名額
            if "推薦等級" in history_df.columns:
                level_text = history_df["推薦等級"].fillna("").astype(str).str.strip()

                history_show_df = history_df[
                    level_text.isin([
                        "🔥 主推",
                        "🥈 副推 1",
                        "🥉 副推 2",
                        "主推",
                        "副推 1",
                        "副推 2",
                    ])
                ].copy()

                if not history_show_df.empty and "推薦狀態" in history_show_df.columns:
                    history_show_df = history_show_df[
                        history_show_df["推薦狀態"].fillna("推薦").astype(str).eq("推薦")
                    ].copy()

                # 排除舊版低信心副推：
                # 副推至少要 64%，否則視為新版 PASS/觀望，不放進一般歷史推薦。
                if not history_show_df.empty and "信心分數" in history_show_df.columns:
                    history_show_df["_信心百分比"] = history_show_df["信心分數"].apply(confidence_to_percent)
                    history_show_df["_推薦等級標準"] = history_show_df["推薦等級"].apply(normalize_rec_level)

                    history_show_df = history_show_df[
                        (
                            history_show_df["_推薦等級標準"].eq("主推")
                            & (history_show_df["_信心百分比"] >= MAIN_PICK_MIN_CONFIDENCE)
                        )
                        |
                        (
                            history_show_df["_推薦等級標準"].isin(["副推 1", "副推 2"])
                            & (history_show_df["_信心百分比"] >= 64)
                        )
                    ].copy()

                    history_show_df = history_show_df.drop(
                        columns=["_信心百分比", "_推薦等級標準"],
                        errors="ignore"
                    )

                history_show_df = history_show_df.tail(30).copy()
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

    # ===== 不推薦歷史紀錄 =====
    pass_history_html = "<p class='empty'>目前沒有不推薦紀錄。</p>"

    if PREDICTION_LOG_CSV.exists():
        pass_df = load_prediction_log()
        if not pass_df.empty and "預測目標日期" in pass_df.columns:
            pass_df = pass_df[
                pass_df["預測目標日期"].apply(stable_season_year).eq(stable_season_year(TODAY_TW))
            ].copy()

        if not pass_df.empty:
            if "推薦狀態" not in pass_df.columns:
                pass_df["推薦狀態"] = ""

            pass_df = pass_df[
                pass_df["推薦狀態"].fillna("").astype(str).eq("不推薦")
                | pass_df["推薦等級"].fillna("").astype(str).eq("PASS")
                | pass_df["推薦類型"].fillna("").astype(str).eq("觀望")
            ].copy()

            if not pass_df.empty:
                if "預測目標日期" in pass_df.columns:
                    pass_df["_日期檢查"] = pd.to_datetime(pass_df["預測目標日期"], errors="coerce").dt.date
                    pass_df = pass_df[pass_df["_日期檢查"] <= TODAY_TW].copy()
                    pass_df = pass_df.drop(columns=["_日期檢查"], errors="ignore")

                results_list = []

                if "預測目標日期" in pass_df.columns:
                    for d in pass_df["預測目標日期"].dropna().unique():
                        try:
                            check_date = pd.to_datetime(d).date()

                            if check_date > TODAY_TW:
                                continue

                            res = fetch_espn_games_by_taiwan_date(check_date)

                            if res is not None and not res.empty:
                                results_list.append(res)
                        except Exception:
                            continue

                if results_list and "game_id" in pass_df.columns:
                    results_df = pd.concat(results_list, ignore_index=True)

                    if "game_id" in results_df.columns:
                        results_df["game_id"] = results_df["game_id"].astype(str)
                        pass_df["game_id"] = pass_df["game_id"].astype(str)

                        keep_cols = [
                            c for c in ["game_id", "away_score", "home_score", "completed"]
                            if c in results_df.columns
                        ]

                        pass_df = pass_df.merge(
                            results_df[keep_cols],
                            on="game_id",
                            how="left"
                        )

                def calc_no_recommend_counter_result(row):
                    away = safe_float(row.get("away_score"), None)
                    home = safe_float(row.get("home_score"), None)

                    if not row.get("completed", False):
                        return "尚未完賽"

                    if away is None or home is None:
                        return "無法驗證"

                    rec_type = str(row.get("推薦類型", ""))
                    rec_pick = str(row.get("推薦內容", ""))

                    # 舊版不推薦資料格式：
                    # PASS觀望｜原最佳候選：大分 215.5
                    # 主推候選不推薦｜原候選：XXX
                    if "原最佳候選：" in rec_pick:
                        rec_pick = rec_pick.split("原最佳候選：", 1)[1].strip()
                    elif "原候選：" in rec_pick:
                        rec_pick = rec_pick.split("原候選：", 1)[1].strip()

                    if rec_type in ["觀望", "PASS", ""]:
                        if "大分" in rec_pick or "小分" in rec_pick or "Over" in rec_pick or "Under" in rec_pick:
                            rec_type = "大小分"
                        else:
                            rec_type = "讓分"

                    if rec_type == "大小分":
                        total_line = safe_float(row.get("total"), None)

                        if total_line is None:
                            return "無法驗證"

                        actual_total = away + home

                        if actual_total == total_line:
                            return "走水"

                        if "大分" in rec_pick:
                            return "原候選會過" if actual_total > total_line else "原候選沒過"

                        if "小分" in rec_pick:
                            return "原候選會過" if actual_total < total_line else "原候選沒過"

                        return "無法驗證"

                    if rec_type == "讓分":
                        home_spread = safe_float(row.get("home_spread"), None)

                        if home_spread is None:
                            return "無法驗證"

                        actual_home_margin = home - away
                        home_cover_value = actual_home_margin + home_spread

                        if home_cover_value == 0:
                            return "走水"

                        if str(row.get("主隊", "")) in rec_pick:
                            return "原候選會過" if home_cover_value > 0 else "原候選沒過"

                        if str(row.get("客隊", "")) in rec_pick:
                            return "原候選會過" if home_cover_value < 0 else "原候選沒過"

                        return "無法驗證"

                    return "無法驗證"

                pass_df["原候選結果"] = pass_df.apply(calc_no_recommend_counter_result, axis=1)

                if "away_score" in pass_df.columns and "home_score" in pass_df.columns:
                    pass_df["比賽分數"] = (
                        pass_df["away_score"].fillna("-").astype(str)
                        + "-"
                        + pass_df["home_score"].fillna("-").astype(str)
                    )
                else:
                    pass_df["比賽分數"] = "—"

                pass_df["隊伍"] = (
                    pass_df["客隊"].apply(short_name).astype(str)
                    + " vs "
                    + pass_df["主隊"].apply(short_name).astype(str)
                )

                verified_pass_df = pass_df[
                    pass_df["原候選結果"].astype(str).isin(["原候選沒過", "原候選會過"])
                ].copy()

                pass_total = len(verified_pass_df)
                pass_success = int((verified_pass_df["原候選結果"] == "原候選沒過").sum())
                pass_fail = int((verified_pass_df["原候選結果"] == "原候選會過").sum())
                pass_rate = (pass_success / pass_total * 100) if pass_total else 0

                pass_summary_html = f"""
                <p class="empty">
                    不推薦可驗證：{pass_total} 筆｜
                    避開成功：{pass_success} 筆｜
                    避開失敗：{pass_fail} 筆｜
                    避開率：{pass_rate:.1f}%
                </p>
                """

                if "預測目標日期" in pass_df.columns:
                    pass_df["排序日期"] = pd.to_datetime(pass_df["預測目標日期"], errors="coerce")
                    pass_df = pass_df.sort_values("排序日期", ascending=False).drop(columns=["排序日期"])

                def clean_no_recommend_pick(row):
                    level = final_level_display(row.get("推薦等級", ""))
                    pick = str(row.get("推薦內容", ""))

                    if "原最佳候選：" in pick:
                        pick = pick.split("原最佳候選：", 1)[1].strip()
                    elif "原候選：" in pick:
                        pick = pick.split("原候選：", 1)[1].strip()

                    if level == "—":
                        return f"不推薦｜{pick}"
                    return f"不推薦｜{pick}"

                def clean_no_recommend_result(value):
                    value = str(value)

                    if value == "原候選沒過":
                        return "✅ 避開成功"
                    if value == "原候選會過":
                        return "❌ 避開失敗"
                    if value == "走水":
                        return "➖ 走水"

                    return "—"

                pass_df["預測"] = pass_df.apply(clean_no_recommend_pick, axis=1)
                pass_df["預測結果"] = pass_df["原候選結果"].apply(clean_no_recommend_result)

                for col in ["預測目標日期", "隊伍", "比賽分數", "預測", "預測結果"]:
                    if col not in pass_df.columns:
                        pass_df[col] = "—"

                pass_df = pass_df[[
                    "預測目標日期",
                    "隊伍",
                    "比賽分數",
                    "預測",
                    "預測結果",
                ]].head(30)

                pass_df = pass_df.rename(columns={
                    "預測目標日期": "日期",
                })

                pass_history_html = pass_summary_html + pass_df.to_html(
                    index=False,
                    escape=False,
                    classes="data-table"
                )

    # ===== 明日預測 =====
    # 報告只保留「明日推薦」，不再另外顯示候選排行與每場預測，避免畫面太雜。
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

    has_tomorrow_games = predictions is not None and not predictions.empty
    has_yesterday_verify = yesterday_verify is not None and not yesterday_verify.empty
    is_completed = nba_has_completed_current_season()
    is_offseason = TODAY_TW.month in (7, 8, 9) or is_completed
    is_live_season = not is_offseason

    current_verified = stable_filter_current_season(stable_build_verified_rows(include_no_recommend=False))
    current_pass = stable_filter_current_season(stable_build_no_recommend_rows())
    current_main = pd.DataFrame()
    if current_verified is not None and not current_verified.empty:
        current_main = current_verified[
            current_verified["推薦等級"].astype(str).str.contains("主推", na=False)
        ].copy()

    if is_live_season:
        main_display = stable_rate_text(current_main) if not current_main.empty else "尚無已驗證紀錄"
        top3_display = stable_rate_text(current_verified) if not current_verified.empty else "尚無已驗證紀錄"
        avoid_display = stable_avoid_rate_text(current_pass) if not current_pass.empty else "尚無已驗證紀錄"
        summary_section_html = f"""
        <h2>{stable_season_label(TODAY_TW)} 正式推薦表現</h2>
        <p class="nba-muted-caption">只計入賽前實際標記為「推薦」、且已有賽果的紀錄。未推薦候選不列入勝率。</p>
        <div class="cards">
          <div class="card"><div class="label">主推勝率</div><div class="value">{main_display}</div></div>
          <div class="card"><div class="label">Top3 已推薦勝率</div><div class="value">{top3_display}</div></div>
        </div>
        <p class="nba-sample-note">目前樣本數仍有限，勝率僅代表已有紀錄的結果，不代表未來命中率。</p>"""
    else:
        summary_section_html = ""

    slate_heading = "今日推薦" if TOMORROW_TW == TODAY_TW else "明日推薦"
    saved_slate_html = nba_saved_slate_html(TOMORROW_TW) if TOMORROW_TW == TODAY_TW else ""

    if saved_slate_html:
        report_title = f"NBA {stable_season_label(TODAY_TW)} 預測報告"
        report_subtitle = f"產生時間：{tw_now_text()}｜今日比賽：{TOMORROW_TW}｜保留原始賽前預測"
        tomorrow_section_html = f"<h2>今日已儲存的賽前預測</h2>{saved_slate_html}"
    elif has_tomorrow_games:
        report_title = f"NBA {stable_season_label(TODAY_TW)} 預測報告"
        report_subtitle = f"產生時間：{tw_now_text()}｜預測日期：{TOMORROW_TW}"
        actual_recs = build_final_recommendations(predictions)
        if actual_recs:
            tomorrow_section_html = f"""
    <h2>{slate_heading}</h2>
    <div class="final-wrap">{final_recommendations_html(predictions)}</div>
"""
        else:
            tomorrow_section_html = f"""
    <h2>{slate_heading}｜目前尚無可用候選</h2>
    {nba_schedule_without_picks_html(predictions)}
"""
    else:
        report_title = (f"NBA {stable_season_label(TODAY_TW)} 預測報告"
                        if is_live_season else "NBA 休賽期報告")
        report_subtitle = (f"產生時間：{tw_now_text()}｜明日賽程尚未取得"
                           if is_live_season else f"產生時間：{tw_now_text()}｜賽季封存 / 休賽期")
        tomorrow_section_html = (f"<h2>{slate_heading}｜賽程尚未取得</h2>" + nba_schedule_without_picks_html(predictions)) if is_live_season else ""

    if has_yesterday_verify:
        yesterday_section_html = f"""
    <h2>昨日驗證</h2>
    {yesterday_top3_summary_html(yesterday_verify)}

    <div class="section">
        {yesterday_table}
    </div>
"""
    else:
        yesterday_section_html = ""

    champion_info = stable_get_champion_info()
    if int(champion_info.get("champion_wins", 0) or 0) >= 4:
        # 下一季決出冠軍後才產生獨立詳細封存；2026 由安裝程序預先凍結。
        archive_file = REPORT_DIR / "archives" / f"{stable_season_year(TODAY_TW)}.html"
        if not archive_file.exists():
            archive_file.parent.mkdir(parents=True, exist_ok=True)
            detail = f"""<div class="season-details-body">
              <h2>{stable_season_label(TODAY_TW)} 勝率總覽</h2>{season_dashboard_html}
              <h2>NBA Finals / 總冠軍賽</h2><div class="section">{finals_html}</div>
              <details class="history-box"><summary>📂 查看歷史推薦紀錄（最近 30 筆）</summary>
                <div class="section">{history_html}</div></details>
              <details class="history-box"><summary>🟡 查看不推薦紀錄（最近 30 筆，不列入勝率）</summary>
                <div class="section">{pass_history_html}</div></details>
              <h2>資料狀態 / 缺漏檢查</h2><div class="section">{health_html}</div>
              </div>"""
            archive_file.write_text(detail, encoding="utf-8")

    season_compact_html = nba_completed_seasons_html()
    recent_results_html, live_history_html = nba_current_season_sections(avoid_display) if is_live_season else ("", "")

    html = f"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{report_title}</title>
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

.season-details {{
    background: #fffdf8;
    border: 1px solid #eadfce;
    border-radius: 16px;
    box-shadow: 0 4px 14px rgba(90, 65, 30, 0.08);
    margin: 26px 0 14px;
    padding: 0;
}}

.season-details summary {{
    cursor: pointer;
    list-style: none;
    padding: 16px 18px;
    font-size: 17px;
    font-weight: 900;
    color: #2b2b2b;
}}

.season-details summary::-webkit-details-marker {{
    display: none;
}}

.season-details summary::after {{
    content: "展開";
    float: right;
    color: #8a765c;
    font-size: 13px;
    font-weight: 800;
}}

.season-details[open] summary::after {{
    content: "收合";
}}

.season-details-body {{
    border-top: 1px solid #eadfce;
    padding: 16px 18px 18px;
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


/* NBA_LIGHT_UI_V2_START */
:root {{
  --nba-ink: #14233d; --nba-muted: #64748b; --nba-line: #dce4ee;
  --nba-bg: #f5f7fb; --nba-accent: #3655d5; --nba-good: #137c67;
  --nba-danger: #bb3d4a; --nba-shadow: 0 5px 20px rgba(17, 35, 64, .045);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0 !important; padding: 0 !important; min-height: 100vh;
  color: var(--nba-ink) !important; background: var(--nba-bg) !important;
  font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", "Segoe UI", sans-serif !important;
  line-height: 1.55;
}}
body::before {{
  content: "NBA  /  預測分析"; display: block; color: #fff;
  background: #111d32; padding: 17px max(24px, calc((100vw - 1130px) / 2));
  font-size: 17px; font-weight: 850; letter-spacing: .015em;
}}
.container {{ max-width: 1180px !important; margin: 0 auto !important; padding: 30px 24px 60px !important; }}
.container > h1 {{
  font-size: clamp(29px, 3.4vw, 43px) !important; line-height: 1.18;
  margin: 0 0 10px !important; padding: 0 !important;
  color: #111d32 !important; letter-spacing: -.04em;
}}
.subtitle {{ color: #64748b !important; margin: 0 0 30px !important; font-size: 13px !important; padding: 0 !important; }}
.subtitle::after {{ content: none !important; }}
.container > h2 {{ color: #14233d !important; font-size: 20px !important; margin: 30px 0 12px !important; letter-spacing: -.015em; }}
.container h2 {{ color: #14233d !important; }}
.nba-muted-caption {{ color: #64748b; font-size: 13px; margin: -5px 0 12px; }}
.nba-sample-note {{ color: #8490a3; font-size: 12px; margin: -10px 0 14px; }}
.final-wrap {{ display: grid !important; grid-template-columns: repeat(3,minmax(0,1fr)) !important;
  background: transparent !important; border: 0 !important; box-shadow: none !important;
  gap: 14px !important; padding: 0 !important; margin: 0 0 14px !important; overflow: visible !important; }}
.final-card {{ background: #fff !important; border: 1px solid var(--nba-line) !important;
  border-radius: 15px !important; padding: 21px !important;
  box-shadow: var(--nba-shadow) !important; min-width: 0; }}
.final-card.final-main {{ border: 2px solid #8299e2 !important; padding: 20px !important; background: #fff !important;
  box-shadow: 0 0 0 1px #dce4ff !important; }}
.final-level {{ color: #5e6f8d !important; font-size: 13px !important; font-weight: 850 !important; }}
.final-game {{ color: #14233d !important; font-size: 16px !important; margin-top: 15px !important; }}
.final-pick {{ color: #0e1f3a !important; font-size: clamp(23px,2.2vw,30px) !important;
  letter-spacing: -.035em; margin: 7px 0 12px !important; overflow-wrap: anywhere; }}
.final-score, .final-reason {{ color: #627087 !important; font-size: 13px !important; line-height: 1.7 !important;
  overflow-wrap: anywhere; }}
.final-reason {{ border-top: 1px solid #edf0f5; padding-top: 10px; }}
.cards {{ display: grid !important; grid-template-columns: repeat(2,minmax(0,1fr)) !important;
  gap: 14px !important; margin: 0 0 18px !important; }}
.card {{ background: #fff !important; border: 1px solid var(--nba-line) !important;
  border-radius: 13px !important; padding: 20px 24px !important;
  box-shadow: var(--nba-shadow) !important; }}
.card .label {{ color: #68758c !important; font-size: 13px !important; font-weight: 750 !important; }}
.card .value {{ color: #14233d !important; font-size: clamp(20px,2.5vw,28px) !important;
  margin-top: 5px !important; font-weight: 900 !important; overflow-wrap: anywhere; }}
.section {{ background: #fff !important; border: 1px solid var(--nba-line) !important;
  border-radius: 14px !important; box-shadow: var(--nba-shadow) !important;
  padding: 14px !important; margin: 0 0 18px !important;
  overflow-x: auto !important; -webkit-overflow-scrolling: touch; }}
table.data-table, .dataframe.data-table {{ width: 100% !important; border-collapse: collapse !important;
  border: 0 !important; background: #fff !important; color: var(--nba-ink) !important; }}
table.data-table th, .dataframe.data-table th {{ color: #586883 !important; font-size: 12px !important;
  text-align: left; background: #f3f6fb !important; padding: 12px 13px !important;
  border-bottom: 1px solid var(--nba-line) !important; white-space: nowrap; }}
table.data-table td, .dataframe.data-table td {{ color: #22314d !important; font-size: 13px !important;
  padding: 13px 12px !important; vertical-align: top; border-bottom: 1px solid #edf1f6 !important;
  white-space: nowrap; }}
table.data-table tr:nth-child(even) td, .dataframe.data-table tr:nth-child(even) td {{ background: #fcfdff !important; }}
.nba-avoidance-results {{ border-top: 1px solid #dce4ee; margin-top: 32px; padding-top: 5px; }}
.nba-avoidance-results .section {{ border-color: #e0e7f0 !important; }}
.nba-avoid-meta {{ display: inline-block; padding: 10px 13px; margin: 0 0 12px; border: 1px solid #dfe6f0;
  border-radius: 9px; background: #fff; color: #14233d; font-size: 14px; font-weight: 750; }}
.nba-avoid-meta small {{ color: #64748b; font-weight: 500; font-size: 12px; }}
.history-box, .season-details {{ background: #fff !important; border: 1px solid var(--nba-line) !important;
  border-radius: 13px !important; box-shadow: var(--nba-shadow) !important;
  margin: 12px 0 16px !important; padding: 0 !important; overflow: hidden !important; }}
.history-box > summary, .season-details > summary {{ list-style: none !important; cursor: pointer !important;
  background: #fff !important; color: #152946 !important;
  padding: 17px 19px !important; font-size: 15px !important; font-weight: 800 !important;
  line-height: 1.65 !important; overflow-wrap: anywhere; }}
.history-box summary::-webkit-details-marker, .season-details summary::-webkit-details-marker {{ display:none; }}
.history-box > summary::before, .season-details > summary::before {{ content: ''; color: var(--nba-accent);
  font-size: 17px; margin-right: 8px; }}
.history-box[open] > summary::before, .season-details[open] > summary::before {{ content: ''; }}
.history-box[open] > summary, .season-details[open] > summary {{ border-bottom:1px solid var(--nba-line); }}
.season-details > summary::after {{ content: '已完成' !important; color: #697789 !important;
  border: 1px solid #e0e7ef; background: #f8fafc !important;
  border-radius: 99px; padding: 3px 9px; font-size: 11px !important;
  float: right; margin: 1px 0 0 8px; }}
.season-details-body {{ background: #fff !important; color: var(--nba-ink) !important;
  border: 0 !important; box-shadow: none !important; padding: 18px !important; }}
.verify-summary {{ background: #f4f6fa !important; border:1px solid #e1e7f0 !important;
  color: #1f3150 !important; border-radius: 10px !important; box-shadow: none !important; }}
.empty, p.empty {{ color: #64748b !important; font-weight: 550 !important; }}
.result-win {{ color: #137c67 !important; }} .result-loss, .result-lose {{ color: #bb3d4a !important; }}
.result-push {{ color: #96630b !important; }} .result-pending {{ color: #64748b !important; }}
a {{ color: var(--nba-accent) !important; }}
.container::after {{ content: '本報告資料僅供研究分析使用，不作為任何投資或投注建議。';
  display: block; margin: 28px auto 0; text-align: center; color: #8793a5; font-size: 12px; }}
@media (max-width: 900px) {{
  .container {{ padding: 23px 15px 44px !important; }}
  body::before {{ padding: 14px 17px; }}
  .final-wrap {{ grid-template-columns: 1fr !important; }}
  .final-card {{ padding: 17px !important; }}
  .final-card.final-main {{ padding: 16px !important; }}
  table.data-table, .dataframe.data-table {{ min-width: 640px !important; }}
  .season-details > summary::after {{ float: none !important; display: inline-block; }}
}}
@media (max-width: 520px) {{
  .cards {{ grid-template-columns: 1fr !important; }}
  .container > h1 {{ font-size: 29px !important; }}
  .container > h2 {{ font-size: 18px !important; }}
  .final-pick {{ font-size: 26px !important; }}
  table.data-table, .dataframe.data-table {{ min-width: 640px !important; }}
}}
/* NBA_TOP3_UI_CLEAR_V1 */
.final-wrap .final-card.final-main {{
  border: 2px solid #235bcb !important;
  border-left: 7px solid #235bcb !important;
  background: #eaf2ff !important;
  box-shadow: 0 8px 22px rgba(29,78,216,.15) !important;
}}
.final-wrap .final-card.final-main .final-level {{
  color: #164aa6 !important;
  font-weight: 900 !important;
}}
/* NBA_LIGHT_UI_V2_END */

</style>
</head>

<body>
<div class="container">
    <h1>{report_title}</h1>
    <div class="subtitle">{report_subtitle}</div>

    {tomorrow_section_html}

    {summary_section_html}

    {recent_results_html}

    {live_history_html}

    {season_compact_html}

</div>
</body>
</html>
"""

    REPORT_HTML.write_text(html, encoding="utf-8")


# NBA_MIDNIGHT_SLATE_FIX_V1

def nba_choose_prediction_day(now_tw=None, today_games=None):
    """台灣日期跨過午夜後，當日球賽未全部結束就不要跳到隔天。"""
    if now_tw is None:
        now_tw = datetime.now(TAIWAN_TZ)
    today = now_tw.date()
    if today_games is None:
        today_games = fetch_espn_games_by_taiwan_date(today)
    if today_games is None or today_games.empty:
        # ESPN 暫時無回應時，優先保留今天已有的賽前紀錄，避免預測消失。
        log = load_prediction_log()
        if (log is not None and not log.empty and "預測目標日期" in log.columns
                and log["預測目標日期"].astype(str).eq(str(today)).any()):
            return today
    else:
        if "completed" not in today_games.columns:
            return today
        finished = today_games["completed"].fillna(False).astype(str).str.lower().isin(("true", "1", "yes"))
        if not finished.all():
            return today
    return today + timedelta(days=1)


def nba_saved_slate_html(target):
    """午夜後優先顯示已存的賽前 Top3，不用當下盤口重排。"""
    from html import escape
    log = load_prediction_log()
    if log is None or log.empty or "預測目標日期" not in log.columns:
        return ""
    slate = log[log["預測目標日期"].astype(str).eq(str(target))].copy()
    if slate.empty:
        return ""
    slate["_rank"] = slate["推薦等級"].apply(rec_level_order)
    slate = slate[slate["_rank"].isin((1, 2, 3))].sort_values("_rank")
    if slate.empty:
        return ""
    cards = []
    for _, r in slate.iterrows():
        level = normalize_rec_level(r.get("推薦等級", ""))
        status = str(r.get("推薦狀態", "推薦")).strip() or "推薦"
        status = "不推薦" if status == "不推薦" else "推薦"
        team = f"{short_name(r.get('客隊', ''))} vs {short_name(r.get('主隊', ''))}"
        pick_type, pick = stable_parse_original_pick(r.get("推薦類型", ""), r.get("推薦內容", ""))
        level_text = final_level_display(level)
        klass = " final-main" if level == "主推" else ""
        cards.append(
            f'<div class="final-card{klass}">'
            f'<div class="final-level">{escape(level_text)}｜{escape(pick_type)}</div>'
            f'<div class="final-game">{escape(team)}</div>'
            f'<div class="final-pick">{escape(pick_short_name(pick))}</div>'
            f'<div class="final-score">賽前狀態：{escape(status)}｜'
            f'信心：{escape(str(r.get("信心分數", "—")))}</div>'
            '</div>'
        )
    return ('<p class="nba-muted-caption">本批預測已在賽前儲存；午夜後保留原始內容，'
            '不會改盤口或事後重排。實際結果請見下方驗證區。</p>'
            '<div class="final-wrap">' + ''.join(cards) + '</div>')

def main():
    global TOMORROW_TW
    TOMORROW_TW = nba_choose_prediction_day()
    total_start = time.time()

    print("程式開始執行：daily_report_v3.py")
    print("台灣現在：", tw_now_text())
    print("台灣今天：", TODAY_TW)
    print("預測目標日期（按當日賽程狀態選擇）：", TOMORROW_TW)
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
