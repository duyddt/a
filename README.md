# Web app thẩm định dữ liệu pháp lý & Phân tích lỗi OCR

Hệ thống thẩm định dữ liệu pháp lý (*Human-in-the-loop*) thuộc dự án **Trợ lý AI Pháp lý Thuế – Kế toán**. Ứng dụng cho phép nhập văn bản pháp luật (**PDF gốc + JSON `LegalDocument**`), lưu cả cấu trúc lẫn PDF gốc vào PostgreSQL, dựng ảnh từng trang PDF khi có người xem, cung cấp REST API và **giao diện đối chiếu hai khung kiểu File Explorer**:

* **Khung trái (Bản gốc PDF Scan):** Hiển thị ảnh trang PDF gốc sắc nét, tự động nhảy tới trang chứa đơn vị đang chọn, hỗ trợ phóng to/thu nhỏ và chuyển trang nhanh.
* **Tô sáng vùng nội dung trên PDF:** Bấm một Điều/Khoản/Điểm trong cây, vùng nội dung tương ứng trên ảnh trang được tô vàng. Khung PDF mặc định "⤢ Vừa trang": cả trang nằm gọn trong khung, đúng tỉ lệ, không phải cuộn; đơn vị sang trang sau thì bấm số trang ở đầu khung. Bấm vào trang để phóng vừa chiều rộng ngay tại chỗ bấm. PDF có lớp chữ (PDF số hoặc đã OCR) được tô đúng từng dòng theo toạ độ ký tự. Trang scan hoặc ảnh chụp được tô đúng từng dòng theo toạ độ từ do Tesseract trả về: toạ độ lưu sẵn lúc nhập văn bản, hoặc nạp bằng lệnh `page-words`. Ảnh chụp tay được cân bằng độ sáng nền trước khi OCR. Chỉ khi trang chưa có toạ độ và máy không có Tesseract thì mới ước lượng theo bố cục đoạn văn (khung viền nét đứt, ghi rõ "ước lượng"). Cơ chế và kết quả kiểm thử: [`docs/to-vang-ban-scan.md`](docs/to-vang-ban-scan.md).
* **Giao diện sáng/tối:** nút mặt trời/mặt trăng ở góc phải thanh tiêu đề; lựa chọn được nhớ trên trình duyệt, chưa chọn thì theo giao diện của hệ điều hành.
* **Khung phải (File Explorer & Đối chiếu trực tiếp):** Hiển thị cây thư mục pháp lý `Điều → Khoản → Điểm` kèm thanh đường dẫn (*Breadcrumb*) và thanh công cụ *Ribbon* phẳng.
* **Chế độ sửa tập trung:** Bấm **✎ Sửa chữ** thì màn hình chỉ còn hai thứ:
  - bên trái là trang PDF scan, có vùng tô của mục đang sửa;
  - bên phải là chữ của mục đó trong một ô sửa lớn, kèm một thanh nhỏ: **Lưu**, **Hủy**, lý do OCR sai (tùy chọn) và cỡ chữ `A-` / `A+`.

  Thanh tiêu đề, báo cáo độ chính xác, cây cấu trúc, lịch sử và khung so sánh đều ẩn đi. Phím `Ctrl+Enter` để lưu, `Esc` để hủy. Lưu xong thì quay về giao diện thường và hiện kết quả; lưu lỗi (ví dụ chữ không đổi) thì ở lại sửa tiếp. Màn hình hẹp chia đôi theo chiều dọc: PDF ở trên, ô sửa ở dưới.
* **Tô màu vị trí lỗi (Visual Diff) & Phân tích nguyên nhân OCR:** Tự động đối sánh văn bản (`SequenceMatcher` để tô màu, khoảng cách Levenshtein chính xác để đếm lỗi), tô đỏ gạch ngang ký tự hệ thống OCR đọc sai và tô xanh ký tự đã sửa đúng ngay trên câu; thống kê tần suất lỗi, nguyên nhân sai và đề xuất giải pháp cải tiến độ chính xác.
* **Thang đo độ chính xác khoa học:** Đánh giá tự động trên tập nhãn chuẩn đã duyệt (*Ground Truth*) theo chuẩn quốc tế UNLV/ICDAR (**CER/CAR**, **WER/WAR**) kết hợp chỉ số chuyên biệt cho văn bản pháp luật (**UEM**, **SFI**, **Composite Score**).
* **Quản lý Tài khoản, Phân quyền & Lưu tiến trình cá nhân:** Đăng nhập có mật khẩu (`PBKDF2-HMAC-SHA256`). Mọi trang đều cần đăng nhập, và chỉ **Admin tạo tài khoản**. Có ba vai trò: Admin, Người phê duyệt, Người chỉnh sửa. Hệ thống tự lưu vị trí đơn vị và trang đang duyệt của từng tài khoản, nên khi tắt máy mở lại sẽ nhảy thẳng đến tiến độ hiện tại.

**Công nghệ:** Python, FastAPI, PostgreSQL (`psycopg` 3 + `psycopg_pool`), Jinja2, HTMX (tích hợp sẵn `/static/htmx.min.js` chạy offline) và `pypdfium2`.

---

## 1. Chạy bằng Docker Compose (khuyên dùng cho máy mới)

Chỉ cần cài **Docker Desktop** (Windows/macOS) hoặc Docker Engine + Compose (Linux); không cần cài Python hay PostgreSQL.

```powershell
Copy-Item .env.example .env        # macOS/Linux: cp .env.example .env
# Mở .env, đặt POSTGRES_PASSWORD (và ADMIN_PASSWORD nếu muốn tự chọn mật khẩu admin). File .env không đưa lên Git.
docker compose up --build -d
docker compose logs api            # xem log khởi động
```

Mở <http://127.0.0.1:8080>. Lần đầu khởi động, ứng dụng tự chạy các file migration (`001` đến `010`), tạo tài khoản `admin` và nhập các văn bản trong thư mục `./data`. Khi khởi động lại, văn bản có cùng mã băm SHA-256 được bỏ qua để không tạo bản trùng.

