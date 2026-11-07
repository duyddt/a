# Web app thẩm định dữ liệu pháp lý

Web app thẩm định: nhập một văn bản (PDF gốc + JSON `LegalDocument`), lưu vào PostgreSQL, render ảnh từng trang, trả API, và màn hình hai khung (ảnh trang PDF bên trái, cây Điều → Khoản → Điểm bên phải; bấm một đơn vị thì khung trái nhảy tới trang chứa nó). Người soát có thể Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc; mọi thao tác được lưu lại và xuất được thành JSON.

Công nghệ: FastAPI + PostgreSQL + Jinja2 + HTMX. Ảnh trang render bằng `pypdfium2` ngay lúc nhập.

## Chạy bằng Docker Compose (một lệnh)

```powershell
Copy-Item .env.example .env        # rồi mở .env và đặt POSTGRES_PASSWORD (file .env không lên Git)
docker compose up --build
```

Mở http://127.0.0.1:8000. Lần đầu API tự chạy migration và nhập mọi văn bản trong `./data` (hiện có Nghị định 254/2026/NĐ-CP, 55 trang, mất khoảng vài chục giây để render ảnh). Khởi động lại thì nhập lại cùng văn bản sẽ bị bỏ qua, không tạo bản trùng. Muốn tắt nhập tự động, đặt `AUTO_IMPORT_DIR=` (rỗng) trong `.env`.

Trang `/docs` liệt kê và cho thử từng endpoint của API. Xóa sạch dữ liệu để làm lại: `docker compose down -v`.

## Nhập một văn bản

Một văn bản = `source.pdf` (PDF gốc) + `document.json` (theo `LegalDocument`/`LegalUnit`, mỗi đơn vị có `source_pages`; mô tả đầy đủ ở `docs/input-format.md` của repo thiết kế).

Dòng lệnh (trong container đang chạy):

```powershell
docker compose exec api python -m app.cli import --pdf /seed/ND254_2026/source.pdf --json /seed/ND254_2026/document.json
docker compose exec api python -m app.cli import-dir /seed/ND254_2026
docker compose exec api python -m app.cli import-all /seed
```

Thêm `--replace` để thay bản cũ khi đã có cùng `document_id` nhưng nội dung khác. Thư mục `./data` được gắn vào container ở `/seed` (chỉ đọc); để nhập văn bản mới, đặt nó vào `data/<document_id>/` rồi chạy `import-dir`.

Qua API:

```powershell
curl.exe -F "pdf=@data/ND254_2026/source.pdf" -F "document=@data/ND254_2026/document.json" http://127.0.0.1:8000/api/documents
```

Kết quả: `201` đã tạo (`created`/`replaced`), `200` văn bản y hệt đã có (`unchanged`), `409` cùng `document_id` nhưng nội dung khác (gửi thêm `-F replace=true` để thay), `422` JSON hoặc PDF sai, kèm danh sách lỗi cụ thể (sai `unit_id`, thiếu `source_pages`, số trang ngoài PDF, đơn vị cha không có trong file...). Mã băm tính trên PDF và JSON đã chuẩn hóa, nên đổi định dạng JSON (thụt lề, thứ tự khóa) không làm thành bản mới.

## API

| Endpoint | Trả về |
|---|---|
| `GET /api/documents` | Danh sách văn bản: số hiệu, tên, số trang, số đơn vị |
| `GET /api/documents/{document_id}` | Chi tiết kèm `tree`: cây Điều → Khoản → Điểm dựng theo `parent_unit_id`, giữ đúng thứ tự trong file. Mỗi nút có `label` (Điều 9, Khoản 1, Điểm a), `title`, `snippet`, `is_confident`, `review_status`, `source_pages`, `children` |
| `GET /api/documents/{document_id}/units/{unit_id}` | Một đơn vị đủ nội dung (`LegalUnit`) |
| `GET /api/documents/{document_id}/pages/{n}` | Ảnh PNG trang thứ n (đếm từ 1) |
| `POST /api/documents` | Nhập văn bản (xem trên) |
| `GET/POST /api/documents/{document_id}/units/{unit_id}/reviews` | Lịch sử / ghi một thao tác thẩm định (xem mục Thẩm định) |
| `GET /api/documents/{document_id}/progress` | Số đơn vị theo trạng thái |
| `GET /api/documents/{document_id}/export` | Tải JSON các đơn vị đã duyệt |

