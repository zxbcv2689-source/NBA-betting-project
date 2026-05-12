from flask import Flask, send_file
from pathlib import Path
import subprocess

app = Flask(__name__)

PROJECT_DIR = Path(__file__).resolve().parent
REPORT_HTML = PROJECT_DIR / "reports" / "daily_report_v3.html"

@app.route("/")
def home():
    # 🔥 每次打開網頁，自動執行預測程式
    subprocess.run(["python", "daily_report_v3.py"])

    if REPORT_HTML.exists():
        return send_file(REPORT_HTML)
    return "報告產生失敗"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)