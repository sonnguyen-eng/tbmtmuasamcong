import os
import time
import smtplib
import requests
from datetime import datetime, timedelta
import zoneinfo # Xử lý chính xác múi giờ Việt Nam
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = SENDER_EMAIL

# Từ khóa tìm kiếm của bạn (nên mở rộng thêm các từ đồng nghĩa)
KEYWORDS = ["Nội soi", "nội soi tiêu hóa", "nội soi ERCP", "Nội soi khí phế quản", "bàn mổ", "đèn mổ", "máy hấp tiệt trùng"]

def fetch_tbmt_data(keyword):
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    # Ép múi giờ về Việt Nam (GMT+7) chính xác 100%
    tz_vn = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    today_vn = datetime.now(tz_vn)
    
    # Quét dữ liệu trong 2 ngày gần nhất để đảm bảo không bị sót gói thầu đăng muộn
    from_date = (today_vn - timedelta(days=1)).strftime("%Y-%m-%d")
    to_date = today_vn.strftime("%Y-%m-%d")
    
    payload = {
        "page": 0,
        "size": 100, # Tăng số lượng kết quả lấy về trên 1 từ khóa lên 100
        "type": "TBMT",
        "keySearch": keyword,
        "publishDateFrom": f"{from_date}T00:00:00Z",
        "publishDateTo": f"{to_date}T23:59:59Z"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60) # Tăng timeout kết nối lên 60s
        if response.status_code == 200:
            return response.json().get("result", {}).get("data", [])
        return []
    except Exception as e:
        print(f"Lỗi khi tìm từ khóa '{keyword}': {e}")
        return []

def collect_all_tbmt(keywords):
    all_bids = {}
    for kw in keywords:
        print(f"Đang quét từ khóa: '{kw}'...")
        items = fetch_tbmt_data(kw)
        for item in items:
            bid_id = item.get("bidNo") or item.get("notifyNo") or item.get("id")
            if bid_id and bid_id not in all_bids:
                all_bids[bid_id] = item
        
        # Tạm dừng 2 giây giữa mỗi lần tìm từ khóa để quét kỹ hơn và không bị chặn IP
        time.sleep(2)

    return list(all_bids.values())
