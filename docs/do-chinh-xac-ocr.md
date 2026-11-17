# Cơ chế đo độ chính xác OCR & tách cấu trúc

Tài liệu kỹ thuật cho nhóm phát triển và nhóm thẩm định. Nguồn gốc: tài liệu *"Cơ chế tính độ chính xác OCR — Review App"*
của nhóm, đã đối chiếu từng công thức với mã nguồn hiện hành (`app/services/review.py`, hàm `build_ocr_report`).
Mục 7 mô tả cách đo trên cả bộ dữ liệu bằng script; kết quả lần đo gần nhất nằm ở
[`bao-cao-kiem-thu-bo-du-lieu.md`](bao-cao-kiem-thu-bo-du-lieu.md).

## 1. Nguyên lý: Ground Truth do con người xác nhận

Hệ thống OCR/parser không tự biết mình đọc đúng bao nhiêu. Muốn đo phải có bản chuẩn (Ground Truth, GT). App dùng mô hình
*human-in-the-loop* gồm ba bước:

1. **Lưu bản gốc, không ghi đè.** Khi nhập văn bản, nội dung OCR/parser của từng Điều/Khoản/Điểm nằm trong bảng
   `legal_unit` (`migrations/001_init.sql`). Mỗi thao tác của người soát được *ghi thêm* vào `review_event`
   (`migrations/002_review.sql`, có trigger chặn UPDATE/DELETE). Lần sửa chữ lưu `content_before` / `content_after`.
2. **Bấm "✓ Duyệt" thì bản hiện hành thành GT.** Chỉ đơn vị có trạng thái `approved` được coi là GT. Nếu đơn vị đã
   được sửa, bản sửa cuối là GT. Nếu duyệt thẳng, bản gốc là GT. Sửa sau khi đã duyệt sẽ đưa đơn vị về "Chưa soát"
   (`fold_events`).
3. **Đối sánh tự động.** `build_ocr_report(doc, reviews)` lấy:
   - `orig_text`: bản OCR gốc, tức `content_before` của lần sửa *đầu tiên* (`UnitReview.original_content`);
   - `current_text`: nội dung hiện hành, tức `unit.content` sau `apply_edits` trong `db_catalog.py`.

   Hàm tính khoảng cách chỉnh sửa giữa hai bản rồi ra năm chỉ số dưới đây. Báo cáo cập nhật ngay sau mỗi thao tác
   (HTMX OOB swap).

> **Vì sao phải bấm Duyệt thì số mới đổi?** Đơn vị "Chưa soát" hoặc đang sửa dở chưa phải GT. Vì vậy CAR, WAR và
> UEM không tính các đơn vị này. Riêng SFI tính trên mọi đơn vị đã được chạm tới (đã duyệt, gắn cờ hoặc báo lỗi cấu
> trúc). Con số báo cáo vì thế chỉ dựa trên dữ liệu người đã xác nhận.

## 2. Bộ năm chỉ số

| Ký hiệu | Tên | Nguồn gốc | Đo gì | Trọng số |
|---|---|---|---|---|
| CAR / CER | Character Accuracy / Error Rate | Levenshtein [1], UNLV-ISRI [2], ICDAR 2017 [3] | % ký tự đúng trên tổng ký tự GT | 0,4 |
| WAR / WER | Word Accuracy / Error Rate | UNLV-ISRI [2], ICDAR 2017 [3] | % tiếng đúng; rất nhạy với lỗi dấu tiếng Việt | 0,3 |
| SFI | Structural Fidelity Index | Nhóm tự thiết kế cho văn bản pháp lý | % đơn vị đã soát không bị báo lỗi cấu trúc | 0,2 |
| UEM | Unit Exact Match | Exact Match [4] áp cho `LegalUnit` | % đơn vị duyệt thẳng, không sửa ký tự nào | 0,1 |
| Score | Composite Legal-OCR Score | Tổng có trọng số | Chất lượng toàn pipeline: 70% chữ, 30% cấu trúc | — |

## 3. Công thức (đúng như mã nguồn)

