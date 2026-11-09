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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# 1. CẤU HÌNH BIẾN MÔI TRƯỜNG & DANH SÁCH 14 TỪ KHÓA Y TẾ
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

# 3. KÍCH HOẠT NÚT TÌM KIẾM TRÊN GIAO DIỆN ĐỂ MUA SẮM CÔNG RENDER BẢNG DỮ LIỆU
def fetch_tbmt_by_browser_dom(driver):
    print("-> Đang truy cập Mua Sắm Công qua Chrome Headless...")
    target_url = f"https://{BASE_DOMAIN}/web/guest/contractor-selection?render=index"
    driver.get(target_url)
    wait = WebDriverWait(driver, 15)
    
    collected_items = []

    try:
        time.sleep(5)
        # Bấm nút Tìm kiếm hoặc phím Enter để kích hoạt load dữ liệu bảng
        try:
            search_btn = driver.find_elements(By.XPATH, "//button[contains(text(),'Tìm kiếm') or contains(@class,'btn-search')]")
            if search_btn:
                search_btn[0].click()
                print("   + Đã kích hoạt click nút 'Tìm kiếm' trên giao diện.")
                time.sleep(5)
        except Exception:
            pass

        # Đọc dữ liệu từ mảng biến toàn cục Liferay hoặc DOM
        js_extract = """
        let results = [];
        // 1. Quét từ mảng lưu tạm trên window
        window.performance.getEntries().forEach(e => {
            if (e.name && e.name.includes('services/lcnt_tbmcgtt_hsmt')) {
                console.log('Found API Call:', e.name);
            }
        });

        // 2. Quét thẻ chứa dữ liệu gói thầu
        let cards = document.querySelectorAll('.card-item-tbmt, .table-contractor-selection tbody tr, div[class*="item-tbmt"]');
        cards.forEach(c => {
            let txt = c.innerText || "";
            if (txt.length > 20) {
                results.push({
                    bidNo: c.querySelector('a, .code, .bid-no')?.innerText?.trim() || "",
                    bidName: txt.substring(0, 150).replace(/\\n/g, ' '),
                    rawText: txt
                });
            }
        });

        // 3. Quét toàn bộ thẻ 'a' chứa mã thông báo mời thầu
        if (results.length === 0) {
            let links = document.querySelectorAll('a[href*="tbmtNo"], a[href*="initiator"]');
            links.forEach(l => {
                let code = l.innerText.trim();
                let parentText = l.parentElement?.parentElement?.innerText || code;
                if (code) {
                    results.push({
                        bidNo: code,
                        bidName: parentText.substring(0, 150).replace(/\\n/g, ' '),
                        rawText: parentText
                    });
                }
            });
        }
        return results;
        """
        
        items = driver.execute_script(js_extract)
        if items and isinstance(items, list):
            collected_items.extend(items)
            print(f"   + Bóc tách thành công {len(collected_items)} bản ghi gói thầu từ DOM trình duyệt.")
        else:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            print(f"   + Lấy dữ liệu Text giao diện tổng thể: {len(body_text)} ký tự.")
            collected_items.append({
                "bidNo": "TBMT-ALL",
                "bidName": "Dữ liệu trang danh sách Mua Sắm Công",
                "rawText": body_text
            })

    except Exception as e:
        print(f"[LỖI RENDER GIAO DIỆN] {e}")

    return collected_items

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
        code = str(item.get("bidNo") or item.get("notifyNo") or item.get("code") or item.get("id") or "")
        title = str(item.get("bidName") or item.get("name") or item.get("title") or "")
        investor = str(item.get("procuringEntityName") or item.get("investorName") or "")
        full_text = extract_searchable_text(item)
        
        matched_kw = [kw for kw in KEYWORDS if kw.lower() in full_text]
        if matched_kw and code not in unique_codes:
            unique_codes.add(code)
            location = "Tiêu đề" if any(kw.lower() in f"{code} {title}".lower() for kw in matched_kw) else "Chi tiết Hàng hóa / Biểu mẫu 10B"
            candidates.append({
                "code": code if code != "TBMT-ALL" else "TBMT-YTE",
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
            <p>Hệ thống đã thu thập thành công dữ liệu ({raw_count} bản ghi) nhưng chưa phát hiện gói thầu khớp 14 từ khóa theo dõi.</p>
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
            <p>Đã thu thập dữ liệu từ Mua Sắm Công và bóc tách sâu phát hiện <b>{len(matched_list)}</b> gói thầu phù hợp:</p>
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
    print(f"=== CRAWL & BÓC TÁCH SÂU TBMT BẰNG SELENIUM - NGÀY {TODAY_STR} ===")
    driver = init_driver()
    try:
        raw_list = fetch_tbmt_by_browser_dom(driver)
        candidates = pre_filter_candidates(raw_list)
        final_matches = analyze_with_gemini(candidates)
        send_email_report(final_matches, len(raw_list))
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
