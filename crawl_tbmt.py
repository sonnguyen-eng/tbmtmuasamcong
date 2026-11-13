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

# 1. CẤU HÌNH BIẾN MÔI TRƯỜNG & DANH SÁCH 19 TỪ KHÓA Y TẾ
RAW_GEMINI_KEYS = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_API_KEYS = [k.strip() for k in RAW_GEMINI_KEYS.split(",") if k.strip()]
current_key_index = 0

SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "").strip()
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "").strip()
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "").strip() or SENDER_EMAIL

LAST_RUN_ENV = os.environ.get("LAST_RUN_TIMESTAMP", "").strip()

KEYWORDS = [
    "nội soi", "Bàn mổ", "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", 
    "ERCP", "Monitor theo dõi bệnh nhân", "Máy hấp tiệt trùng", "Nhiệt độ thấp", 
    "máy gây mê", "Bơm tiêm điện", "máy truyền dịch", "bàn khám sản", 
    "Monitor sản khoa", "máy hấp ướt", "giường ICU", "giường cấp cứu", 
    "Bồn oxy lỏng", "Oxy trung tâm"
]

BROAD_QUERIES = [
    "thiết bị y tế", "trang thiết bị y tế", "y tế", 
    "bệnh viện", "dụng cụ y tế", "vật tư y tế"
]

# 2. DANH SÁCH 14 TỈNH / THÀNH PHỐ MỤC TIÊU (BAO GỒM TÊN CŨ/MỚI & VIẾT TẮT)
TARGET_LOCATIONS = [
    "Thành phố Đà Nẵng", "Tỉnh Quảng Ngãi", "Tỉnh Đắc Lắc", "Tỉnh Đắk Lắk", "Tỉnh Gia Lai", 
    "Tỉnh Khánh Hòa", "Tỉnh Lâm Đồng", "Thành phố Hồ Chí Minh", "Thành phố Đồng Nai", 
    "Tỉnh Tây Ninh", "Tỉnh Đồng Tháp", "Tỉnh Vĩnh Long", "Tỉnh An Giang", 
    "Thành phố Cần Thơ", "Tỉnh Cà Mau"
]

LOCATION_ALIASES = {
    "Thành phố Đà Nẵng": ["đà nẵng", "quảng nam", "tam kỳ", "hội an"],
    "Tỉnh Quảng Ngãi": ["quảng ngãi", "kon tum"],
    "Tỉnh Đắk Lắk": ["đắk lắk", "đắc lắc", "tây nguyên", "buôn ma thuột", "phú yên", "tuy hòa"],
    "Tỉnh Gia Lai": ["gia lai", "pleiku", "bình định", "quy nhơn"],
    "Tỉnh Khánh Hòa": ["khánh hòa", "nha trang", "ninh thuận", "phan rang"],
    "Tỉnh Lâm Đồng": ["lâm đồng", "đà lạt", "bảo lộc", "đắk nông", "bình thuận", "phan thiết"],
    "Thành phố Hồ Chí Minh": ["hồ chí minh", "tp.hcm", "tphcm", "tp hcm", "sài gòn", "thủ đức", "bình dương", "bà rịa", "vũng tàu"],
    "Thành phố Đồng Nai": ["đồng nai", "biên hòa", "long khánh", "bình phước", "đồng xoài"],
    "Tỉnh Tây Ninh": ["tây ninh", "long an", "tân an"],
    "Tỉnh Đồng Tháp": ["đồng tháp", "cao lãnh", "sa đéc", "tiền giang", "mỹ tho"],
    "Tỉnh Vĩnh Long": ["vĩnh long", "bến tre", "trà vinh"],
    "Tỉnh An Giang": ["an giang", "long xuyên", "châu đốc", "kiên giang", "rạch giá", "phú quốc"],
    "Thành phố Cần Thơ": ["cần thơ", "hậu giang", "sóc trăng"],
    "Tỉnh Cà Mau": ["cà mau", "bạc liêu"]
}

TZ_VN = timezone(timedelta(hours=7))
NOW_VN = datetime.now(TZ_VN)
TODAY_STR = NOW_VN.strftime("%H:%M %d/%m/%Y")