Mọi chuỗi được chuẩn hóa xuống dòng (`normalize_text`: `\r\n` và `\r` thành `\n`). Các tổng chỉ cộng trên đơn vị
`approved`. Mọi kết quả làm tròn 2 chữ số. Số lượng đơn vị hiển thị ở UI và API nằm trong `build_ocr_report`.

### 3.1. Khoảng cách chỉnh sửa — `_edit_distance_seq(a, b)`

Hàm trả về **khoảng cách Levenshtein** [1]: số thao tác tối thiểu gồm thay thế (S), xóa (D) và thêm (I) để biến bản
OCR thành bản chuẩn.

- S: OCR đọc sai một ký tự/tiếng, ví dụ `đ`→`d`;
- D: ký tự rác phải xóa, ví dụ vết bẩn hoặc con dấu;
- I: chữ OCR bỏ sót phải gõ thêm.

Tổng lỗi = S + D + I. Hàm nhận chuỗi ký tự (cho CER) hoặc danh sách tiếng `text.split()` (cho WER).

Cài đặt dùng thuật toán song song bit của Myers [5], theo cách trình bày của Hyyrö [6]. Thuật toán cho kết quả
đúng bằng quy hoạch động cổ điển nhưng xử lý cả một cột bảng quy hoạch động bằng vài phép toán bit trên số nguyên Python.
Một đơn vị 2.000 ký tự chỉ mất vài mili giây, và không cần thư viện ngoài.

> **Lịch sử.** Trước ngày 2026-11-17 hàm dùng `difflib.SequenceMatcher` (mặc định `autojunk=True`). Cách đó không cho
> số lỗi tối thiểu, và với chuỗi từ 200 phần tử trở lên còn coi ký tự phổ biến là "rác". Trên đơn vị 1.800–2.300 ký tự
> của ND254, chèn 25 lỗi mất dấu thì hàm cũ đếm thành 25–34 lỗi (thừa tới 36%). Hàm mới đếm đúng 25. Kiểm thử
> `tests/test_pdf_parser.py` so hàm mới với quy hoạch động trên chuỗi ngẫu nhiên và trên chính trường hợp này.
> `SequenceMatcher` vẫn được dùng để *hiển thị* diff (mục 5), vì ở đó chỉ cần chỗ khác nhau dễ đọc, không cần số tối thiểu.

### 3.2. CER và CAR

```
CER (%) = round(100 × total_char_edits / total_gt_chars, 2)
CAR (%) = round(max(0, 100 − CER), 2)
```

- `total_gt_chars` = tổng `max(len(current_text), 1)` của các đơn vị đã duyệt.
- `total_char_edits` = tổng `_edit_distance_seq(orig_text, current_text)` của các đơn vị đã duyệt *có sửa*.

Ví dụ: một Khoản 200 ký tự, OCR sai 2 ký tự (`đ`→`d`, `O`→`0`). Khi đó CER = 1,00% và CAR = 99,00%.
CER có thể vượt 100% khi bản OCR dài hơn bản chuẩn nhiều (phải xóa nhiều). Khi đó CAR bị chặn ở 0.

### 3.3. WER và WAR

Tiếng Việt tách tiếng bằng khoảng trắng. Một lỗi dấu (`thuế` → `thuê`) chỉ sai 1/4 ký tự nhưng sai trọn một tiếng.

```
WER (%) = round(100 × total_word_edits / total_gt_words, 2)
WAR (%) = round(max(0, 100 − WER), 2)
```

Ví dụ: Khoản 50 tiếng, sai dấu 1 tiếng. Khi đó WER = 2,00% và WAR = 98,00%.

### 3.4. UEM

```
UEM (%) = round(100 × exact_match_units / approved_units, 2)
```

Một đơn vị được tính là nguyên vẹn khi `edit_count == 0` hoặc khi `orig_text == current_text` (đã sửa nhưng trả về
đúng bản gốc). Ví dụ: duyệt 20 đơn vị, 18 đơn vị không cần sửa. Khi đó UEM = 90,00%.

### 3.5. SFI

```
SFI (%) = round(100 × (1 − structure_error_units / reviewed_units), 2)
```

