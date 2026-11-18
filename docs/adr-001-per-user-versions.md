# ADR-001: Bản riêng của từng người nằm chung bảng `review_event`

- **Trạng thái:** Đã chấp nhận (17/11/2026)
- **Liên quan:** `migrations/009_review_tracks.sql`, `app/services/review.py` (`fold_tracks`, `decide`),
  `app/services/db_catalog.py` (`get`, `record_review`)

## Bối cảnh

Trước đây mọi thao tác thẩm định của mọi người dồn vào **một** trạng thái chung cho mỗi đơn vị: `fold_events` gộp hết
sự kiện của `review_event`. Hai người cùng sửa một văn bản thì người sau ghi đè người trước.

Yêu cầu mới:

- Giữ được bản OCR gốc, bản riêng của từng người chỉnh sửa và bản chốt của người phê duyệt / Admin.
- Các bản độc lập với nhau. Duyệt, gắn cờ và báo lỗi cũng tính riêng theo từng bản.
- Người phê duyệt chọn một bản làm bản chốt, rồi duyệt hoặc ghi chú sai sót. Chỉ Admin được sửa chữ bản chốt.
- Mỗi người một màu để phân biệt.

## Quyết định

**Thêm cột `track` vào `review_event`, không tạo bảng mới:**

- `editor`: sự kiện thuộc bản riêng của người ở cột `reviewer`.
- `final`: sự kiện thuộc bản chốt chung.

Sự kiện của mỗi bản vẫn được gộp bằng đúng hàm `fold_events` như trước.

- **Người chỉnh sửa** (`reviewer`) chỉ ghi vào `editor`. **Người phê duyệt** (`approver`) và **Admin** chỉ ghi vào
  `final`. Track do server suy ra từ vai trò (`review.track_of`); client không chọn được.
- **Thao tác mới `adopt` (Chọn bản):**
  - chỉ có trên `final` (ràng buộc CHECK trong DB);
  - `content_after` là chữ của bản nguồn (`ocr` hoặc tên người chỉnh sửa), do server tự đọc trong cùng giao dịch và
    dưới cùng khóa `lock_unit`; không tin chữ client gửi lên;
  - về mặt gộp, `adopt` giống `edit`: đổi chữ của bản chốt, và nếu bản chốt đang "Đã duyệt" thì quay về "Chưa soát" để
    phải duyệt lại.
- **Sự kiện có trước migration** được coi là bản chốt (`DEFAULT 'final'` lúc thêm cột). Sau đó bỏ DEFAULT, nên mọi
  lệnh INSERT mới phải nêu rõ track.
- **Đọc theo người xem:** `DbCatalog.get(document_id, viewer, version)` nạp mọi bản (`Tracks`) rồi trả về "bản đang
  xem":
  - người chỉnh sửa luôn nhận bản của mình;
  - người phê duyệt / Admin nhận bản chốt, hoặc bản của một người chỉnh sửa khi hỏi đúng tên.

  Nhờ vậy phần lớn code đang đọc `loaded.reviews` / `loaded.doc` (cây, tiến độ, áp bản sửa) giữ nguyên.
- **Màu:** người thứ *n* thao tác đầu tiên trên văn bản nhận màu `--person-n` (8 màu, xoay vòng). Màu chỉ tính khi hiển
  thị, không lưu vào DB.

## Các phương án đã cân nhắc

| Phương án | Vì sao không chọn |
| --- | --- |
| Bảng riêng cho mỗi loại bản (`editor_event`, `final_event`) | Lặp lại cấu trúc, trigger chỉ-ghi-thêm, khóa và truy vấn. Lịch sử "mọi bản" của một đơn vị phải UNION hai bảng. |
| Bảng "bản hiện hành" cập nhật tại chỗ (`unit_version`) | Mất tính chỉ-ghi-thêm: không còn biết ai đổi gì, lúc nào. Mâu thuẫn với trigger `review_event_no_update_delete` đang bảo đảm lịch sử bất biến. |
| Không có track; suy ra bản theo vai trò hiện tại của `reviewer` | Đổi vai trò một tài khoản sẽ làm sự kiện cũ nhảy sang bản khác. Track phải cố định lúc ghi. |

## Hệ quả

- **Tốt:**
  - Lịch sử vẫn bất biến và nằm một chỗ.
  - Gộp trạng thái dùng lại `fold_events` đã có test.
  - `?version=all` chỉ cần sắp theo `id`.
  - Migration chạy được trên DB đang có dữ liệu mà không đổi nghĩa sự kiện cũ.
- **Phải chú ý:**
  - Mọi chỗ đọc trạng thái phải nói rõ đọc bản nào.
  - Xuất JSON và báo cáo độ chính xác qua API luôn dùng bản chốt; trang web hiển thị theo bản của người xem.
  - Thêm một vai trò mới thì phải quyết định nó ghi vào track nào (`review.ROLE_ACTIONS`, `review.track_of`).
- **Chi phí:** `get` đọc mọi sự kiện của văn bản (mọi bản) một lần. Chỉ mục `review_event_track_idx`
  (`document_id, track, reviewer, id`) giữ truy vấn này nhanh khi số người tăng.
