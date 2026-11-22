import os
import sys
import json
from datetime import datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

TZ_VN = timezone(timedelta(hours=7))
SYNC_TIME_STR = datetime.now(TZ_VN).strftime("%H:%M %d/%m/%Y")

GSHEET_ID = os.environ.get("GSHEET_ID", "").strip()
GCP_SA_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
INPUT_FILE = "latest_results.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

SALES_WORKFLOW_HEADERS = [
    "Sales Địa Bàn (B02)",
    "Đăng Ký NSX (B03)",
    "Go / No-Go (B04)",
    "Trạng Thái HSDT (B05)",
    "Kết Quả Thầu (B06)"
]

SHEET_HEADERS_BY_TAB = {
    "YCCG": [
        "Mã YCCG (Key)", "Giai Đoạn", "Ngày Quét", "Tỉnh / TP",
        "Đơn Vị Yêu Cầu", "Nội Dung Yêu Cầu Báo Giá", "Hạn Nộp Báo Giá",
        "Từ Khóa Khớp", "Vị Trí Khớp", "BU Gợi Ý",
        "Tóm Tắt Chi Tiết AI", "Link Chi Tiết"
    ] + SALES_WORKFLOW_HEADERS,
    "KHLCNT": [
        "Mã KHLCNT (Key)", "Số Quyết Định", "Giai Đoạn", "Ngày Đăng",
        "Tỉnh / TP", "Chủ Đầu Tư", "Tên KHLCNT", "Tổng Mức Đầu Tư",
        "Thời Gian Dự Kiến LCNT", "Từ Khóa Khớp", "Vị Trí Khớp",
        "BU Gợi Ý", "Tóm Tắt Chi Tiết AI", "Link Chi Tiết"
    ] + SALES_WORKFLOW_HEADERS,
    "TBMT": [
        "Mã TBMT (Key)", "Phiên Bản", "Giai Đoạn", "Ngày Quét",
        "Tỉnh / TP", "Bên Mời Thầu", "Tên Gói Thầu",
        "Giá Trị Gói Thầu (VND)", "Từ Khóa Khớp", "Vị Trí Khớp",
        "BU Gợi Ý", "Tóm Tắt Chi Tiết AI", "Link Chi Tiết"
    ] + SALES_WORKFLOW_HEADERS
}

COLUMN_WIDTHS_BY_TAB = {
    "YCCG": [155, 165, 130, 145, 220, 320, 135, 140, 135, 155, 340, 110, 140, 140, 130, 140, 140],
    "KHLCNT": [155, 130, 165, 130, 145, 220, 320, 145, 145, 140, 135, 155, 340, 110, 140, 140, 130, 140, 140],
    "TBMT": [155, 85, 165, 130, 145, 220, 320, 150, 140, 135, 155, 340, 110, 140, 140, 130, 140, 140]
}

# Các vùng miền dùng dấu phẩy làm dấu thập phân -> Google Sheets bắt buộc dùng dấu ';' phân cách tham số hàm
COMMA_LOCALE_PREFIXES = (
    "en_", "ja_", "ko_", "zh_", "th_", "ms_", "fil_", "hi_", "ar_", "he_"
)


def get_formula_separator(spreadsheet):
    """Tự động kiểm tra Locale của Google Sheet để trả về ',' hoặc ';' phù hợp."""
    try:
        locale = str(getattr(spreadsheet, "locale", "") or "").strip()
        print(f"-> Phát hiện Locale Google Sheet: '{locale}'")
        if locale.lower().startswith(COMMA_LOCALE_PREFIXES):
            return ","
        return ";"
    except Exception:
        return ";"


def connect_gsheet():
    if not GSHEET_ID or not GCP_SA_JSON:
        print("[BỎ QUA GSHEET] Chưa cấu hình Secret GSHEET_ID hoặc GCP_SERVICE_ACCOUNT_JSON.")
        return None
    try:
        creds_dict = json.loads(GCP_SA_JSON)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client.open_by_key(GSHEET_ID)
    except Exception as e:
        print(f"[LỖI KẾT NỐI GOOGLE SHEET] {e}")
        return None


def apply_sheet_layout_and_wrap(spreadsheet, ws, tab_name):
    """Tự động bật xuống dòng (WRAP), căn lề, cố định tiêu đề và đặt độ rộng cột chuẩn."""
    try:
        sheet_id = ws.id
        col_widths = COLUMN_WIDTHS_BY_TAB.get(tab_name, [])
        num_cols = len(col_widths) or 18
        ws.format(f"A1:{gspread.utils.rowcol_to_a1(2000, num_cols)}", {
            "wrapStrategy": "WRAP",
            "verticalAlignment": "TOP"
        })
        ws.format("1:1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.88, "green": 0.94, "blue": 1.0},
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP"
        })
        ws.freeze(rows=1, cols=1)

        requests = []
        for idx, width_px in enumerate(col_widths):
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": idx,
                        "endIndex": idx + 1
                    },
                    "properties": {"pixelSize": width_px},
                    "fields": "pixelSize"
                }
            })
        if requests:
            spreadsheet.batch_update({"requests": requests})
    except Exception as e:
        print(f"[CẢNH BÁO FORMAT SHEET] {e}")