- `reviewed_units` = số đơn vị có trạng thái khác `unreviewed` (đã duyệt, gắn cờ hoặc lỗi cấu trúc).
- `structure_error_units` = số đơn vị bị bấm "⚠ Báo lỗi cấu trúc", ví dụ cắt nhầm Điểm thành Khoản hoặc gộp hai Khoản.

Ví dụ: soát 50 đơn vị, có 1 đơn vị lỗi cấu trúc. Khi đó SFI = 98,00%.

### 3.6. Điểm tổng hợp

```
Score (%) = round(0.4 × CAR + 0.3 × WAR + 0.2 × SFI + 0.1 × UEM, 2)
```

Điểm chỉ hiện khi cả bốn thành phần đều có giá trị, tức là đã có ít nhất một đơn vị được duyệt.

## 4. Ví dụ tính tay (ND254/2026/NĐ-CP, 4 đơn vị)

| Đơn vị | Ký tự GT | Tiếng GT | Thao tác | Lỗi |
|---|---:|---:|---|---|
| `ND254_2026_D1_K1` | 200 | 45 | Duyệt thẳng | 0 |
| `ND254_2026_D1_K1_a` | 300 | 65 | Sửa `d)`→`đ)` và 1 dấu thanh, rồi duyệt | 2 ký tự, 2 tiếng |
| `ND254_2026_D1_K1_b` | 500 | 110 | Duyệt thẳng | 0 |
| `ND254_2026_D1_K2` | 150 | 30 | Báo lỗi cấu trúc | không vào GT chữ |

Các bước tính:

- reviewed = 4, approved = 3, exact = 2, structure_error = 1.
- GT chữ là 1.000 ký tự và 220 tiếng.
- CER = 2/1000 = 0,20%, nên CAR = 99,80%.
- WER = 2/220 = 0,91%, nên WAR = 99,09%.
- UEM = 2/3 = 66,67%.
- SFI = 1 − 1/4 = 75,00%.
- Score = 0,4·99,80 + 0,3·99,09 + 0,2·75,00 + 0,1·66,67 = 39,92 + 29,727 + 15,00 + 6,667 = **91,31%**.

## 5. Phân tích lỗi tự động

Mỗi đơn vị có sửa chữ đi qua ba hàm sau:

- `build_inline_diff_html(before, after)`: tô `<del class="diff-del">` (đỏ, gạch ngang) tại chỗ OCR đọc sai và
  `<ins class="diff-ins">` (xanh) tại chỗ người soát sửa. Nội dung được escape HTML trước khi chèn thẻ.
- `extract_char_diffs(before, after)`: lấy từng cặp `(ocr_read → corrected_to)`. `Counter` gom lại thành **top 20 cặp
  hay nhầm** (`common_substitutions`), cùng thống kê nguyên nhân người soát chọn (`reasons_summary`).
- `_suggest_solution(ocr_read, corrected_to, reason)`: gợi ý kỹ thuật theo mẫu lỗi:
  - mờ/nhòe: scan 300 DPI, khử nhiễu Gaussian, nhị phân hóa thích nghi;
  - nhầm `d`/`đ`: luật thứ tự Điểm (a, b, c, d, đ, e, g…) cùng từ điển hậu xử lý;
  - nhầm số/chữ (`0/O`, `1/l`, `5/S`): ràng buộc ngữ cảnh bằng regex cho số hiệu, ngày tháng, số Khoản/Điểm;
  - mất chữ sát lề hoặc dư ký tự do dấu đỏ: nới padding khi tách dòng, lọc kênh màu đỏ (HSV masking).

## 6. Vị trí trong mã nguồn

