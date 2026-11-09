import os
import time
import smtplib
import requests
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 1. Cấu hình Email & Từ khóa
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = SENDER_EMAIL

KEYWORDS = [
    "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản", 
    "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP"
]
EXCLUDE_KEYWORD = "ống soi"

TZ_VN = timezone(timedelta(hours=7))
TODAY_STR = datetime.now(TZ_VN).strftime("%d/%m/%Y")

# 2. API TBMT khớp 100% Payload F12
def fetch_tbmt_api():
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://muasamcong.mof.gov.vn",
        "Referer": "https://muasamcong.mof.gov.vn/web/guest/contractor-selection",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    collected_items = []
    # Quét 4 trang đầu tiên (200 TBMT mới nhất)
    for page in range(4):
        payload = {
            "pageSize": 50,
            "pageNumber": page,
            "sort": "createdDate desc",
            "popularKeyword": {
                "status": 1
            }
        }
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=25)
            if res.status_code == 200:
                data = res.json()
                items = data.get("result", {}).get("data", [])
                if not items and isinstance(data.get("result"), list):
                    items = data.get("result", [])

                if not items:
                    break
                collected_items.extend(items)
            time.sleep(0.3)
        except Exception as e:
            print(f"[CẢNH BÁO TBMT API] Trang {page}: {e}")
            break

    return collected_items

def main_tbmt():
    print(f"=== BẮT ĐẦU QUÉT TBMT - NGÀY {TODAY_STR} ===")
    raw_list = fetch_tbmt_api()
    print(f"-> Thu thập thô từ server: {len(raw_list)} TBMT.")
    
    unique_matches = {}
    for item in raw_list:
        code = str(item.get("bidNo") or item.get("notifyNo") or item.get("id") or "")
        title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
        investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
        
        full_text = f"{code} {title} {investor}".lower()
        
        # Bỏ qua nếu chứa "ống soi"
        if EXCLUDE_KEYWORD.lower() in full_text:
            continue
            
        # Lọc theo danh mục từ khóa
        for kw in KEYWORDS:
            if kw.lower() in full_text:
                if code and code not in unique_matches:
                    unique_matches[code] = {
                        "code": code,
                        "title": title,
                        "investor": investor,
                        "matched_kw": kw
                    }
                break

    matched_list = list(unique_matches.values())
    print(f"-> Số TBMT khớp từ khóa: {len(matched_list)} gói.")

    # Render HTML Báo cáo
    if not matched_list:
        html_body = f"<h3>BÁO CÁO TBMT NGÀY {TODAY_STR}</h3><p>Hệ thống kết nối thành công nhưng không tìm thấy TBMT nào khớp từ khóa theo dõi.</p>"
    else:
        html_body = f"""
        <h2>BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h2>
        <p>Phát hiện <b>{len(matched_list)}</b> TBMT phù hợp:</p>
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #fff2e6;">
                <th>STT</th><th>Mã TBMT</th><th>Tên Gói Thầu</th><th>Bên Mời Thầu</th><th>Từ Khóa</th><th>Chi Tiết</th>
            </tr>
        """
        for idx, item in enumerate(matched_list, 1):
            link = f"https://muasamcong.mof.gov.vn/vi/web/guest/initiator?tbmtNo={item['code']}"
            html_body += f"""
            <tr>
                <td align="center">{idx}</td>
                <td><b>{item['code']}</b></td>
                <td>{item['title']}</td>
                <td>{item['investor']}</td>
                <td align="center">{item['matched_kw']}</td>
                <td align="center"><a href="{link}" target="_blank">Xem chi tiết</a></td>
            </tr>
            """
        html_body += "</table>"

    # Gửi Email
    if SENDER_EMAIL and SENDER_PASSWORD:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[TBMT Y TẾ] Báo cáo ngày {TODAY_STR}"
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
            print("[SUCCESS] Đã gửi Email TBMT thành công!")
        except Exception as e:
            print(f"[LỖI EMAIL] {e}")

if __name__ == "__main__":
    main_tbmt()
