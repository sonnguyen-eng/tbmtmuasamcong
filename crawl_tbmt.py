import os
import time
import smtplib
import json
import requests
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 1. Cấu hình Email & Từ khóa
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = SENDER_EMAIL

KEYWORDS = [
    "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản", "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP", "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp", "máy gây mê giúp thở", "Bơm tiêm điện", "máy truyền dịch"
]
EXCLUDE_KEYWORD = "ống soi"

TZ_VN = timezone(timedelta(hours=7))
TODAY_STR = datetime.now(TZ_VN).strftime("%d/%m/%Y")


# 2. Truy vấn API TBMT theo F12 chuẩn
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


# 3. Hàm trích xuất TOÀN BỘ văn bản bao gồm dữ liệu hàng hóa chi tiết
def extract_full_searchable_text(item):
    try:
        return json.dumps(item, ensure_ascii=False).lower()
    except Exception:
        return ""


def main_tbmt():
    print(f"=== BẮT ĐẦU QUÉT TBMT SÂU DANH MỤC HÀNG HÓA - NGÀY {TODAY_STR} ===")
    raw_list = fetch_tbmt_api()
    print(f"-> Thu thập thô từ server: {len(raw_list)} TBMT.")
    
    unique_matches = {}
    for item in raw_list:
        code = str(item.get("bidNo") or item.get("notifyNo") or item.get("id") or "")
        title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
        investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
        
        full_searchable_text = extract_full_searchable_text(item)
        
        # 1. Loại trừ "ống soi"
        if EXCLUDE_KEYWORD.lower() in full_searchable_text:
            continue
            
        # 2. Quét từ khóa trong toàn bộ dữ liệu chi tiết
        for kw in KEYWORDS:
            if kw.lower() in full_searchable_text:
                if code and code not in unique_matches:
                    matched_location = "Tiêu đề" if kw.lower() in f"{code} {title}".lower() else "Chi tiết Hàng hóa"
                    unique_matches[code] = {
                        "code": code,
                        "title": title,
                        "investor": investor,
                        "matched_kw": kw,
                        "location": matched_location
                    }
                break

    matched_list = list(unique_matches.values())
    print(f"-> Khớp từ khóa (bao gồm danh mục hàng hóa): {len(matched_list)} gói TBMT.")

    # Render HTML Báo cáo
    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống kết nối thành công nhưng không tìm thấy từ khóa theo dõi trong tiêu đề hoặc danh mục hàng hóa chi tiết.</p>
        </div>
        """
    else:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0275d8;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h2>
            <p>Phát hiện <b>{len(matched_list)}</b> TBMT phù hợp danh mục theo dõi (đã quét sâu danh mục mặt hàng):</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <tr style="background-color: #fff2e6;">
                    <th align="center">STT</th>
                    <th>Mã TBMT</th>
                    <th>Tên Gói Thầu</th>
                    <th>Bên Mời Thầu</th>
                    <th align="center">Từ Khóa</th>
                    <th align="center">Vị Trí Khớp</th>
                    <th align="center">Chi Tiết</th>
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
                    <td align="center"><span style="background-color: #ffffcc;">{item['matched_kw']}</span></td>
                    <td align="center"><i>{item['location']}</i></td>
                    <td align="center"><a href="{link}" target="_blank">Xem chi tiết</a></td>
                </tr>
            """
        html_body += "</table></div>"

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
            print(f"[SUCCESS] Đã gửi báo cáo TBMT thành công tới {RECEIVER_EMAIL}!")
        except Exception as e:
            print(f"[LỖI GỬI EMAIL] {e}")


if __name__ == "__main__":
    main_tbmt()