| Thành phần | Tệp | Hàm / phần tử |
|---|---|---|
| Lịch sử thẩm định, GT | `app/services/review.py` | `fold_events`, `decide`, `apply_edits` |
| 5 chỉ số và diff | `app/services/review.py` | `_edit_distance_seq`, `extract_char_diffs`, `build_inline_diff_html`, `_suggest_solution`, `build_ocr_report` |
| Áp bản sửa vào nội dung | `app/services/db_catalog.py` | `doc.legal_units = apply_edits(...)` |
| Thẻ điểm và bảng lỗi | `app/templates/partials/ocr_report.html` | `#ocr-report-box`, `.metrics-grid` |
| Thanh tiến độ | `app/templates/partials/progress.html` | Hiện CAR và UEM ở đầu trang |
| API | `app/api.py` | `GET /api/documents/{document_id}/ocr-report` |
| Tạo bản OCR gốc khi chỉ tải lên PDF | `app/services/pdf_parser.py` | `auto_build_json_from_pdf`, `extract_pages_text` (lớp chữ, OCR khi cần), `build_document_from_pages` |
| Tách Điều → Khoản → Điểm và siêu dữ liệu | `app/services/legal_parser.py` | `parse_legal_document`; đơn vị có điểm đáng ngờ được đặt `is_confident = false` |
| Đo trên cả bộ dữ liệu | `scripts/evaluate_dataset_accuracy.py` | Xem mục 7 |

## 7. Đo trên cả bộ dữ liệu (ngoài app)

Trong app, chỉ số chỉ có khi đã có người duyệt. Để đo nhanh chất lượng **luồng nhập PDF** (`auto_build_json_from_pdf`,
dùng khi người dùng chỉ tải lên PDF) trên bộ dữ liệu lớn, dùng script:

```powershell
$env:PYTHONUTF8 = "1"
# (A) văn bản mẫu + (B) PDF có lớp chữ trong bộ dữ liệu
.venv\Scripts\python.exe scripts\evaluate_dataset_accuracy.py "bo du lieu" --seeds data --out eval.json

# (C) OCR thật trên bản scan của văn bản mẫu. Cần Tesseract kèm gói 'vie'; máy không có thì chạy trong Docker image
docker build -t lawproject-review-app:ocr-check .
docker run --rm -e PYTHONUTF8=1 -v "${PWD}/scripts:/app/scripts:ro" -v "${PWD}/data:/seeds:ro" `
    lawproject-review-app:ocr-check python scripts/evaluate_dataset_accuracy.py --seeds /seeds --ocr
