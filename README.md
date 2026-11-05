# Hệ thống Thẩm định Dữ liệu Pháp lý (Trợ lý AI Pháp lý Thuế – Kế toán)

Hệ thống web app phục vụ thẩm định văn bản quy phạm pháp luật đã qua bước bóc tách (Điều, Khoản, Điểm), cung cấp giao diện đối chiếu trực quan hai khung giữa bản scan PDF gốc và cấu trúc dữ liệu JSON, bảo đảm chất lượng dữ liệu chuẩn hóa trước khi chuyển tiếp cho hệ thống truy xuất (Work Package 2 - WP2) và các module tiếp theo.

Hệ thống cung cấp giao diện hai khung:
- Khung trái: Hiển thị trang scan PDF gốc, tự động render ảnh từng trang theo yêu cầu bằng thư viện pypdfium2.
- Khung phải: Cây cấu trúc Điều, Khoản, Điểm; thanh tiến độ thẩm định; bảng hướng dẫn 4 tiêu chuẩn kiểm tra; thanh công cụ thao tác thẩm định và nhật ký lịch sử sửa đổi.

---

## 1. Hướng dẫn Cài đặt và Vận hành

### Yêu cầu môi trường
- Python 3.10 trở lên.
- Hệ điều hành: Windows, Linux hoặc macOS.

### Các bước chạy ứng dụng (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PYTHONUTF8=1; pytest
uvicorn app.main:app --reload
```

Sau khi khởi động, truy cập hệ thống tại địa chỉ: http://127.0.0.1:8000

### Cấu hình cơ sở dữ liệu
- Mặc định: Hệ thống sử dụng SQLite tại đường dẫn `data/review.db`.
- Cấu hình PostgreSQL (tùy chọn): Thiết lập biến môi trường `DATABASE_URL`:
```powershell
$env:DATABASE_URL="postgresql://user:password@localhost:5432/law_review"
```

---

## 2. Tiêu chuẩn Thẩm định và 4 Thao tác trên Giao diện

Người thẩm định tiến hành kiểm tra từng đơn vị pháp lý theo 4 tiêu chuẩn bắt buộc:
1. Đúng chữ: Kiểm tra chính tả, dấu tiếng Việt, con số so với trang scan PDF gốc.
2. Đúng ranh giới: Đơn vị bắt đầu và kết thúc chính xác, không bị cắt ngang hoặc nuốt đoạn văn khác.
3. Đúng cấp và số: Xác định đúng cấp Điều, Khoản hoặc Điểm; số thứ tự liên tục, không nhảy cóc.
4. Không sót: Đối chiếu trang scan PDF, bảo đảm không bỏ sót bất kỳ câu hoặc đoạn nào.

### 4 Thao tác thẩm định
- [Duyệt]: Xác nhận đơn vị đạt đầy đủ 4 tiêu chuẩn. Đơn vị trên cây chuyển sang viền xanh lá và gán nhãn "Đã duyệt".
- [Sửa chữ]: Mở form chỉnh sửa trực tiếp nội dung tại chỗ. Hệ thống lưu trữ theo cơ chế Append-only (chỉ ghi thêm bản ghi revision mới, không ghi đè hay làm mất nội dung gốc từ parser). Cho phép xem lại nội dung gốc và nhật ký các lần sửa.
- [Gắn cờ]: Áp dụng khi chữ scan mờ hoặc chưa chắc chắn về nội dung. Yêu cầu nhập ghi chú lý do để lưu vết.
- [Báo lỗi cấu trúc]: Áp dụng khi phát hiện lỗi sai cấp, sai số thứ tự, cần tách hoặc cần gộp đơn vị. Người thẩm định chọn phân loại lỗi và nhập ghi chú để chuyển cho đội ngũ kỹ thuật cải tiến parser.

---

## 3. Quản lý Người dùng và Giao diện

### Tài khoản người thẩm định
Hệ thống hỗ trợ chuyển đổi tài khoản nhanh thông qua danh sách người dùng trên thanh điều hướng:
- `lead`: Trưởng nhóm Thẩm định
- `duy.nguyen`: Nguyễn Đức Duy (Reviewer)
- `dung.nguyen`: Nguyễn Đăng Hùng Dũng (Reviewer)
- `phuong.tran`: Trần Thị Mai Phương (Reviewer)
- `long.luu`: Lưu Thiên Long (Reviewer)

Mọi hành động thẩm định (duyệt, sửa chữ, gắn cờ, báo lỗi) đều lưu vết chính xác mã định danh người thực hiện và thời điểm tương ứng. Các phiên làm việc của nhiều người thẩm định trên các văn bản khác nhau hoàn toàn độc lập, không gây xung đột dữ liệu.

### Chế độ hiển thị Sáng / Tối (Light / Dark Mode)
- Tích hợp nút chuyển đổi chế độ Sáng / Tối trực tiếp trên thanh điều hướng, tự động lưu lựa chọn vào localStorage.
- Giao diện được thiết kế độ tương phản cao, bảo đảm văn bản pháp lý, nhãn trạng thái và đường viền khung không bị chìm xuống nền ở cả hai chế độ.
- Khung PDF được bố trí nền tối riêng biệt để làm nổi bật trang scan PDF màu trắng, hạn chế mỏi mắt trong quá trình rà soát.

---

## 4. Quy trình Xuất Dữ liệu (Export)

### Xuất trực tiếp từ giao diện
- Người dùng chọn chức năng [Xuất kết quả] tại màn hình danh sách hoặc màn hình chi tiết văn bản.
- Hệ thống trả về tệp nén ZIP `{document_id}_export.zip` bao gồm 2 tệp JSON:
  1. `{document_id}_verified.json`: Văn bản đã thẩm định theo đúng schema `LegalDocument` chung, nội dung tự động cập nhật bản sửa mới nhất. Tệp này có thể nạp trực tiếp bằng model Pydantic của backend hệ thống.
  2. `{document_id}_parser_feedback.json`: Báo cáo tổng hợp toàn bộ đơn vị bị gắn cờ hoặc báo lỗi cấu trúc, kèm theo phân loại lỗi, ghi chú, số trang và người thực hiện để phục vụ sửa parser.

### Endpoint xuất dữ liệu
- HTTP GET: `/documents/{document_id}/export`

---

## 5. Đặc tả REST API

| Phương thức | Endpoint | Chức năng | Dữ liệu yêu cầu (Body) |
| :--- | :--- | :--- | :--- |
| POST | `/api/documents/{id}/units/{uid}/approve` | Duyệt đơn vị | Không yêu cầu (xác thực qua cookie) |
| POST | `/api/documents/{id}/units/{uid}/edit-text` | Sửa chữ (Append-only) | `{"new_content": "Nội dung mới..."}` |
| POST | `/api/documents/{id}/units/{uid}/flag` | Gắn cờ chưa chắc chắn | `{"note": "Lý do gắn cờ..."}` |
| POST | `/api/documents/{id}/units/{uid}/structural-error` | Báo lỗi cấu trúc | `{"error_type": "wrong_level", "note": "Chi tiết..."}` |
| GET | `/api/documents/{id}/units/{uid}/history` | Xem lịch sử sửa và quyết định | Trả về JSON chi tiết lịch sử |
| GET | `/api/documents/{id}/progress` | Thống kê tiến độ thẩm định | Trả về số lượng duyệt, sửa, cờ, lỗi cấu trúc |

---

## 6. Cấu trúc Mã nguồn

```text
app/
  contracts/            Pydantic schemas (LegalDocument, LegalUnit)
  db/                   SQLAlchemy models, kết nối DB và seed user
  services/
    auth.py             Xử lý phiên làm việc và cookie người dùng
    review_service.py   Nghiệp vụ duyệt, sửa chữ append-only, gắn cờ, báo lỗi, xuất dữ liệu
    store.py            Nạp văn bản từ thư mục data/<id>/
    render_pdf.py       Render ảnh trang PDF bằng pypdfium2
    views.py            Khởi tạo dữ liệu cây và khung xem trang
  templates/            Giao diện HTML (Jinja2 + HTMX)
  static/               Tệp style.css và thư viện htmx.min.js
