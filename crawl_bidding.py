import os
import time
import smtplib
import requests
from datetime import datetime, timedelta
import zoneinfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from google import genai

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD")
RECEIVER_EMAIL = SENDER_EMAIL

# Danh sách TỪ KHÓA SẢN PHẨM / THIẾT BỊ cần tìm trong DANH MỤC CHI TIẾT
PRODUCT_KEYWORDS = [
     "nội soi", "nội soi tiêu hóa", "nội soi khí phế quản", "Bàn mổ", 
    "đèn mổ", "hệ thống khí y tế", "vận chuyển mẫu", "ERCP"
]

def fetch_all_tbmt_today():
    """Lấy toàn bộ thông báo mời thầu mới trong ngày từ API popular-keyword/get-list"""
    url = "https://muasamcong.mof.gov.vn/o/egp-portal-contractor-selection-v2/services/popular-keyword/get-list"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    tz_vn = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    today_vn = datetime.now(tz_vn)
    today_str = today_vn.strftime("%Y-%m-%d")
    
    payload = {
        "page": 0,
        "size": 150,  # Quét mở rộng 150 thông báo mới nhất
        "type": "TBMT",
        "keySearch": "", # Quét toàn bộ để không bị trôi/sót gói thầu đặt tên chung chung
        "publishDateFrom": f"{today_str}T00:00:00Z",
        "publishDateTo": f"{today_str}T23:59:59Z"
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        if response.status_code == 200:
            return response.json().get("result", {}).get("data", [])
        return []
    except Exception as e:
        print(f"[Lỗi TBMT] Không thể kết nối API: {e}")
        return []

def deep_analyze_tbmt_with_gemini(raw_data, product_keywords):
    """Dùng Gemini phân tích chuyên sâu danh mục lô hàng / vật tư của TBMT"""
    if not raw_data:
        return "<p>Hôm nay không có Thông báo mời thầu mới nào được đăng tải.</p>"

    client = genai.Client(api_key=GEMINI_API_KEY)
    
    prompt = f"""
    Bạn là một Trợ lý CNTT & Chuyên gia đấu thầu.
    Dưới đây là danh sách toàn bộ Thông báo mời thầu (TBMT) lấy từ muasamcong.mof.gov.vn trong ngày:
    {raw_data}

    NHIỆM VỤ CỦA BẠN:
    1. Duyệt qua từng gói thầu và KIỂM TRA SÂU VÀO DANH MỤC SẢN PHẨM/CHI TIẾT MẶT HÀNG/LÔ HÀNG bên trong dữ liệu.
    2. Lọc ra các gói thầu có xuất hiện các sản phẩm thuộc danh mục: {product_keywords}.
       *LƯU Ý:* Kể cả khi Tên gói thầu chỉ ghi chung chung (ví dụ: 'Mua sắm thiết bị y tế', 'Trang bị CNTT năm 2026'), nhưng nếu trong danh mục mặt hàng chi tiết có chứa sản phẩm quan tâm thì VẪN LỌC RA.
    
    3. Định dạng kết quả thành BẢNG HTML đẹp mắt để gửi qua Email bao gồm các cột:
       - Mã TBMT
       - Tên gói thầu
       - Chi tiết sản phẩm phát hiện (Nêu rõ tên mặt hàng tìm thấy trong danh mục)
       - Bên mời thầu / Chủ đầu tư
       - Giá gói thầu / Dự toán
       - Thời điểm đóng thầu
       - Link trực tiếp: `<a href="https://muasamcong.mof.gov.vn/vi/web/guest/initiator?tbmtNo=[MÃ_TBMT]">Xem chi tiết</a>`

    4. Nếu không có gói thầu nào chứa sản phẩm phù hợp trong danh mục chi tiết, trả về: "Hôm nay không có Thông báo mời thầu nào chứa sản phẩm thuộc danh mục theo dõi."
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
        print("-> Đã gửi Email báo cáo TBMT chi tiết thành công!")
    except Exception as e:
        print(f"[Lỗi gửi Email] {e}")

if __name__ == "__main__":
    print("=== BẮT ĐẦU SOÁT CHI TIẾT DANH MỤC THÔNG BÁO MỜI THẦU (TBMT) ===")
    raw_tbmt = fetch_all_tbmt_today()
    print(f"-> Đã thu thập {len(raw_tbmt)} gói TBMT trong ngày. Đang chuyển cho Gemini phân tích danh mục...")
    
    email_body = deep_analyze_tbmt_with_gemini(raw_tbmt, PRODUCT_KEYWORDS)
    
    tz_vn = zoneinfo.ZoneInfo("Asia/Ho_Chi_Minh")
    today_str = datetime.now(tz_vn).strftime('%d/%m/%Y')
    subject = f"[TBMT CHI TIẾT] Báo cáo sản phẩm danh mục ngày {today_str}"
    
    send_email(subject, email_body)
    print("=== HOÀN THÀNH ===")
