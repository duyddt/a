# Báo cáo kiểm thử trên bộ dữ liệu (2026-11-17)

## Phạm vi

- **Dữ liệu:** bộ dữ liệu luuthlong đưa lên `aairlab-ou/HeThongTroLyPhapLy_Project`, nhánh `develop`, gồm 67 PDF chia
  theo 10 lĩnh vực. Thư mục cục bộ `bo du lieu/` không đưa lên Git.
- **Đối tượng kiểm thử:** luồng *chỉ tải lên PDF* (`auto_build_json_from_pdf` → `prepare_import` → importer), chạy qua
  hai đường:
  - gọi hàm trực tiếp cho cả 67 tệp;
  - dùng app thật (tài khoản admin, form `/documents/upload`, DB thử `review_test`) cho các tệp tiêu biểu, rồi mở trang
    thẩm định và bấm cây Điều/Khoản bằng trình duyệt.
- **Cách đo độ chính xác:** xem [`do-chinh-xac-ocr.md`](do-chinh-xac-ocr.md), mục 7. Tái tạo bằng
  `scripts/evaluate_dataset_accuracy.py` (lệnh ở mục 6).
- **Trạng thái:** lần kiểm thử đầu tìm ra L1–L10. Cả mười lỗi đã được sửa theo kiểu *viết test hỏng trước, sửa sau*.
  Trong lúc sửa và đo lại bằng OCR thật, tìm thêm L11–L13, cũng đã sửa. Mỗi lỗi có test tương ứng trong
  `tests/test_pdf_parser.py`. Toàn bộ 211 test, gồm cả bộ PostgreSQL, đều qua.
- **Đã nhập cả bộ dữ liệu vào PostgreSQL** (mục 7): 65 văn bản, kèm PDF gốc. Kiểm thử dữ liệu sau khi nhập tìm thêm L14
  (số hiệu, tên văn bản đọc từ bản OCR) và L15 (ngoặc lạ do OCR làm mất Điều). Cả hai đã sửa; các văn bản được tách lại
  bằng bộ tách mới (mục 7.1). Kết quả: 16.952 đơn vị; số Điều bị thiếu từ 305 còn 15 (mục 7.1).
- **Văn bản được sắp theo thư mục chủ đề như `bo du lieu`** (mục 7.2): 10 thư mục I → X, cả 65 văn bản đều đã xếp.

## 1. Tổng quan bộ dữ liệu

| Hạng mục | Trước khi sửa | Sau khi sửa |
|---|---|---|
| Số PDF | 67 | 67 |
| PDF scan, không có lớp chữ | 57 | 57 |
| PDF có lớp chữ | 10. Trong đó 9 là văn bản có Điều/Khoản/Điểm; `ND337_2025.pdf` không phải văn bản quy phạm (D2) | như cũ |
| Nhập được | 67/67, kể cả các tệp nhập sai (L1, L7) | Trên máy không có OCR: 11/67, PDF scan **bị từ chối kèm lý do**. Qua Docker có Tesseract: **cả 67 tệp**, thành 65 văn bản trong PostgreSQL vì 2 tệp trùng byte (D1, D3) được bỏ qua (mục 7) |
| Đơn vị giữ chỗ "trang scan chưa có lớp chữ" | 3.317, ở 55 văn bản | 0 |
| Đơn vị đánh dấu "cần soát kỹ" (`is_confident = false`) | không có | Văn bản có lớp chữ: 32/2.466, trong đó 24 là đơn vị theo trang của ND337 vì văn bản này không có Điều nào. Cả bộ sau khi OCR và sửa L14, L15: 1.115/16.952 (6,6%) |
| Số hiệu / ngày bị bịa | 64/67 văn bản | 0. Số hiệu OCR đọc hỏng thì lấy theo quy ước tên tệp (L14), khớp danh mục Excel ở mọi văn bản có số; bản để trống số (TT101, TT60) thì để "(chưa rõ số hiệu)"; ngày không thấy thì để trống |

## 2. Lỗi logic của hệ thống

Mức độ: **Cao** = sai hoặc mất dữ liệu; **TB** = kết quả tách sai; **Thấp** = chất lượng hoặc độ bền.

