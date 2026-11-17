"""Tô sáng trên bản scan / ảnh chụp nhờ toạ độ từng từ do OCR trả về (app/services/page_words.py + highlight.py)."""
import json

import pytest
from PIL import Image

from app.contracts import LegalUnit
from app.services import highlight, page_words
from app.services.highlight import locate
from app.services.page_words import Word
from app.services.store import DocumentStore

LINE_H = 0.02


def page_of(lines, top=0.05, pitch=0.04, left=0.15):
    """Các dòng chữ của một trang giả: mỗi từ rộng theo số ký tự, các dòng cách đều nhau."""
    words = []
    for index, line in enumerate(lines):
        y, x = top + index * pitch, left
        for text in line.split():
            width = 0.012 * len(text)
            words.append(Word(text, (x, y, x + width, y + LINE_H), index))
            x += width + 0.01
    return tuple(words)


def line_top(index, top=0.05, pitch=0.04):
    return top + index * pitch


def unit(unit_id, content, parent=None, clause=None, point=None, pages=(1,), article=5, title="Phạm vi"):
    return LegalUnit(unit_id=unit_id, parent_unit_id=parent, article=article, clause=clause, point=point, title=title,
                     content=content, source_pages=list(pages))


PAGE_1 = [
    "12",  # số trang
    "Điều 5. Phạm vi",
    "1. Khoản một gồm các loại sau:",
    "a) Thuế;",
    "b) Phí.",
    "2. Khoản hai gồm các loại sau:",
    "a) Thuế;",
    "b) Lệ phí.",
    "Nơi nhận:",
    "- Như Điều 5;",
]
K1 = "1. Khoản một gồm các loại sau:\na) Thuế;\nb) Phí."
K2 = "2. Khoản hai gồm các loại sau:\na) Thuế;\nb) Lệ phí."
UNITS = [
    unit("X_D5", f"{K1}\n{K2}"),
    unit("X_D5_K1", K1, "X_D5", 1),
    unit("X_D5_K1_a", "a) Thuế;", "X_D5_K1", 1, "a"),
    unit("X_D5_K1_b", "b) Phí.", "X_D5_K1", 1, "b"),
    unit("X_D5_K2", K2, "X_D5", 2),
    unit("X_D5_K2_a", "a) Thuế;", "X_D5_K2", 2, "a"),
    unit("X_D5_K2_b", "b) Lệ phí.", "X_D5_K2", 2, "b"),
]
BY_ID = {u.unit_id: u for u in UNITS}


def rects_for(words, unit_id, units=UNITS, page=1):
    by_id = {u.unit_id: u for u in units}
    return highlight._locate_in_layer(highlight._words_layer(words), units, by_id[unit_id], page)


def span_lines(rects):
    """(dòng đầu, dòng cuối) mà vùng tô phủ, theo chỉ số dòng của page_of."""
    top, bottom = min(r[1] for r in rects), max(r[3] for r in rects)
    first = round((top + 0.004 - 0.05) / 0.04)
    last = round((bottom - 0.004 - LINE_H - 0.05) / 0.04)
    return first, last


# ---------------------------------------------------------------- so khớp tuần tự trên chữ OCR


def test_repeated_short_point_is_found_inside_its_own_clause():
    words = page_of(PAGE_1)
    assert span_lines(rects_for(words, "X_D5_K1_a")) == (3, 3)
    assert span_lines(rects_for(words, "X_D5_K2_a")) == (6, 6)  # "a) Thuế;" lần thứ hai, không phải lần đầu
    assert span_lines(rects_for(words, "X_D5_K2_b")) == (7, 7)


def test_article_covers_heading_to_last_point_without_page_number_or_recipients():
    words = page_of(PAGE_1)
    assert span_lines(rects_for(words, "X_D5")) == (1, 7)
    assert span_lines(rects_for(words, "X_D5_K1")) == (2, 4)
    assert span_lines(rects_for(words, "X_D5_K2")) == (5, 7)