Khung trái nhảy tới `source_pages[0]` của đơn vị được bấm. `review_status` (`unreviewed`, `approved`, `flagged`, `structure_error`) tính từ lịch sử thẩm định.

## Thẩm định

Mở một văn bản, bấm một dòng trong cây thì khung trái nhảy tới trang, khung chi tiết bên phải hiện nội dung và các nút:

| Nút | Việc làm | Trạng thái sau đó |
|---|---|---|
| Duyệt | Chữ, ranh giới, số hiệu đều đúng | Đã duyệt (xanh) |
| Gắn cờ | Nghi ngờ, cần người khác xem; bắt buộc ghi lý do | Gắn cờ (cam) |
| Báo lỗi cấu trúc | Sai cấp (Khoản thành Điểm...), thiếu hoặc thừa đơn vị; bắt buộc ghi lý do | Lỗi cấu trúc (đỏ) |
| Sửa chữ | Sửa nội dung cho đúng ảnh trang; bản trước khi sửa nằm trong lịch sử | Nếu đang "Đã duyệt" thì về "Chưa soát" (chữ đã đổi, cần duyệt lại); trạng thái khác giữ nguyên |
| Bỏ đánh dấu | Đưa về chưa soát | Chưa soát (xám) |

- Ô "Người soát" bắt buộc, được nhớ trong trình duyệt (cookie `reviewer`). Chưa có đăng nhập: tên do người soát tự gõ.
- **Lịch sử chỉ ghi thêm.** Mỗi thao tác là một dòng ở bảng `review_event`; trigger trong PostgreSQL từ chối UPDATE và DELETE. Trạng thái hiện tại của đơn vị luôn được tính lại từ lịch sử. Bản gốc trong `legal_unit` không bao giờ bị sửa; nội dung hiển thị và xuất ra là bản sửa gần nhất.
- Tải lại trang hoặc khởi động lại container vẫn còn (dữ liệu nằm trong volume của PostgreSQL).
- Thanh tiến độ ở đầu màn hình soát và ở danh sách: đã duyệt / tổng số đơn vị, kèm số gắn cờ và lỗi cấu trúc.
- **Xuất JSON:** nút "Xuất JSON đã duyệt" hoặc `GET /api/documents/{id}/export`. Tệp chỉ gồm đơn vị đang ở trạng thái "Đã duyệt", cùng các trường như `LegalDocument`, nội dung là bản hiện hành. Nếu một đơn vị đã duyệt mà đơn vị cha chưa duyệt thì `parent_unit_id` trỏ tới đơn vị không có trong tệp; số trường hợp này nằm ở header `X-Export-Parent-Missing`.

API: `POST /api/documents/{id}/units/{unit_id}/reviews` với `{"action": "approve|edit|flag|structure_error|reset", "reviewer": "Duy", "note": "...", "content": "..."}`; `GET` cùng đường dẫn trả lịch sử (mới nhất trước); `GET /api/documents/{id}/progress`; `GET /api/documents/{id}/export`.

## Chạy không cần Docker

Không đặt `POSTGRES_HOST` hay `DATABASE_URL` thì app chạy ở chế độ xem nhanh: đọc thư mục `data/<document_id>/{source.pdf,document.json}` trực tiếp, không cần PostgreSQL (không nhập được văn bản và không lưu được thao tác thẩm định; các nút thẩm định ẩn đi).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Muốn dùng PostgreSQL có sẵn: đặt `DATABASE_URL=postgresql://user:matkhau@host:5432/dbname` (hoặc `POSTGRES_HOST/PORT/USER/PASSWORD/DB`) và `STORAGE_DIR` (nơi lưu PDF và ảnh trang).

## Test

```powershell
pytest
```

