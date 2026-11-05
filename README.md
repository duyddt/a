# Web app thẩm định dữ liệu pháp lý (Trợ lý AI Pháp lý Thuế – Kế toán)

Khung thẩm định văn bản quy phạm pháp luật đã tách thành Điều, Khoản, Điểm cho người thẩm định kiểm tra, chỉnh sửa và ghi nhận kết quả trước khi đưa vào hệ thống truy xuất (WP2).

Hệ thống cung cấp màn hình hai khung:
* **Khung trái:** Ảnh trang PDF gốc (render trang tự động bằng `pypdfium2`).
* **Khung phải:** Cây Điều / Khoản / Điểm, thanh tiến độ, hướng dẫn 4 tiêu chí kiểm tra, thanh công cụ 4 thao tác thẩm định và lịch sử bản sửa.

---

## 1. Chạy ứng dụng (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
$env:PYTHONUTF8=1; pytest
uvicorn app.main:app --reload
```

Mở trình duyệt tại [**http://127.0.0.1:8000**](http://127.0.0.1:8000).

Mặc định ứng dụng tự khởi tạo cơ sở dữ liệu SQLite tại `data/review.db`. Để dùng **PostgreSQL**, cấu hình biến môi trường:
```powershell
$env:DATABASE_URL="postgresql://user:password@localhost:5432/law_review"
```

---

## 2. Tiêu chuẩn Thẩm định & 4 Thao tác trên Giao diện

Người thẩm định kiểm tra mỗi đơn vị theo **4 tiêu chí**:
1. **Đúng chữ:** Soát dấu tiếng Việt, con số, chính tả so với trang PDF gốc bên trái.
2. **Đúng ranh giới:** Bắt đầu và kết thúc đúng chỗ, không nuốt đoạn của đơn vị khác.
3. **Đúng cấp và số:** Xác định đúng là Điều, Khoản hay Điểm; số thứ tự liên tục.
4. **Không sót:** Đối chiếu bản scan bên trái, đảm bảo không thiếu câu/đoạn nào.

### 4 Thao tác Thẩm định:
* **[✔ Duyệt]:** Xác nhận đơn vị đạt chuẩn. Dòng trên cây chuyển màu xanh lá (`✓ Duyệt`).
* **[✎ Sửa chữ]:** Mở ô soạn thảo sửa trực tiếp nội dung tại chỗ. Sau khi lưu, dòng chuyển màu xanh dương (`✎ Đã sửa`), hiển thị nút *[👁 Đối chiếu bản gốc]* và *[📜 Lịch sử]*. Dữ liệu gốc tuyệt đối không bị ghi đè (lưu trữ dạng append-only).
* **[⚑ Gắn cờ]:** Dành cho trường hợp chữ scan mờ hoặc chưa chắc chắn. Nhập ghi chú ngắn; dòng chuyển màu cam (`⚑ Cờ`).
* **[⚠ Báo lỗi cấu trúc]:** Dành cho trường hợp sai cấp, sai số thứ tự, cần tách hoặc cần gộp đơn vị. Chọn loại lỗi và nhập ghi chú để chuyển cho nhóm kỹ thuật sửa parser. Dòng chuyển màu đỏ (`⚠ Lỗi CT`).

---

## 3. Quản lý Người dùng & Tính Độc lập Thẩm định

* Người thẩm định có thể chuyển đổi tài khoản nhanh qua menu **👤 Người soát** trên góc phải màn hình (các tài khoản mẫu: `lead`, `duy.nguyen`, `dung.nguyen`, `phuong.tran`, `long.luu`).
* **Chế độ Sáng / Tối (Dark / Light mode):** Nút chuyển đổi trực tiếp trên góc phải thanh điều hướng, tự động lưu cấu hình người dùng vào `localStorage`. Độ tương phản và màu sắc được tinh chỉnh chuyên sâu để mọi văn bản và chi tiết luôn rõ ràng, không bị chìm xuống nền.
* Hai người soát hai văn bản khác nhau cùng lúc hoàn toàn độc lập, không ảnh hưởng nhau.
* Mỗi thao tác duyệt hoặc sửa chữ đều ghi nhận chính xác danh tính người thực hiện và thời điểm.

---

## 4. Cách Xuất Kết quả Thẩm định (Export)

### 4.1. Tải trực tiếp trên giao diện
* Bấm nút **[📥 Xuất kết quả]** tại màn hình danh sách hoặc trong màn hình thẩm định.
* Hệ thống trả về file ZIP `{document_id}_export.zip` gồm:
  1. `{document_id}_verified.json`: Văn bản theo đúng schema `LegalDocument` chung, nội dung các đơn vị tự động lấy bản sửa mới nhất (nếu có). Có thể nạp lại trực tiếp bằng model Pydantic.
  2. `{document_id}_parser_feedback.json`: Báo cáo danh sách toàn bộ các đơn vị bị gắn cờ hoặc báo lỗi cấu trúc kèm số trang, loại lỗi, ghi chú và người thực hiện để gửi nhóm parser khắc phục.

### 4.2. Xuất qua đường dẫn trực tiếp
* Tải file ZIP: `GET /documents/{document_id}/export`

---

## 5. Đặc tả REST API

| Phương thức | Endpoint | Mô tả | Request Body mẫu |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/documents/{id}/units/{uid}/approve` | Duyệt đơn vị | Không cần body (lấy user từ cookie) |
| `POST` | `/api/documents/{id}/units/{uid}/edit-text` | Sửa chữ (append-only) | `{"new_content": "Chữ mới..."}` |
| `POST` | `/api/documents/{id}/units/{uid}/flag` | Gắn cờ không chắc | `{"note": "Chữ mờ ở dòng 3..."}` |
| `POST` | `/api/documents/{id}/units/{uid}/structural-error` | Báo lỗi cấu trúc | `{"error_type": "need_split", "note": "Gộp 2 điểm"}` |
| `GET` | `/api/documents/{id}/units/{uid}/history` | Xem toàn bộ lịch sử sửa và quyết định | Trả JSON chi tiết lịch sử |
| `GET` | `/api/documents/{id}/progress` | Xem tiến độ văn bản | Trả số lượng duyệt/cờ/lỗi cấu trúc |