data/
  ND254_2026/           Nghị định 254/2026/NĐ-CP (source.pdf, document.json, ocr_pages/)
  ND255_2026/           Nghị định 255/2026/NĐ-CP (source.pdf, document.json, ocr_pages/)
scripts/
  build_legal_document.py   Kịch bản dựng document.json cho ND254
  prepare_nd255.py          Kịch bản trích xuất và dựng document.json cho ND255
tests/
  test_contracts.py         Kiểm tra ràng buộc Pydantic schema
  test_nd254_document.py    Kiểm tra dữ liệu ND254
  test_nd255_document.py    Kiểm tra dữ liệu ND255
  test_review_operations.py Kiểm tra 4 thao tác, append-only, concurrency, export
  test_api_endpoints.py     Kiểm tra toàn bộ REST API và luồng HTMX
  test_routes.py            Kiểm tra các route FastAPI
  test_services.py          Kiểm tra render template và logic view
```

---

## 7. Kiểm thử Tự động

Hệ thống tích hợp toàn diện 49 kịch bản kiểm thử tự động, bao quát toàn bộ nghiệp vụ thẩm định:
- Kiểm tra tính bất biến của dữ liệu gốc và tính append-only của bản sửa chữ.
- Kiểm tra tính độc lập khi nhiều người thẩm định đồng thời làm việc trên các văn bản khác nhau.
- Kiểm tra tính bền vững của trạng thái khi tải lại trang (F5).
- Kiểm tra tính tương thích tuyệt đối của tệp JSON xuất ra với Pydantic schema chung.

Lệnh thực thi kiểm thử:
```powershell
$env:PYTHONUTF8=1; pytest
```