| # | Mức | Lỗi và bằng chứng | Đã sửa thế nào | Test |
|---|---|---|---|---|
| L1 | Cao | `_find_matching_seed_json` nhận PDF là văn bản mẫu **chỉ vì trùng số trang**. `TT25_2025_BYT.pdf` và `ND352_2025.pdf` (55 trang) đều bị gán `document.json` của ND254_2026; nếu admin *Ghi đè* thì PDF của ND254 bị thay bằng PDF khác | Chỉ khớp theo SHA-256 của tệp | `test_pdf_with_same_page_count_as_a_seed_is_not_taken_for_that_seed` |
| L2 | Cao | Nội dung Điều **bị cắt ở cuối trang**, vì parser xử lý từng trang riêng. 41,7% đơn vị tham chiếu không xuất hiện. Ví dụ Luật 48/2024 Điều 5 có 28 khoản trải trang 2–6, hệ thống chỉ có trang 2 | Bộ tách mới `app/services/legal_parser.py` đọc **cả văn bản** một lần. Mỗi dòng mang số trang của nó nên `source_pages` đúng. Tiêu đề trang Công báo và số trang chỉ bị bỏ khi nằm ở mép trang. Trang trắng nằm giữa một Điều thì Điều đó bị đánh dấu | `test_units_continue_across_page_breaks`, `test_page_headers_chapter_titles_and_closing_formula_are_not_unit_content`, `test_blank_page_inside_an_article_marks_it_not_confident` |
| L3 | TB | Regex Điều quá lỏng: dòng "Điều 109 …" hay "Điều 5 Thông tư này…" xuống hàng giữa câu bị coi là Điều mới (L41, TT58, L03) | Chỉ nhận "Điều N." ở đầu dòng, ngoài trích dẫn, số tăng dần. Cho nhảy tối đa 3 số (OCR có thể làm mất tiêu đề) và có đánh dấu | `test_line_starting_with_an_article_reference_is_not_a_new_article`, `test_skipped_article_number_is_accepted_but_flagged` |
| L4 | TB | Điểm a), b)… trong phần trích dẫn của luật sửa đổi bị tách thành Điểm của luật sửa đổi (L03 Điều 6) | Theo dõi độ sâu trích dẫn “…”; dòng trong trích dẫn là nội dung, không phải cấu trúc | `test_points_inside_quoted_amendment_text_are_not_split`, `test_quoted_amendment_with_straight_quotes_is_still_quoted` |
| L5 | TB | Điều cuối **dính khối ký ban hành / "Nơi nhận"** (TT101 Điều 4: 1.415 ký tự, đúng là 622) | Dừng ở "Nơi nhận:", "TM./KT./TL./TUQ./Q.", "CHỦ TỊCH", "BỘ TRƯỞNG", "THỦ TƯỚNG", "Luật này được Quốc hội…" và "./." | `test_last_article_stops_before_recipients_and_signature` |
| L6 | TB | **Bịa siêu dữ liệu**: `document_no = "<ID>/2026"`, ngày ban hành = ngày hiệu lực = hôm nay (64/67 văn bản) | Đọc "Số:"/"Luật số:", loại văn bản, tên văn bản, ngày ký ở đầu văn bản (Luật: ngày thông qua), ngày hiệu lực "có hiệu lực … từ ngày …"; nhận cả "dd/mm/yyyy". Không thấy thì để trống | `test_metadata_is_read_from_the_document`, `test_law_metadata_uses_passing_date_and_law_number`, `test_missing_metadata_is_left_unknown_not_invented`, `test_type_and_name_on_one_line_and_numeric_dates`, `test_draft_with_blank_number_and_date_is_left_unknown` |
| L7 | TB | PDF scan được "nhập thành công" dù **không có OCR**; lỗi OCR bị nuốt bằng `except: pass`; ND252 mất 51 giây chỉ để render 133 trang rồi OCR thất bại | Không có OCR thì từ chối ngay, **không render trang**, nêu rõ cách khắc phục. Lỗi OCR được ghi log. Dockerfile cài `tesseract-ocr` + `tesseract-ocr-vie`, `requirements.txt` thêm `pytesseract`. Đã kiểm tra trong image: Tesseract 5.5.0, có gói `vie`, `ocr_available() = True` | `test_scan_pdf_without_ocr_is_refused_without_rendering_pages`, `test_pages_without_any_text_are_refused`, `test_blank_pages_get_no_placeholder_units_when_articles_were_found` |
| L8 | Thấp | Nhánh `build_document` / `read_pages` là mã chết (ImportError bị nuốt) | Xóa nhánh này | (mã đã xóa) |
| L9 | Thấp | `pdf_parser.py` gọi pdfium không qua `PDFIUM_LOCK` | Mọi lệnh pdfium chạy trong khóa; OCR chạy ngoài khóa để không chặn luồng khác | `test_pdfium_is_only_opened_while_holding_the_shared_lock` |
| L10 | Thấp | Thang đo: `_edit_distance_seq` dùng `SequenceMatcher` (`autojunk=True`) nên **đếm thừa lỗi** tới 36% | Levenshtein chính xác bằng thuật toán song song bit Myers/Hyyrö (`do-chinh-xac-ocr.md`, mục 3.1) | `test_edit_distance_matches_levenshtein_on_random_strings_and_word_lists`, `test_scattered_ocr_errors_in_long_units_are_counted_exactly` |
| L11 | Cao | *(Tìm ra khi đo bằng OCR thật.)* **Một dấu ngoặc nhiễu làm mất cả phần sau văn bản.** Tesseract đọc vết bẩn thành `" c) Số biên lai…` (ND254 trang 33). Dấu `"` này mở một trích dẫn không bao giờ đóng, nên Điều 24–45 bị coi là trích dẫn; khi van an toàn xả thì đã lỡ quá 3 Điều nên không bắt nhịp lại được. Cùng họ lỗi: `"`` ở trang 43, `chữa bệnh".` (dấu đóng bị hiểu là mở) và `“CẤP LẠI’’` (đóng bằng hai dấu nháy đơn) ở TT25 | Xác định chiều của dấu `"` theo chữ hai bên (dính chữ trước là đóng, dính chữ sau là mở, còn lại là nhiễu). Coi `’’`/`‘‘` là ngoặc kép. Bỏ ký tự nhiễu đầu dòng (`_ | ~ • * ^` …) khi nhận dạng cấu trúc, nhưng giữ nguyên nội dung và đánh dấu. Sau khi van xả thì nhận Điều kế tiếp bất kể khoảng cách, có đánh dấu | `test_stray_ocr_quote_does_not_hide_the_rest_of_the_document`, `test_two_apostrophes_close_a_curly_quote`, `test_ocr_noise_before_an_article_heading_is_ignored_but_flagged`, `test_articles_after_an_unclosed_quote_are_found_again` |
| L12 | TB | **Hàng đánh số của biểu mẫu nằm trong thân Điều** ("1. Tiền 110", "2. Các khoản nợ phải thu 120"…) và liệt kê a)–e) trong biểu mẫu bị tách thành Khoản/Điểm giả, đẩy Khoản/Điểm thật sang sai chỗ (TT58 Điều 5, 6, 9, 10) | Danh sách đánh số lại từ "1." (hoặc "a)") giữa chừng là danh sách lồng. Khi số tiếp theo hợp với cả hai cách hiểu thì giữ danh sách lồng và đánh dấu để người soát xem | `test_numbered_rows_of_an_embedded_form_are_not_clauses` |
| L13 | TB | Ký hiệu Khoản/Điểm bị Tesseract đọc lệch nên mất đơn vị: `©)` thay `c)`, `ø)`/`gø)` thay `g)`, `1.Tổ` mất dấu cách, `đ)` ở chỗ phải là `d)` | Nhận các dạng này vì © và ø không có trong tiếng Việt; `đ)`→`d)` đối xứng với quy tắc `d)`→`đ)` đã có. Mọi đơn vị nhận theo cách này đều bị đánh dấu | `test_common_ocr_misreads_of_point_and_clause_markers_are_recognised_and_flagged` |
| L14 | TB | *(Tìm ra khi nhập cả bộ.)* **Số hiệu và tên văn bản đọc từ bản OCR thường sai**, mà đây là chữ hiện ở danh sách văn bản. Trên 65 văn bản: 16 không có số hiệu; ND145 ra `4452020/NĐ-CP` (đúng là 145/2020/NĐ-CP); 8 văn bản lấy dòng quốc hiệu làm tên ("QUỐC HỘI CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"); 12 văn bản có tên là chữ nhiễu ("e7 É )", "f†?T(", "rr9T0)"…), do Tesseract đọc con dấu, chữ ký, dấu "ĐẾN" ở trang đầu | **Số hiệu** phải đúng dạng `số/năm/ký hiệu`. Đọc hỏng (`4452020/NĐ-CP`, `2£5/2020`) hoặc không thấy thì lấy theo quy ước tên tệp của bộ dữ liệu (`ND145_2020` → `145/2020/NĐ-CP`). Số hiệu đọc được đúng dạng trong văn bản vẫn được ưu tiên (tệp `TT52_2025_BTC.pdf` thực ra là Thông tư 152, D1); bản để trống số (TT101, D6) vẫn để trống. Loại văn bản không thấy thì lấy theo số hiệu hoặc tên tệp. **Tên:** bỏ ký tự lạ trước chữ đầu, bỏ dòng chữ của dấu đóng ("CÔNG VĂN ĐẾN", "Cổng thông tin điện tử Chính phủ", "Giờ… Kính chuyển") và dòng ít chữ cái. Tên bị hỏng như vậy thì lấy từ câu ban hành ("Quốc hội ban hành Luật Quản lý thuế."), vì câu này nằm ở thân văn bản nên ít bị dấu đè. Không bao giờ lấy quốc hiệu hay tiêu ngữ; không có tên thì ghi "Luật số 109/2025/QH15". Không dùng tệp Excel danh mục: số hiệu trong đó trùng với quy ước tên tệp, còn cột tên chỉ là mô tả ("Thông tư được mô tả là…", "(cần đối chiếu trích yếu)"), không phải trích yếu chính thức. Văn bản đã nhập được tách lại và nhập lại, giữ nguyên PDF gốc (mục 7.1) | `test_unreadable_document_number_falls_back_to_the_file_name_convention`, `test_a_well_formed_document_number_in_the_text_wins_over_the_file_name`, `test_title_never_falls_back_to_the_national_motto_or_ocr_garbage`, `test_garbage_title_is_replaced_by_the_enacting_sentence`, `test_title_mixed_with_a_received_stamp_is_taken_from_the_enacting_sentence`, `test_stamp_inside_the_title_lines_prefers_the_complete_enacting_sentence`, `test_stray_marks_before_the_title_are_dropped`, `test_short_law_names_are_still_titles` |
| L15 | Cao | *(Tìm ra khi soát các số Điều bị thiếu sau khi nhập cả bộ.)* **Dấu ngoặc lạ OCR chèn giữa câu làm mất các Điều phía sau.** Tesseract đọc vết bẩn thành dấu mở ngoặc giữa chữ (`bảo "hiểm`, `ngân “hàng`), `“` ở cuối dòng, `“®`, `“%`, hoặc ngay trước tiêu đề thật (`"Điều 37.`, `” Điều 4.`, `'Điều 14.`). Ngoặc này không bao giờ đóng, nên tới 200 dòng sau đó bị coi là trích dẫn, và mọi tiêu đề Điều trong đó bị bỏ qua. Trên 65 văn bản có 305 số Điều bị thiếu, nhiều nhất là L59 (63), ND168 (40), L45 (32), ND253 (26). Ngoài ra OCR đọc lệch chính dòng tiêu đề: `Điều. 51.`, `Điều, 31.`, `Điều 166:`, `Điền 147.` | Phân biệt hai loại ngoặc. **Trích dẫn khối** (sau "như sau:", "…sau đây", kể cả khi OCR làm mất dấu ":", hoặc mở ngay trước "1.", "a)", "Điều N." của văn bản được sửa) giữ cách xử lý cũ. **Ngoặc trong câu** chưa đóng thì hết hiệu lực sau 8 dòng, và gặp đúng Điều/Khoản/Điểm kế tiếp thì đóng luôn. `“` không có chữ theo sau là vết bẩn. Ký tự lạ trước "Điều N." được bỏ qua nếu số Điều đúng thứ tự. Tiêu đề đọc lệch được nhận khi đúng số Điều kế tiếp, sau số là "." hoặc ":" và tên viết hoa; dấu "," sau số là câu tham chiếu xuống hàng ("Điều 5, Điều 6 Thông tư này…" ở TT58) nên không nhận. Mọi chỗ nhận theo cách này đều bị đánh dấu "cần soát kỹ". **Kết quả: số Điều bị thiếu từ 305 còn 15** (mục 7) | `test_stray_quotes_inside_words_do_not_hide_the_following_articles`, `test_unclosed_quote_in_a_sentence_does_not_swallow_the_next_clause`, `test_unclosed_quote_in_a_sentence_expires_after_a_few_lines`, `test_lone_curly_quote_without_text_after_it_is_ocr_noise`, `test_stray_quote_or_mark_before_a_real_article_heading_is_recognised_and_flagged`, `test_quoted_article_of_an_amended_law_is_still_quoted_when_it_has_the_next_number`, `test_quoted_next_article_after_a_lead_in_whose_colon_was_lost_is_still_quoted`, `test_quoted_clause_after_an_amendment_line_without_colon_is_still_quoted`, `test_misread_article_heading_with_the_next_number_is_recognised_and_flagged`, `test_misread_article_heading_needs_the_next_number_and_a_capitalised_title` |

