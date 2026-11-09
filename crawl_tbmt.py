import os
import time
import json
import smtplib
import ssl
import requests
import urllib3
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from requests.adapters import HTTPAdapter

# 1. TẮT CẢNH BÁO MÃ HÓA SSL CHO SERVER MUA SẮM CÔNG
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

# 2. CẤU HÌNH BIẾN MÔI TRƯỜNG & ĐỦ 14 TỪ KHÓA CHUẨN Y TẾ
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", SENDER_EMAIL).strip()

KEYWORDS = [
    "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản",
    "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP",
    "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp",
    "máy gây mê giúp thở", "Bơm tiêm điện", "máy truyền dịch"
]

TZ_VN = timezone(timedelta(hours=7))
TODAY_STR = datetime.now(TZ_VN).strftime("%d/%m/%Y")
BASE_DOMAIN = "muasamcong.mof.gov.vn"

# BƯỚC 1: LẤY DỮ LIỆU THÔ VỚI PAYLOAD "pageNumber" CHUỖI (CHỐNG LỖI HTTP 500)
def fetch_tbmt_list_raw(session, max_pages=10):
    url = f"https://{BASE_DOMAIN}/o/egp-portal-contractor-selection-v2/services/lcnt_tbmcgtt_hsmt"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": f"https://{BASE_DOMAIN}",
        "Referer": f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    collected_items = []
    
    for page in range(max_pages):
        # Đã fix: pageNumber dạng chuỗi "0", "1"... để ElasticSearch không bị HTTP 500
        payload = {
            "pageSize": 50,
            "pageNumber": str(page),
            "sort": "createdDate desc",
            "query": [
                {
                    "index": "es-contractor-selection",
                    "filters": [
                        {"fieldName": "type", "searchType": "in", "fieldValues": ["es-notify-contractor"]}
                    ]
                }
            ]
        }
        try:
            res = session.post(url, json=payload, headers=headers, timeout=25, verify=False)
            if res.status_code == 200:
                data = res.json()
                data_list = []
                
                # Fix lỗi truy xuất mảng/object từ NotebookLM
                if isinstance(data, dict):
                    data_list = data.get("result", {}).get("data", [])
                    if not data_list and isinstance(data.get("result"), list):
                        data_list = data.get("result", [])
                elif isinstance(data, list) and len(data) > 0:
                    data_list = data[0].get("result", {}).get("data", [])

                if not data_list:
                    print(f"-> Dừng phân trang tại trang {page}: Đã thu thập xong danh sách mới.")
                    break
                    
                collected_items.extend(data_list)
                print(f" + Trang {page + 1}: Lấy thành công {len(data_list)} bản ghi (Tích lũy: {len(collected_items)}).")
                if len(data_list) < 50:
                    break
            else:
                print(f"[CẢNH BÁO MẠNG HTTP {res.status_code}] Trang {page}")
            time.sleep(0.3)
        except Exception as e:
            print(f"[LỖI TRUY VẤN MẠNG] Trang {page}: {e}")
            continue
            
    return collected_items

# BƯỚC 2: DEEP-SCAN TOÀN BỘ CẤU TRÚC JSON & CHUỖI ESCAPE FORMVALUE
def extract_searchable_text(item):
    try:
        raw_str = json.dumps(item, ensure_ascii=False).lower()
        form_val = item.get("formValue")
        if form_val and isinstance(form_val, str):
            raw_str += " " + form_val.lower()
        return raw_str
    except Exception:
        return ""

def pre_filter_candidates(raw_list):
    candidates = []
    unique_codes = set()
    for item in raw_list:
        code = str(item.get("bidNo") or item.get("notifyNo") or item.get("code") or item.get("id") or "")
        title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
        investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
        full_text = extract_searchable_text(item)
        
        # Quét 14 từ khóa (KHÔNG LOẠI TRỪ ỐNG SOI)
        matched_kw = [kw for kw in KEYWORDS if kw.lower() in full_text]
        if matched_kw and code not in unique_codes:
            unique_codes.add(code)
            location = "Tiêu đề" if any(kw.lower() in f"{code} {title}".lower() for kw in matched_kw) else "Chi tiết Hàng hóa / Biểu mẫu 10B"
            candidates.append({
                "code": code,
                "title": title,
                "investor": investor,
                "matched_kw": matched_kw,
                "location": location,
                "snippet": full_text[:1200]
            })
    return candidates

