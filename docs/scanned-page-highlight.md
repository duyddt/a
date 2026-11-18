# Tô vàng đúng vùng nội dung trên bản scan và ảnh chụp

Khi bấm một Điều/Khoản/Điểm trên cây, khung PDF tô vàng các dòng chữ của đơn vị đó trên trang. Tài liệu này mô tả cách
app tìm các dòng ấy (`app/services/highlight.py`, `app/services/page_words.py`) và kết quả kiểm thử trên bộ dữ liệu.

## 1. Ba cách xác định vùng, theo thứ tự ưu tiên

| Cách | Khi nào dùng | Nguồn toạ độ | Hiển thị |
| --- | --- | --- | --- |
| `text` | PDF có lớp chữ (PDF số, PDF scan đã OCR sẵn) | toạ độ từng ký tự, đọc bằng pypdfium2 | khung vàng nét liền |
| `ocr` | Trang chỉ có ảnh (bản scan, ảnh chụp) | toạ độ từng từ do Tesseract trả về, lưu trong bảng `page_words` | khung vàng nét liền |
| `layout` | Trang chỉ có ảnh, chưa có toạ độ từ và máy không có Tesseract | tách khối đoạn văn trên ảnh, ghép theo thứ tự | khung nét đứt, ghi "(ước lượng)" |

Trước đây trang scan chỉ có cách `layout`. Cách này đếm khối đoạn văn nên hay lệch: chọn Điểm b lại tô Điểm a, đơn vị
nằm sau một bảng thì không tô gì, có khi tô lẫn sang Điều khác.

## 2. Toạ độ từ OCR (`page_words.py`)

- **Lúc nhập văn bản** (tải lên, `import-pdfs`), Tesseract đọc mỗi trang scan **một lần** và trả về cả chữ lẫn toạ độ
  từng từ (`run_and_get_multiple_output`, đầu ra `txt` + `tsv`). Chữ vẫn y như trước. Toạ độ được lưu vào bảng
  `page_words` trong cùng giao dịch nhập văn bản.
- **Khóa lưu là** `(pdf_sha256, page, engine)`:
  - toạ độ gắn với đúng tệp PDF, PDF đổi thì tự dùng bản mới;
  - `engine = "tesseract-vie-200dpi"` ghi cách OCR. Ảnh trang được dựng ở 200 DPI, giống hệt lúc nhập.
  - Xóa văn bản thì toạ độ của PDF không còn văn bản nào dùng cũng bị xóa.
- **Văn bản nhập trước đây** chưa có toạ độ thì có hai cách:
  - chạy `python -m app.cli page-words`: chỉ OCR các trang không có lớp chữ và chưa có toạ độ; dừng giữa chừng thì lần
    sau làm tiếp;
  - hoặc để app tự OCR trang đó lần đầu có người mở, nếu máy có Tesseract. Mỗi trang chỉ OCR một lần, kết quả được lưu
    lại.
- **Chế độ thư mục file** lưu toạ độ ở `words/pNNN.json`, cạnh bộ đệm ảnh trang.

### Ảnh chụp tay

Ảnh chụp bằng điện thoại thường có nền giấy ngả màu và chỗ sáng chỗ tối. Tesseract đọc vùng tối rất kém: trong thử
nghiệm, cả một dòng bị bỏ và Điểm a không tìm được. `prepare_for_ocr` xử lý việc này như sau:

1. **Ước lượng độ sáng nền:** thu nhỏ ảnh 16 lần, lọc cực đại để xóa nét chữ, rồi làm mờ.
2. **Kiểm tra có phải ảnh chụp không:**
   - nếu nền đều và trắng (phân vị 5% ≥ 225, chênh lệch ≤ 25) thì **giữ nguyên ảnh**, nên bản scan cho chữ y như lúc
     nhập. Đo trên 109 trang scan thật: phân vị 5% thấp nhất là 241, chênh lệch lớn nhất là 14;
   - ngược lại thì **chia mỗi điểm ảnh cho độ sáng nền tại đó**, để nền thành trắng đều.
3. **Không xoay, không cắt ảnh**, nên toạ độ từ vẫn khớp với ảnh trang đang hiển thị.

## 3. So khớp tuần tự (`highlight.py`)

Cả hai cách `text` và `ocr` dựng một "tầng chữ": chuỗi ký tự của trang, mỗi ký tự có một hộp toạ độ. Chuỗi được chuẩn
hoá (bỏ dấu, chữ thường, bỏ khoảng trắng và dấu câu). Nội dung đơn vị được chuẩn hoá giống vậy rồi tìm trong chuỗi:

- **Đi theo thứ tự đơn vị trên trang:**
  - đơn vị con bắt đầu từ chỗ của cha trở đi;
  - đơn vị kế tiếp (không phải con) bắt đầu sau đơn vị trước;
  - nhờ vậy, đoạn ngắn lặp lại (ví dụ "a) Thuế;" ở hai Khoản) không bị nhận nhầm.
- **Chỗ kết thúc** là lần xuất hiện cuối cùng của phần đuôi nội dung, tính trước đơn vị kế tiếp không thuộc nó. Vì vậy
  "Nơi nhận", chữ ký và Điều sau không bị tô lẫn.
- **Tìm mờ** khi chữ OCR sai vài ký tự hoặc nội dung đã được sửa: lấy đoạn trùng dài nhất, rồi chỉnh mép theo khối trùng
  đầu tiên hoặc cuối cùng.
- **Không bao giờ tô** dòng chỉ có số trang ở đầu hoặc cuối trang.
- **Đơn vị nối từ trang trước** (hoặc còn tiếp ở trang sau) bỏ tối đa 3 dòng ở mép trang không có trong nội dung, ví
  dụ "26 p" hay "$‹ * z L3": đó là nhiễu OCR ở mép ảnh.
- **OCR mất nhãn đầu dòng** ("c) Tr" mất, chỉ còn "ường hợp…"): chỗ bắt đầu không lấn sang mấy ký tự cuối dòng trên.
- **Đơn vị không có dòng nào trên trang:** ví dụ phần "nối trang" thật ra chỉ là số trang bị tách nhầm vào nội dung. Khi
  đó app **không tô gì**, thay vì đoán theo bố cục.

## 4. Kết quả kiểm thử trên bộ dữ liệu

Thực hiện ngày 17/11/2026 trong Docker có Tesseract 5.5 `vie`, chỉ đọc DB và OCR trong bộ nhớ. Mỗi văn bản lấy ngẫu
nhiên 2 trang scan, tổng cộng 109 trang của 57 văn bản.

| Chỉ số | Kết quả |
| --- | --- |
| Cặp (đơn vị, trang) | 725 |
| Tìm được vùng bằng toạ độ OCR | 724. Cặp còn lại là phần "nối trang" chỉ gồm số trang tách nhầm, đúng ra không có gì để tô |
| Đơn vị bắt đầu trên trang mà phần đầu (tên Điều hoặc đầu nội dung) khớp chính xác | 600/600 |
| Vùng mới trùng với ước lượng bố cục cũ (IoU ≥ 0,9) | 223/724; IoU < 0,5: 345/724 |
| Thời gian OCR | khoảng 2,8 giây/trang, một nhân CPU |

**Về độ trùng thấp với cách cũ:** IoU thấp là do cách cũ sai, không phải cách mới sai. Xem ảnh đối chiếu các ca trùng ít
nhất thì cách cũ có ba kiểu lỗi:
- tô nhầm sang Điểm ngay trên (chọn Điểm b của Điều 22 Luật 108/2025/QH15 thì tô Điểm a);
- không tô gì khi đơn vị nằm sau một bảng;
- tô lẫn phần căn cứ hoặc Điều khác.

Cách mới tô đúng các dòng của đơn vị trong mọi ca đã xem.

**Ảnh chụp giả lập:** lấy ảnh trang scan, làm nghiêng 2,5°, méo phối cảnh, nền giấy vàng có vệt sáng tối, làm mờ rồi nén
JPEG chất lượng 55.

| Trang | Không cân bằng sáng | Có cân bằng sáng (`prepare_for_ocr`) |
| --- | --- | --- |
| Luật 108/2025/QH15 trang 26 | 12/14 đơn vị; các Điểm b, c bị lệch | 14/14, đúng dòng |
| Nghị định 176/2025/NĐ-CP trang 1 | 6/6 | 6/6 |
| Thông tư 87/2026/TT-BTC trang 2 | 10/11 | 11/11 |

## 5. Giới hạn

- **Nghiêng nhiều:** hộp tô là hình chữ nhật thẳng theo từng dòng, nên ảnh chụp nghiêng nhiều (trên vài độ) sẽ có khung
  hơi rộng hơn dòng chữ. App không tự xoay ảnh: xoay sẽ làm toạ độ lệch với ảnh trang đang hiển thị.
- **Máy không có Tesseract và trang chưa có toạ độ:** vẫn dùng ước lượng bố cục (ghi rõ "ước lượng"). Chạy
  `python -m app.cli page-words` trong Docker image của dự án để nạp sẵn toạ độ cho mọi trang.
- **Nội dung đã sửa khác hẳn chữ trên trang:** tìm mờ cần khoảng một nửa đoạn neo trùng. Không đạt thì app quay về cách
  tìm riêng từng đơn vị, rồi tới ước lượng bố cục.