## 3. Vấn đề của bộ dữ liệu (cần nhóm dữ liệu xử lý)

| # | Vấn đề | Đề xuất |
|---|---|---|
| D1 | `TT152_2025_BTC.pdf` (VI) và `TT52_2025_BTC.pdf` (VIII) **giống hệt từng byte**, nên một trong hai là sai tệp | Lấy lại đúng tệp |
| D2 | `ND337_2025.pdf` thực chất là bảng "CẬP NHẬT HỆ THỐNG VĂN BẢN PHÁP LUẬT KẾ TOÁN THUẾ MỚI NHẤT", không phải Nghị định 337/2025. App nay nhập thành 24 đơn vị theo trang, tất cả bị đánh dấu | Lấy lại đúng văn bản |
| D3 | `ND141_2026.pdf` có ở cả hai thư mục V và VI (cùng nội dung) | Giữ một bản, ghi chú thuộc hai lĩnh vực |
| D4 | `ND255_2026.pdf` trong bộ dữ liệu có 77 trang, bản mẫu `data/ND255_2026` có 68 trang. Tải lên bị báo "đã tồn tại với nội dung khác". Khi nhập cả bộ, hệ thống giữ bản mẫu 68 trang đã soát, không nhập bản 77 trang | Xác định bản đúng; nếu là bản 77 trang thì nhập lại với `--replace` |
| D5 | 57/67 PDF là ảnh scan | Đã nhập qua Docker image có Tesseract (mục 7). Tìm được bản có lớp chữ trên Công báo thì chất lượng sẽ tốt hơn OCR |
| D6 | `TT101_2025_BNNMT.pdf` là bản **chưa điền số và ngày** ("Số: /2025/TT-BNNMT", "ngày tháng năm 2025"). Lớp chữ của `TT25_2025_BYT.pdf` **thiếu khối đầu văn bản** (số hiệu, ngày ký). `TT60_2025_BYT.pdf` cũng là bản chưa điền số và ngày ("Số:  /2025/TT-BYT"). App để trống số hiệu của TT101 và TT60 vì văn bản ghi rõ là để trống. Số hiệu của TT25 lấy theo tên tệp (L14). Ngày ký của cả ba văn bản để trống, không đoán | Lấy bản đã ban hành / bản có đủ trang đầu |

