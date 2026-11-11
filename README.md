# Web app thẩm định dữ liệu pháp lý & Phân tích lỗi OCR

Hệ thống thẩm định dữ liệu pháp lý (*Human-in-the-loop*) thuộc dự án **Trợ lý AI Pháp lý Thuế – Kế toán**. Ứng dụng cho phép nhập văn bản pháp luật (**PDF gốc + JSON `LegalDocument**`), lưu trữ vào PostgreSQL, render ảnh từng trang PDF, cung cấp REST API và **giao diện đối chiếu hai khung kiểu File Explorer**:

* **Khung trái (Bản gốc PDF Scan):** Hiển thị ảnh trang PDF gốc sắc nét, tự động nhảy tới trang chứa đơn vị đang chọn, hỗ trợ phóng to/thu nhỏ và chuyển trang nhanh.
* **Khung phải (File Explorer & Đối chiếu trực tiếp):** Hiển thị cây thư mục pháp lý `Điều → Khoản → Điểm` kèm thanh đường dẫn (*Breadcrumb*) và thanh công cụ *Ribbon* phẳng.
* **Chế độ Sửa 1 mục tập trung (Inline Focus Mode):** Khi bấm **Sửa chữ**, hệ thống tự động thu ẩn toàn bộ cây cấu trúc để chỉ giữ lại đúng mục đang sửa nằm ngang hàng 1-1 với trang PDF gốc (không dùng cửa sổ pop-up), kèm nút phóng to/thu nhỏ cỡ chữ (`A-` / `A+`) giúp đối chiếu từng dòng dễ dàng.
* **Tô màu vị trí lỗi (Visual Diff) & Phân tích nguyên nhân OCR:** Tự động đối sánh văn bản bằng thuật toán `SequenceMatcher`, tô đỏ gạch ngang ký tự hệ thống OCR đọc sai và tô xanh ký tự đã sửa đúng ngay trên câu; thống kê tần suất lỗi, nguyên nhân sai và đề xuất giải pháp cải tiến độ chính xác.
* **Thang đo độ chính xác khoa học:** Đánh giá tự động trên tập nhãn chuẩn đã duyệt (*Ground Truth*) theo chuẩn quốc tế UNLV/ICDAR (**CER/CAR**, **WER/WAR**) kết hợp chỉ số chuyên biệt cho văn bản pháp luật (**UEM**, **SFI**, **Composite Score**).
* **Quản lý Tài khoản, Phân quyền Admin & Lưu tiến trình cá nhân:** Đăng nhập có mật khẩu (`PBKDF2-HMAC-SHA256`), người dùng tự tạo tài khoản mặc định ở chế độ **Chỉ xem (`viewer`)** cho đến khi được **Admin cấp quyền soát (`reviewer`)**. Tự động lưu vị trí đơn vị và trang đang duyệt của từng tài khoản để khi tắt máy mở lại sẽ nhảy thẳng đến tiến độ hiện tại.

**Công nghệ:** Python, FastAPI, PostgreSQL (`psycopg` 3 + `psycopg_pool`), Jinja2, HTMX (tích hợp sẵn `/static/htmx.min.js` chạy offline) và `pypdfium2`.

---

## 1. Chạy bằng Docker Compose (Một lệnh)

```powershell
Copy-Item .env.example .env        # Mở .env và đặt POSTGRES_PASSWORD (file .env không đưa lên Git)
docker compose up --build

```

Mở `[http://127.0.0.1:8000](http://127.0.0.1:8000)`. Lần đầu khởi động, ứng dụng tự động chạy toàn bộ các file migration (`001` đến `004`) và nhập các văn bản trong thư mục `./data` (bao gồm Nghị định `254/2026/NĐ-CP` gồm 55 trang scan). Khi khởi động lại, văn bản có cùng mã băm SHA-256 sẽ được bỏ qua để không tạo bản trùng.

* Xem và thử nghiệm trực tiếp toàn bộ REST API tại: `[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)`.
* Xóa sạch dữ liệu container để khởi tạo lại từ đầu: `docker compose down -v`.

---

## 2. Chạy trực tiếp trên máy (Không cần Docker)

### Chế độ đầy đủ chức năng (Có PostgreSQL)

