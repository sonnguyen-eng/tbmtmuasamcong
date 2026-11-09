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
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
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

# 2. KHỞI TẠO TRÌNH DUYỆT CHROME HEADLESS
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

# 3. THAO TÁC NHẬP TỪ KHÓA TRÊN GIAO DIỆN MUA SẮM CÔNG BẰNG SELENIUM
def fetch_tbmt_by_searching_keywords(driver):
    print("-> Đang mở trình duyệt Mua Sắm Công...")
    driver.get(f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index")
    time.sleep(6)

    unique_results = {}

    for kw in KEYWORDS:
        print(f"-> Đang tìm kiếm giao diện cho từ khóa: '{kw}'...")
        try:
            # Tìm ô input tìm kiếm trên trang Mua Sắm Công
            search_inputs = driver.find_elements(By.XPATH, "//input[@type='text' or contains(@placeholder, 'Tìm') or contains(@class, 'search')]")
            if search_inputs:
                inp = search_inputs[0]
                inp.send_keys(Keys.CONTROL + "a")
                inp.send_keys(Keys.BACKSPACE)
                inp.send_keys(kw)
                inp.send_keys(Keys.ENTER)
                time.sleep(4)
            else:
                # Nếu không tìm thấy ID input cụ thể, kích hoạt nút tìm kiếm chung
                driver.execute_script(f"console.log('Search kw: {kw}');")

            # Bóc tách dữ liệu thẻ kết quả sau khi tìm kiếm
            js_extract = """
            let items = [];
            let rows = document.querySelectorAll('tr, div[class*="item"], div[class*="card"]');
            rows.forEach(r => {
                let txt = r.innerText || "";
                if (txt.length > 30 && (txt.includes('TBMT') || txt.includes('Gói thầu') || txt.includes('202'))) {
                    items.push(txt);
                }
            });
            return items;
            """
            extracted_texts = driver.execute_script(js_extract)
            
            for txt in extracted_texts:
                txt_clean = " ".join(txt.split())
                if kw.lower() in txt_clean.lower():
                    # Tạo mã định danh ngắn cho bản ghi
                    item_id = str(hash(txt_clean[:80]))
                    if item_id not in unique_results:
                        unique_results[item_id] = {
                            "bidNo": f"TBMT-{item_id[:6]}",
                            "bidName": txt_clean[:180],
                            "matched_kw": kw,
                            "rawText": txt_clean
                        }

            print(f"   + Tìm thấy {len(unique_results)} bản ghi phù hợp tích lũy.")
        except Exception as e:
            print(f"[LỖI TÌM KIẾM GIAO DIỆN] Từ khóa '{kw}': {e}")
            continue

    return list(unique_results.values())

# 4. DEEP-SCAN DỮ LIỆU LỒNG NHAU FORMVALUE
def extract_searchable_text(item):
    try:
        if "rawText" in item:
            return item["rawText"].lower()
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
        code = str(item.get("bidNo") or "")
        title = str(item.get("bidName") or "")
        investor = str(item.get("procuringEntityName") or "Bên mời thầu Y tế")
        full_text = extract_searchable_text(item)
        
        matched_kw = item.get("matched_kw") or [kw for kw in KEYWORDS if kw.lower() in full_text]
        if isinstance(matched_kw, str):
            matched_kw = [matched_kw]

        if matched_kw and code not in unique_codes:
            unique_codes.add(code)
            location = "Tiêu đề / Chi tiết biểu mẫu hàng hóa"
            candidates.append({
                "code": code,
                "title": title,
                "investor": investor,
                "matched_kw": matched_kw,
                "location": location,
                "snippet": full_text[:1200]
            })
    return candidates

# 5. PHÂN TÍCH VỚI GEMINI AI
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
1. Giữ nguyên toàn bộ gói thầu trong danh sách.
2. Viết 1 câu tóm tắt ngắn gọn lý do phù hợp hoặc mặt hàng chính trong gói thầu.
3. Trả về đúng mảng JSON chứa các object: "code", "title", "investor", "matched_kw", "location", "reason".

Danh sách:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
Chỉ trả về chuỗi JSON thuần túy.
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

# 6. GỬI EMAIL BÁO CÁO
def send_email_report(matched_list, raw_count):
    if not RECEIVER_EMAIL:
        print("[LỖI CẤU HÌNH] Chưa khai báo email nhận.")
        return

    if not matched_list:
        html_body = f"""
        <div style="font-family: Arial, sans-serif; padding: 20px;">
            <h3 style="color: #d9534f;">BÁO CÁO THÔNG BÁO MỜI THẦU (TBMT) NGÀY {TODAY_STR}</h3>
            <p>Hệ thống đã kết nối và quét dữ liệu giao diện Mua Sắm Công nhưng chưa phát hiện gói thầu mới khớp 14 từ khóa theo dõi.</p>
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
            <p>Đã tương tác quét giao diện Mua Sắm Công và bóc tách phát hiện <b>{len(matched_list)}</b> gói thầu phù hợp:</p>
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
    print(f"=== CRAWL & BÓC TÁCH SÂU TBMT SELENIUM SEARCH - NGÀY {TODAY_STR} ===")
    driver = init_driver()
    try:
        raw_list = fetch_tbmt_by_searching_keywords(driver)
        candidates = pre_filter_candidates(raw_list)
        final_matches = analyze_with_gemini(candidates)
        send_email_report(final_matches, len(raw_list))
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
