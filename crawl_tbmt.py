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

# 1. TẮT CẢNH BÁO MÃ HÓA SSL VỚI SERVER MUA SẮM CÔNG
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

# 2. CẤU HÌNH DỮ LIỆU & DANH SÁCH 14 TỪ KHÓA CHUẨN Y TẾ
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


# 3. TRUY VẤN DANH SÁCH TBMT THÔ (FIX TRIỆT ĐỂ LỖI HTTP 400 & HTTP 500)
def fetch_tbmt_list_raw(session):
    url_primary = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    url_fallback = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/lcnt_tbmcgtt_hsmt"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://muasamcong.mof.gov.vn",
        "Referer": "https://muasamcong.mof.gov.vn/web/guest/contractor-selection",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    collected_items = []
    # Quét 10 trang dữ liệu TBMT mới nhất (500 gói thầu)
    for page in range(10):
        # Payload chuẩn F12 với popularKeyword chống lỗi HTTP 400
        payload_primary = {
            "pageSize": 50,
            "pageNumber": page,
            "sort": "createdDate desc",
            "popularKeyword": {
                "status": 1
            }
        }
        
        try:
            res = session.post(url_primary, json=payload_primary, headers=headers, timeout=25, verify=False)
            
            # Nếu Endpoint chính không trả về 200, tự động switch sang Fallback Endpoint
            if res.status_code != 200:
                payload_fb = {
                    "pageSize": 50,
                    "pageNumber": page,
                    "sort": "createdDate desc",
                    "query": [
                        {
                            "index": "es-tbmt",
                            "matchType": "all-1",
                            "matchFields": ["notifyNo", "bidName"]
                        }
                    ]
                }
                res = session.post(url_fallback, json=payload_fb, headers=headers, timeout=25, verify=False)

            if res.status_code == 200:
                data = res.json()
                data_list = []
                if isinstance(data, dict):
                    data_list = data.get("result", {}).get("data", [])
                    if not data_list and isinstance(data.get("result"), list):
                        data_list = data.get("result", [])
                elif isinstance(data, list) and len(data) > 0:
                    data_list = data[0].get("result", {}).get("data", [])

                if not data_list:
                    print(f"-> Dừng quét tại trang {page}: Đã lấy hết danh sách gói thầu mới.")
                    break
                
                collected_items.extend(data_list)
                print(f"   + Lấy thành công trang {page + 1}: thêm {len(data_list)} TBMT (Tích lũy: {len(collected_items)}).")
                if len(data_list) < 50:
                    break
            else:
                print(f"[CẢNH BÁO HTTP {res.status_code}] Tại trang {page}")
                continue
            time.sleep(0.3)
        except Exception as e:
            print(f"[LỖI TRUY VẤN MẠNG] Trang {page}: {e}")
            continue

    return collected_items


# 4. TRUY VẤN TÌM KIẾM THEO TỪ KHÓA BẰNG SMART SEARCH
def search_tbmt_by_keyword(session, keyword):
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/smart/search"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://muasamcong.mof.gov.vn",
        "Referer": "https://muasamcong.mof.gov.vn/web/guest/contractor-selection",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    
    items_found = []
    kw_str = str(keyword).strip()
    
    for page in range(3):
        payload = [
            {
                "pageSize": 50,
                "pageNumber": str(page),
                "query": [
                    {
                        "index": "es-tbmt",
                        "matchType": "all-1",
                        "matchFields": ["notifyNo", "bidName"],
                        "keySearch": kw_str
                    },
                    {
                        "name": "type",
                        "searchType": "in",
                        "fieldValues": ["es-tbmt"]
                    }
                ]
            }
        ]
        try:
            res = session.post(url, json=payload, headers=headers, timeout=20, verify=False)
            if res.status_code == 200:
                data = res.json()
                data_list = []
                if isinstance(data, list) and len(data) > 0:
                    data_list = data[0].get("result", {}).get("data", [])
                elif isinstance(data, dict):
                    data_list = data.get("result", {}).get("data", [])

                if not data_list:
                    break
                items_found.extend(data_list)
                if len(data_list) < 50:
                    break
            time.sleep(0.2)
        except Exception:
            break

    return items_found


# 5. CHUYỂN TOÀN BỘ NESTED JSON THÀNH STR ĐỂ DEEP-SEARCH LOCAL
def extract_full_searchable_text(item):
    try:
        return json.dumps(item, ensure_ascii=False).lower()
    except Exception:
        return ""


# 6. CHƯƠNG TRÌNH CHÍNH
def main_tbmt():
    print(f"=== BẮT ĐẦU CRAWL VÀ BÓC TÁCH SÂU TBMT - NGÀY {TODAY_STR} ===")
    
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    
    unique_matches = {}
    
    # BƯỚC 1: Kéo danh sách gói thầu TBMT tổng thể từ server
    print("-> Đang tải danh sách TBMT tổng thể...")
    raw_list = fetch_tbmt_list_raw(session)
    print(f"-> Thu thập thành công tổng cộng: {len(raw_list)} bản ghi thô từ máy chủ.")
    
    # Quét từ khóa trong danh sách thầu thô
    for item in raw_list:
        code = str(item.get("bidNo") or item.get("notifyNo") or item.get("code") or item.get("id") or "")
        title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
        investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
        full_text = extract_full_searchable_text(item)
        
        for kw in KEYWORDS:
            if kw.lower() in full_text:
                if code and code not in unique_matches:
                    location = "Tiêu đề" if kw.lower() in f"{code} {title}".lower() else "Chi tiết Hàng hóa"
                    unique_matches[code] = {
                        "code": code,
                        "title": title,
                        "investor": investor,
                        "matched_kw": kw,
                        "location": location
                    }

    # BƯỚC 2: Quét trực tiếp 14 từ khóa để đảm bảo không bỏ sót
    print("-> Đang thực hiện tìm kiếm sâu theo từng từ khóa y tế...")
    for kw in KEYWORDS:
        kw_items = search_tbmt_by_keyword(session, kw)
        for item in kw_items:
            code = str(item.get("bidNo") or item.get("notifyNo") or item.get("code") or item.get("id") or "")
            title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
            investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
            
            if code and code not in unique_matches:
                unique_matches[code] = {
                    "code": code,
                    "title": title,
                    "investor": investor,
                    "matched_kw": kw,
                    "location": "Chi tiết Hàng hóa / Biểu mẫu 10B"
                }

    matched_list = list(unique_matches.values())
    print(f"==================================================")
    print(f"-> Số gói TBMT độc bản khớp danh mục 14 từ khóa: {len(matched_list)}")
    print(f"==================================================")

    # BƯỚC 3: Render báo cáo HTML và gửi Email
    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã kết nối thành công tới server Mua Sắm Công nhưng chưa phát hiện TBMT phù hợp với 14 từ khóa theo dõi.</p>
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
                    <td align="center"><a href="{link}" target="_blank">Xem ngay</a></td>
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