def test_unit_continuing_from_previous_page_skips_page_number_and_stops_before_next_article():
    units = [
        unit("X_D5", "Phần đầu ở trang trước\ntiếp theo của điều năm.", pages=(1, 2)),
        unit("X_D6", "Nghị định có hiệu lực từ ngày ký.", pages=(2,), article=6, title="Hiệu lực"),
    ]
    words = page_of(["13", "tiếp theo của điều năm.", "Điều 6. Hiệu lực", "Nghị định có hiệu lực từ ngày ký.", "14"])
    assert span_lines(rects_for(words, "X_D5", units, page=2)) == (1, 1)
    assert span_lines(rects_for(words, "X_D6", units, page=2)) == (2, 3)


def test_ocr_mistakes_and_reviewer_edits_still_find_the_unit():
    words = page_of(["Điều 5. Phạm vi", "1. Khoản rnột gồm các loại sau:", "a) Thuế;", "b) Phi.",
                     "2. Khoản hai gồm cấc loại sau:", "a) Thuế;", "b) Lệ phí."])
    edited = [*UNITS[:4], unit("X_D5_K2", "2. Khoản hai (đã sửa) gồm các loại sau:\na) Thuế;\nb) Lệ phí.", "X_D5", 2),
              *UNITS[5:]]
    assert span_lines(rects_for(words, "X_D5_K1", edited)) == (1, 3)
    assert span_lines(rects_for(words, "X_D5_K2", edited)) == (4, 6)


# ---------------------------------------------------------------- đọc kết quả OCR, lưu / nạp


TSV_ROWS = [
    ("level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width", "height", "conf", "text"),
    (1, 1, 0, 0, 0, 0, 0, 0, 1000, 2000, -1, ""),
    (5, 1, 1, 1, 1, 1, 100, 200, 80, 30, 95.1, "Điều"),
    (5, 1, 1, 1, 1, 2, 190, 200, 30, 30, 96, "5."),
    (5, 1, 1, 1, 2, 1, 100, 260, 50, 30, 91, "Thuế;"),
    (5, 1, 1, 1, 2, 2, 160, 260, 5, 30, 12, " "),
    (5, 1, 2, 1, 1, 1, 100, 320, 50, 30, 90, "Phí."),
]
TSV = "\n".join("\t".join(map(str, row)) for row in TSV_ROWS)


def test_tesseract_tsv_becomes_normalized_words_with_line_numbers():
    words = page_words.words_from_tsv(TSV, 1000, 2000)
    assert [w.text for w in words] == ["Điều", "5.", "Thuế;", "Phí."]
    assert words[0].box == (0.1, 0.1, 0.18, 0.115)
    assert [w.line for w in words] == [0, 0, 1, 2]  # dòng đổi khi (khối, đoạn, dòng) đổi


def test_words_round_trip_through_json():
    words = page_words.words_from_tsv(TSV, 1000, 2000)
    assert page_words.from_json(json.loads(json.dumps(page_words.to_json(words)))) == words


def test_words_layer_gives_each_character_part_of_its_word_box():
    layer = highlight._words_layer((Word("ab", (0.1, 0.2, 0.3, 0.25), 0), Word("c", (0.4, 0.2, 0.5, 0.25), 1)))
    assert layer.raw == "ab\nc" and layer.normalized == "abc"
    assert layer.boxes[0] == pytest.approx((0.1, 0.2, 0.2, 0.25)) and layer.boxes[1] == pytest.approx((0.2, 0.2, 0.3, 0.25))
    assert layer.boxes[2] is None and layer.boxes[3] == (0.4, 0.2, 0.5, 0.25)


# ---------------------------------------------------------------- locate() trên PDF chỉ có ảnh


@pytest.fixture
def scanned(tmp_path):
    """Văn bản một trang, PDF chỉ có ảnh (không có lớp chữ), như bản scan hoặc ảnh chụp."""
    folder = tmp_path / "data" / "X"
    folder.mkdir(parents=True)
    Image.new("RGB", (850, 1100), "white").save(folder / "source.pdf")
    document = {"document_id": "X", "document_no": "1/2026/NĐ-CP", "document_type": "Nghị định", "title": "Văn bản scan",
                "issuer": "Chính phủ", "status": "active", "legal_units": [u.model_dump() for u in UNITS]}
    (folder / "document.json").write_text(json.dumps(document, ensure_ascii=False, default=str), encoding="utf-8")
    return DocumentStore(tmp_path / "data").get("X")


