import pandas as pd

def generate_pick_reason(row):
    pick_type = row["推薦類型"]
    pick = row["推薦內容"]
    confidence = row["信心分數"]
    team_away = row["客隊"]
    team_home = row["主隊"]

    # 把分數轉成比較像人話的描述
    if confidence >= 85:
        confidence_text = "信心非常高"
    elif confidence >= 75:
        confidence_text = "信心偏高"
    elif confidence >= 65:
        confidence_text = "信心中等"
    else:
        confidence_text = "信心普通"

    # 根據不同推薦類型，生成不同文字
    if pick_type == "讓分":
        reason = (
            f"本場推薦 {pick}。"
            f"對戰組合為 {team_away} 對 {team_home}。"
            f"模型信心分數為 {confidence} 分，屬於{confidence_text}。"
            f"本場讓分方向相對明確，可列入今日重點觀察。"
        )

    elif pick_type == "獨贏":
        reason = (
            f"本場推薦 {pick}。"
            f"對戰組合為 {team_away} 對 {team_home}。"
            f"模型信心分數為 {confidence} 分，屬於{confidence_text}。"
            f"此場勝負方向較清楚，適合作為今日投注參考。"
        )

    elif pick_type == "大小分":
        reason = (
            f"本場推薦 {pick}。"
            f"對戰組合為 {team_away} 對 {team_home}。"
            f"模型信心分數為 {confidence} 分，屬於{confidence_text}。"
            f"若比賽節奏符合預期，大小分方向有機會打出。"
        )

    else:
        reason = (
            f"本場推薦 {pick}。"
            f"模型信心分數為 {confidence} 分，屬於{confidence_text}。"
            f"可作為今日觀察選項。"
        )

    return reason


def build_daily_report(df):
    report = "🔥 今日 NBA 推薦報告\n\n"

    for i, row in df.iterrows():
        rank = row["排名"]
        matchup = f'{row["客隊"]} vs {row["主隊"]}'
        pick = row["推薦內容"]
        confidence = row["信心分數"]
        analysis = row["分析文字"]

        if rank == "首推":
            title = "🔥 今日首推"
        else:
            title = "⭐ 副推"

        report += f"{title}\n"
        report += f"對戰組合：{matchup}\n"
        report += f"推薦玩法：{pick}\n"
        report += f"信心分數：{confidence}\n"
        report += f"分析內容：{analysis}\n"
        report += "------------------------------\n\n"

    return report


# 這裡是我們自己手動做的測試資料
data = [
    {
        "客隊": "Lakers",
        "主隊": "Warriors",
        "推薦類型": "讓分",
        "推薦內容": "Lakers -4.5",
        "信心分數": 87,
        "排名": "首推"
    },
    {
        "客隊": "Celtics",
        "主隊": "Heat",
        "推薦類型": "獨贏",
        "推薦內容": "Celtics ML",
        "信心分數": 82,
        "排名": "副推"
    },
    {
        "客隊": "Suns",
        "主隊": "Nuggets",
        "推薦類型": "大小分",
        "推薦內容": "Over 228.5",
        "信心分數": 78,
        "排名": "副推"
    }
]

# 把資料變成表格（DataFrame）
df_recommend = pd.DataFrame(data)

# 新增一欄：分析文字
df_recommend["分析文字"] = df_recommend.apply(generate_pick_reason, axis=1)

# 組合成完整報告
report_text = build_daily_report(df_recommend)

# 印出結果
print(report_text)