---

## 6. Cấu trúc Dự án

```
app/
  contracts/            # Pydantic schemas (LegalDocument, LegalUnit)
  db/                   # SQLAlchemy models, session, seed users
  services/
    auth.py             # Quản lý reviewer và cookie
    review_service.py   # Nghiệp vụ duyệt, sửa chữ append-only, cờ, lỗi cấu trúc, xuất dữ liệu
    store.py            # Nạp văn bản gốc từ data/<id>/
    render_pdf.py       # Render ảnh từng trang PDF bằng pypdfium2
    views.py            # Dựng cây và khung xem trang cho template
  templates/            # Giao diện Jinja2 + HTMX
  static/style.css, htmx.min.js
data/
  ND254_2026/           # Nghị định 254/2026/NĐ-CP (55 trang, 416 đơn vị)
  ND255_2026/           # Nghị định 255/2026/NĐ-CP (68 trang, 239 đơn vị)
tests/
  test_contracts.py         # Kiểm tra quy tắc model contracts
  test_nd254_document.py    # Kiểm tra cấu trúc ND254
  test_nd255_document.py    # Kiểm tra cấu trúc ND255
  test_review_operations.py # Kiểm tra 4 thao tác, append-only, concurrency, export
  test_api_endpoints.py     # Kiểm tra toàn bộ REST API và luồng thao tác
  test_routes.py            # Kiểm tra route FastAPI
  test_services.py          # Kiểm tra render template và logic view
```

---

## 7. Kiểm thử Tự động

Toàn bộ **49 tests** tự động được chạy qua lệnh:
```powershell
$env:PYTHONUTF8=1; pytest
```
Bao gồm kiểm tra tính bất biến của dữ liệu gốc, tính append-only của bản sửa, tính đồng thời của 2 reviewer, và tính hợp lệ khi nạp lại JSON bằng Pydantic.