Yêu cầu đã tạo database `review` trong PostgreSQL (`CREATE DATABASE review;`). Mở PowerShell tại thư mục gốc của dự án và chạy:

```powershell
$env:DATABASE_URL = "postgresql://postgres:<mật_khẩu>@localhost:5432/review"
$env:STORAGE_DIR = "storage"
$env:AUTO_IMPORT_DIR = "data"
python -m uvicorn app.main:app --reload --port 8080

```

Mở trình duyệt tại: `http://localhost:8080`.

### Chế độ xem nhanh (Thư mục file – Không cần PostgreSQL)

Nếu không đặt biến môi trường `DATABASE_URL` hoặc `POSTGRES_HOST`, ứng dụng tự động đọc trực tiếp từ `data/<document_id>/{source.pdf,document.json}`. Ở chế độ này, người dùng xem được cây cấu trúc và ảnh trang PDF nhưng không lưu được thao tác thẩm định vào DB.

---

## 3. Cơ chế Tài khoản, Phân quyền (RBAC) & Lưu tiến trình

| Cấp độ quyền (`role`) | Quyền hạn trên hệ thống | Cách cấp quyền |
| --- | --- | --- |
| **Khách / Chỉ xem (`viewer`)** | Xem danh sách văn bản, mở cây Điều/Khoản/Điểm, lật trang và phóng to ảnh PDF. Toàn bộ nút *Duyệt / Sửa chữ / Gắn cờ* bị khóa. | Mặc định khi người dùng mới tự đăng ký tại `/login` hoặc chọn *Vào xem nhanh*. |
| **Người soát (`reviewer`)** | Toàn quyền thực hiện *Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc, Bỏ đánh dấu*, ghi nhận nguyên nhân lỗi OCR, xuất JSON đã duyệt và lưu tiến trình cá nhân. | Do **Admin** tạo trực tiếp hoặc bấm **✓ Cấp quyền soát** trong trang Quản trị. |
| **Quản trị viên (`admin`)** | Toàn quyền của Người soát + Truy cập trang **Quản lý & Cấp quyền tài khoản (`/admin/users`)** để tạo tài khoản mới, cấp quyền soát hoặc thu hồi quyền về Chỉ xem. | Tài khoản quản trị hệ thống. |

* **Lưu tiến trình tự động (`reviewer_progress`):** Mỗi khi người soát chọn đơn vị hoặc thực hiện thao tác thẩm định, hệ thống tự động lưu lại `last_unit_id` và `last_page` gắn với tài khoản đó. Khi tắt máy hoặc đăng nhập lại, hàm `resume_unit_id` tự động mở đúng đơn vị đang sửa dở hoặc đơn vị *"Chưa soát"* kế tiếp.

---

## 4. Thao tác Thẩm định trên Giao diện File Explorer

Khi chọn một đơn vị trong cây cấu trúc, khung trái tự động nhảy tới `source_pages[0]` của đơn vị đó và khung phải hiển thị thanh công cụ phẳng (*Explorer Ribbon*):

| Nút thao tác | Chức năng thực hiện | Trạng thái đơn vị sau thao tác |
| --- | --- | --- |
| **✓ Duyệt** | Xác nhận nội dung văn bản, số hiệu và cấp độ cấu trúc khớp hoàn toàn với ảnh PDF gốc. | `Đã duyệt` (Chấm xanh lá) |
| **✎ Sửa chữ (Chỉ hiện mục này)** | Ẩn toàn bộ cây cấu trúc để tập trung sửa nội dung của riêng mục đang chọn; chọn nguyên nhân lỗi OCR và lưu bản sửa. | Nếu đang `Đã duyệt` sẽ chuyển về `Chưa soát` (để duyệt lại chữ mới); các trạng thái khác giữ nguyên. |
| **⚑ Gắn cờ** | Đánh dấu đơn vị có điểm nghi ngờ cần kiểm tra thêm (bắt buộc nhập lý do). | `Gắn cờ` (Chấm cam) |
| **⚠ Báo lỗi cấu trúc** | Báo lỗi tách sai cấp (Khoản thành Điểm...), nhảy số thứ tự hoặc gộp nhầm (bắt buộc nhập lý do). | `Lỗi cấu trúc` (Chấm đỏ) |
| **↺ Bỏ đánh dấu** | Đưa trạng thái đơn vị về lại trạng thái chưa kiểm tra. | `Chưa soát` (Chấm xám) |

