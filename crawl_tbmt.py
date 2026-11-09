import os
import time
import smtplib
import json
import requests
import urllib3
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from requests.adapters import HTTPAdapter

# 1. TẮT CẢNH BÁO SSL SECURITY LEVEL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers('DEFAULT@SECLEVEL=1:ALL')
        except Exception:
            ctx.set_ciphers('DEFAULT:ALL')
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

# 2. CẤU HÌNH EMAIL & 14 TỪ KHÓA
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = SENDER_EMAIL

KEYWORDS = [
    "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản", 
    "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP",
    "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp", 
    "máy gây mê giúp thở", "Bơm tiêm điện", "máy truyền dịch"
]
TZ_VN = timezone(timedelta(hours=7))
TODAY_STR = datetime.now(TZ_VN).strftime("%d/%m/%Y")


# 3. TRUY VẤN API TBMT VỚI PAYLOAD F12 CHUẨN XÁC
def fetch_tbmt_by_keyword(session, keyword):
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://muasamcong.mof.gov.vn",
        "Referer": "https://muasamcong.mof.gov.vn/web/guest/contractor-selection",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    items_found = []
    # Quét 5 trang đầu tiên cho mỗi từ khóa (250 gói/từ khóa)
    for page in range(5):
        # Payload đóng gói đúng chuẩn F12 từ tài liệu của anh
        payload = {
            "pageSize": 50,
            "pageNumber": str(page),
            "query": [
                {
                    "index": "popular-keyword",
                    "keySearch": str(keyword).strip()
                }
            ]
        }
        try:
            res = session.post(url, json=payload, headers=headers, timeout=25, verify=False)
            if res.status_code == 200:
                data = res.json()
                data_list = []
                if isinstance(data.get("result"), dict):
                    data_list = data.get("result", {}).get("data", [])
                elif isinstance(data.get("result"), list):
                    data_list = data.get("result", [])

                if not data_list:
                    break
                items_found.extend(data_list)
                if len(data_list) < 50:
                    break
            time.sleep(0.2)
        except Exception as e:
            print(f"[CẢNH BÁO TBMT API] Từ khóa '{keyword}' trang {page}: {e}")
            break

    return items_found


# 4. TRÍCH XUẤT VĂN BẢN QUÉT SÂU DANH MỤC HÀNG HÓA
def extract_full_searchable_text(item):
    try:
        return json.dumps(item, ensure_ascii=False).lower()
    except Exception:
        return ""


# 5. CHƯƠNG TRÌNH CHÍNH
def main_tbmt():
    print(f"=== BẮT ĐẦU QUÉT THÔNG BÁO MỜI THẦU (TBMT) - NGÀY {TODAY_STR} ===")
    
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    
    unique_matches = {}
    total_raw_scanned = 0

    for kw in KEYWORDS:
        print(f"-> Đang truy vấn từ khóa chuẩn F12: '{kw}'...")
        raw_items = fetch_tbmt_by_keyword(session, kw)
        total_raw_scanned += len(raw_items)
        print(f"   + Tìm thấy: {len(raw_items)} bản ghi.")

        for item in raw_items:
            code = str(item.get("bidNo") or item.get("notifyNo") or item.get("id") or "")
            title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
            investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
            
            full_searchable_text = extract_full_searchable_text(item)
            
            # 1. Bỏ qua nếu có từ khóa loại trừ "ống soi"
            if EXCLUDE_KEYWORD.lower() in full_searchable_text:
                continue
                
            # 2. Ghi nhận gói thầu khớp
            if code and code not in unique_matches:
                matched_location = "Tiêu đề" if kw.lower() in f"{code} {title}".lower() else "Chi tiết Hàng hóa"
                unique_matches[code] = {
                    "code": code,
                    "title": title,
                    "investor": investor,
                    "matched_kw": kw,
                    "location": matched_location
                }

    matched_list = list(unique_matches.values())
    print(f"==================================================")
    print(f"-> Tổng kết bản ghi thô thu thập: {total_raw_scanned}")
    print(f"-> Số gói TBMT độc bản khớp từ khóa: {len(matched_list)}")
    print(f"==================================================")

    # 6. RENDER HTML VÀ GỬI EMAIL
    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã kết nối thành công nhưng không tìm thấy thông báo thầu nào khớp danh mục 14 từ khóa theo dõi.</p>
        </div>
        """
    else:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0275d8;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h2>
            <p>Phát hiện <b>{len(matched_list)}</b> TBMT phù hợp danh mục từ khóa theo dõi (đã bóc tách sâu chi tiết hàng hóa):</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%;">
                <tr style="background-color: #fff2e6;">
                    <th align="center">STT</th>
                    <th>Mã TBMT</th>
                    <th>Tên Gói Thầu</th>
                    <th>Bên Mời Thầu</th>
                    <th align="center">Từ Khóa Khớp</th>
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
