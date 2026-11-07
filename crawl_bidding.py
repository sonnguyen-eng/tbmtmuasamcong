import os
import smtplib
import requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

# Lấy các thông tin bảo mật từ Cấu hình GitHub
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = SENDER_EMAIL # Gửi về chính email của bạn

# DANH SÁCH TỪ KHÓA BẠN MUỐN TÌM (Có thể chỉnh sửa danh sách này)
KEYWORDS = ["phần mềm", "máy tính", "chuyển đổi số", "hạ tầng CNTT", "thiết bị mạng"]

def fetch_muasamcong_data():
    url = "https://muasamcong.mof.gov.vn/api/egp/bidding/services/app/tbmt/search"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    payload = {
        "page": 0,
        "size": 50,
        "searchType": "TBMT",
        "publishDateFrom": f"{today_str}T00:00:00Z",
        "publishDateTo": f"{today_str}T23:59:59Z"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json().get("result", {}).get("data", [])
        return []
    except Exception as e:
        print(f"Lỗi kết nối Mua Sắm Công: {e}")
        return []

def filter_and_summarize_with_gemini(raw_data, keywords):
    if not raw_data:
        return "<p>Hôm nay không có thông báo mời thầu mới nào được đăng tải.</p>"

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    Bạn là Trợ lý CNTT chuyên nghiệp. Dưới đây là dữ liệu thầu hôm nay lấy từ muasamcong.mof.gov.vn:
    {raw_data}

    Nhiệm vụ:
    1. Lọc ra các gói thầu/mời chào giá liên quan đến các từ khóa: {', '.join(keywords)}.
    2. Trình bày kết quả bằng định dạng HTML đẹp mắt để gửi email.
    3. Với mỗi gói thầu tìm thấy, trình bày rõ: Tên gói thầu, Bên mời thầu, Giá dự toán, Ngày đóng thầu và Mã TBMT.
    4. Nếu không có gói thầu nào khớp từ khóa, hãy ghi rõ: "Không có gói thầu phù hợp trong ngày hôm nay."
    """

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text

def send_email(subject, html_content):
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
        print("Đã gửi email thành công!")
    except Exception as e:
        print(f"Lỗi gửi email: {e}")

if __name__ == "__main__":
    raw_bids = fetch_muasamcong_data()
    email_body = filter_and_summarize_with_gemini(raw_bids, KEYWORDS)
    subject = f"[TBMT CNTT] Thông báo gói thầu ngày {datetime.now().strftime('%d/%m/%Y')}"
    send_email(subject, email_body)
