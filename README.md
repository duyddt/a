# Web app thẩm định dữ liệu pháp lý: khung chạy được

Khung cho buổi kiểm tra giữa chặng 30/10: nhập một văn bản (PDF + JSON), hiển thị màn hình hai khung (ảnh trang PDF bên trái, cây Điều/Khoản/Điểm bên phải), bấm một đơn vị thì khung trái nhảy tới trang chứa nó.

Công nghệ: FastAPI + Jinja2 + HTMX. Render trang PDF bằng `pypdfium2`.

## Chạy (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
uvicorn app.main:app --reload
```

Mở http://127.0.0.1:8000. Màn hình danh sách có Nghị định 254/2026/NĐ-CP (55 trang, 416 đơn vị). Bấm vào, rồi bấm các dòng trong cây: ví dụ Điều 20 nhảy sang trang 30, Điều 45 sang trang 47, Phụ lục sang trang 48.

**Về dữ liệu `data/ND254_2026/`:** `document.json` là BẢN NHÁP, dựng bằng `scripts/build_legal_document.py` từ bản chép chữ từng trang (`ocr_pages/`). PDF là bản scan không có lớp chữ, nên chữ được đọc bằng mắt từ ảnh, chưa ai đối chiếu từng chữ. Số hiệu, ngày ban hành (30/6/2026) và ngày hiệu lực (01/7/2026, Điều 43) lấy từ chính văn bản, chưa đối chiếu nguồn chính thức. Đây chính là thứ người soát cần kiểm tra bằng app.

Dựng lại `document.json` sau khi sửa bản chép: `python scripts/build_legal_document.py --pages-dir data/ND254_2026/ocr_pages --out data/ND254_2026/document.json`. Script báo mọi chỗ số thứ tự bất thường và đặt `is_confident=false` thay vì tự sửa.

HTMX được nạp từ `unpkg.com`, nên máy chạy demo cần có mạng (hoặc tải `htmx.min.js` về `app/static/` và sửa `base.html`).

## Thêm văn bản

Mỗi văn bản một thư mục trong `data/` (đổi thư mục gốc bằng biến môi trường `DATA_DIR`):

```
data/<document_id>/
  source.pdf
  document.json     # theo LegalDocument/LegalUnit, mỗi đơn vị có source_pages
```

App render ảnh từng trang khi trang đó được mở lần đầu, lưu vào `data/<document_id>/pages/` (đã nằm trong `.gitignore`). Thay `source.pdf` bằng file khác thì ảnh cũ tự bị bỏ. Nếu `document.json` sai, trang sẽ hiện danh sách lỗi cụ thể (sai `unit_id`, thiếu `source_pages`, số trang ngoài PDF...) thay vì báo lỗi chung chung.

## Cấu trúc

```
app/
  main.py               # các route (FastAPI)
  contracts/            # model TỐI GIẢN, TẠM THỜI theo docs/contracts.md
  services/
    store.py            # nạp văn bản từ thư mục data/ (sau này đổi sang PostgreSQL ở đây)
    render_pdf.py       # PDF -> ảnh từng trang
    views.py            # dựng cây và khung xem trang cho template
  templates/            # list, review, partials/{viewer,tree,detail}
  static/style.css
data/ND254_2026/        # văn bản thật (bản nháp): source.pdf, document.json, ocr_pages/
scripts/
  build_legal_document.py   # ocr_pages/*.txt -> document.json (tách Điều/Khoản/Điểm, gán source_pages)
  make_sample_pdf.py        # tạo PDF mẫu 3 trang cho test
tests/
  fixtures/data/ND254_2026/ # văn bản mẫu 3 trang, nội dung giữ chỗ, dùng cho test
```

Cách HTMX hoạt động ở đây: bấm một dòng trong cây gửi `GET /documents/<id>/units/<unit_id>`. Server trả một đoạn HTML gồm nội dung chi tiết (vào `#detail`) cùng khung trái và cây đã cập nhật (gắn `hx-swap-oob`, htmx tự thay đúng chỗ). Nút trang trước/sau và phóng to gọi `/documents/<id>/viewer?...` và thay `#viewer`. Không có JavaScript tự viết.

## Cần làm tiếp

1. **Thay `app/contracts/` bằng bản sao nguyên văn `backend/app/contracts/` của repo chính** (model hiện tại là bản tối giản tự viết theo `docs/contracts.md`, chưa đối chiếu với `test_contracts.py`). Trường `source_pages` là trường đề xuất thêm, cần PR riêng vào repo chính.
2. **PostgreSQL.** Thẻ việc của Duy yêu cầu FastAPI và PostgreSQL. Khung này chưa có DB: `services/store.py` đọc thẳng file. Bước tiếp theo là lưu văn bản đã nhập vào PostgreSQL (bảng `document_version` bất biến) rồi thêm `review_event` cho phần lưu kết quả thẩm định (hạn 3/11).
3. **Thao tác thẩm định:** Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc, thanh tiến độ ở màn hình danh sách (Dũng, nối với backend của Duy).
4. Thử với D3 thật của Phương và Long khi có.

## Giới hạn của bản nháp ND254

- Phụ lục (trang 48 đến 55) là MỘT đơn vị `ND254_2026_PL1`, chưa tách cấu trúc, đánh dấu `is_confident=false`.
- Điều 9 khoản 4: bản in có hai Điểm đều ghi `d)`, script ghi Điểm thứ hai là `đ` và đánh dấu `is_confident=false`.
- Đoạn không có ký hiệu đứng sau một Điểm được gắn vào Điểm đó (và vào Khoản, Điều cha). Với văn bản có thể thuộc Khoản chứ không thuộc Điểm, người soát cần kiểm tra.
- Các tầng a.1), a.2.1) nằm trong nội dung của Điểm cha, chưa tách thành đơn vị riêng vì schema chung chỉ có Điều, Khoản, Điểm.
- `status = active` và `effective_from` là giá trị đọc từ văn bản, chưa xác minh tình trạng hiệu lực hiện tại. `amended_by` và `replaced_by` để trống vì chưa kiểm tra.

## Đã kiểm tra gì

`pytest` gồm test model (quy ước `unit_id`, trùng id, cha sau con, `document_no` lệch), test nạp dữ liệu và render trang, test dựng cây, và test template cho hành vi bấm đơn vị thì khung trái hiển thị đúng trang. `tests/test_routes.py` kiểm tra các route FastAPI bằng `TestClient`; file này tự bỏ qua nếu chưa cài `fastapi` và `httpx`.