if LAST_RUN_ENV:
    try:
        START_TIME = datetime.fromisoformat(LAST_RUN_ENV.replace("Z", "+00:00")).astimezone(TZ_VN)
    except Exception:
        START_TIME = NOW_VN - timedelta(hours=24)
else:
    START_TIME = NOW_VN - timedelta(hours=24)

BASE_DOMAIN = "muasamcong.mof.gov.vn"
PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.8-flash-lite"

# Phân tầng giới hạn quét:
# - Từ khóa sản phẩm cụ thể: tối đa 150 bản ghi (Trang 0: 100, Trang 1: 50)
# - Từ khóa chung để mở biểu mẫu 10B/HSMT chi tiết: tối đa 500 bản ghi (5 trang x 100)
SPECIFIC_MAX_RECORDS = 150
BROAD_MAX_RECORDS = 500

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

# 4. LỌC THỜI GIAN ĐĂNG TRONG KHOẢNG [START_TIME -> NOW_VN] (Chuẩn giờ VN UTC+7)
def is_published_in_time_window(item):
    pub_date_str = item.get("publicDate") or item.get("originalPublicDate") or ""
    if not pub_date_str:
        return True
    
    try:
        clean_date_str = pub_date_str.split(".")[0].replace("Z", "")
        dt_obj = datetime.strptime(clean_date_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=TZ_VN)
        return START_TIME <= dt_obj <= NOW_VN
    except Exception:
        return True

# 5. HÀM ĐỊNH DẠNG GIÁ TRỊ GÓI THẦU DẠNG SỐ (VD: 110,440,000.00)
def format_currency(val):
    if not val:
        return "0.00"
    try:
        if isinstance(val, list) and len(val) > 0:
            val = val[0]
            
        num = float(val)
        if num == 0:
            return "0.00"
        return f"{num:,.2f}"
    except Exception:
        return str(val)

# 6. KIỂM TRA & CHUẨN HÓA ĐỊA PHƯƠNG
def check_location(item, raw_str):
    found_locs = []
    investor = str(item.get("investorName") or item.get("procuringEntityName") or "").lower()
    title = str(item.get("bidName") or "").lower()
    search_text = f"{investor} {title} {raw_str}".lower()

    locations_arr = item.get("locations", [])
    if isinstance(locations_arr, list):
        for loc in locations_arr:
            if isinstance(loc, dict):
                prov_name = loc.get("provName", "")
                dist_name = loc.get("districtName", "")
                search_text += f" {prov_name} {dist_name}".lower()

    for canonical_name, aliases in LOCATION_ALIASES.items():
        if any(alias in search_text for alias in aliases):
            found_locs.append(canonical_name)

    return list(set(found_locs))

# 7. BÓC TÁCH DEEP-SCAN SÂU BIỂU MẪU 10B / HSMT (API GET-ALL-HSMT)
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

# 8. HÀM TẠO URL CHI TIẾT CHUẨN V2
def build_detail_url(item, code, notify_ver):
    notify_id = str(item.get("id") or item.get("notifyId") or "")
    plan_no = str(item.get("planNo") or item.get("khlcntNo") or "")
    bid_form = str(item.get("bidForm") or "DTRR")
    
    url = (
        f"https://{BASE_DOMAIN}/web/guest/contractor-selection?"
        f"p_p_id=egpportalcontractorselectionv2_WAR_egpportalcontractorselectionv2"
        f"&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
        f"&_egpportalcontractorselectionv2_WAR_egpportalcontractorselectionv2_render=detail-v2"
        f"&type=es-notify-contractor"
        f"&stepCode=notify-contractor-step-1-tbmt"
        f"&notifyNo={code}"
        f"&notifyVersion={notify_ver}"
    )
    
    if notify_id:
        url += f"&id={notify_id}&notifyId={notify_id}"
    if plan_no:
        url += f"&planNo={plan_no}"
    if bid_form:
        url += f"&bidForm={bid_form}"
        
    url += "&processApply=LDT&bidMode=1_MTHS&isInternet=1"
    return url