# BƯỚC 3: PHÂN TÍCH VÀ BỔ SUNG LÝ DO TÓM TẮT BẰNG GEMINI AI (KHÔNG BỎ SÓT GÓI THẦU KHỚP)
def analyze_with_gemini(candidates):
    if not candidates:
        return []
    if not GEMINI_API_KEY:
        print("[THÔNG BÁO] Chưa cấu hình GEMINI_API_KEY. Dùng kết quả lọc từ khóa trực tiếp.")
        return candidates

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""
Bạn là chuyên gia phân tích dữ liệu đấu thầu Y tế.
Dưới đây là danh sách các Thông báo mời thầu (TBMT) đã khớp với danh mục từ khóa theo dõi: {json.dumps(KEYWORDS, ensure_ascii=False)}

Yêu cầu:
1. Giữ nguyên tất cả các gói thầu trong danh sách đầu vào.
2. Với mỗi gói thầu, viết 1 câu tóm tắt ngắn gọn lý do phù hợp hoặc thiết bị chính xuất hiện trong gói thầu.
3. Trả về đúng mảng JSON chứa các object:
   - "code": mã TBMT
   - "title": tên gói thầu
   - "investor": bên mời thầu
   - "matched_kw": danh sách từ khóa khớp
   - "location": vị trí khớp
   - "reason": tóm tắt lý do/thiết bị từ AI

Danh sách:
{json.dumps(candidates, ensure_ascii=False, indent=2)}

Chỉ trả về chuỗi JSON thuần túy (không kèm codeblock markdown hay chữ thừa).
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        content = response.text.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        
        return json.loads(content.strip())
    except Exception as e:
        print(f"[THÔNG BÁO AI] {e}. Dùng kết quả quét từ khóa trực tiếp.")
        return candidates

# BƯỚC 4: GỬI BÁO CÁO EMAIL QUA SMTP
def send_email_report(matched_list, raw_count):
    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã thu thập <b>{raw_count}</b> bản ghi nhưng chưa phát hiện gói thầu khớp danh mục 14 từ khóa theo dõi.</p>
        </div>
        """
    else:
        rows = ""
        for idx, item in enumerate(matched_list, 1):
            code = item.get("code", "")
            title = item.get("title", "")
            investor = item.get("investor", "")
            location = item.get("location", "Biểu mẫu / Hàng hóa")
            reason = item.get("reason") or "Khớp từ khóa theo dõi y tế"
            matched_kw = item.get("matched_kw", "")
            if isinstance(matched_kw, list):
                matched_kw = ", ".join(matched_kw)
                
            link = f"https://{BASE_DOMAIN}/vi/web/guest/initiator?tbmtNo={code}"
            rows += f"""
            <tr>
                <td align="center">{idx}</td>
                <td><b>{code}</b></td>
                <td>{title}</td>
                <td>{investor}</td>
                <td align="center"><span style="background-color: #ffffcc; padding: 3px 6px; border-radius: 3px;">{matched_kw}</span></td>
                <td align="center"><i>{location}</i></td>
                <td><small style="color: #333;">{reason}</small></td>
                <td align="center"><a href="{link}" target="_blank" style="color: #0275d8; font-weight: bold;">Xem ngay</a></td>
            </tr>
            """
            
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0275d8;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h2>
            <p>Đã quét <b>{raw_count}</b> bản ghi thô và bóc tách sâu phát hiện <b>{len(matched_list)}</b> gói thầu phù hợp:</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; border-color: #ddd;">
                <tr style="background-color: #fff2e6;">
                    <th align="center">STT</th>
                    <th>Mã TBMT</th>
                    <th>Tên Gói Thầu</th>
                    <th>Bên Mời Thầu</th>
                    <th align="center">Từ Khóa Khớp</th>
                    <th align="center">Vị Trí Khớp</th>
                    <th>Phân Tích Chi Tiết</th>
                    <th align="center">Chi Tiết</th>
                </tr>
                {rows}
            </table>
        </div>
        """

    if SENDER_EMAIL and SENDER_PASSWORD:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[TBMT Y TẾ] Báo cáo gói thầu ngày {TODAY_STR}"
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
            print(f"[SUCCESS] Đã gửi báo cáo TBMT thành công tới {RECEIVER_EMAIL}!")
        except Exception as e:
            print(f"[LỖI GỬI EMAIL SMTP] {e}")

def main():
    print(f"=== CRAWL & BÓC TÁCH SÂU TBMT - NGÀY {TODAY_STR} ===")
    session = requests.Session()
    session.mount("https://", CustomSSLAdapter())
    
    try:
        session.get(f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index", timeout=15, verify=False)
    except Exception:
        pass

    raw_list = fetch_tbmt_list_raw(session, max_pages=10)
    candidates = pre_filter_candidates(raw_list)
    final_matches = analyze_with_gemini(candidates)
    send_email_report(final_matches, len(raw_list))

if __name__ == "__main__":
    main()
