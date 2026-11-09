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

# 1. CẤU HÌNH BIẾN MÔI TRƯỜNG & DANH SÁCH 12 TỪ KHÓA Y TẾ
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "").strip() or SENDER_EMAIL

KEYWORDS = [
    "nội soi", "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", 
    "ERCP", "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp", 
    "máy gây mê", "Bơm tiêm điện", "máy truyền dịch"
]

# 2. DANH SÁCH 14 TỈNH / THÀNH PHỐ MỤC TIÊU
TARGET_LOCATIONS = [
    "Thành phố Đà Nẵng", "Tỉnh Quảng Ngãi", "Tỉnh Đắk Lắk", "Tỉnh Gia Lai", 
    "Tỉnh Khánh Hòa", "Tỉnh Lâm Đồng", "Thành phố Hồ Chí Minh", "Thành phố Đồng Nai", 
    "Tỉnh Tây Ninh", "Tỉnh Đồng Tháp", "Tỉnh Vĩnh Long", "Tỉnh An Giang", 
    "Thành phố Cần Thơ", "Tỉnh Cà Mau"
]

TZ_VN = timezone(timedelta(hours=7))
NOW_VN = datetime.now(TZ_VN)
TODAY_STR = NOW_VN.strftime("%d/%m/%Y")
TODAY_DATE_ONLY = NOW_VN.date()

BASE_DOMAIN = "muasamcong.mof.gov.vn"

# 3. KHỞI TẠO CHROME HEADLESS
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

# 4. KIỂM TRA LỌC ĐÚNG TBMT ĐĂNG TỪ 00H00 NGÀY HÔM NAY
def is_published_strictly_today(item):
    pub_date_str = item.get("publicDate") or item.get("originalPublicDate") or ""
    if not pub_date_str:
        return True
    
    try:
        clean_date_str = pub_date_str.split(".")[0].replace("Z", "")
        dt_obj = datetime.strptime(clean_date_str, "%Y-%m-%dT%H:%M:%S")
        return dt_obj.date() == TODAY_DATE_ONLY
    except Exception:
        return True

# 5. HÀM KIỂM TRA & CHUẨN HÓA TỈNH / THÀNH PHỐ
def check_location(item, raw_str):
    found_locs = []
    
    investor = str(item.get("investorName") or item.get("procuringEntityName") or "").lower()
    title = str(item.get("bidName") or "").lower()
    search_text = f"{investor} {title} {raw_str}"

    locations_arr = item.get("locations", [])
    if isinstance(locations_arr, list):
        for loc in locations_arr:
            if isinstance(loc, dict):
                prov_name = loc.get("provName", "")
                dist_name = loc.get("districtName", "")
                search_text += f" {prov_name} {dist_name}".lower()

    if "đắk lắk" in search_text or "đắc lắc" in search_text or "tây nguyên" in search_text or "buôn ma thuột" in search_text:
        found_locs.append("Tỉnh Đắk Lắk")

    for target in TARGET_LOCATIONS:
        clean_kw = target.replace("Thành phố ", "").replace("Tỉnh ", "").lower()
        if clean_kw in search_text:
            if clean_kw == "đồng nai":
                found_locs.append("Thành phố Đồng Nai")
            elif clean_kw not in ["đắk lắk", "đắc lắc"]:
                found_locs.append(target)

    return list(set(found_locs))

# 6. BÓC TÁCH DEEP-SCAN SÂU BIỂU MẪU 10B / HSMT (API GET-ALL-HSMT)
def get_hsmt_form_values(driver, notify_no, notify_version="00", bid_field="HH"):
    js_get_hsmt = """
    var callback = arguments[arguments.length - 1];
    var notifyNo = arguments[0];
    var notifyVersion = arguments[1];
    var bidField = arguments[2];
    
    fetch('/api/unau/portal/ebidinv/get-all-hsmt', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*'
        },
        body: JSON.stringify({
            body: {
                notifyNo: notifyNo,
                notifyVersion: notifyVersion,
                bidField: bidField,
                processType: "LDT"
            }
        })
    })
    .then(response => response.json())
    .then(data => callback(data))
    .catch(err => callback({ error: err.toString() }));
    """
    try:
        res = driver.execute_async_script(js_get_hsmt, notify_no, notify_version, bid_field)
        if isinstance(res, (list, dict)):
            return json.dumps(res, ensure_ascii=False).lower()
    except Exception:
        pass
    return ""