* REST API và tài liệu thử trực tiếp: <http://127.0.0.1:8080/docs>.
* Đăng nhập quản trị: **`admin` / `admin123`** (đổi bằng `ADMIN_PASSWORD` trong `.env`).
* Cổng 8080 đã bị chiếm: đặt `APP_PORT=8090` trong `.env`. Cho máy khác trong mạng LAN truy cập: `APP_BIND=0.0.0.0`.
* Nâng cấp từ bản cũ (container từng chạy bằng root) mà gặp lỗi ghi `/storage`: `docker compose run --rm --user root api chown -R 1000:1000 /storage`.
* Xóa sạch dữ liệu để khởi tạo lại từ đầu: `docker compose down -v`.

---

## 2. Chạy trực tiếp trên máy (không dùng Docker cho app)

Yêu cầu Python 3.12 trở lên. Ứng dụng tự đọc file `.env` ở thư mục gốc dự án nên không cần đặt biến môi trường theo cú pháp riêng của từng shell (biến môi trường đặt trực tiếp vẫn được ưu tiên hơn `.env`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1           # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
Copy-Item .env.example .env            # sửa POSTGRES_PASSWORD / POSTGRES_HOST hoặc DATABASE_URL
python -m uvicorn app.main:app --port 8080
```

Mở <http://127.0.0.1:8080> và đăng nhập **`admin` / `admin123`**.

### Chế độ đầy đủ chức năng (có PostgreSQL)

Cần một PostgreSQL đang chạy và database trùng với `.env`. Nếu chưa cài PostgreSQL, có thể chạy tạm bằng Docker:

```powershell
docker run -d --name review-db -e POSTGRES_USER=review -e POSTGRES_PASSWORD=<mật_khẩu> -e POSTGRES_DB=review -p 127.0.0.1:5432:5432 postgres:16
```

**Dữ liệu nằm ở đâu:** mọi thứ nằm trong PostgreSQL, gồm văn bản, các Điều/Khoản/Điểm, lịch sử thẩm định, tài khoản và cả
**PDF gốc** (bảng `document_pdf`). Thư mục `STORAGE_DIR` (mặc định `storage/`) chỉ là **bộ đệm**: khi có người mở văn bản,
app chép PDF từ PostgreSQL ra đó và dựng ảnh đúng những trang được xem. Xóa bộ đệm không mất dữ liệu; lần xem sau tự dựng
lại. Thư mục này còn giữ khóa ký phiên tự sinh (`.secret_key`, xem mục 3); xóa khóa thì mọi người phải đăng nhập lại.

Đặt `AUTO_IMPORT_DIR=data` trong `.env` nếu muốn tự nhập các văn bản trong `data/` khi khởi động. Nếu PostgreSQL chưa chạy hoặc sai mật khẩu, sau `DB_CONNECT_TIMEOUT` giây (mặc định 10) ứng dụng vẫn khởi động ở **chế độ chỉ xem** và hiện cảnh báo trên trang danh sách thay vì dừng hẳn.

### Chế độ xem nhanh (thư mục file, không cần PostgreSQL)

Bỏ trống cả `DATABASE_URL` và `POSTGRES_HOST`: ứng dụng đọc trực tiếp từ `data/<document_id>/{source.pdf,document.json}`. Ở chế độ này người dùng xem được cây cấu trúc và ảnh trang PDF nhưng không lưu được thao tác thẩm định; tài khoản chỉ nằm trong bộ nhớ (mất khi tắt app).

### Công cụ tùy chọn

**OCR** chỉ cần khi tải lên PDF **scan mới** (không có lớp chữ và không trùng tệp mẫu trong `data/`). Docker image đã cài sẵn Tesseract kèm gói tiếng Việt. Chạy trực tiếp trên máy thì cài chương trình Tesseract OCR có gói `vie` (`pytesseract` đã nằm trong `requirements.txt`; trên Windows app tự tìm `C:\Program Files\Tesseract-OCR\tesseract.exe`), hoặc đặt `GEMINI_API_KEY` trong `.env`. Không có OCR thì PDF scan bị **từ chối kèm lý do**; các tính năng còn lại vẫn chạy bình thường.

---

## 3. Cơ chế Tài khoản, Phân quyền (RBAC) & Lưu tiến trình

| Cấp độ quyền (`role`) | Quyền hạn trên hệ thống | Cách cấp quyền |
| --- | --- | --- |
| **Người chỉnh sửa (`reviewer`)** | Trên văn bản **được giao**: Duyệt, Sửa chữ, Gắn cờ, Báo lỗi cấu trúc, Bỏ đánh dấu trên **bản riêng của mình**, rồi bấm **Hoàn thành**; ghi nhận nguyên nhân lỗi OCR; lưu tiến trình cá nhân. Chỉ thấy bản OCR gốc và bản của mình. | Do **Admin** tạo trong trang Quản trị. |
| **Người phê duyệt (`approver`)** | Trên văn bản **đã chuyển sang phê duyệt**: xem mọi bản (mỗi người chỉnh sửa một màu), **Chọn bản** làm bản chốt, Duyệt / Gắn cờ kèm ghi chú / Báo lỗi cấu trúc / Bỏ đánh dấu trên **bản chốt**, rồi **Trả lại** hoặc **Hoàn tất phê duyệt**. Không sửa chữ. Xuất JSON đã duyệt. | Do **Admin** tạo hoặc đổi vai trò trong trang Quản trị. |
| **Quản trị viên (`admin`)** | Toàn quyền: mọi việc của người phê duyệt trên mọi văn bản (không cần chờ chuyển sang), cộng thêm **sửa chữ bản chốt**; trang **Phân công (`/admin/assign`)**; trang **Quản lý tài khoản (`/admin/users`)** để tạo tài khoản, đổi vai trò, khóa / mở khóa; tải lên, ghi đè (`replace`) và xóa văn bản. | Tạo tự động lần đầu chạy (xem bên dưới). |

* **Không còn tự đăng ký và chế độ khách:** chưa đăng nhập thì mọi trang chuyển về `/login`, còn `/api` trả 401.
* **Tài khoản bị khóa** vẫn đăng nhập và đọc được văn bản, nhưng không thao tác được gì.
* **Tài khoản `viewer` cũ** được migration `007_roles.sql` đổi thành Người chỉnh sửa và vẫn giữ trạng thái khóa. Admin mở khóa nếu muốn cho người đó làm việc.

### Đăng nhập & bảo mật

* **Tài khoản quản trị mặc định: `admin` / `admin123`**, để ai clone dự án về cũng chạy thử được ngay với vai trò admin. Mỗi lần khởi động, tài khoản `ADMIN_USERNAME` (mặc định `admin`) được tạo nếu chưa có, được trả lại quyền admin và đặt lại mật khẩu theo `ADMIN_PASSWORD` (để trống = `admin123`). **Khi cho người khác truy cập (server, mạng LAN) phải đặt `ADMIN_PASSWORD` riêng** trong `.env`, vì `admin123` ai cũng biết; app ghi cảnh báo vào log khi còn dùng mật khẩu mặc định. **Quên mật khẩu admin:** đặt `ADMIN_PASSWORD` mới rồi khởi động lại.
* Tên tài khoản dài 3–64 ký tự (chữ không dấu, số, `.`, `_`, `-`); mật khẩu tối thiểu 8 ký tự. Tạo tài khoản không bao giờ ghi đè tài khoản đã có. Ô mật khẩu có nút con mắt để hiện hoặc ẩn chữ đang gõ.
* Phiên đăng nhập là cookie `HttpOnly`, ký HMAC bằng `SECRET_KEY` (để trống thì tự sinh và lưu ở `<STORAGE_DIR>/.secret_key`) và hết hạn sau `SESSION_DAYS` ngày. Đổi khóa hoặc xóa file này sẽ đăng xuất mọi người.
* Sai mật khẩu 10 lần trong 15 phút: tạm khóa đăng nhập theo (địa chỉ IP, tài khoản).
* Mọi thao tác thẩm định cần tài khoản đang hoạt động (không bị khóa); tải lên, ghi đè và xóa văn bản chỉ dành cho `admin`. Người soát được ghi nhận theo **tài khoản đang đăng nhập** (không còn ô tự gõ tên).
* REST API dùng cookie phiên (trình duyệt) hoặc HTTP Basic cho công cụ dòng lệnh: `curl.exe -u <tài_khoản>:<mật_khẩu> ...`.

* **Lưu tiến trình tự động (`reviewer_progress`):** Mỗi khi người soát chọn đơn vị hoặc thực hiện thao tác thẩm định, hệ thống tự động lưu lại `last_unit_id` và `last_page` gắn với tài khoản đó. Khi tắt máy hoặc đăng nhập lại, hàm `resume_unit_id` tự động mở đúng đơn vị đang sửa dở hoặc đơn vị *"Chưa soát"* kế tiếp.

---

## 4. Thao tác Thẩm định trên Giao diện File Explorer

**Danh sách văn bản theo thư mục chủ đề.** Màn hình đầu tiên được sắp giống thư mục `bo du lieu` của bộ dữ liệu:

- Thư mục gốc **Văn bản pháp luật** liệt kê 10 thư mục chủ đề theo đúng thứ tự I → X:
  - I. Quản lý thuế
  - II. Hóa đơn điện tử
  - III. Thuế GTGT
  - IV. Thuế thu nhập cá nhân
  - V. Thuế thu nhập doanh nghiệp
  - VI. Hộ kinh doanh, cá nhân kinh doanh
  - VII. Đăng ký doanh nghiệp
  - VIII. Chế độ kế toán
  - IX. Bảo hiểm xã hội
  - X. Lao động - Tiền lương

  Thứ tự là theo số La Mã, không theo bảng chữ cái, nên IX đứng sau VIII. Mỗi thư mục ghi số văn bản, số đơn vị, số trang
  và tiến độ thẩm định cộng dồn. Văn bản chưa thuộc thư mục nào nằm ngay ở gốc.
- Bấm vào một thư mục (`/?folder=III`) để xem văn bản bên trong:
  - văn bản xếp theo cột STT của tệp danh mục `Danh_muc_van_ban_phap_luat.xlsx`;
  - phần tiếp (`L59_2020_QH14_tiep`) đứng ngay sau phần chính;
  - dòng `..` đưa về thư mục gốc;
  - thanh địa chỉ ghi đường dẫn, ví dụ "Văn bản pháp luật › III. Thuế GTGT".
- Một văn bản có thể nằm ở nhiều thư mục, đúng như bộ dữ liệu: ND141 ở V và VI, TT152 (= TT52) ở VI và VIII. Dòng của văn
  bản đó ghi "Cũng nằm trong: …" kèm liên kết sang thư mục kia.
- Cây thư mục bên trái có ô **Lọc** theo mã, số hiệu hoặc tên. Ô lọc tìm trong mọi thư mục và tự mở thư mục có văn bản khớp.
- Mở văn bản từ một thư mục thì nút quay lại trên trang thẩm định đưa về đúng thư mục đó.
- Khung **Thêm văn bản mới** có ô chọn thư mục, chọn sẵn thư mục đang mở.
- Chế độ thư mục file, hoặc DB chưa có danh mục, vẫn hiện danh sách phẳng như trước.

Khi chọn một đơn vị trong cây cấu trúc, khung trái tự động nhảy tới `source_pages[0]` của đơn vị đó và khung phải hiển thị thanh công cụ phẳng (*Explorer Ribbon*):

| Nút thao tác | Chức năng thực hiện | Trạng thái đơn vị sau thao tác |
| --- | --- | --- |
| **✓ Duyệt** | Xác nhận nội dung văn bản, số hiệu và cấp độ cấu trúc khớp hoàn toàn với ảnh PDF gốc. | `Đã duyệt` (Chấm xanh lá) |
| **✎ Sửa chữ** | Mở chế độ sửa tập trung: chỉ còn trang PDF và chữ của mục đang chọn. Chọn nguyên nhân lỗi OCR (tùy chọn), `Ctrl+Enter` để lưu, `Esc` để hủy. | Nếu đang `Đã duyệt` sẽ chuyển về `Chưa soát` (để duyệt lại chữ mới); các trạng thái khác giữ nguyên. |
| **⚑ Gắn cờ** | Đánh dấu đơn vị có điểm nghi ngờ cần kiểm tra thêm (bắt buộc nhập lý do). | `Gắn cờ` (Chấm cam) |
| **⚠ Báo lỗi cấu trúc** | Báo lỗi tách sai cấp (Khoản thành Điểm...), nhảy số thứ tự hoặc gộp nhầm (bắt buộc nhập lý do). | `Lỗi cấu trúc` (Chấm đỏ) |
| **↺ Bỏ đánh dấu** | Đưa trạng thái đơn vị về lại trạng thái chưa kiểm tra. | `Chưa soát` (Chấm xám) |

### Phân công và chuyển sang phê duyệt

1. **Admin phân công** ở trang **🧑‍💼 Phân công (`/admin/assign`)**. Đánh dấu từng văn bản, hoặc bấm **Giao cả thư
   mục**, rồi chọn một hay nhiều người chỉnh sửa và một người phê duyệt. Người đã được giao thì giữ nguyên. Bấm ✕ cạnh
   tên để gỡ phân công.
2. **Người chỉnh sửa** thấy khối **📋 Việc được giao cho tôi** ở trang danh sách. Chỉ văn bản được giao mới có nút thao
   tác; văn bản khác chỉ xem. Làm xong thì bấm **✔ Hoàn thành văn bản**: hộp xác nhận báo số đơn vị còn chưa soát. Sau
   khi bấm, bản của người đó chỉ còn xem.
3. **Văn bản chuyển sang người phê duyệt ngay khi mọi người chỉnh sửa được giao đã bấm Hoàn thành**, không phải chờ các
   văn bản khác. Văn bản hiện trong khối **📥 Chờ tôi phê duyệt** của người phê duyệt được giao. Nếu văn bản chưa giao
   cho người phê duyệt nào thì mọi người phê duyệt đều thấy. Người phê duyệt chỉ thao tác được trên văn bản đã chuyển
   sang.
4. Người phê duyệt so sánh các bản, chọn bản, duyệt hoặc gắn cờ kèm ghi chú, rồi chọn một trong hai:
   - **↩ Trả lại** (bắt buộc ghi chú): mọi người chỉnh sửa trở về "chưa hoàn thành" và thấy ghi chú ngay trên trang văn
     bản;
   - **✔ Hoàn tất phê duyệt**: chỉ được khi mọi đơn vị trên bản chốt đã duyệt.
5. Admin **mở lại** được văn bản đã phê duyệt, hoặc cho riêng một người đã bấm Hoàn thành sửa tiếp (nút ↺ ở trang
   Phân công).

| Trạng thái | Khi nào |
| --- | --- |
| Chưa phân công | Chưa giao cho người chỉnh sửa nào |
| Đang chỉnh sửa | Còn người chỉnh sửa chưa bấm Hoàn thành |
| Trả lại | Người phê duyệt trả lại, chờ người chỉnh sửa nộp lại |
| Chờ phê duyệt | Mọi người chỉnh sửa được giao đã bấm Hoàn thành |
| Đã phê duyệt | Người phê duyệt hoặc Admin đã Hoàn tất |

Lịch sử phân công nằm trong bảng `workflow_event`, cũng chỉ ghi thêm. Trạng thái luôn tính lại từ lịch sử
(`app/services/workflow.py`). Quyền thao tác được kiểm tra trong cùng giao dịch với thao tác thẩm định: thao tác thẩm
định giữ khóa chia sẻ theo văn bản, còn Hoàn thành / Trả lại / Hoàn tất giữ khóa độc quyền. Vì vậy một thao tác không
thể lọt vào giữa lúc kiểm tra quyền và lúc ghi.

### Thống kê tiến độ (Admin)

Trang **📊 Thống kê (`/admin/stats`)**, lọc được theo thư mục:

* **Tổng quan:** số văn bản theo từng trạng thái (Chưa phân công, Đang chỉnh sửa, Trả lại, Chờ phê duyệt, Đã phê duyệt).
* **Theo người:** vai trò, số văn bản được giao, **% hoàn thành = văn bản đã xong / văn bản được giao**, số văn bản đang
  chờ duyệt (người phê duyệt), số đơn vị đã xử lý, lần thao tác gần nhất.
  - Người chỉnh sửa xong một văn bản khi bấm Hoàn thành. Đơn vị đã xử lý của họ là đơn vị đã duyệt, gắn cờ hoặc báo lỗi
    cấu trúc trên bản riêng.
  - Người phê duyệt xong một văn bản khi Hoàn tất phê duyệt. Đơn vị đã xử lý của họ là đơn vị đã duyệt trên bản chốt.
* **Theo văn bản:** trạng thái, từng người chỉnh sửa (màu của họ, ✓ nếu đã Hoàn thành, số đơn vị đã xử lý), người phê
  duyệt, % đơn vị đã duyệt trên bản chốt.

Cách tính nằm trong `app/services/stats.py`; cùng số liệu có ở `GET /api/stats`.

### Bản riêng của từng người

Mỗi đơn vị có nhiều **bản** độc lập với nhau:

| Bản | Ai ghi | Ai xem |
| --- | --- | --- |
| **Bản OCR gốc** | Thuật toán chuyển PDF thành chữ (bảng `legal_unit`, không bao giờ đổi) | Mọi người |
| **Bản của từng người chỉnh sửa** | Chính người đó: sửa chữ, duyệt, gắn cờ, báo lỗi… chỉ tác động lên bản của mình | Người đó, người phê duyệt, Admin |
| **Bản chốt** | Người phê duyệt và Admin: chọn bản, duyệt, gắn cờ kèm ghi chú…; chỉ Admin sửa chữ | Người phê duyệt, Admin; xuất JSON và báo cáo độ chính xác qua API lấy bản này |

* Hai người chỉnh sửa cùng một văn bản không ghi đè lên nhau: `bt1` sửa thành chữ A, `bt2` sửa thành chữ B thì cả hai bản
  đều được giữ, cùng với trạng thái (duyệt / gắn cờ / lỗi cấu trúc) riêng của mỗi người.
* Người phê duyệt và Admin thấy khung **⇄ So sánh các bản** ở mỗi đơn vị: bản OCR gốc, rồi bản của từng người chỉnh sửa.
  Mỗi người một màu (8 màu, có bản cho giao diện tối): chip tên, vạch màu trên cây thư mục, và phần chữ khác bản OCR gốc
  tô theo màu người đó. Màu gán theo thứ tự phân công; người đã bị gỡ phân công
  nhưng có thao tác thì xếp sau, theo lần thao tác đầu tiên.
* **⇩ Chọn bản này** chép chữ của bản được chọn vào bản chốt (sự kiện `adopt`, ghi chú "Chọn bản của bt2"). Server tự lấy
  chữ của bản nguồn, không tin chữ do trình duyệt gửi lên. Bản chốt đổi chữ thì phải Duyệt lại.
* Tiến độ trên danh sách và thanh trên cùng tính theo bản của người đang xem: người chỉnh sửa thấy tiến độ của mình,
  người phê duyệt và Admin thấy tiến độ của bản chốt.
* Thiết kế và lý do: [`docs/adr-001-ban-rieng-tung-nguoi.md`](docs/adr-001-ban-rieng-tung-nguoi.md).

* **Lịch sử bất biến (Append-only):** Mọi sự kiện được ghi thêm vào bảng `review_event`, cột `track` cho biết sự kiện
  thuộc bản riêng (`editor`, của người ở cột `reviewer`) hay bản chốt (`final`). Trigger `review_event_no_update_delete` trong PostgreSQL chặn mọi lệnh `UPDATE` và `DELETE`. Văn bản gốc trong bảng `legal_unit` được giữ nguyên vẹn 100%.

---

## 5. Thang đo Độ chính xác Hệ thống & Phân tích Lỗi OCR

Tuân thủ nguyên tắc **"Không đoán"**, hệ thống chỉ tính toán độ chính xác trên các đơn vị **đã duyệt (`approved`)** — đóng vai trò là nhãn chuẩn (*Ground Truth*). `GET /api/documents/{document_id}/ocr-report` và xuất JSON dùng **bản chốt**; báo cáo trên trang văn bản tính theo bản người đang xem (người chỉnh sửa thấy số liệu của bản mình):

1. **CAR & CER (Character Accuracy / Error Rate – Chuẩn UNLV ISRI & ICDAR):**

$$\text{CER} = \frac{S + D + I}{N_{\text{GT}}}, \quad \text{CAR} = \max(0, 100\% - \text{CER})$$



*(Trong đó $S, D, I$ là số ký tự thay thế, xóa, thêm giữa bản OCR gốc và bản chuẩn đã duyệt; $N_{\text{GT}}$ là tổng số ký tự bản chuẩn).*
2. **WAR & WER (Word Accuracy / Error Rate):** Đo tỷ lệ chính xác và tỷ lệ lỗi ở cấp độ từ/tiếng (đặc biệt nhạy với lỗi mất dấu hoặc sai dấu thanh trong tiếng Việt).
3. **UEM (Unit Exact Match):** Tỷ lệ % đơn vị pháp lý được trích xuất chính xác hoàn toàn ngay từ đầu (chữ được *Duyệt* trùng hoàn toàn bản OCR gốc, không phải *Sửa chữ* hay chọn bản khác).
4. **SFI (Structural Fidelity Index):** Chỉ số toàn vẹn cấu trúc phân cấp `Điều → Khoản → Điểm` (tỷ lệ đơn vị đã soát không bị lỗi cấu trúc).
5. **Điểm tổng hợp hệ thống (Composite Legal-OCR Score):**

$$\text{Score} = 0.4 \times \text{CAR} + 0.3 \times \text{WAR} + 0.2 \times \text{SFI} + 0.1 \times \text{UEM}$$

Công thức đầy đủ (đúng như mã nguồn), ví dụ tính tay, hạn chế đã biết và cách đo trên cả bộ dữ liệu: [`docs/do-chinh-xac-ocr.md`](docs/do-chinh-xac-ocr.md). Kết quả kiểm thử trên bộ dữ liệu 67 PDF: [`docs/bao-cao-kiem-thu-bo-du-lieu.md`](docs/bao-cao-kiem-thu-bo-du-lieu.md).



---

## 6. Nhập văn bản mới (CLI & API)

Mỗi văn bản gồm 2 tệp: `source.pdf` và `document.json`, hoặc chỉ PDF (app tự tách cấu trúc, xem cuối mục này). Lệnh
nhập ghi thẳng vào PostgreSQL, kể cả PDF gốc; không ghi gì ra đĩa.

**Qua dòng lệnh (CLI):**

```powershell
python -m app.cli import --pdf data/ND254_2026/source.pdf --json data/ND254_2026/document.json
python -m app.cli import-dir data/ND254_2026
python -m app.cli import-all data
python -m app.cli import-pdfs "bo du lieu"      # mọi PDF trong thư mục và thư mục con, không cần document.json
python -m app.cli page-words                    # OCR toạ độ từ cho trang scan chưa có (để tô vàng đúng dòng)
```

`page-words` dành cho văn bản nhập trước khi có bảng `page_words`, hoặc nhập kèm `document.json`: nhập PDF scan không kèm JSON thì toạ độ đã được lưu ngay lúc OCR. Lệnh chỉ OCR trang không có lớp chữ và chưa có toạ độ, dừng giữa chừng thì lần sau làm tiếp. Thêm `--document ND254_2026` để làm riêng một văn bản, `--workers N` để đặt số trang OCR cùng lúc, `--force` để OCR lại. Cần Tesseract `vie`; máy không có thì chạy trong Docker image của dự án như ví dụ nhập PDF scan bên dưới, thay `import-pdfs /dataset` bằng `page-words`.

*(Thêm cờ `--replace` nếu muốn ghi đè văn bản đã có cùng `document_id` nhưng khác nội dung).*

`import-pdfs` in kết quả từng tệp ngay khi xong:

- tệp trùng từng byte với tệp đã gặp thì bỏ qua;
- văn bản đã có trong DB (mã lấy từ tên tệp, ví dụ `ND255_2026.pdf` → `ND255_2026`) thì bỏ qua **trước khi** tách chữ
  hay OCR. Nhờ vậy, bị ngắt giữa chừng thì chạy lại đúng lệnh cũ, không phải OCR lại;
- PDF trùng từng byte với PDF của một văn bản **đã có trong DB** dưới mã khác (`TT52_2025_BTC.pdf` = `TT152_2025_BTC.pdf`)
  cũng được bỏ qua mà không tách lại;
- PDF scan mà máy không có OCR thì báo `TỪ CHỐI` và nhập tiếp các tệp khác. Lệnh thoát mã 1 nếu có tệp bị từ chối hoặc lỗi.

`import-pdfs` cũng **xếp văn bản vào thư mục chủ đề**:

- Thư mục con cấp 1 như `I. Quản lý thuế` là danh mục của mọi PDF bên trong. Mã danh mục là số La Mã (`I`), nên đổi tên
  thư mục thì chỉ cập nhật tên.
- Nhập riêng một thư mục đánh số, ví dụ `import-pdfs "bo du lieu/II. Hóa đơn điện tử"`, thì PDF trong đó thuộc danh mục II.
- Văn bản đã có, bản trùng byte và bản trùng nội dung đều được xếp **thêm** vào thư mục của tệp. Mỗi dòng kết quả ghi
  `· danh mục …`.
- Văn bản không bị gỡ khỏi thư mục cũ, và nhập lại bằng `--replace` vẫn giữ các thư mục cũ. Muốn chuyển văn bản sang thư
  mục khác thì phải xóa liên kết cũ trong bảng `document_category`.
- Thứ tự trong thư mục lấy từ tệp `.xlsx` ở thư mục gốc (cột "STT" và "Tên file theo quy ước"). Văn bản không có trong
  tệp danh mục xếp sau, theo thứ tự Luật → Nghị định → Thông tư.

Vì văn bản đã có thì không phải tách lại, chạy `import-pdfs "bo du lieu"` trên một DB đã nhập đủ chỉ mất vài giây và chỉ
gán thư mục.

**Nhập PDF scan bằng Docker** (máy không cài Tesseract). Image của dự án đã có Tesseract tiếng Việt. Ví dụ dưới đây dùng
chung mạng với container PostgreSQL tên `review-db` (mục 2), chỉ gắn thư mục ở chế độ đọc:

```powershell
docker build -t review-app .
docker run --rm --network container:review-db -e OCR_WORKERS=4 `
  -e DATABASE_URL=postgresql://review:<mật_khẩu>@127.0.0.1:5432/review `
  -v "${PWD}/bo du lieu:/dataset:ro" review-app python -m app.cli import-pdfs /dataset
```

Đang chạy bằng Docker Compose (mục 1) thì chỉ cần
`docker compose run --rm -v "${PWD}/bo du lieu:/dataset:ro" api python -m app.cli import-pdfs /dataset`.

**Qua REST API** (chỉ Admin):

```powershell
curl.exe -u admin:<mật_khẩu> -F "pdf=@data/ND254_2026/source.pdf" -F "document=@data/ND254_2026/document.json" http://127.0.0.1:8080/api/documents
```

**Chỉ tải lên PDF, không kèm `document.json`** (nút tải lên trên giao diện, hoặc API bỏ trống `document`). App tự dựng
`document.json` (`app/services/pdf_parser.py`):

1. PDF trùng **từng byte** (SHA-256) với `source.pdf` của một văn bản mẫu trong `data/` thì dùng `document.json` đã soát của
   văn bản đó.
2. Ngược lại, app đọc lớp chữ từng trang. Trang không có chữ thì OCR, nếu có Tesseract `vie` hoặc `GEMINI_API_KEY`.
3. `app/services/legal_parser.py` tách cấu trúc **trên cả văn bản**, nên Điều/Khoản trải qua nhiều trang vẫn liền mạch:
   - bỏ tiêu đề trang Công báo, số trang, tên Chương/Mục và khối ký ban hành;
   - không tách cấu trúc bên trong phần trích dẫn “…” và trong hàng của biểu mẫu. Ngoặc mở giữa câu mà không đóng
     (OCR hay chèn dấu ngoặc lạ) thì hết hiệu lực sau vài dòng hoặc khi gặp đúng Điều/Khoản/Điểm kế tiếp;
   - đọc số hiệu, loại văn bản, tên, ngày ký, ngày hiệu lực ngay trong văn bản:
     - số hiệu OCR đọc hỏng hoặc không thấy thì lấy theo quy ước tên tệp của bộ dữ liệu (`ND145_2020.pdf` →
       `145/2020/NĐ-CP`, `L48_2024_QH15.pdf` → `48/2024/QH15`, `TT90_2026_BTC.pdf` → `90/2026/TT-BTC`);
     - văn bản để trống số ("Số: /2025/TT-BNNMT") và ngày không thấy thì để trống, không tự điền;
     - tên bị dấu đóng hay chữ rác OCR làm hỏng thì lấy từ câu ban hành ("Chính phủ ban hành Nghị định …"). Không bao
       giờ lấy quốc hiệu làm tên; không đọc được tên thì ghi "Luật số 109/2025/QH15".
4. Đơn vị có điểm đáng ngờ được đánh dấu **chưa chắc chắn** (`is_confident = false`) để người soát xem kỹ. Ví dụ: nhảy số
   Điều/Khoản, ký hiệu OCR đọc lệch như `©)` thay `c)`, trang trắng giữa Điều, ngoặc kép không đóng.
5. Không đọc được chữ nào (PDF scan khi chưa có OCR) thì từ chối nhập và nêu lý do. Không tạo đơn vị giữ chỗ.

**Tốc độ OCR.** Tesseract chỉ chạy trên CPU và mỗi trang chỉ dùng một nhân: đọc một trang 200 DPI mất khoảng 2,5 giây.
App OCR nhiều trang cùng lúc, số trang đặt bằng `OCR_WORKERS` (mặc định `min(4, số nhân CPU)`). Đo trong Docker trên máy
16 nhân: 1 luồng 2,46 giây/trang, 4 luồng 0,77 giây/trang, 8 luồng 0,59 giây/trang. Mỗi ảnh trang đang chờ OCR chiếm
khoảng 12 MB RAM; app chỉ dựng trước tối đa 2 × `OCR_WORKERS` ảnh. OCR vẫn chạy ngay trong request tải lên, nên với văn
bản scan dài, dùng `import-pdfs` sẽ tiện hơn. Muốn OCR trên GPU thì phải đổi sang bộ OCR khác (chưa làm), vì Tesseract
không dùng được GPU.

Độ chính xác đo được của luồng này: [`docs/bao-cao-kiem-thu-bo-du-lieu.md`](docs/bao-cao-kiem-thu-bo-du-lieu.md), mục 4.

---

## 7. Danh sách Endpoint REST API (`/api`)

| Phương thức & Đường dẫn | Mô tả chức năng |
| --- | --- |
| `GET /api/documents` | Danh sách văn bản kèm số trang, số đơn vị, tiến độ thẩm định và `categories` (các thư mục chứa văn bản). |
| `GET /api/categories` | Các thư mục chủ đề theo thứ tự I → X, kèm số văn bản và mã văn bản theo thứ tự trong thư mục. |
| `GET /api/documents/{document_id}` | Chi tiết văn bản kèm cây `tree` (`Điều → Khoản → Điểm`) giữ đúng thứ tự trong file. |
| `GET /api/documents/{document_id}/units/{unit_id}` | Thông tin và nội dung của một đơn vị (`LegalUnit`) trong bản của người gọi. Người phê duyệt / Admin thêm `?version=<tên người chỉnh sửa>` để xem bản của người đó. |
| `GET /api/documents/{document_id}/units/{unit_id}/versions` | Các bản của đơn vị: `ocr`, bản của từng người chỉnh sửa (kèm trạng thái, ghi chú, số màu), `final`. Người chỉnh sửa chỉ nhận `ocr` và bản của mình. |
| `GET /api/documents/{document_id}/pages/{page}` | Ảnh PNG của trang PDF thứ `page` (đếm từ 1). |
| `POST /api/documents` | Tải lên và nhập một văn bản mới (`pdf` + `document` + `replace` + `category`). Bỏ trống `document` thì app tự tách từ PDF (mục 6). `category` là mã thư mục đã có (ví dụ `II`); mã không có thì trả 422 và không lưu gì. Chỉ Admin. |
| `GET /api/documents/{document_id}/progress` | Thống kê số lượng đơn vị theo từng trạng thái thẩm định. |
| `GET /api/documents/{document_id}/ocr-report` | Báo cáo chi tiết độ chính xác (`CAR, WAR, UEM, SFI, Score`), danh sách lỗi ký tự OCR và giải pháp khắc phục. |
| `GET /api/documents/{document_id}/units/{unit_id}/reviews` | Lịch sử các sự kiện thẩm định của một đơn vị trong bản của người gọi (mới nhất xếp trước). Người phê duyệt / Admin: `?version=all` lấy mọi bản; mỗi sự kiện có `track`. |
| `POST /api/documents/{document_id}/units/{unit_id}/reviews` | Ghi nhận một thao tác thẩm định (`approve`, `edit`, `flag`, `structure_error`, `reset`, `adopt`). Người chỉnh sửa ghi vào bản riêng, người phê duyệt / Admin ghi vào bản chốt. `adopt` cần `source` = `ocr` hoặc tên người chỉnh sửa. Thao tác ngoài quyền trả 422. |
| `GET /api/documents/{document_id}/export` | Xuất tệp JSON chuẩn `LegalDocument` chỉ gồm các đơn vị **Đã duyệt trên bản chốt**, nội dung là bản chốt. |
| `GET /api/stats` | Thống kê tiến độ từng người và từng văn bản (chỉ Admin), `?folder=II` để lọc theo thư mục. |
| `GET /api/documents/{document_id}/workflow` | Trạng thái quy trình: `status`, người chỉnh sửa (đã Hoàn thành chưa, số màu), người phê duyệt, ghi chú trả lại. |
| `POST /api/documents/{document_id}/workflow` | `{"action": "submit"}` (Hoàn thành), `return` (kèm `note`), `finalize`; Admin thêm `assign` / `unassign` (`target_user`, `target_role` = `editor` hoặc `approver`) và `reopen`. Sai quyền hoặc sai trạng thái trả 422 kèm `problems`. |

---

## 8. Cấu trúc thư mục dự án

```text
review-app/
├── docker-compose.yml, Dockerfile, .env.example
├── migrations/
│   ├── 001_init.sql               # Bảng document, legal_unit
│   ├── 002_review.sql             # Bảng review_event (chỉ ghi thêm, trigger chặn UPDATE/DELETE)
│   ├── 003_accounts.sql           # Bảng reviewer_account & reviewer_progress (lưu tiến trình)
│   ├── 004_auth_roles.sql         # Phân quyền role ('admin', 'reviewer', 'viewer') & is_approved
│   ├── 005_document_pdf.sql       # PDF gốc của từng văn bản (PostgreSQL là nơi lưu duy nhất)
│   ├── 006_category.sql           # Thư mục chủ đề (category) và văn bản trong từng thư mục (document_category)
│   ├── 007_roles.sql              # Ba vai trò admin / approver / reviewer (bỏ viewer)
│   ├── 008_page_words.sql         # Toạ độ từng từ OCR của trang scan (để tô vàng đúng dòng)
│   ├── 009_review_tracks.sql      # Bản riêng của từng người (cột track) và thao tác Chọn bản (adopt)
│   └── 010_workflow.sql           # Phân công, Hoàn thành, Trả lại, Hoàn tất phê duyệt (bảng workflow_event, chỉ ghi thêm)
├── app/
│   ├── main.py                    # FastAPI app, routes giao diện File Explorer & xác thực RBAC
│   ├── web_auth.py                # Xác định người dùng (cookie phiên / HTTP Basic), chặn open redirect
│   ├── api.py, schemas.py         # REST API JSON (/api) và Pydantic schemas (/docs)
│   ├── config.py, db.py           # Cấu hình (.env + biến môi trường), connection pool & auto-migration
│   ├── repository.py              # Toàn bộ truy vấn SQL thuần (psycopg 3)
│   ├── cli.py                     # Dòng lệnh: import / import-dir / import-all / import-pdfs / page-words
│   ├── contracts/                 # Model LegalDocument, LegalUnit & quy ước định danh
│   ├── services/
│   │   ├── auth.py                # Băm mật khẩu PBKDF2-HMAC-SHA256, token phiên có hạn, khóa ký, giới hạn đăng nhập sai
│   │   ├── accounts.py            # Tài khoản, phân quyền, admin khởi tạo, vị trí đang soát
│   │   ├── review.py              # Luật thẩm định, bản riêng từng người, Visual Diff, thang đo CER/WER/UEM/SFI, xuất JSON
│   │   ├── workflow.py            # Quy trình phân công → Hoàn thành → phê duyệt (trạng thái tính từ lịch sử)
│   │   ├── stats.py               # Thống kê tiến độ từng người / từng văn bản cho Admin
│   │   ├── importer.py            # Kiểm tra đầu vào, SHA-256 hash, lưu đơn vị + PDF gốc vào PostgreSQL
│   │   ├── pdf_parser.py          # Chỉ tải lên PDF: khớp văn bản mẫu, đọc lớp chữ / OCR song song, dựng document.json
│   │   ├── legal_parser.py        # Tách Điều → Khoản → Điểm trên cả văn bản và đọc siêu dữ liệu
│   │   ├── categories.py          # Thư mục chủ đề: tên thư mục "I. …", thứ tự La Mã, đọc tệp danh mục .xlsx, gom văn bản
│   │   ├── highlight.py           # Vùng tô vàng của đơn vị trên ảnh trang (so khớp tuần tự trên tầng chữ)
│   │   ├── page_words.py          # Toạ độ từng từ OCR (Tesseract), cân bằng sáng ảnh chụp, lưu / nạp sẵn
│   │   ├── db_catalog.py          # Nguồn dữ liệu PostgreSQL (chép PDF ra bộ đệm khi cần)
│   │   ├── store.py               # Nguồn dữ liệu chế độ thư mục file
│   │   └── tree.py, views.py, render_pdf.py, models.py
│   ├── templates/                 # Giao diện Jinja2 (login, admin_users, admin_assign, admin_stats, list, review, partials/)
│   └── static/                    # CSS File Explorer (style.css) & htmx.min.js nội bộ
├── data/ND254_2026/, ND255_2026/  # Văn bản mẫu: Nghị định 254/2026/NĐ-CP và 255/2026/NĐ-CP (source.pdf, document.json, ocr_pages/)
├── docs/                          # Tài liệu kỹ thuật: đo độ chính xác, báo cáo kiểm thử, tô vàng bản scan, ADR
├── scripts/
│   ├── build_legal_document.py    # Script dựng document.json từ ocr_pages/*.txt
│   ├── evaluate_dataset_accuracy.py  # Đo độ chính xác luồng nhập PDF trên cả bộ dữ liệu (docs/do-chinh-xac-ocr.md, mục 7)
│   ├── make_sample_pdf.py         # Script tạo PDF mẫu cho kiểm thử
│   └── prepare_nd255.py           # Dựng ocr_pages/ và document.json cho ND255_2026 theo cấu trúc ND254_2026
└── tests/                         # Bộ kiểm thử tự động pytest