# 9. TRÍCH XUẤT THÔNG TIN TBMT THEO KHUNG THỜI GIAN PHÂN TẦNG
def fetch_tbmt_today(driver):
    print(f"-> Mở Session Mua Sắm Công TBMT (Khung quét: {START_TIME.strftime('%H:%M %d/%m/%Y')} -> {TODAY_STR})...")
    driver.get(f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index")
    time.sleep(10)

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

    unique_matches = {}
    scanned_codes = set()

    from_date_iso = START_TIME.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_date_iso = NOW_VN.strftime("%Y-%m-%dT%H:%M:%S.999Z")

    js_fetch_search = """
    var callback = arguments[arguments.length - 1];
    var kw = arguments[0];
    var pageNum = arguments[1];
    var pageSize = arguments[2];
    var fromDate = arguments[3];
    var toDate = arguments[4];
    var token = arguments[5];
    
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
            pageSize: pageSize,
            pageNumber: String(pageNum),
            query: [{
                index: "es-contractor-selection",
                keyWord: kw,
                matchType: "all-1",
                matchFields: ["notifyNo", "bidName", "planName"],
                filters: [
                    { fieldName: "type", searchType: "in", fieldValues: ["es-notify-contractor"] },
                    { fieldName: "caseKHKQ", searchType: "not_in", fieldValues: ["1"] },
                    { fieldName: "publicDate", searchType: "range", from: fromDate, to: toDate }
                ]
            }]
        }])
    })
    .then(response => response.json())
    .then(data => callback(data))
    .catch(err => callback({ error: err.toString() }));
    """

    query_plan = []
    for kw in KEYWORDS:
        query_plan.append((kw, SPECIFIC_MAX_RECORDS))
    for kw in BROAD_QUERIES:
        query_plan.append((kw, BROAD_MAX_RECORDS))

    for kw, max_records in query_plan:
        print(f"-> Truy vấn API Smart Search cho: '{kw}' (Giới hạn tối đa {max_records} bản ghi)...")
        matched_in_kw = 0
        fetched_in_kw = 0
        page_num = 0

        while fetched_in_kw < max_records:
            current_page_size = min(100, max_records - fetched_in_kw)
            try:
                res = driver.execute_async_script(
                    js_fetch_search,
                    kw,
                    page_num,
                    current_page_size,
                    from_date_iso,
                    to_date_iso,
                    token_val
                )
                data_list = []
                
                if isinstance(res, list) and len(res) > 0:
                    first_item = res[0]
                    if isinstance(first_item, dict):
                        data_list = first_item.get("page", {}).get("content", [])
                elif isinstance(res, dict):
                    data_list = res.get("page", {}).get("content", [])

                if not isinstance(data_list, list) or not data_list:
                    break

                fetched_in_kw += len(data_list)
                older_count = 0

                for item in data_list:
                    if not isinstance(item, dict):
                        continue

                    if not is_published_in_time_window(item):
                        older_count += 1
                        continue

                    code = str(item.get("notifyNo") or item.get("notifyNoStand") or item.get("bidNo") or item.get("id") or "")
                    if not code or code in unique_matches or code in scanned_codes:
                        continue

                    scanned_codes.add(code)

                    notify_ver = str(item.get("notifyVersion") or "00")
                    bid_field = str(item.get("bidField") or "HH")

                    bid_name_raw = item.get("bidName")
                    if isinstance(bid_name_raw, list) and len(bid_name_raw) > 0:
                        title = str(bid_name_raw[0])
                    else:
                        title = str(bid_name_raw or item.get("name") or "")
                        
                    investor = str(item.get("investorName") or item.get("procuringEntityName") or "")
                    
                    est_amount_raw = item.get("estAmount") or item.get("investTotal") or item.get("bidPrice")
                    formatted_amount = format_currency(est_amount_raw)
                    detail_url = build_detail_url(item, code, notify_ver)

                    raw_str = json.dumps(item, ensure_ascii=False).lower()

                    matched_province = check_location(item, raw_str)
                    if not matched_province:
                        continue

                    hsmt_str = get_hsmt_form_values(driver, code, notify_ver, bid_field)
                    if hsmt_str:
                        raw_str += " " + hsmt_str

                    matched_kws = [k for k in KEYWORDS if k.lower() in raw_str]
                    if matched_kws:
                        matched_in_kw += 1
                        location_detail = "Tiêu đề" if any(k.lower() in f"{code} {title}".lower() for k in matched_kws) else "Chi tiết Hàng hóa / Biểu mẫu 10B"
                        unique_matches[code] = {
                            "code": code,
                            "notify_ver": notify_ver,
                            "title": title,
                            "amount": formatted_amount,
                            "investor": investor,
                            "province": ", ".join(matched_province),
                            "matched_kw": ", ".join(matched_kws),
                            "location": location_detail,
                            "detail_url": detail_url,
                            "snippet": raw_str[:1200]
                        }

                if len(data_list) < current_page_size or older_count > len(data_list) * 0.7:
                    break

                page_num += 1
                time.sleep(0.4)
            except Exception as e:
                print(f"[CẢNH BÁO FETCH TBMT] Từ khóa '{kw}' (Trang {page_num}): {e}")
                break

        print(f"    + Server trả về {fetched_in_kw} bản ghi -> Khớp khung thời gian, địa phương & từ khóa: {matched_in_kw} gói thầu.")
        time.sleep(0.3)

    return list(unique_matches.values())

# 10. PHÂN TÍCH VỚI GEMINI AI (CHIA LÔ + RETRY 503 + FALLBACK GEMINI 3 + XOAY KEY)
def analyze_batch_with_gemini(batch_items):
    global current_key_index, GEMINI_API_KEYS
    from google import genai
    from google.genai import types

    models_to_try = [PRIMARY_MODEL, FALLBACK_MODEL, "gemini-3.8-pro"]

    prompt = f"""
Bạn là chuyên gia phân tích dữ liệu đấu thầu Y tế.
Dưới đây là danh sách các Thông báo mời thầu (TBMT) phát hành từ {START_TIME.strftime('%H:%M %d/%m/%Y')} đến {TODAY_STR} đã lọc theo địa phương và 19 từ khóa y tế.

Yêu cầu:
1. Giữ nguyên toàn bộ các gói thầu trong danh sách đầu vào.
2. Viết 1 câu tóm tắt ngắn gọn lý do phù hợp hoặc mặt hàng chính trong gói thầu.
3. Trả về đúng mảng JSON chứa các object đầy đủ thuộc tính và thêm "reason".

Danh sách:
{json.dumps(batch_items, ensure_ascii=False, indent=2)}
Chỉ trả về chuỗi JSON thuần túy (không kèm markdown).
"""

    for model_name in models_to_try:
        key_idx = current_key_index
        while key_idx < len(GEMINI_API_KEYS):
            key = GEMINI_API_KEYS[key_idx]
            client = genai.Client(api_key=key)

            for attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(response_mime_type="application/json")
                    )
                    if response and response.text:
                        current_key_index = key_idx
                        return json.loads(response.text.strip())
                except Exception as e:
                    error_msg = str(e)
                    print(f"[CẢNH BÁO AI GOOGLE-GENAI ({model_name})] {error_msg}")

                    if "404" in error_msg or "not_found" in error_msg.lower():
                        key_idx = len(GEMINI_API_KEYS)
                        break

                    if any(k in error_msg.lower() for k in ["429", "resource_exhausted", "quota"]):
                        key_idx += 1
                        if key_idx < len(GEMINI_API_KEYS):
                            print(f"🔄 Đã đổi sang Gemini API Key thứ [{key_idx + 1}/{len(GEMINI_API_KEYS)}]")
                        break

                    if "503" in error_msg or "unavailable" in error_msg.lower():
                        if attempt == 0:
                            print(f"⏳ Model {model_name} đang quá tải, thử lại sau 10s...")
                            time.sleep(10)
                            continue
                        key_idx = len(GEMINI_API_KEYS)
                        break

                    key_idx = len(GEMINI_API_KEYS)
                    break

    return batch_items