def test_scanned_page_uses_stored_ocr_words(scanned, monkeypatch):
    monkeypatch.setattr(page_words, "ocr_ready", lambda: False)  # không OCR được: chưa có toạ độ thì ước lượng bố cục
    assert locate(scanned, "X_D5_K2_a", 1).method != "ocr"
    page_words.store(scanned, 1, page_of(PAGE_1))
    found = locate(scanned, "X_D5_K2_a", 1)
    assert found.method == "ocr" and span_lines(found.rects) == (6, 6)


def test_scanned_page_is_ocred_once_when_tesseract_is_available(scanned, monkeypatch):
    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        return "", page_of(PAGE_1)

    monkeypatch.setattr(page_words, "ocr_ready", lambda: True)
    monkeypatch.setattr(page_words, "ocr_image", fake_ocr)
    assert locate(scanned, "X_D5_K1", 1).method == "ocr" and locate(scanned, "X_D5_K2", 1).method == "ocr"
    assert len(calls) == 1 and calls[0][0] > 1000  # ảnh 200 DPI như lúc nhập, OCR một lần rồi dùng lại
    assert page_words.cached(scanned, 1) == page_of(PAGE_1)


# ---------------------------------------------------------------- ảnh chụp tay: cân bằng độ sáng nền trước khi OCR


def text_like(image, color):
    from PIL import ImageDraw

    draw = ImageDraw.Draw(image)
    for row in range(8):
        for col in range(10):
            draw.rectangle([60 + col * 90, 80 + row * 120, 120 + col * 90, 100 + row * 120], fill=color)
    return image


def test_clean_scans_go_to_ocr_unchanged():
    scan = text_like(Image.new("L", (1000, 1100), 250), 20)
    assert page_words.prepare_for_ocr(scan) is scan  # bản scan nền trắng đều: OCR y như lúc nhập văn bản