## 4. Độ chính xác đo được

### 4.1. Trong app (Ground Truth do người duyệt)

Vẫn **chưa có số**: DB `review` chưa có đơn vị nào được duyệt. Con số chính thức chỉ có sau khi nhóm thẩm định duyệt trên
app. Từ nay app dùng khoảng cách Levenshtein chính xác (L10), nên số liệu đúng ngay từ đầu.

### 4.2. Luồng nhập PDF, đo ngoài app

Công thức là `build_ocr_report` của app. Ba phép đo (A), (B), (C) được mô tả ở mục 7 tài liệu cơ chế.

| Phép đo | Đơn vị GT | Recall | Precision | CAR | WAR | UEM | SFI | **Composite** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Trước khi sửa** — (B) 9 văn bản có lớp chữ, toàn luồng | 2.013 | 53,20% | 71,69% | 55,68% | 55,81% | 73,76% | 71,69% | **60,73%** |
| **Trước khi sửa** — (C) OCR bản scan | — | Không đo được: không có OCR, 55 trang thành 55 đơn vị giữ chỗ | | | | | | |
| **Sau khi sửa** — (A) văn bản mẫu ND254 + ND255, GT độc lập | 649 | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| **Sau khi sửa** — (B) 9 văn bản có lớp chữ (hồi quy) | 2.026 | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| **Sau khi sửa** — (C) **OCR thật** ND254, 55 trang scan, Tesseract 5.5 `vie`, 200 DPI | 415 | **93,98%** | 97,26% | **96,68%** | 94,71% | 27,69% | 97,26% | **89,31%** |
| *Mốc giữa: đã sửa L1–L10, chưa sửa L11–L13* — (C) ND254 | 415 | 50,84% | 98,14% | 39,17% | 36,91% | 27,01% | 98,14% | 49,07% |