* **Lịch sử bất biến (Append-only):** Mọi sự kiện được ghi thêm vào bảng `review_event`. Trigger `review_event_no_update_delete` trong PostgreSQL chặn mọi lệnh `UPDATE` và `DELETE`. Văn bản gốc trong bảng `legal_unit` được giữ nguyên vẹn 100%.

---

## 5. Thang đo Độ chính xác Hệ thống & Phân tích Lỗi OCR

Tuân thủ nguyên tắc **"Không đoán"**, hệ thống chỉ tính toán độ chính xác trên các đơn vị **đã được người soát duyệt (`approved`)** — đóng vai trò là nhãn chuẩn (*Ground Truth*):

1. **CAR & CER (Character Accuracy / Error Rate – Chuẩn UNLV ISRI & ICDAR):**

$$\text{CER} = \frac{S + D + I}{N_{\text{GT}}}, \quad \text{CAR} = \max(0, 100\% - \text{CER})$$



*(Trong đó $S, D, I$ là số ký tự thay thế, xóa, thêm giữa bản OCR gốc và bản chuẩn đã duyệt; $N_{\text{GT}}$ là tổng số ký tự bản chuẩn).*
2. **WAR & WER (Word Accuracy / Error Rate):** Đo tỷ lệ chính xác và tỷ lệ lỗi ở cấp độ từ/tiếng (đặc biệt nhạy với lỗi mất dấu hoặc sai dấu thanh trong tiếng Việt).
3. **UEM (Unit Exact Match):** Tỷ lệ % đơn vị pháp lý được trích xuất chính xác hoàn toàn ngay từ đầu (được bấm *Duyệt* mà không cần qua bước *Sửa chữ*).
4. **SFI (Structural Fidelity Index):** Chỉ số toàn vẹn cấu trúc phân cấp `Điều → Khoản → Điểm` (tỷ lệ đơn vị đã soát không bị lỗi cấu trúc).
5. **Điểm tổng hợp hệ thống (Composite Legal-OCR Score):**

$$\text{Score} = 0.4 \times \text{CAR} + 0.3 \times \text{WAR} + 0.2 \times \text{SFI} + 0.1 \times \text{UEM}$$



---

## 6. Nhập văn bản mới (CLI & API)

Mỗi văn bản gồm 2 tệp: `source.pdf` và `document.json`.

**Qua dòng lệnh (CLI):**

```powershell
python -m app.cli import --pdf data/ND254_2026/source.pdf --json data/ND254_2026/document.json
python -m app.cli import-dir data/ND254_2026
python -m app.cli import-all data

```

*(Thêm cờ `--replace` nếu muốn ghi đè văn bản đã có cùng `document_id` nhưng khác nội dung).*

**Qua REST API:**

```powershell
curl.exe -F "pdf=@data/ND254_2026/source.pdf" -F "document=@data/ND254_2026/document.json" http://127.0.0.1:8080/api/documents

```

---

## 7. Danh sách Endpoint REST API (`/api`)

| Phương thức & Đường dẫn | Mô tả chức năng |
| --- | --- |
| `GET /api/documents` | Danh sách văn bản kèm số trang, số đơn vị và tiến độ thẩm định. |
| `GET /api/documents/{document_id}` | Chi tiết văn bản kèm cây `tree` (`Điều → Khoản → Điểm`) giữ đúng thứ tự trong file. |
| `GET /api/documents/{document_id}/units/{unit_id}` | Thông tin và nội dung hiện hành của một đơn vị (`LegalUnit`). |
| `GET /api/documents/{document_id}/pages/{page}` | Ảnh PNG của trang PDF thứ `page` (đếm từ 1). |
| `POST /api/documents` | Tải lên và nhập một văn bản mới (`pdf` + `document` + `replace`). |
| `GET /api/documents/{document_id}/progress` | Thống kê số lượng đơn vị theo từng trạng thái thẩm định. |
| `GET /api/documents/{document_id}/ocr-report` | Báo cáo chi tiết độ chính xác (`CAR, WAR, UEM, SFI, Score`), danh sách lỗi ký tự OCR và giải pháp khắc phục. |
| `GET /api/documents/{document_id}/units/{unit_id}/reviews` | Lịch sử các sự kiện thẩm định của một đơn vị (mới nhất xếp trước). |
| `POST /api/documents/{document_id}/units/{unit_id}/reviews` | Ghi nhận một thao tác thẩm định (`approve`, `edit`, `flag`, `structure_error`, `reset`). |
| `GET /api/documents/{document_id}/export` | Xuất tệp JSON chuẩn `LegalDocument` chỉ gồm các đơn vị **Đã duyệt** kèm nội dung đã sửa mới nhất. |