```

---

## 9. Kiểm thử (Testing)

Chạy toàn bộ các bài kiểm thử đơn vị và kiểm thử route (không yêu cầu DB):

```powershell
python -m pytest

```

`tests/test_workflow.py` và `tests/test_stats.py` kiểm tra luật quy trình (ai được làm gì ở trạng thái nào) và cách tính
thống kê mà không cần DB; luồng đầy đủ qua API và trang web nằm trong `tests/test_db_import.py`.
`tests/test_security.py` là bộ test hồi quy cho các lỗi bảo mật đã sửa (giả mạo cookie, path traversal khi xóa, open redirect, mật khẩu mặc định...).
`tests/test_pdf_parser.py` là bộ test hồi quy cho luồng *chỉ tải lên PDF* và hàm khoảng cách của thang đo (lỗi L1–L13 trong `docs/bao-cao-kiem-thu-bo-du-lieu.md`).

Để chạy thêm bộ kiểm thử tích hợp trên PostgreSQL thật (`tests/test_db_import.py`), trỏ biến `TEST_DATABASE_URL` vào một database dành riêng cho việc test (lưu ý: test sẽ xóa các bảng trong DB đó để kiểm tra lại từ đầu). Ví dụ dùng một container tạm ở cổng riêng:

```powershell
docker run -d --name review-test-db -e POSTGRES_USER=review -e POSTGRES_PASSWORD=review_test_pw -e POSTGRES_DB=review_test -p 127.0.0.1:55432:5432 postgres:16
$env:TEST_DATABASE_URL = "postgresql://review:review_test_pw@127.0.0.1:55432/review_test"
python -m pytest
docker rm -f review-test-db
```