def get_or_create_tab(spreadsheet, tab_name, headers):
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        all_sheets = spreadsheet.worksheets()
        if len(all_sheets) == 1 and not all_sheets[0].get_all_values():
            ws = all_sheets[0]
            ws.update_title(tab_name)
        else:
            ws = spreadsheet.add_worksheet(title=tab_name, rows=2000, cols=len(headers) + 2)

    existing_first_row = ws.row_values(1)
    if not existing_first_row:
        ws.append_row(headers, value_input_option="USER_ENTERED")
        apply_sheet_layout_and_wrap(spreadsheet, ws, tab_name)
    return ws


def build_unique_key(source_type, item):
    code = str(item.get("code") or "").strip()
    if not code:
        return ""
    if source_type == "TBMT":
        ver = str(item.get("notify_ver") or "00").strip()
        return f"{code}-{ver}"
    return code


def format_hyperlink(url, sep=";"):
    clean_url = str(url or "").strip().replace('"', '%22')
    if not clean_url:
        return ""
    return f'=HYPERLINK("{clean_url}"{sep} "Xem chi tiết")'


def sync_data(source_type):
    if source_type not in SHEET_HEADERS_BY_TAB:
        print(f"[LỖI THAM SỐ] Tên sheet '{source_type}' không hợp lệ (chỉ nhận YCCG, KHLCNT, TBMT).")
        return

    if not os.path.exists(INPUT_FILE):
        print(f"[INFO] Không tìm thấy file {INPUT_FILE} từ Bước 1. Dừng đồng bộ.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        try:
            records = json.load(f)
        except Exception as e:
            print(f"[LỖI ĐỌC JSON] {e}")
            return

    if not records or not isinstance(records, list):
        print(f"[INFO] Không có bản ghi mới nào trong {INPUT_FILE} để ghi lên Google Sheet.")
        return

    spreadsheet = connect_gsheet()
    if not spreadsheet:
        return

    sep = get_formula_separator(spreadsheet)
    headers = SHEET_HEADERS_BY_TAB[source_type]
    ws = get_or_create_tab(spreadsheet, source_type, headers)

    existing_keys = set(k.strip() for k in ws.col_values(1)[1:] if k.strip())
    new_rows = []
    empty_sales_cols = ["", "", "", "", ""]

    for item in records:
        u_key = build_unique_key(source_type, item)
        if not u_key or u_key in existing_keys:
            continue
        existing_keys.add(u_key)

        stage = item.get("stage") or (
            "Tiền dự án (Thẩm định giá)" if "thẩm định giá" in str(item.get("matched_kw", "")).lower()
            else ("Mời báo giá (YCBG)" if source_type == "YCCG" else source_type)
        )
        bu_group = item.get("bu_group") or "Chưa phân nhóm BU"
        province = item.get("province", "")
        investor = item.get("investor", "")
        title = item.get("title", "")
        matched_kw = item.get("matched_kw", "")
        location = item.get("location", "")
        reason = str(item.get("reason") or "").strip()
        auto_note = str(item.get("auto_note") or "").strip()
        full_summary = f"{reason}\n[Note: {auto_note}]" if (auto_note and auto_note not in reason) else (reason or auto_note)
        link_cell = format_hyperlink(item.get("detail_url", ""), sep=sep)

        if source_type == "YCCG":
            row = [
                u_key,
                stage,
                SYNC_TIME_STR,
                province,
                investor,
                title,
                item.get("bid_close_date", "Chưa xác định"),
                matched_kw,
                location,
                bu_group,
                full_summary,
                link_cell
            ] + empty_sales_cols
        elif source_type == "KHLCNT":
            row = [
                u_key,
                f"'{item.get('decision_no', '')}" if item.get("decision_no") else "",
                stage,
                item.get("public_date") or SYNC_TIME_STR,
                province,
                investor,
                title,
                item.get("invest_total", "Chưa cập nhật"),
                item.get("expected_time", "Xem chi tiết"),
                matched_kw,
                location,
                bu_group,
                full_summary,
                link_cell
            ] + empty_sales_cols
        else:  # TBMT
            ver_str = str(item.get("notify_ver") or "00").strip()
            row = [
                u_key,
                f"'{ver_str}",
                stage,
                SYNC_TIME_STR,
                province,
                investor,
                title,
                item.get("amount", "0.00"),
                matched_kw,
                location,
                bu_group,
                full_summary,
                link_cell
            ] + empty_sales_cols

        new_rows.append(row)

    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
        apply_sheet_layout_and_wrap(spreadsheet, ws, source_type)
        print(f"[SUCCESS GSHEET] Đã ghi thêm {len(new_rows)} dòng MỚI (dấu phân cách '{sep}') và định dạng xuống dòng trên sheet '{source_type}'.")
    else:
        print(f"[INFO GSHEET] Toàn bộ {len(records)} bản ghi đã tồn tại trên sheet '{source_type}' (0 dòng trùng được ghi thêm).")


if __name__ == "__main__":
    src = sys.argv[1].upper() if len(sys.argv) > 1 else "TBMT"
    sync_data(src)