---

## 8. Cấu trúc thư mục dự án

```text
review-app/
├── docker-compose.yml, Dockerfile, .env.example
├── migrations/
│   ├── 001_init.sql               # Bảng document, legal_unit
│   ├── 002_review.sql             # Bảng review_event (chỉ ghi thêm, trigger chặn UPDATE/DELETE)
│   ├── 003_accounts.sql           # Bảng reviewer_account & reviewer_progress (lưu tiến trình)
│   └── 004_auth_roles.sql         # Phân quyền role ('admin', 'reviewer', 'viewer') & is_approved
├── app/
│   ├── main.py                    # FastAPI app, routes giao diện File Explorer & xác thực RBAC
│   ├── api.py, schemas.py         # REST API JSON (/api) và Pydantic schemas (/docs)
│   ├── config.py, db.py           # Cấu hình biến môi trường, connection pool & auto-migration
│   ├── repository.py              # Toàn bộ truy vấn SQL thuần (psycopg 3)
│   ├── cli.py                     # Công cụ dòng lệnh nhập văn bản (import / import-dir / import-all)
│   ├── contracts/                 # Model LegalDocument, LegalUnit & quy ước định danh
│   ├── services/
│   │   ├── auth.py                # Băm mật khẩu PBKDF2-HMAC-SHA256 & ký session token
│   │   ├── review.py              # Luật thẩm định, Visual Diff, thang đo CER/WER/UEM/SFI, xuất JSON
│   │   ├── importer.py            # Kiểm tra đầu vào, SHA-256 hash, staging & render PDF
│   │   ├── db_catalog.py          # Nguồn dữ liệu PostgreSQL
│   │   ├── store.py               # Nguồn dữ liệu chế độ thư mục file
│   │   └── tree.py, views.py, render_pdf.py, models.py
│   ├── templates/                 # Giao diện Jinja2 (login, admin_users, list, review, partials/)
│   └── static/                    # CSS File Explorer (style.css) & htmx.min.js nội bộ
├── data/ND254_2026/               # Dữ liệu Nghị định 254/2026/NĐ-CP (source.pdf, document.json, ocr_pages/)
├── scripts/
│   ├── build_legal_document.py    # Script dựng document.json từ ocr_pages/*.txt
│   └── make_sample_pdf.py         # Script tạo PDF mẫu cho kiểm thử
└── tests/                         # Bộ kiểm thử tự động pytest

```

---

## 9. Kiểm thử (Testing)

Chạy toàn bộ các bài kiểm thử đơn vị và kiểm thử route (không yêu cầu DB):

```powershell
python -m pytest

```

Để chạy thêm bộ kiểm thử tích hợp trên PostgreSQL thật (`tests/test_db_import.py`), trỏ biến `TEST_DATABASE_URL` vào một database dành riêng cho việc test (lưu ý: test sẽ xóa các bảng trong DB đó để kiểm tra lại từ đầu):

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:<mật_khẩu>@localhost:5432/review_test"
python -m pytest tests/test_db_import.py

```

---

### Cách lưu vào file `README.md` và đẩy lên GitHub

Bạn mở file `README.md` trong VS Code (hoặc Notepad), dán toàn bộ nội dung phía trên vào và lưu lại, sau đó chạy 3 lệnh này trong PowerShell để cập nhật lên GitHub:

```powershell
git add README.md
git commit -m "docs: update README with full architecture, RBAC auth, Explorer UI and OCR metrics"
git push origin main

```