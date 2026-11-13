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

# Tính toán mốc START_TIME dựa trên lịch chạy tự động trước đó
if LAST_RUN_ENV:
    try:
        START_TIME = datetime.fromisoformat(LAST_RUN_ENV.replace("Z", "+00:00")).astimezone(TZ_VN)
    except Exception:
        START_TIME = NOW_VN - timedelta(hours=24)
else:
    START_TIME = NOW_VN - timedelta(hours=24)

BASE_DOMAIN = "muasamcong.mof.gov.vn"
PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-2.5-flash"

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

    query_plan = [(kw, SPECIFIC_MAX_RECORDS) for kw in
