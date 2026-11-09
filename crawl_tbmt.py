import os
import time
import json
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# 1. CẤU HÌNH BIẾN MÔI TRƯỜNG & DANH SÁCH 14 TỪ KHÓA Y TẾ CHUẨN
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "").strip() or SENDER_EMAIL

KEYWORDS = [
    "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản",
    "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP",
    "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp",
    "máy gây mê giúp thở", "Bơm tiêm điện", "máy truyền dịch"
]

TZ_VN = timezone(timedelta(hours=7))
TODAY_STR = datetime.now(TZ_VN).strftime("%d/%m/%Y")
BASE_DOMAIN = "muasamcong.mof.gov.vn"

# 2. KHỞI TẠO CHROME HEADLESS
def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    return driver

# 3. TRÍCH XUẤT DỮ LIỆU CHUẨN TỪ ENDPOINT SMART SEARCH CHỨA PAGE.CONTENT
def fetch_tbmt_by_smart_search_curl(driver):
    print("-> Đang mở trình duyệt và khởi tạo Session Mua Sắm Công...")
    driver.get(f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index")
    time.sleep(10)

    # Trích xuất Token bảo mật động sinh ra trên trình duyệt
    js_get_token = """
    let token = '';
    let entries = window.performance.getEntriesByType('resource');
    for (let e of entries) {
        if (e.name && e.name.includes('smart/search?token=')) {
            let match = e.name.match(/token=([^&]+)/);
            if (match) {
                token = match[1];
                break;
            }
        }
    }
    return token;
    """
    token_val = driver.execute_script(js_get_token)
    if token_val:
        print(f"-> Token bảo mật trích xuất từ Session: {token_val[:20]}...")
    else:
        print("-> Đang dùng URL service mặc định...")

    unique_matches = {}

    js_fetch_search = """
    var callback = arguments[arguments.length - 1];
    var kw = arguments[0];
    var token = arguments[1];
    
    let url = '/o/egp-portal-contractor-selection-v2/services/smart/search';
    if (token) {
        url += '?token=' + token;
    }
    
    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*'
        },
        body: JSON.stringify([{
            pageSize: 50,
            pageNumber: "0",
            query: [{
                index: "es-contractor-selection",
                keyWord: kw,
                matchType: "all-1",
                matchFields: ["notifyNo", "bidName"],
                filters: [
                    { fieldName: "type", searchType: "in", fieldValues: ["es-notify-contractor"] },
                    { fieldName: "caseKHKQ", searchType: "not_in", fieldValues: ["1"] }
                ]
            }]
        }])
    })
    .then(response => response.json())
    .then(data => callback(data))
    .catch(err => callback({ error: err.toString() }));
    """

    for kw in KEYWORDS:
        print(f"-> Truy vấn API Smart Search cho từ khóa: '{kw}'...")
        try:
            res = driver.execute_async_script(js_fetch_search, kw, token_val)
            data_list = []
            
            # Trích xuất chính xác từ thuộc tính page.content
            if isinstance(res, list) and len(res) > 0:
                first_item = res[0]
                if isinstance(first_item, dict):
                    data_list = first_item.get("page", {}).get("content", [])
            elif isinstance(res, dict):
                data_list = res.get("page", {}).get("content", [])

            if not isinstance(data_list, list):
                data_list = []

            print(f"   + Server trả về: {len(data_list)} bản ghi cho từ khóa '{kw}'.")

            for item in data_list:
                if not isinstance(item, dict):
                    continue
                    
                code = str(item.get("notifyNo") or item.get("notifyNoStand") or item.get("bidNo") or item.get("id") or "")
                
                # Sửa xử lý bidName là mảng chuỗi
                bid_name_raw = item.get("bidName")
                if isinstance(bid_name_raw, list) and len(bid_name_raw) > 0:
                    title = str(bid_name_raw[0])
                else:
                    title = str(bid_name_raw or item.get("name") or "")
                    
                investor = str(item.get("investorName") or item.get("procuringEntityName") or "")
                
                # Deep-scan bao gồm toàn bộ thuộc tính và chuỗi escape formValue
                raw_str = json.dumps(item, ensure_ascii=False).lower()
                form_val = item.get("formValue")
                if form_val and isinstance(form_val, str):
                    raw_str += " " + form_val.lower()

                if kw.lower() in raw_str:
                    if code and code not in unique_matches:
                        location = "Tiêu đề" if kw.lower() in f"{code} {title}".lower() else "Chi tiết Hàng hóa / Biểu mẫu 10B"
                        unique_matches[code] = {
                            "code": code,
                            "title": title,
                            "investor": investor,
                            "matched_kw": kw,
                            "location": location,
                            "snippet": raw_str[:1200]
                        }
            time.sleep(0.5)
        except Exception as e:
            print(f"[CẢNH BÁO FETCH] Từ khóa '{kw}': {e}")
            continue

    return list(unique_matches.values())

# 4. PHÂN TÍCH VỚI GEMINI AI
def analyze_with_gemini(candidates):
    if not candidates or not GEMINI_API_KEY:
        return candidates

    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""
Bạn là chuyên gia phân tích dữ liệu đấu thầu Y tế.
Dưới đây là danh sách các Thông báo mời thầu (TBMT) đã khớp từ khóa y tế.

Yêu cầu:
1. Giữ nguyên toàn bộ các gói thầu trong danh sách đầu vào.
2. Viết 1 câu tóm tắt ngắn gọn lý do phù hợp hoặc mặt hàng chính trong gói thầu.
3. Trả về đúng mảng JSON chứa các object: "code", "title", "investor", "matched_kw", "location", "reason".

Danh sách:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
Chỉ trả về chuỗi JSON thuần túy (không kèm markdown).
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        content = response.text.strip()
        if content.startswith("```json"): content = content[7:]
        if content.startswith("```"): content = content[3:]
        if content.endswith("```"): content = content[:-3]
        return json.loads(content.strip())
    except Exception as e:
        print(f"[THÔNG BÁO AI] {e}")
        return candidates

# 5. GỬI EMAIL BÁO CÁO
def send_email_report(matched_list, raw_count):
    if not RECEIVER_EMAIL:
        print("[LỖI CẤU HÌNH] Chưa khai báo email nhận.")
        return

    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã truy vấn thành công dữ liệu từ Mua Sắm Công nhưng chưa phát hiện gói thầu mới khớp 14 từ khóa theo dõi.</p>
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
            if isinstance(matched_kw, list): matched_kw = ", ".join(matched_kw)
                
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
            <p>Đã quét dữ liệu Mua Sắm Công và phát hiện <b>{len(matched_list)}</b> gói thầu phù hợp:</p>
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
            print(f"[SUCCESS] Đã gửi báo cáo TBMT thành công tới email: {RECEIVER_EMAIL}!")
        except Exception as e:
            print(f"[LỖI GỬI EMAIL SMTP] {e}")

def main():
    print(f"=== CRAWL & BÓC TÁCH SÂU TBMT SELENIUM SMART SEARCH - NGÀY {TODAY_STR} ===")
    driver = init_driver()
    try:
        matched_list = fetch_tbmt_by_smart_search_curl(driver)
        final_matches = analyze_with_gemini(matched_list)
        send_email_report(final_matches, len(matched_list))
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