```

Script có ba phép đo:

| Phép đo | Đầu vào của hệ thống | Ground Truth | Đo phần nào | Độc lập với bộ tách? |
|---|---|---|---|---|
| (A) Văn bản mẫu | Bản chép chữ từng trang `data/<ID>/ocr_pages/` | `data/<ID>/document.json` (nhóm dựng và soát bằng công cụ khác) | Tách cấu trúc | Có |
| (B) PDF có lớp chữ (≥ 80% số trang có chữ) | Lớp chữ của PDF | Bộ tách tham chiếu viết riêng trong script | Tách cấu trúc, chủ yếu để phát hiện hồi quy | Không hẳn: cùng bộ quy tắc |
| (C) OCR bản scan mẫu (`--ocr`) | `data/<ID>/source.pdf` qua OCR như khi người dùng tải lên | `data/<ID>/document.json` | **Toàn luồng: OCR + tách cấu trúc** | Có |

(C) chỉ có ý nghĩa với văn bản mẫu mà `source.pdf` là ảnh scan. Hiện chỉ ND254_2026 (55 trang) đúng như vậy;
`source.pdf` của ND255_2026 có sẵn lớp chữ nên (C) với ND255 thực chất là đọc lớp chữ.

Các bước chung:

1. **Ghép đơn vị** theo khóa (Điều, Khoản, Điểm). Một Điều chỉ được ghép khi tiêu đề giống ít nhất 60%, để văn bản bị
   nhận nhầm không được tính là "khớp".
2. **Giả lập thẩm định rồi gọi đúng `build_ocr_report` của app:**
   - đơn vị khớp được coi là `approved`, với GT = bản tham chiếu và `original_content` = đầu ra hệ thống;
   - đơn vị hệ thống tách thừa được coi là `structure_error`;
   - đơn vị hệ thống bỏ sót không hiện trong app nên không vào SFI. Phần này báo riêng bằng **recall**
     (khớp / tham chiếu) và **precision** (khớp / hệ thống).
3. **Gộp khoảng trắng** ở cả hai phía trước khi so, vì xuống dòng do dàn trang PDF không phải lỗi nhận dạng.

**Giới hạn của cách đo này:**

- GT của (A) và (C) do nhóm dựng nhưng **chưa** được duyệt trên app.
- (B) dùng cùng bộ quy tắc với bộ tách của app, nên 100% ở (B) nghĩa là "hai bản cài đặt nhất quán", không phải "đúng
  tuyệt đối". Mỗi khi hai bên lệch nhau phải đọc PDF để phân xử. Ngày 2026-11-17 đã phân xử TT58 (hàng của biểu mẫu,
  ngoặc kép lẫn kiểu “…") và L03 (số trong ô bảng): mọi chỗ lệch đều do bộ tham chiếu sai, và bộ tham chiếu đã được sửa.
- Nội dung Điều chứa cả nội dung các Khoản của nó, nên cùng một lỗi được tính ở cả Điều lẫn Khoản. CER gộp vì thế bị
  các Điều dài chi phối.
- Mô hình dữ liệu chỉ có ba cấp Điều → Khoản → Điểm. Tiểu mục kiểu "1.1." hay các nhóm a), b), c) lặp lại cho từng biểu
  mẫu được gộp vào Điểm đứng trước (cả bộ tách lẫn tham chiếu đều làm vậy).

## 8. Hạn chế đã biết của cơ chế và đề xuất

| Vấn đề | Ảnh hưởng | Đề xuất |
|---|---|---|
| ~~`_edit_distance_seq` dùng `SequenceMatcher` với `autojunk=True`~~ | **Đã sửa ngày 2026-11-17** (mục 3.1): nay là Levenshtein chính xác. Số liệu trong app từ trước ngày này có thể thấp hơn thực tế nếu có lỗi rải rác trên đơn vị dài | — |
| UEM thấp với văn bản OCR dù CAR cao | Một lỗi ký tự bất kỳ cũng làm đơn vị mất "nguyên vẹn". Với ND254 qua Tesseract: CAR 96,7% nhưng UEM chỉ 27,7% | Đọc UEM như "tỷ lệ đơn vị người soát không phải sửa", không phải độ chính xác chữ |
| Nội dung Điều lặp lại nội dung Khoản | Một lỗi bị đếm hai lần nếu người soát sửa ở cả hai nơi. Nếu chỉ sửa ở Khoản thì GT của Điều còn sai | Tính CER/WER trên đơn vị lá (Khoản/Điểm, hoặc Điều không có Khoản), hoặc đồng bộ bản sửa từ Khoản lên Điều |
| Đơn vị bị parser bỏ sót không hiện trong app | SFI chỉ thấy lỗi tách thừa, không thấy lỗi tách thiếu | Cho người soát báo "thiếu đơn vị" ở cấp Điều, và tính recall cấu trúc như mục 7 |

## 9. Tài liệu tham khảo (IEEE)

1. V. I. Levenshtein, "Binary codes capable of correcting deletions, insertions, and reversals," *Soviet Physics
   Doklady*, vol. 10, no. 8, pp. 707–710, 1966.
2. S. V. Rice, F. R. Jenkins, and T. A. Nartker, "The Fourth Annual Test of OCR Accuracy," Information Science Research
   Institute, Univ. of Nevada, Las Vegas, Tech. Rep. 95-03, 1995.
3. G. Chiron, A. Doucet, M. Coustaty, M. Visani, and J.-P. Moreux, "ICDAR2017 Competition on Post-OCR Text Correction,"
   in *Proc. 14th IAPR Int. Conf. Document Analysis and Recognition (ICDAR)*, Kyoto, Japan, 2017, pp. 1423–1428.
4. P. Rajpurkar, J. Zhang, K. Lopyrev, and P. Liang, "SQuAD: 100,000+ Questions for Machine Comprehension of Text," in
   *Proc. Conf. Empirical Methods in Natural Language Processing (EMNLP)*, 2016, pp. 2383–2392.
5. G. Myers, "A fast bit-vector algorithm for approximate string matching based on dynamic programming," *Journal of
   the ACM*, vol. 46, no. 3, pp. 395–415, 1999.
6. H. Hyyrö, "Explaining and extending the bit-parallel approximate string matching algorithm of Myers," Dept. of
   Computer and Information Sciences, Univ. of Tampere, Tech. Rep. A-2001-10, 2001.