def analyze_with_gemini(candidates):
    if not candidates or not GEMINI_API_KEYS:
        return candidates

    batch_size = 15
    analyzed_results = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        print(f"🧠 Đang phân tích AI lô {i // batch_size + 1} ({len(batch)} gói thầu)...")
        analyzed_batch = analyze_batch_with_gemini(batch)
        if isinstance(analyzed_batch, list) and len(analyzed_batch) == len(batch):
            analyzed_results.extend(analyzed_batch)
        else:
            analyzed_results.extend(batch)
        time.sleep(1)

    return analyzed_results

# 11. GỬI EMAIL BÁO CÁO
def send_email_report(matched_list):
    if not RECEIVER_EMAIL:
        print("[LỖI CẤU HÌNH] Chưa khai báo email nhận.")
        return

    receiver_list = [e.strip() for e in RECEIVER_EMAIL.replace("\n", "").split(",") if e.strip()]
    if not receiver_list:
        print("[LỖI CẤU HÌNH] Danh sách email nhận không hợp lệ.")
        return

    email_subject = f"[TBMT TBYT - Địa bàn phụ trách] Báo cáo gói thầu ({START_TIME.strftime('%H:%M %d/%m')} - {TODAY_STR})"

    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT)</h3>
            <p>Hệ thống đã quét dữ liệu từ <b>{START_TIME.strftime('%H:%M %d/%m/%Y')}</b> đến <b>{TODAY_STR}</b> nhưng chưa phát hiện gói thầu mới khớp 19 từ khóa y tế tại 14 địa phương theo dõi.</p>
        </div>
        """
    else:
        rows = ""
        for idx, item in enumerate(matched_list, 1):
            code = item.get("code", "")
            title = item.get("title", "")
            amount = item.get("amount", "0.00")
            investor = item.get("investor", "")
            province = item.get("province", "Theo dõi")
            location = item.get("location", "Biểu mẫu / Hàng hóa")
            reason = item.get("reason") or "Khớp từ khóa theo dõi y tế trong Biểu mẫu 10B/HSMT"
            matched_kw = item.get("matched_kw", "")
            detail_url = item.get("detail_url") or f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index"
            
            rows += f"""
            <tr>
                <td align="center">{idx}</td>
                <td><b>{code}</b></td>
                <td>{title}</td>
                <td align="right"><b><span style="color: #2e7d32;">{amount}</span></b></td>
                <td>{investor}</td>
                <td align="center"><b><span style="color: #d9534f;">{province}</span></b></td>
                <td align="center"><span style="background-color: #ffffcc; padding: 3px 6px; border-radius: 3px;">{matched_kw}</span></td>
                <td align="center"><i>{location}</i></td>
                <td><small style="color: #333;">{reason}</small></td>
                <td align="center"><a href="{detail_url}" target="_blank" style="color: #0275d8; font-weight: bold;">Xem ngay</a></td>
            </tr>
            """
            
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #0275d8;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT)</h2>
            <p>Khung thời gian quét: từ <b>{START_TIME.strftime('%H:%M %d/%m/%Y')}</b> đến <b>{TODAY_STR}</b></p>
            <p>Đã lọc phát hiện <b>{len(matched_list)}</b> gói thầu Y tế thuộc địa bàn phụ trách:</p>
            <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; border-color: #ddd;">
                <tr style="background-color: #fff2e6;">
                    <th align="center">STT</th>
                    <th>Mã TBMT</th>
                    <th>Tên Gói Thầu</th>
                    <th align="right">Giá Trị Gói Thầu (VND)</th>
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
        msg["Subject"] = email_subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(receiver_list)
        msg.attach(MIMEText(html_body, "html"))
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.sendmail(SENDER_EMAIL, receiver_list, msg.as_string())
            print(f"[SUCCESS] Đã gửi báo cáo TBMT thành công tới các email: {', '.join(receiver_list)}!")
        except Exception as e:
            print(f"[LỖI GỬI EMAIL SMTP] {e}")

def main():
    print(f"=== CRAWL & DEEP-SCAN TBMT (Khung quét: {START_TIME.strftime('%H:%M %d/%m/%Y')} -> {TODAY_STR}) ===")
    driver = init_driver()
    try:
        matched_list = fetch_tbmt_today(driver)
        final_matches = analyze_with_gemini(matched_list)
        send_email_report(final_matches)
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