Số đơn vị GT của (B) đổi từ 2.013 lên 2.026 vì bộ tham chiếu cũng được sửa sau khi phân xử bằng cách đọc PDF. Bộ tham
chiếu cũ coi hàng của biểu mẫu là Khoản, không đóng được ngoặc kiểu “…", và bỏ cả số trong ô bảng (TT58, L03).

**Đọc kết quả:**

- **Độ chính xác thực tế khi người dùng tải lên một PDF scan** là phép đo (C): Composite **89,3%**, CER 3,32%, WER 5,29%.
  Có 390/415 đơn vị được tách đúng chỗ. 25 đơn vị bị sót và 11 đơn vị thừa đều do OCR đọc một ký hiệu thành một ký
  hiệu *hợp lệ khác* (`c)`→`e)`, `5.`→`3.`, `g)`→`8)`), hoặc mất hẳn ký hiệu đầu dòng. Không thể sửa các trường hợp này
  mà không đoán, nên người soát phải dùng "⚠ Báo lỗi cấu trúc".
- **UEM chỉ 27,7%** dù CAR 96,7%: chỉ cần sai một ký tự là đơn vị không còn nguyên vẹn. UEM nên được đọc là "tỷ lệ đơn vị
  người soát không phải sửa chữ".
- Với PDF có lớp chữ, bộ tách khớp hoàn toàn cả với GT độc lập (A) lẫn bộ tham chiếu (B). Phần việc còn lại của người soát
  là 32 đơn vị bị đánh dấu.
- Mốc giữa cho thấy L11 nghiêm trọng thế nào: chỉ một dấu `"` nhiễu đã kéo Composite từ 89 xuống 49.
- ND255: `source.pdf` có sẵn lớp chữ nên (C) không phải OCR. Kết quả là CAR 99,96%; 4 đơn vị lệch vài ký tự giữa lớp chữ
  PDF và bản chép tay.

### 4.3. 57 PDF scan

Đã nhập hết bằng Docker image có Tesseract (mục 7). Chưa có Ground Truth cho các văn bản này, nên số liệu (C) ở trên vẫn
là ước lượng tốt nhất cho chất lượng của chúng. Độ chính xác thật sẽ có dần khi nhóm thẩm định duyệt và sửa trong app
(mục 4.1).

OCR vẫn chạy ngay trong request tải lên. Đã giảm thời gian chờ bằng OCR song song (0,61 giây/trang với 4 luồng, trước
đây 2,6 giây/trang), nhưng văn bản 839 trang như TT89 vẫn mất khoảng 7 phút. Với bản scan dài, hãy dùng lệnh
`import-pdfs` (mục 7). Đề xuất tiếp theo: chuyển OCR khi tải lên qua giao diện sang tác vụ nền có trạng thái "đang OCR".

## 5. Giao diện với văn bản mới nhập

Đã kiểm tra trên app thật với code đã sửa (DB thử `review_test`, khung nhìn 1366×657):

- L48_2024_QH15 Điều 5 có chip **Trang 2, 3, 4, 5, 6** (trước đây chỉ có "Trang 2"). Khoản 20 Điều 5 ở trang 5. Vùng tô
  vàng nằm gọn trong khung PDF.
- TT58_2026_BTC Điểm b Khoản 2 Điều 10 trải trang 27–31, đúng như văn bản.
- Thanh tiêu đề hiện đúng tên và số hiệu, ví dụ "THUẾ GIÁ TRỊ GIA TĂNG · 48/2024/QH15".
- Tải lên PDF scan (`ND352_2025.pdf`, `ND252_2026.pdf`) thì quay về danh sách với thông báo "Không đọc được chữ nào trong
  PDF: đây là bản scan chưa có lớp chữ…", sau 0,3 giây (trước đây là 51 giây và 133 đơn vị giữ chỗ).
- Không có lỗi console; trang không bị cuộn.

## 6. Chạy lại phép đo