Phần không cần DB chạy luôn: model, kiểm tra đầu vào khi nhập, mã băm, ghi và render tệp, dựng cây, template, chế độ thư mục file và các route/API (nếu đã cài `fastapi`, `httpx`). Test với PostgreSQL thật (`tests/test_db_import.py`: nhập, nhập lại, xung đột, thay thế, từ chối dữ liệu sai, API, CLI) tự bỏ qua nếu không đặt `TEST_DATABASE_URL`. Test đó (nay gồm cả luồng thẩm định: các thao tác, lịch sử không sửa được, sửa chữ, nhập lại, xuất JSON) XÓA các bảng `review_event`, `document`, `legal_unit`, `schema_migrations` của DB trỏ tới, nên dùng một DB riêng:

```powershell
$env:TEST_DATABASE_URL = "postgresql://review:<mật khẩu>@localhost:5432/review_test"
pytest tests/test_db_import.py
```

## 2. Tiêu chuẩn Thẩm định và 4 Thao tác trên Giao diện

```
docker-compose.yml, Dockerfile, .env.example
migrations/001_init.sql    # bảng document, legal_unit (áp dụng tự động khi API khởi động)
migrations/002_review.sql  # bảng review_event (chỉ ghi thêm)
app/
  main.py                  # FastAPI: giao diện (Jinja2 + HTMX) và gắn router API
  api.py, schemas.py       # API JSON và định dạng response (hiện ở /docs)
  config.py, db.py         # cấu hình từ biến môi trường, kết nối + migration
  repository.py            # mọi câu SQL
  cli.py                   # lệnh nhập
  contracts/               # model TỐI GIẢN, TẠM THỜI theo docs/contracts.md
  services/
    importer.py            # kiểm tra, mã băm, render, lưu (idempotent)
    db_catalog.py, store.py  # nguồn dữ liệu: PostgreSQL / thư mục file
    review.py              # quy tắc thẩm định: trạng thái tính từ lịch sử, kiểm tra thao tác, tiến độ, xuất
    tree.py, views.py, render_pdf.py, models.py
  templates/, static/
data/ND254_2026/           # Nghị định 254: source.pdf, document.json (BẢN NHÁP), ocr_pages/
scripts/build_legal_document.py   # ocr_pages/*.txt -> document.json
tests/
```

## Lưu ý

- **`app/contracts/` là bản tạm.** Khi có repo chính, thay cả thư mục này bằng bản sao nguyên văn `backend/app/contracts/`. Trường `source_pages` là trường đề xuất thêm, cần PR riêng vào repo chính.
- **`data/ND254_2026/document.json` là bản nháp**, dựng từ bản chép chữ ảnh scan, chưa ai đối chiếu từng chữ. Ngày ban hành, hiệu lực và `status` lấy từ chính văn bản, chưa xác minh với nguồn chính thức. Phụ lục (trang 48 đến 55) là một đơn vị chưa tách cấu trúc; các tầng a.1), a.2.1) nằm trong nội dung của Điểm cha. Đây là thứ người soát cần kiểm tra bằng app. Dựng lại sau khi sửa bản chép: `python scripts/build_legal_document.py --pages-dir data/ND254_2026/ocr_pages --out data/ND254_2026/document.json`.
- **`--replace` sau khi đã thẩm định:** lịch sử thẩm định được giữ (khớp theo `unit_id`, không bị xóa), nên một đơn vị đã duyệt vẫn hiện "Đã duyệt" và bản sửa chữ vẫn được áp dụng dù nội dung nhập lại đã khác. App chưa phát hiện việc này. Sau khi nhập lại văn bản đã soát, cần xem lại các đơn vị đổi nội dung. Tốt nhất đừng `--replace` khi việc soát đã bắt đầu.
- Chưa có đăng nhập; tên người soát do người dùng tự gõ, nên không dùng làm bằng chứng ai đã làm gì.
- Sửa chữ một Điều không tự sửa nội dung các Khoản con (và ngược lại); mỗi đơn vị được sửa riêng.
- HTMX nạp từ `unpkg.com`; máy demo cần có mạng, hoặc tải `htmx.min.js` về `app/static/` và sửa `base.html`.
- Cây có hai dòng JavaScript nhỏ: đổi highlight dòng đang chọn (để giữ trạng thái đóng/mở của cây) và nút "Mở hết / Thu hết".
