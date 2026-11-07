import os
import smtplib
import requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

# Nạp thông tin bảo mật từ cấu hình GitHub Secrets
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = SENDER_EMAIL  # Gửi trực tiếp về email của bạn

# DANH SÁCH TỪ KHÓA CẦN TÌM KIẾM THÔNG BÁO MỜI THẦU
KEYWORDS = ["Nội soi", "ERCP", "nội soi tiêu hóa", "Nội soi dạ dày", "Nội soi khí phế quản", "bàn mổ", "đèn mổ", "máy hấp tiệt trùng", "Hệ thống khí y tế", "Vận chuyển mẫu"]

def fetch_tbmt_data(keyword):
    """
    Gọi API popular-keyword/get-list của muasamcong.mof.gov.vn để lấy danh sách Thông báo mời thầu
    """
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    payload = {
        "page": 0,
        "size": 50,
        "type": "TBMT",  # Tìm kiếm Thông báo mời thầu
        "keySearch": keyword,
        "publishDateFrom": f"{today_str}T00:00:00Z",
        "publishDateTo": f"{today_str}T23:59:59Z"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            # Trích xuất mảng dữ liệu từ kết quả trả về của API
            return res_json.get("result", {}).get("data", [])
        else:
            print(f"[Cảnh báo] HTTP status {response.status_code} khi tìm từ khóa: '{keyword}'")
            return []
    except Exception as e:
        print(f"[Lỗi] Khi gọi API Mua sắm công với từ khóa '{keyword}': {e}")
        return []

def collect_all_tbmt(keywords):
    """
    Duyệt qua danh sách từ khóa và gom tất cả các gói thầu lại (loại bỏ trùng lặp)
    """
    all_bids = {}
    
    for kw in keywords:
        print(f"-> Đang tìm kiếm Thông báo mời thầu với từ khóa: '{kw}'...")
        items = fetch_tbmt_data(kw)
        for item in items:
            # Xác định khóa duy nhất (ID gói thầu hoặc Mã TBMT) để chống trùng
            bid_id = item.get("bidNo") or item.get("notifyNo") or item.get("id") or item.get("code")
            if bid_id and bid_id not in all_bids:
                all_bids[bid_id] = item

    return list(all_bids.values())

def generate_report_with_gemini(biddings_data, keywords):
    """
    Nhờ Gemini phân tích, lọc và tổng hợp thành bảng HTML gửi qua email
    """
    if not biddings_data:
        return "<p>Hôm nay không có Thông báo mời thầu mới nào phù hợp với danh mục từ khóa theo dõi.</p>"

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    Bạn là một Trợ lý CNTT chuyên nghiệp. Dưới đây là danh sách Thông báo mời thầu vừa lấy về từ muasamcong.mof.gov.vn theo các từ khóa {keywords}:
    {biddings_data}

    Nhiệm vụ của bạn:
    1. Trình bày nội dung trên dưới dạng BẢNG HTML chuyên nghiệp, bố cục rõ ràng để gửi qua Email.
    2. Các cột thông tin chính bao gồm:
       - Mã TBMT
       - Tên gói thầu
       - Bên mời thầu / Chủ đầu tư
       - Giá gói thầu / Dự toán (nếu có)
       - Thời điểm đóng thầu
       - Link xem chi tiết: Tạo đường dẫn trực tiếp dạng `<a href="https://muasamcong.mof.gov.vn/vi/web/guest/initiator?tbmtNo=[MÃ_TBMT]">Xem chi tiết</a>`.
    3. Thêm một lời chào ngắn ở đầu email và phần tổng kết số lượng gói thầu ở cuối email.
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

def send_email(subject, html_content):
    """Gửi email qua SMTP Gmail"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL

    part = MIMEText(html_content, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print("-> Đã gửi thành công email báo cáo Thông báo mời thầu!")
    except Exception as e:
        print(f"[Lỗi] Không thể gửi email: {e}")

if __name__ == "__main__":
    print("=== BẮT ĐẦU QUY TRÌNH QUÉT THÔNG BÁO MỜI THẦU ===")
    results = collect_all_tbmt(KEYWORDS)
    print(f"-> Tổng số Thông báo mời thầu tìm thấy (đã khử trùng): {len(results)}")
    
    print("-> Đang gửi dữ liệu sang Gemini để tổng hợp báo cáo...")
    email_body = generate_report_with_gemini(results, KEYWORDS)
    
    subject = f"[TBMT Noi Soi] Danh sách Thông báo mời thầu ngày {datetime.now().strftime('%d/%m/%Y')}"
    send_email(subject, email_body)
    print("=== HOÀN THÀNH ===")