# 7. QUÉT TỔNG THỂ TẤT CẢ GÓI THẦU TRONG NGÀY + DEEP-SCAN BIỂU MẪU 10B
def fetch_tbmt_today(driver):
    print(f"-> Đang kết nối Mua Sắm Công (Quét tổng thể TBMT ngày {TODAY_STR})...")
    driver.get(f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index")
    time.sleep(10)

    # Đổi cơ chế: Gọi lấy danh sách tất cả TBMT mới nhất phát hành (không search theo từ khóa để tránh bỏ sót)
    js_fetch_all = """
    var callback = arguments[arguments.length - 1];
    
    fetch('/o/egp-portal-contractor-selection-v2/services/lcnt_tbmcgtt_hsmt', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*'
        },
        body: JSON.stringify({
            pageSize: 100,
            pageNumber: 0,
            sort: "publicDate desc"
        })
    })
    .then(response => response.json())
    .then(data => callback(data))
    .catch(err => callback({ error: err.toString() }));
    """

    unique_matches = {}
    try:
        res = driver.execute_async_script(js_fetch_all)
        data_list = []
        if isinstance(res, dict):
            data_list = res.get("result", {}).get("data", []) or res.get("page", {}).get("content", [])
        elif isinstance(res, list) and len(res) > 0:
            first = res[0]
            if isinstance(first, dict):
                data_list = first.get("result", {}).get("data", []) or first.get("page", {}).get("content", []) or first.get("data", [])

        print(f"   + Đã lấy về {len(data_list)} gói thầu mới đăng tải trong ngày.")

        for item in data_list:
            if not isinstance(item, dict):
                continue

            # CHỈ LẤY TBMT PHÁT HÀNH TỪ 00H00 NGÀY HÔM NAY
            if not is_published_strictly_today(item):
                continue

            code = str(item.get("notifyNo") or item.get("notifyNoStand") or item.get("bidNo") or item.get("id") or "")
            notify_ver = str(item.get("notifyVersion") or "00")
            bid_field = str(item.get("bidField") or "HH")

            bid_name_raw = item.get("bidName")
            if isinstance(bid_name_raw, list) and len(bid_name_raw) > 0:
                title = str(bid_name_raw[0])
            else:
                title = str(bid_name_raw or item.get("name") or "")
                
            investor = str(item.get("investorName") or item.get("procuringEntityName") or "")
            
            # TẦNG 1: Gom dữ liệu JSON gói thầu
            raw_str = json.dumps(item, ensure_ascii=False).lower()

            # TẦNG 2: DEEP-SCAN gọi get-all-hsmt bóc tách Biểu mẫu 10B
            if code:
                hsmt_str = get_hsmt_form_values(driver, code, notify_ver, bid_field)
                if hsmt_str:
                    raw_str += " " + hsmt_str

            # LỌC TỪ KHÓA Y TẾ VÀ ĐỊA PHƯƠNG
            matched_kws = [kw for kw in KEYWORDS if kw.lower() in raw_str]
            if matched_kws:
                matched_province = check_location(item, raw_str)
                if matched_province:
                    if code and code not in unique_matches:
                        location_detail = "Tiêu đề" if any(k.lower() in f"{code} {title}".lower() for k in matched_kws) else "Chi tiết Hàng hóa / Biểu mẫu 10B"
                        unique_matches[code] = {
                            "code": code,
                            "title": title,
                            "investor": investor,
                            "province": ", ".join(matched_province),
                            "matched_kw": ", ".join(matched_kws),
                            "location": location_detail,
                            "snippet": raw_str[:1200]
                        }

    except Exception as e:
        print(f"[CẢNH BÁO QUÉT] Lỗi bóc tách: {e}")

    return list(unique_matches.values())

# 8. PHÂN TÍCH VỚI GEMINI AI
def analyze_with_gemini(candidates):
    if not candidates or not GEMINI_API_KEY:
        return candidates

    target_models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""
Bạn là chuyên gia phân tích dữ liệu đấu thầu Y tế.
Dưới đây là danh sách các Thông báo mời thầu (TBMT) phát hành trong ngày {TODAY_STR} đã lọc theo địa phương và 12 từ khóa y tế.

Yêu cầu:
1. Giữ nguyên toàn bộ các gói thầu trong danh sách đầu vào.
2. Viết 1 câu tóm tắt ngắn gọn lý do phù hợp hoặc mặt hàng chính trong gói thầu.
3. Trả về đúng mảng JSON chứa các object: "code", "title", "investor", "province", "matched_kw", "location", "reason".

Danh sách:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
Chỉ trả về chuỗi JSON thuần túy (không kèm markdown).
"""
        content = None
        for model_name in target_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                content = response.text.strip()
                if content:
                    break
            except Exception:
                continue

        if not content:
            return candidates

        if content.startswith("```json"): content = content[7:]
        if content.startswith("```"): content = content[3:]
        if content.endswith("```"): content = content[:-3]
        return json.loads(content.strip())
    except Exception as e:
        print(f"[THÔNG BÁO AI] {e}")
        return candidates

# 9. GỬI EMAIL BÁO CÁO
def send_email_report(matched_list):
    if not RECEIVER_EMAIL:
        print("[LỖI CẤU HÌNH] Chưa khai báo email nhận.")
        return

    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã quét dữ liệu từ 00h00 ngày {TODAY_STR} đến thời điểm hiện tại nhưng chưa phát hiện gói thầu mới khớp 12 từ khóa y tế tại 14 địa phương theo dõi.</p>
        </div>
        """
    else:
        rows = ""
        for idx, item in enumerate(matched_list, 1):
            code = item.get("code", "")
            title = item.get("title", "")
            investor = item.get("investor", "")
            province = item.get("province", "Theo dõi")
            location = item.get("location", "Biểu mẫu / Hàng hóa")
            reason = item.get("reason") or "Khớp từ khóa theo dõi y tế trong Biểu mẫu 10B/HSMT"
            matched_kw = item.get("matched_kw", "")
                
            link = f"https://{BASE_DOMAIN}/vi/web/guest/initiator?tbmtNo={code}"
            rows += f"""
            <tr>
                <td align="center">{idx}</td>
                <td><b>{code}</b></td>
                <td>{title}</td>
                <td>{investor}</td>
                <td align="center"><b><span style="color: #d9534f;">{province}</span></b></td>
                <td align="center"><span style="background-color: #ffffcc; padding: 3px 6px; border-radius: 3px;">{matched_kw}</span></td>
                <td align="center"><i>{location}</i></td>
                <td><small style="color: #333;">{reason}</small></td>
                <td align="center"><a href="{link}" target="_blank" style="color: #0275d8; font-weight: bold;">Xem ngay</a></td>
            </tr>
            """
            
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0275d8;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h2>
            <p>Đã lọc phát hiện <b>{len(matched_list)}</b> gói thầu Y tế phát hành từ 00h00 ngày hôm nay ({TODAY_STR}) thuộc địa phương theo dõi:</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; border-color: #ddd;">
                <tr style="background-color: #fff2e6;">
                    <th align="center">STT</th>
                    <th>Mã TBMT</th>
                    <th>Tên Gói Thầu</th>
                    <th>Bên Mời Thầu</th>
                    <th align="center">Tỉnh / TP</th>
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
        msg["Subject"] = f"[TBMT Y TẾ - LỌC TỈNH] Báo cáo gói thầu ngày {TODAY_STR}"
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
    print(f"=== CRAWL & DEEP-SCAN TBMT ĐĂNG TRONG NGÀY {TODAY_STR} ===")
    driver = init_driver()
    try:
        matched_list = fetch_tbmt_today(driver)
        final_matches = analyze_with_gemini(matched_list)
        send_email_report(final_matches)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