```powershell
$env:PYTHONUTF8 = "1"
.venv\Scripts\python.exe -m pytest -q tests\test_pdf_parser.py
.venv\Scripts\python.exe scripts\evaluate_dataset_accuracy.py "bo du lieu" --seeds data --out eval.json   # (A) + (B)

docker build -t lawproject-review-app:ocr-check .                                                          # (C)
docker run --rm -e PYTHONUTF8=1 -v "${PWD}/scripts:/app/scripts:ro" -v "${PWD}/data:/seeds:ro" `
    lawproject-review-app:ocr-check python scripts/evaluate_dataset_accuracy.py --seeds /seeds --ocr
```

Khi (B) báo lệch, đọc đoạn PDF tương ứng để phân xử trước khi sửa bộ tách hay bộ tham chiếu.

## 7. Nhập bộ dữ liệu vào hệ thống (2026-11-17)

Cả bộ dữ liệu đã được nhập vào DB `review` mà app đang dùng, bằng lệnh `python -m app.cli import-pdfs` (README, mục 6).

**Nơi lưu.** Tất cả nằm trong PostgreSQL: văn bản, đơn vị, và **PDF gốc** (bảng mới `document_pdf`, migration `005`). Lúc
nhập không ghi gì ra đĩa. Trước đây importer dựng sẵn ảnh PNG của mọi trang vào `STORAGE_DIR`; với bộ này sẽ là khoảng
3.800 ảnh, tức khoảng 2 GB. Nay `STORAGE_DIR` chỉ là bộ đệm: app chép PDF từ PostgreSQL ra và dựng ảnh đúng những trang
được xem. Xóa bộ đệm thì lần xem sau tự dựng lại.

| Đợt | Chạy ở đâu | Kết quả | Thời gian |
|---|---|---|---|
| 1. PDF có lớp chữ | Trên máy (không có OCR) | 10 văn bản mới; ND254, ND255 đã có nên bỏ qua; 2 tệp trùng byte bị bỏ qua; 53 PDF scan bị từ chối để chờ đợt 2 | 9 giây |
| 2. PDF scan | Docker image có Tesseract, `OCR_WORKERS=4`, chỉ gắn thư mục đọc, nối thẳng vào container PostgreSQL | 53/53 văn bản mới, không lỗi; 3.253 trang OCR | 1.993 giây (33 phút), **0,61 giây/trang** |

**Trong PostgreSQL sau khi nhập:**

- 65 văn bản (67 tệp, trừ 2 tệp trùng byte), 3.807 trang;
- 65 PDF gốc, mã SHA-256 đều khớp, tổng 179 MB; cả DB 200 MB;
- 14.464 đơn vị, trong đó 975 (6,7%) bị đánh dấu "cần soát kỹ". Nhiều nhất là L59_2020_QH14_tiep (94/699), ND252_2026
  (91/883) và L45_2019_QH14 (82/880);
- chưa có thao tác thẩm định nào.

Đây là số liệu lúc nhập lần đầu. Sau khi sửa L14, L15 và tách lại thì xem mục 7.1.

**Vì sao OCR trước đây cần hơn 2 giờ.** Tesseract 5 dùng mạng LSTM đọc từng dòng chữ trên CPU, mất khoảng 2,46 giây cho
một trang 200 DPI (dựng ảnh chỉ mất 0,06 giây). Code cũ OCR **lần lượt từng trang trên một nhân CPU**, trong khi máy có
16 nhân: 3.253 trang × 2,46 giây ≈ 2,2 giờ. Tesseract tự chia một trang cho nhiều luồng cũng không nhanh hơn (đo được
2,46 và 2,45 giây/trang). Vì vậy app nay OCR nhiều trang cùng lúc, mỗi tiến trình Tesseract một luồng
(`OMP_THREAD_LIMIT=1`), và chỉ dựng trước tối đa 2 × `OCR_WORKERS` ảnh để văn bản 839 trang không chiếm hết RAM.
Tesseract không chạy được trên GPU. Muốn dùng GPU (máy có RTX 4050) thì phải đổi bộ OCR, ví dụ PaddleOCR + VietOCR, rồi
đo lại độ chính xác. Việc này để giai đoạn sau.

**Kiểm tra trên app thật** (cổng 8080, trình duyệt Chrome, khung nhìn 1366×657): 28/28 mục đạt.

- Danh sách có đủ 65 văn bản.
- L48 Điều 5 (PDF có lớp chữ), ND252 Khoản 1 Điều 3, TT89 Điều 2 (839 trang) và ND145 Khoản 1 Điều 10 (PDF vừa OCR):
  - mở đúng trang;
  - có ảnh trang và vùng tô vàng; PDF scan dùng kiểu "ước lượng";
  - không cuộn ngang;
  - bộ đệm chỉ có các trang vừa xem.
- Xóa thư mục đệm của ND252 rồi mở lại vẫn có ảnh, dựng lại từ PDF trong PostgreSQL.
- Bộ đệm cũ của ND254, có từ trước khi PDF nằm trong DB, vẫn hiển thị và được gắn mã PDF.
- Không có lỗi console.

Danh sách văn bản lúc đó cho thấy lỗi L14 (mục 2): nhiều văn bản OCR có tên hoặc số hiệu sai. Đã sửa (mục 7.1).

**Chạy lại:**

```powershell
$env:DATABASE_URL = "postgresql://review:<mật_khẩu>@127.0.0.1:<cổng>/review"
python -m app.cli import-all data                 # văn bản mẫu (có document.json đã soát)
python -m app.cli import-pdfs "bo du lieu"        # PDF có lớp chữ; PDF scan bị từ chối nếu máy không có OCR
docker run --rm --network container:<container PostgreSQL> -e OCR_WORKERS=4 `
  -e DATABASE_URL=postgresql://review:<mật_khẩu>@127.0.0.1:5432/review `
  -v "${PWD}/bo du lieu:/dataset:ro" <image của app> python -m app.cli import-pdfs /dataset