def test_photos_get_an_even_white_background_without_moving_anything():
    from PIL import Image as PILImage

    shade = PILImage.linear_gradient("L").resize((1000, 1100)).point(lambda v: 90 + v * 150 // 255)  # tối dần lên trên
    photo = text_like(shade.convert("RGB"), (15, 15, 15))
    flat = page_words.prepare_for_ocr(photo)
    assert flat.size == photo.size
    gray = flat.convert("L")
    assert min(gray.getpixel((40, y)) for y in range(20, 1080, 50)) >= 225  # nền: trắng đều từ trên xuống dưới
    assert max(gray.getpixel((90, 90 + row * 120)) for row in range(8)) <= 80  # chữ vẫn đậm, đúng chỗ cũ


# ---------------------------------------------------------------- chữ OCR của ảnh chụp: nhiễu ở mép, mất chữ đầu dòng


def test_noisy_page_number_and_edge_noise_are_not_highlighted():
    units = [unit("X_D5", "Phần đầu ở trang trước\ntiếp theo của điều năm.", pages=(1, 2)),
             unit("X_D6", "Nghị định có hiệu lực từ ngày ký.", pages=(2,), article=6, title="Hiệu lực")]
    words = page_of(["26 p", "$‹ * z L3 “ ˆ^ ` Al", "tiếp theo của điều năm.", "Điều 6. Hiệu lực",
                     "Nghị định có hiệu lực từ ngày ký.", "| ,"])
    assert span_lines(rects_for(words, "X_D5", units, page=2)) == (2, 2)


def test_lost_label_at_line_start_still_starts_the_point_on_its_own_line():
    words = page_of(["Điều 5. Phạm vi", "1. Khoản một gồm các loại sau:", "a) Thuế;", "b) Phí.",
                     "2. Khoản hai gồm các loại sau:", "a) Thuế;", "b) Lệ phí giao thông đường bộ tại trụ sở người nộp thuế;",
                     "ường hợp kiểm tra sau thông quan thực hiện theo quy định."])  # OCR mất "c) Tr"
    k2 = "2. Khoản hai gồm các loại sau:\na) Thuế;\nb) Lệ phí giao thông đường bộ tại trụ sở người nộp thuế;\n" \
         "c) Trường hợp kiểm tra sau thông quan thực hiện theo quy định."
    units = [unit("X_D5", f"{K1}\n{k2}"), *UNITS[1:4], unit("X_D5_K2", k2, "X_D5", 2), UNITS[5],
             unit("X_D5_K2_b", "b) Lệ phí giao thông đường bộ tại trụ sở người nộp thuế;", "X_D5_K2", 2, "b"),
             unit("X_D5_K2_c", "Trường hợp kiểm tra sau thông quan thực hiện theo quy định.", "X_D5_K2", 2, "c")]
    assert span_lines(rects_for(words, "X_D5_K2_c", units)) == (7, 7)
    assert span_lines(rects_for(words, "X_D5_K2_b", units)) == (6, 6)


def test_unit_running_onto_the_next_page_stops_before_bottom_edge_noise():
    units = [unit("X_D5", "Phạm vi gồm nội dung dòng một\nvà nội dung dòng hai tiếp sang trang sau", pages=(1, 2))]
    words = page_of(["Điều 5. Phạm vi", "Phạm vi gồm nội dung dòng một", "và nội dung dòng hai", "‹ z L3 ˆ^ Al"])
    assert span_lines(rects_for(words, "X_D5", units)) == (0, 2)


def test_continuation_that_is_only_a_stray_page_number_takes_nothing_from_the_next_point():
    b = "b) Điều 3 Thông tư số 128 của Bộ trưởng Bộ Tài chính về thuế thu nhập.\n12."  # "12." là số trang, tách nhầm
    c = "c) Điều 6 Thông tư số 103 ngày 06 tháng 8 của Bộ trưởng Bộ Tài chính hướng dẫn thực hiện."
    d = "d) Điều 3 Thông tư số 119 ngày 25 tháng 8 năm 2014."
    units = [unit("X_D5", f"3. Bãi bỏ:\n{b}\n{c}\n{d}", pages=(1, 2)),
             unit("X_D5_K3", f"3. Bãi bỏ:\n{b}\n{c}\n{d}", "X_D5", 3, pages=(1, 2)),
             unit("X_D5_K3_b", b, "X_D5_K3", 3, "b", pages=(1, 2)),
             unit("X_D5_K3_c", c, "X_D5_K3", 3, "c", pages=(2,)),
             unit("X_D5_K3_d", d, "X_D5_K3", 3, "d", pages=(2,))]
    words = page_of(["12", "c) Điều 6 Thông tư số 103 ngày 06 tháng 8", "của Bộ trưởng Bộ Tài chính hướng dẫn thực hiện.",
                     "d) Điều 3 Thông tư số 119 ngày 25 tháng 8 năm 2014."])
    layer = highlight._words_layer(words)
    assert highlight._locate_in_layer(layer, units, units[2], 2) == ()
    assert highlight._rects_from_layer(layer, units, units[2], 2) == ()  # không rơi xuống cách tìm dự phòng
    assert span_lines(rects_for(words, "X_D5_K3_c", units, page=2)) == (1, 2)


def test_locate_shows_nothing_rather_than_a_guess_when_the_unit_has_no_line_on_the_page(scanned, monkeypatch):
    page_words.store(scanned, 1, page_of(PAGE_1))
    monkeypatch.setattr(highlight, "_locate_in_layer", lambda *args: ())  # tìm được, nhưng trang này không có dòng nào
    monkeypatch.setattr(highlight, "_layout_rects", lambda *args: pytest.fail("không được đoán theo bố cục"))
    assert locate(scanned, "X_D5_K2_a", 1) == highlight.NO_HIGHLIGHT
