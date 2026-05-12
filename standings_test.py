import requests
import os
from dotenv import load_dotenv

print("開始抓 standings")

load_dotenv()
api_key = os.getenv("BALLDONTLIE_API_KEY")

url = "https://api.balldontlie.io/nba/v1/standings"

headers = {
    "Authorization": api_key
}

params = {
    "season": 2025
}

response = requests.get(url, headers=headers, params=params)

print("狀態碼：", response.status_code)
print(response.text[:1000])