```

Văn bản đã có thì được bỏ qua trước khi OCR, nên chạy lại sau khi bị ngắt không phải OCR lại.

### 7.1. Kiểm thử dữ liệu đã nhập và tách lại (2026-11-17)

**Kiểm thử.**

- **Quét toàn bộ app** trên một DB bản sao (`review_sweep`), để không ghi gì vào DB thật: cả 65 văn bản; mỗi văn bản
  gọi API văn bản, trang HTML, tiến độ, báo cáo OCR, xuất JSON, tối đa 40 đơn vị (API và HTML), khung xem và ảnh trang.
  Lần 1: 6.041 request, **0 lỗi** (không có 4xx/5xx, không có traceback). Chậm nhất là trang L41 (1.043 đơn vị):
  2,0 giây.
- **Kiểm tra tính toàn vẹn dữ liệu** thì tìm ra hai lỗi:
  - L14: số hiệu và tên văn bản sai;
  - L15: **305 số Điều bị thiếu** (khoảng trống trong dãy số Điều của từng văn bản).

  Lần theo các số Điều bị thiếu thì thấy dòng tiêu đề vẫn nằm trong nội dung Điều liền trước, sau một dấu ngoặc lạ do OCR
  chèn vào.

**Sửa.** Xem dòng L14, L15 ở mục 2. Mỗi thay đổi đều có test viết trước. Bộ đo hồi quy (mục 6) vẫn đạt 100%: văn bản mẫu
649/649, PDF có lớp chữ 2.026/2.026 đơn vị, không thừa đơn vị nào.

**Tách lại.** Chữ từng trang của 65 PDF được đọc một lần, bằng Tesseract trong Docker như lần nhập
đầu, và lưu tạm ngoài repo. Bộ tách mới chạy trên chữ đó, rồi nhập vào DB bằng `Importer.import_bytes(…, replace=True)`
kèm đúng PDF gốc. Hai văn bản mẫu ND254, ND255 (`document.json` đã soát) giữ nguyên. Thao tác này thử trên bản sao
trước rồi mới làm trên DB `review`. Lúc đó DB chưa có thao tác thẩm định nào (`review_event` = 0), nên thay văn bản
không mất kết quả soát.

| Trên 63 văn bản tách từ PDF | Trước | Sau |
|---|---|---|
| Số Điều nhận được | 1.831 | 2.151 |
| Số Điều bị thiếu (khoảng trống trong dãy số) | 305 | 15 |
| Đơn vị | 13.809 | 16.297 |
| Văn bản không có số hiệu | 16 | 2: TT101, TT60 là bản để trống số (D6) |
| Văn bản có loại "Văn bản pháp lý" (không xác định) | 8 | 0 |
| Tên là quốc hiệu hoặc chữ rác OCR | 20 | 0. ND144, ND274 không đọc được tên nên ghi "Nghị định số …" |

- Không văn bản nào bị giảm số Điều.
- Nhiều nhất là L59 (thiếu 63 → 2), ND168 (40 → 1), L45 (32 → 1), ND253 (26 → 0), ND145 (24 → 0).
- Văn bản sửa đổi vẫn giữ trích dẫn trong Điều sửa đổi. Ví dụ ND310 có 4 Điều, Điều 1 chứa phần trích dẫn từ trang 1
  đến 15; ND296 có 22 Điều, mỗi Điều sửa một Điều của ND168.
- Số hiệu khớp cột "Số hiệu" của `Danh_muc_van_ban_phap_luat.xlsx` ở mọi văn bản, trừ:
  - TT101, TT60: để trống số;
  - dòng TT52, TT133 của danh mục: không có tệp riêng (D1; TT133 không tải được).
- **15 số Điều còn thiếu** là chỗ OCR làm mất hẳn dòng tiêu đề, hoặc đọc lệch tới mức không thể nhận an toàn, ví dụ
  "Điều 29 Xứ phạt…" không có dấu chấm. Các Điều liền sau những chỗ này đều đã bị đánh dấu "cần soát kỹ" ("thiếu Điều
  N"), nên người soát sẽ thấy.

**DB `review` sau khi tách lại:**

- 65 văn bản, 3.807 trang, 16.952 đơn vị. Trong đó 1.115 (6,6%) bị đánh dấu "cần soát kỹ", nhiều nhất là L59_tiep
  (104/858), L45 (103/1.031), ND252 (91/965);
- 65 PDF, SHA-256 đều khớp;
- DB 213 MB, sau khi chạy `VACUUM FULL` bảng `document_pdf` để dọn bản PDF cũ còn lại sau khi thay văn bản.

**Kiểm tra lại sau khi tách:**

- Quét toàn bộ app trên bản sao: 6.159 request, **0 lỗi**.
- Kiểm tra trong tiến trình (TestClient): 28/28 mục đạt. Gồm đăng nhập; danh sách 65 văn bản không có quốc hiệu làm tên;
  ND145 hiện `145/2020/NĐ-CP`; mở các Điều vừa tìm lại được (ND253 Điều 45, L59 Điều 80, ND168 Điều 100, TT69 Điều 9,
  L45 Điều 150, ND310 Điều 3) qua API và giao diện; ảnh trang dựng từ PDF trong PostgreSQL.
- **Kiểm tra bằng trình duyệt làm sau.** Lúc tách lại, app ở cổng 8080 bị dừng giữa chừng vì máy thiếu RAM (còn khoảng
  2 GB trống). Ngày 18/11/2026 đã kiểm tra lại trên Chrome: mở đủ 10 thư mục và 65 văn bản, không có lỗi console.

**Đề xuất.** Lưu chữ từng trang (lớp chữ hoặc OCR) vào PostgreSQL lúc nhập. Khi bộ tách được sửa, chỉ cần tách lại từ
chữ đã lưu, không phải OCR lại 3.000+ trang; lần này phải đọc lại toàn bộ PDF để lấy chữ.

### 7.2. Sắp văn bản theo thư mục chủ đề (2026-11-17)

Bộ dữ liệu chia văn bản vào 10 thư mục, từ "I. Quản lý thuế" đến "X. Lao động - Tiền lương". Tệp
`Danh_muc_van_ban_phap_luat.xlsx` ghi thứ tự văn bản trong từng thư mục. Hệ thống nay lưu cấu trúc đó trong PostgreSQL:

- bảng `category` chứa các thư mục;
- bảng `document_category` ghi văn bản thuộc thư mục nào, kèm thứ tự (migration `006_category.sql`);
- màn hình danh sách hiện văn bản theo cây thư mục (README, mục 4);
- `import-pdfs` tự xếp văn bản vào thư mục theo đường dẫn tệp (README, mục 6).

**Áp dụng lên DB `review`.** Chạy `python -m app.cli import-pdfs "bo du lieu"`, mất khoảng 3 giây. Không văn bản nào phải
tách hay OCR lại. Dữ liệu cũ không đổi: vẫn 65 văn bản, 0 thao tác thẩm định.

| Thư mục | Số văn bản | Ghi chú |
| --- | ---: | --- |
| I. Quản lý thuế | 8 | |
| II. Hóa đơn điện tử | 2 | |
| III. Thuế GTGT | 10 | |
| IV. Thuế thu nhập cá nhân | 3 | |
| V. Thuế thu nhập doanh nghiệp | 6 | có ND141 |
| VI. Hộ kinh doanh, cá nhân kinh doanh | 5 | có ND141 (cùng tệp với thư mục V) |
| VII. Đăng ký doanh nghiệp | 7 | L59_2020_QH14_tiep đứng ngay sau L59_2020_QH14 |
| VIII. Chế độ kế toán | 4 | TT52_2025_BTC trùng byte TT152 (D1): thư mục trỏ tới văn bản TT152 |
| IX. Bảo hiểm xã hội | 9 | |
| X. Lao động - Tiền lương | 13 | |

Tổng là 67 tệp thành 65 văn bản; không văn bản nào nằm ngoài thư mục. Thứ tự trong mỗi thư mục theo cột STT của tệp
danh mục. TT133_2016_BTC có trong tệp danh mục nhưng không có PDF ("không tải được pdf"), nên không xuất hiện.

**Kiểm thử.**

- **Tự động:** 23 test mới trong `tests/test_categories.py`, `tests/test_db_import.py` và `tests/test_routes.py`. Chúng
  kiểm tra:
  - tên thư mục và thứ tự La Mã;
  - đọc tệp danh mục `.xlsx`;
  - gom văn bản vào thư mục và cộng dồn tiến độ;
  - nhập lại hoặc đổi tên thư mục;
  - bản trùng byte trong cùng lần nhập và với văn bản đã có;
  - nhập riêng một thư mục;
  - `--replace` giữ thư mục, xóa văn bản thì gỡ khỏi thư mục;
  - tải lên kèm thư mục, kể cả mã thư mục sai;
  - giao diện thư mục, nút quay lại;
  - chế độ thư mục file.
- **Trình duyệt:** Playwright chạy Chrome thật trên một bản sao của DB `review`; bản sao đã xóa sau khi kiểm tra. Cả
  33/33 kiểm tra đều qua:
  - thứ tự và số văn bản của 10 thư mục;
  - thứ tự văn bản trong thư mục III;
  - thanh địa chỉ;
  - ô lọc ("141" ra ND141 ở hai thư mục);
  - liên kết "Cũng nằm trong";
  - nút quay lại từ trang thẩm định;
  - khách xem nhanh không bị đưa sang trang đăng nhập;
  - giao diện tối;
  - màn hình 768px và 375px không cuộn ngang trang;
  - di chuyển bằng phím Tab;
  - không có lỗi console.
