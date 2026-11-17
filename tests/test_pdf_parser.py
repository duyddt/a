"""Luồng "chỉ tải lên PDF" (app/services/pdf_parser.py) và hàm khoảng cách của thang đo.

Mỗi test ứng với một lỗi trong docs/bao-cao-kiem-thu-bo-du-lieu.md (L1...L13).
"""
import io
import json
import os
import random
import threading
import time
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.services import pdf_parser
from app.services.models import ImportProblem
from app.services.pdf_parser import auto_build_json_from_pdf, build_document_from_pages
from app.services.review import _edit_distance_seq

ROOT = Path(__file__).resolve().parent.parent


def blank_pdf(pages: int) -> bytes:
    """PDF toàn trang trắng, không có lớp chữ (như bản scan khi chưa OCR được)."""
    pdf = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            pdf.new_page(595, 842)
        buffer = io.BytesIO()
        pdf.save(buffer)
    finally:
        pdf.close()
    return buffer.getvalue()


def build(pages: list[str], filename: str = "L48_2024_QH15.pdf") -> tuple[dict, dict[str, dict]]:
    doc = json.loads(build_document_from_pages(pages, filename))
    return doc, {unit["unit_id"]: unit for unit in doc["legal_units"]}


def squash(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture
def no_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_parser, "_seed_dir", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------- L1: khớp văn bản mẫu chỉ vì trùng số trang

def test_pdf_with_same_page_count_as_a_seed_is_not_taken_for_that_seed(no_seed):
    seed = no_seed / "ND254_2026"
    (seed / "ocr_pages").mkdir(parents=True)
    for page in range(1, 4):
        (seed / "ocr_pages" / f"p{page:03d}.txt").write_text("PRINTED_PAGE: none\n\nx", encoding="utf-8")
    (seed / "document.json").write_bytes(b'{"seed": true}')
    other = blank_pdf(3)
    (seed / "source.pdf").write_bytes(other + b"%khac")

    assert pdf_parser._find_matching_seed_json(other) is None

    (seed / "source.pdf").write_bytes(other)  # đúng từng byte: vẫn nhận ra văn bản mẫu
    assert pdf_parser._find_matching_seed_json(other) == b'{"seed": true}'


# ---------------------------------------------------------------- L2: Điều bị cắt ở cuối trang

LAW_PAGES = [
    ("CÔNG BÁO/Số 1527 + 1528/Ngày 29-12-2024 77\n"
    "QUỐC HỘI\nLuật số: 48/2024/QH15\nLUẬT\nTHUẾ GIÁ TRỊ GIA TĂNG\n"
    "Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;\n"
    "Chương I\nNHỮNG QUY ĐỊNH CHUNG\n"
    "Điều 1. Phạm vi điều chỉnh\nLuật này quy định về thuế giá trị gia tăng.\n"
    "Điều 2. Đối tượng không chịu thuế\n1. Sản phẩm cây trồng.\n2. Sản phẩm giống vật nuôi, cây\n"),
    ("78 CÔNG BÁO/Số 1527 + 1528/Ngày 29-12-2024\n"
    "trồng.\n3. Muối, gồm:\na) Muối tinh;\nb) Muối i-ốt.\n"
    "Chương II\nĐIỀU KHOẢN THI HÀNH\n"
    "Điều 3. Hiệu lực thi hành\nLuật này có hiệu lực thi hành từ ngày 01 tháng 7 năm 2025./.\n"
    "Luật này được Quốc hội nước Cộng hòa xã hội chủ nghĩa Việt Nam khóa XV, kỳ họp thứ 8 thông qua ngày\n"
    "26 tháng 11 năm 2024.\nCHỦ TỊCH QUỐC HỘI\n"),
]


def test_units_continue_across_page_breaks():
    _, units = build(LAW_PAGES)
    d2 = units["L48_2024_QH15_D2"]
    assert d2["source_pages"] == [1, 2]
    assert "3. Muối, gồm:" in d2["content"] and "b) Muối i-ốt." in d2["content"]
    assert squash(units["L48_2024_QH15_D2_K2"]["content"]) == "2. Sản phẩm giống vật nuôi, cây trồng."
    assert units["L48_2024_QH15_D2_K2"]["source_pages"] == [1, 2]
    assert units["L48_2024_QH15_D2_K3"]["source_pages"] == [2]
    assert squash(units["L48_2024_QH15_D2_K3_b"]["content"]) == "b) Muối i-ốt."


def test_page_headers_chapter_titles_and_closing_formula_are_not_unit_content():
    _, units = build(LAW_PAGES)
    everything = "\n".join(unit["content"] for unit in units.values())
    assert "CÔNG BÁO" not in everything
    assert "ĐIỀU KHOẢN THI HÀNH" not in everything and "Chương II" not in everything
    assert "Quốc hội" not in units["L48_2024_QH15_D3"]["content"]
    assert units["L48_2024_QH15_D1"]["chapter"] == "I" and units["L48_2024_QH15_D3"]["chapter"] == "II"
    assert list(units) == [
        "L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D2_K1", "L48_2024_QH15_D2_K2",
        "L48_2024_QH15_D2_K3", "L48_2024_QH15_D2_K3_a", "L48_2024_QH15_D2_K3_b", "L48_2024_QH15_D3",
    ]


def test_blank_page_inside_an_article_marks_it_not_confident():
    _, units = build(["Điều 1. Một\n1. Khoản một,", "", "2. Khoản hai.\nĐiều 2. Hai\nNội dung."])
    assert units["L48_2024_QH15_D1"]["is_confident"] is False
    assert units["L48_2024_QH15_D2"]["is_confident"] is True


# ---------------------------------------------------------------- L3: regex Điều quá lỏng, giữ lần xuất hiện đầu

def test_line_starting_with_an_article_reference_is_not_a_new_article():
    _, units = build([
        ("Điều 1. Phạm vi\nĐối tượng quy định tại\nĐiều 5 của Luật này.\nCăn cứ khoản 1\nĐiều 2 Luật này.\n"
        "Điều 2. Đối tượng áp dụng\n1. Khoản một.\n")
    ])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D2_K1"]
    assert units["L48_2024_QH15_D2"]["title"] == "Đối tượng áp dụng"
    assert "Điều 5 của Luật này." in units["L48_2024_QH15_D1"]["content"]


def test_skipped_article_number_is_accepted_but_flagged():
    _, units = build(["Điều 1. Một\nNội dung.\nĐiều 3. Ba\nNội dung."])
    assert units["L48_2024_QH15_D3"]["is_confident"] is False
    assert units["L48_2024_QH15_D1"]["is_confident"] is True


# ---------------------------------------------------------------- L4: Điểm trong phần trích dẫn của luật sửa đổi

def test_points_inside_quoted_amendment_text_are_not_split():
    _, units = build([
        ("Điều 1. Sửa đổi Luật Điện lực\n1. Sửa đổi khoản 1 Điều 4 như sau:\n“1. Nhà nước độc quyền:\n"
        "a) Điều độ hệ thống điện;\nb) Vận hành lưới điện.\nĐiều 4. Không phải Điều mới.”.\n2. Bãi bỏ khoản 3 Điều 5.\n")
    ])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D1_K1", "L48_2024_QH15_D1_K2"]
    assert "a) Điều độ hệ thống điện;" in units["L48_2024_QH15_D1_K1"]["content"]


def test_point_d_read_twice_becomes_point_dd_and_is_flagged():
    _, units = build(["Điều 1. Một\n1. Gồm:\na) A;\nb) B;\nc) C;\nd) D;\nd) Đ.\n"])
    assert units["L48_2024_QH15_D1_K1_đ"]["content"] == "d) Đ."
    assert units["L48_2024_QH15_D1_K1_đ"]["is_confident"] is False


# ---------------------------------------------------------------- L5: Điều cuối dính khối ký ban hành

def test_last_article_stops_before_recipients_and_signature():
    _, units = build([
        ("Điều 1. Hiệu lực thi hành\n1. Thông tư này có hiệu lực từ ngày 01 tháng 01 năm 2026.\n"
        "2. Nếu có vướng mắc, đề nghị phản ánh về Bộ Tài chính./.\n"
        "Nơi nhận:\n- Văn phòng Chính phủ;\n- Lưu: VT.\nKT. BỘ TRƯỞNG\nTHỨ TRƯỞNG\n")
    ])
    assert squash(units["L48_2024_QH15_D1_K2"]["content"]) == "2. Nếu có vướng mắc, đề nghị phản ánh về Bộ Tài chính./."
    assert "Nơi nhận" not in units["L48_2024_QH15_D1"]["content"]


# ---------------------------------------------------------------- L6: siêu dữ liệu bịa (số hiệu <ID>/2026, ngày = hôm nay)

def test_metadata_is_read_from_the_document():
    doc, _ = build([
        ("CHÍNH PHỦ\nCỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc\nSố: 254/2026/NĐ-CP\n"
        "Hà Nội, ngày 30 tháng 6 năm 2026\nNGHỊ ĐỊNH\nQuy định chi tiết về hóa đơn điện tử,\nchứng từ điện tử\n"
        "Căn cứ Luật Tổ chức Chính phủ;\nĐiều 1. Hiệu lực thi hành\n"
        "Nghị định này có hiệu lực thi hành từ ngày 01 tháng 7 năm 2026./.\n")
    ], "ND254_2026.pdf")
    assert doc["document_no"] == "254/2026/NĐ-CP"
    assert doc["document_type"] == "Nghị định"
    assert doc["title"] == "Quy định chi tiết về hóa đơn điện tử, chứng từ điện tử"
    assert doc["issued_date"] == "2026-06-30" and doc["effective_from"] == "2026-07-01"


def test_law_metadata_uses_passing_date_and_law_number():
    doc, _ = build(LAW_PAGES)
    assert doc["document_no"] == "48/2024/QH15" and doc["document_type"] == "Luật"
    assert doc["title"] == "THUẾ GIÁ TRỊ GIA TĂNG"
    assert doc["issued_date"] == "2024-11-26" and doc["effective_from"] == "2025-07-01"


# ---------------------------------------------------------------- L14: số hiệu và tên văn bản OCR sai

@pytest.mark.parametrize(("filename", "number_line", "document_no", "document_type"), [
    ("ND145_2020.pdf", "Số: 4452020/NĐ-CP Hà Nội, ngày 14 tháng 12 năm 2020", "145/2020/NĐ-CP", "Nghị định"),
    ("ND125_2020.pdf", "Số:2£5/2020/NĐ-CP Hà Nội, ngày19tháng 10 năm 2020", "125/2020/NĐ-CP", "Nghị định"),
    ("L109_2025_QH15.pdf", "Luật sô: 1Ø9/2025/QH15", "109/2025/QH15", "Luật"),
    ("TT91_2026_BTC.pdf", "Số: 9I/2026/TT-BTC", "91/2026/TT-BTC", "Thông tư"),
    ("L59_2020_QH14_tiep.pdf", "", "59/2020/QH14", "Luật"),
])
def test_unreadable_document_number_falls_back_to_the_file_name_convention(filename, number_line, document_no,
                                                                          document_type):
    doc, _ = build([f"CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n{number_line}\nĐiều 1. Một\nNội dung."], filename)
    assert doc["document_no"] == document_no
    assert doc["document_type"] == document_type


def test_a_well_formed_document_number_in_the_text_wins_over_the_file_name():
    # TT52_2025_BTC.pdf trong bộ dữ liệu thực ra là Thông tư 152/2025/TT-BTC (D1)
    doc, _ = build(["BỘ TÀI CHÍNH\nSố: 152/2025/TT-BTC\nTHÔNG TƯ\nHướng dẫn kế toán\nĐiều 1. Một\nNội dung."],
                   "TT52_2025_BTC.pdf")
    assert doc["document_no"] == "152/2025/TT-BTC"


def test_title_never_falls_back_to_the_national_motto_or_ocr_garbage():
    doc, _ = build([("QUÓC HỘI CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc\nT87)\n"
                    "Điều 1. Một\nNội dung.")], "L109_2025_QH15.pdf")
    assert doc["title"] == "Luật số 109/2025/QH15"


def test_garbage_title_is_replaced_by_the_enacting_sentence():
    doc, _ = build([("CHÍNH PHỦ\nSố: 274/2025/NĐ-CP\nNGHỊ ĐỊNH\nT87)\nCăn cứ Luật Tổ chức Chính phủ;\n"
                    "Theo đề nghị của Bộ trưởng Bộ Nội vụ;\n"
                    "Chính phủ ban hành Nghị định quy định chi tiết về chậm đóng, trốn đóng bảo hiểm xã hội\n"
                    "bắt buộc, bảo hiểm thất nghiệp.\nĐiều 1. Một\nNội dung.")], "ND274_2025.pdf")
    assert doc["title"] == "Quy định chi tiết về chậm đóng, trốn đóng bảo hiểm xã hội bắt buộc, bảo hiểm thất nghiệp"


def test_title_mixed_with_a_received_stamp_is_taken_from_the_enacting_sentence():
    # chữ OCR thật của L108_2025_QH15 trang 1: dấu "CÔNG VĂN ĐẾN" in đè lên tên Luật
    doc, _ = build([("QUÓC HỘI CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\nĐộc lập - Tự do - Hạnh phúc\n"
                    "Luật số: 108/2025/QH15\n_ VĂN PHÒNG CHÍNH PHÙ\nCÔNG VĂN ĐẾN\n"
                    "Giờ... ý... Ngày.... 3Â (ø,....... QUẢN LÝ THUÉ\n: Kính chuyển....Ì-2..... . ;\n"
                    "ph um cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam đã được sửa\n"
                    "đối, bổ sung một số điều theo Nghị quyết số 203/2025/QH15;\n"
                    "Quốc hội ban hành Luật Quản lý thuế.\nGỐNG THÔNG TỊN ĐIỆN TỦ CHÍNH PHÙ\nLUẬT\n"
                    "ĐẾN uy 261012046...\nT\nChương I\nQUY ĐỊNH CHUNG\nĐiều 1. Phạm vi điều chỉnh\nNội dung.")],
                   "L108_2025_QH15.pdf")
    assert doc["document_no"] == "108/2025/QH15" and doc["document_type"] == "Luật"
    assert doc["title"] == "Quản lý thuế"


def test_stamp_inside_the_title_lines_prefers_the_complete_enacting_sentence():
    # chữ OCR thật của ND310_2025 trang 1: dấu Cổng thông tin chèn vào giữa dòng thứ hai của tên
    doc, _ = build([("CHÍNH PHỦ CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n———— Độc lập - Tự do - Hạnh phúc\n"
                    "Số: 310/2025/NĐ-CP Hà Nội, ngày 02 tháng 12 năm 2025\nNGHỊ ĐỊNH\n"
                    "Sửa đối, bố sung một số điều của Nghị định số 125/2020/NĐ-CP\n"
                    "TW iỆt TỪ CHÍNH PHÙ PHÙ hgày 19 tháng 10 năm 2020 của Chính phú quy định xử phạt\n"
                    "vi phạm hành chính về thuế, hóa đơn\n[sissis _\nCăn cứ Luật Tổ chức Chính phủ số 63/2025/QH15;\n"
                    "__ Chính phủ ban hành Nghị định sửa đổi, bổ sung một số điều của Nghị định\n"
                    "số 125/2020/NĐ-CP ngày 19 tháng 10 năm 2020 của Chính phủ quy định xử\n"
                    "phạt vi phạm hành chính về thuê, hóa ẩơn.\nĐiều 1. Một\nNội dung.")], "ND310_2025.pdf")
    assert doc["title"] == ("Sửa đổi, bổ sung một số điều của Nghị định số 125/2020/NĐ-CP ngày 19 tháng 10 năm 2020 "
                            "của Chính phủ quy định xử phạt vi phạm hành chính về thuê, hóa ẩơn")


@pytest.mark.parametrize("first_title_line", ['" Quy định chi tiết', '"| Quy định chi tiết', "“Quy định chi tiết",
                                              "5. Quy định chi tiết", "” Quy định chi tiết"])
def test_stray_marks_before_the_title_are_dropped(first_title_line):
    doc, _ = build([(f"CHÍNH PHỦ\nSố: 338/2025/NĐ-CP\nNGHỊ ĐỊNH\n{first_title_line}\nmột số điều của Luật Việc làm\n"
                    "Căn cứ Luật Tổ chức Chính phủ;\nĐiều 1. Một\nNội dung.")], "ND338_2025.pdf")
    assert doc["title"] == "Quy định chi tiết một số điều của Luật Việc làm"


def test_short_law_names_are_still_titles():
    doc, _ = build(["QUỐC HỘI\nBộ luật số: 45/2019/QH14\nBỘ LUẬT\nLAO ĐỘNG\nCăn cứ Hiến pháp;\nĐiều 1. Một\nNội dung."],
                   "L45_2019_QH14.pdf")
    assert doc["title"] == "LAO ĐỘNG" and doc["document_type"] == "Bộ luật"


def test_missing_metadata_is_left_unknown_not_invented():
    doc, _ = build(["Điều 1. Một\nNội dung không có số hiệu và ngày tháng."], "X_2026.pdf")
    assert doc["document_no"] == pdf_parser.UNKNOWN_DOCUMENT_NO
    assert doc["issued_date"] is None and doc["effective_from"] is None


# ---------------------------------------------------------------- L7: PDF scan "nhập thành công" khi không có OCR

def test_scan_pdf_without_ocr_is_refused_without_rendering_pages(no_seed, monkeypatch):
    monkeypatch.setattr(pdf_parser, "ocr_available", lambda: False)
    calls = []
    monkeypatch.setattr(pdf_parser, "_ocr_page_image", lambda image: calls.append(image) or ("", None))
    with pytest.raises(ImportProblem) as caught:
        auto_build_json_from_pdf(blank_pdf(2), "SCAN_2026.pdf")
    assert "OCR" in caught.value.problems[0]
    assert calls == []


def test_pages_without_any_text_are_refused():
    with pytest.raises(ImportProblem):
        build_document_from_pages(["", "  \n"], "SCAN_2026.pdf")


def test_blank_pages_get_no_placeholder_units_when_articles_were_found():
    _, units = build(["Điều 1. Một\nNội dung.", ""])
    assert list(units) == ["L48_2024_QH15_D1"]


# ---------------------------------------------------------------- L9: pdfium phải chạy trong PDFIUM_LOCK

def test_pdfium_is_only_opened_while_holding_the_shared_lock(no_seed, monkeypatch):
    data = blank_pdf(1)
    real_document = pdfium.PdfDocument
    lock = pdf_parser.PDFIUM_LOCK

    def checked_document(*args, **kwargs):
        assert lock._is_owned(), "pdfium được gọi ngoài PDFIUM_LOCK"
        return real_document(*args, **kwargs)

    monkeypatch.setattr(pdf_parser, "ocr_available", lambda: False)
    monkeypatch.setattr(pdf_parser.pdfium, "PdfDocument", checked_document)
    with pytest.raises(ImportProblem):
        auto_build_json_from_pdf(data, "SCAN_2026.pdf")


# ---------------------------------------------------------------- OCR song song nhiều trang

def scan_pdf(pages: int) -> bytes:
    """PDF scan giả: mỗi trang trắng nhưng rộng khác nhau, để biết ảnh đưa vào OCR là của trang nào."""
    pdf = pdfium.PdfDocument.new()
    try:
        for index in range(pages):
            pdf.new_page(300 + 10 * index, 400)
        buffer = io.BytesIO()
        pdf.save(buffer)
    finally:
        pdf.close()
    return buffer.getvalue()


class FakeOcr:
    """Thay Tesseract: chờ một lúc rồi trả "Trang N", đếm số trang đang OCR và số ảnh đã dựng mà chưa OCR xong."""

    def __init__(self, delays):
        self.delays = delays
        self.guard = threading.Lock()
        self.running = self.peak_running = 0
        self.rendered = self.finished = self.peak_waiting = 0

    def page_of(self, image) -> int:
        return round((image.width * 72 / pdf_parser.OCR_DPI - 300) / 10)

    def rendered_one(self):
        with self.guard:
            self.rendered += 1
            self.peak_waiting = max(self.peak_waiting, self.rendered - self.finished)

    def __call__(self, image) -> str:
        page = self.page_of(image)
        with self.guard:
            self.running += 1
            self.peak_running = max(self.peak_running, self.running)
        time.sleep(self.delays[page])
        with self.guard:
            self.running -= 1
            self.finished += 1
        return f"Trang {page + 1} đã OCR", None


@pytest.fixture
def fake_ocr(monkeypatch):
    def install(pages: int, workers: int) -> FakeOcr:
        rng = random.Random(7)
        ocr = FakeOcr([rng.uniform(0.01, 0.05) for _ in range(pages)])
        monkeypatch.setattr(pdf_parser, "ocr_available", lambda: True)
        monkeypatch.setattr(pdf_parser, "_ocr_page_image", ocr)
        monkeypatch.setenv("OCR_WORKERS", str(workers))
        real_render = pdf_parser._render_for_ocr

        def counted_render(page):
            image = real_render(page)
            ocr.rendered_one()
            return image

        monkeypatch.setattr(pdf_parser, "_render_for_ocr", counted_render)
        return ocr

    return install


def test_scanned_pages_are_ocred_in_parallel_and_kept_in_page_order(no_seed, fake_ocr):
    ocr = fake_ocr(pages=12, workers=4)
    pages = pdf_parser.extract_pages_text(scan_pdf(12))
    assert pages == [f"Trang {n} đã OCR" for n in range(1, 13)]
    assert 1 < ocr.peak_running <= 4  # nhiều trang cùng lúc, không vượt số luồng đã đặt


def test_parallel_ocr_does_not_hold_every_page_image_in_memory(no_seed, fake_ocr):
    """Văn bản hàng trăm trang: chỉ dựng trước tối đa 2 × số luồng ảnh, không dựng hết rồi mới OCR."""
    ocr = fake_ocr(pages=16, workers=2)
    pdf_parser.extract_pages_text(scan_pdf(16))
    assert ocr.finished == 16
    assert ocr.peak_waiting <= 4


@pytest.mark.parametrize(("value", "expected"), [("3", 3), ("0", None), ("abc", None), ("", None)])
def test_ocr_workers_comes_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv("OCR_WORKERS", value)
    default = min(4, os.cpu_count() or 1)
    assert pdf_parser.ocr_workers() == (expected or default)


# ---------------------------------------------------------------- L10: khoảng cách chỉnh sửa phải là Levenshtein thật

def levenshtein(a, b) -> int:
    previous = list(range(len(b) + 1))
    for i, item_a in enumerate(a, 1):
        current = [i]
        for j, item_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (item_a != item_b)))
        previous = current
    return previous[-1]


def test_edit_distance_matches_levenshtein_on_random_strings_and_word_lists():
    rng = random.Random(7)
    for _ in range(400):
        a = "".join(rng.choice("aăâdđ ") for _ in range(rng.randint(0, 40)))
        b = "".join(rng.choice("aăâdđ ") for _ in range(rng.randint(0, 40)))
        assert _edit_distance_seq(a, b) == levenshtein(a, b)
        assert _edit_distance_seq(a.split(), b.split()) == levenshtein(a.split(), b.split())
    long_a = "x" * 150 + "y" * 150
    assert _edit_distance_seq(long_a, long_a[::-1]) == levenshtein(long_a, long_a[::-1])


def test_scattered_ocr_errors_in_long_units_are_counted_exactly():
    doc = json.loads((ROOT / "data" / "ND254_2026" / "document.json").read_text(encoding="utf-8"))
    texts = [unit["content"] for unit in doc["legal_units"] if 1500 < len(unit["content"]) < 2500][:4]
    swap = {"đ": "d", "ế": "ê", "ạ": "a", "ư": "u", "ơ": "o", "ộ": "ô", "ả": "a", "ố": "ô"}
    rng = random.Random(1)
    assert len(texts) == 4
    for truth in texts:
        ocr = list(truth)
        for index in rng.sample([i for i, ch in enumerate(truth) if ch in swap], 25):
            ocr[index] = swap[ocr[index]]
        assert _edit_distance_seq("".join(ocr), truth) == 25


# ---------------------------------------------------------------- các trường hợp gặp trên bộ dữ liệu thật

def test_quote_opened_curly_and_closed_straight_does_not_hide_next_article():
    _, units = build([('Điều 1. Một\n- Dòng “Lý do nộp" ghi rõ nội dung.\n- Dòng “Số tiền": ghi bằng số.\n'
                      "Điều 2. Hai\n1. Khoản một.")])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D2_K1"]


def test_type_and_name_on_one_line_and_numeric_dates():
    doc, _ = build([("QUỐC HỘI\nLuật số: 50/2024/QH15\nLUẬT CÔNG ĐOÀN\nCăn cứ Hiến pháp;\n"
                    "Điều 1. Hiệu lực thi hành\nLuật này có hiệu lực thi hành kể từ ngày 01/7/2025./.")])
    assert doc["document_type"] == "Luật" and doc["title"] == "CÔNG ĐOÀN"
    assert doc["effective_from"] == "2025-07-01"


def test_draft_with_blank_number_and_date_is_left_unknown():
    doc, _ = build([("Số: /2025/TT-BNNMT Hà Nội, ngày tháng năm 2025\nTHÔNG TƯ\nHướng dẫn máy chuyên dùng\n"
                    "Căn cứ Luật Thuế giá trị gia tăng số 48/2024/QH15 ngày 26 tháng 11 năm 2024;\n"
                    "Điều 1. Phạm vi\nNội dung.")], "TT101_2025_BNNMT.pdf")
    assert doc["document_no"] == pdf_parser.UNKNOWN_DOCUMENT_NO and doc["issued_date"] is None
    assert doc["document_type"] == "Thông tư"


def test_numbered_rows_of_an_embedded_form_are_not_clauses():
    _, units = build([(
        "Điều 10. Báo cáo tài chính\n1. Về trách nhiệm lập báo cáo:\na) Hằng năm phải lập;\nb) Nộp cho cơ quan thuế.\n"
        "2. Hệ thống báo cáo tài chính\na) Biểu mẫu báo cáo tài chính\nMẫu số B01 - DNSN\n1. Tiền 110\n"
        "2. Các khoản nợ phải thu 120\n3. Hàng tồn kho 130\n1. Các khoản nợ phải trả 310\n2. Thuế phải nộp 320\n"
        "b) Nội dung và phương pháp lập báo cáo\nChi phí gồm:\na) Chi phí nhân công;\nb) Chi phí vật liệu;\n"
        "c) Chi phí khấu hao.\nĐiều 11. Hiệu lực\nNội dung."
    )], "TT58_2026_BTC.pdf")
    assert list(units) == [
        "TT58_2026_BTC_D10", "TT58_2026_BTC_D10_K1", "TT58_2026_BTC_D10_K1_a", "TT58_2026_BTC_D10_K1_b",
        "TT58_2026_BTC_D10_K2", "TT58_2026_BTC_D10_K2_a", "TT58_2026_BTC_D10_K2_b", "TT58_2026_BTC_D11",
    ]
    assert "3. Hàng tồn kho 130" in units["TT58_2026_BTC_D10_K2_a"]["content"]
    assert "c) Chi phí khấu hao." in units["TT58_2026_BTC_D10_K2_b"]["content"]


# ---------------------------------------------------------------- L11: một dấu ngoặc nhiễu của OCR làm mất phần còn lại

def test_stray_ocr_quote_does_not_hide_the_rest_of_the_document():
    # OCR đọc vết bẩn thành '"' đứng riêng đầu dòng; '".' dính sau chữ là dấu đóng, không mở trích dẫn mới
    text = ('Điều 1. Một\n1. Gồm:\na) A;\nb) B;\n" c) Số biên lai là số thứ tự.\nĐiều 2. Hai\nNội dung chữa bệnh".\n'
            '"` Bảo đảm khuôn dạng dữ liệu.\n')
    text += "".join(f"Điều {n}. Tên {n}\nNội dung.\n" for n in range(3, 9))
    _, units = build([text])
    assert [u for u in units if u.count("_") == 3] == [f"L48_2024_QH15_D{n}" for n in range(1, 9)]
    assert "L48_2024_QH15_D1_K1_c" in units


def test_two_apostrophes_close_a_curly_quote():
    _, units = build(["Điều 1. Một\n1. Đóng dấu từ “CẤP LẠI’’ trên giấy.\n2. Khoản hai.\nĐiều 2. Hai\nNội dung."])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D1_K1", "L48_2024_QH15_D1_K2", "L48_2024_QH15_D2"]


def test_quoted_amendment_with_straight_quotes_is_still_quoted():
    _, units = build([('Điều 1. Sửa đổi\n1. Sửa đổi Điều 5 như sau:\n"Điều 2. Không phải Điều mới\n'
                      'a) Điểm trích dẫn;\nb) Điểm trích dẫn."\n2. Khoản hai.\nĐiều 2. Hai\nNội dung.')])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D1_K1", "L48_2024_QH15_D1_K2", "L48_2024_QH15_D2"]


def test_ocr_noise_before_an_article_heading_is_ignored_but_flagged():
    _, units = build(["Điều 1. Một\nNội dung.\n_ Điều 2. Hai\nNội dung.\n| Điều 3. Ba\nNội dung."])
    assert [u for u in units] == ["L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D3"]
    assert units["L48_2024_QH15_D2"]["title"] == "Hai"
    assert units["L48_2024_QH15_D2"]["is_confident"] is False


def test_articles_after_an_unclosed_quote_are_found_again():
    filler = "".join(f"dòng {i}\n" for i in range(60))
    text = "Điều 1. Một\nSửa đổi khoản 2 như sau:\n“Thay cụm từ abc bằng cụm từ khác.\n"
    text += "".join(f"Điều {n}. Tên {n}\n{filler}" for n in range(2, 7)) + "Điều 7. Bảy\nNội dung."
    _, units = build([text])
    # Trích dẫn khối (mở sau "như sau:") không đóng: Điều 2–5 nằm trong đoạn bị coi là trích dẫn nên mất;
    # sau khi van xả, Điều 6 được nhận lại (có đánh dấu)
    assert [u for u in units if u.count("_") == 3] == ["L48_2024_QH15_D1", "L48_2024_QH15_D6", "L48_2024_QH15_D7"]
    assert units["L48_2024_QH15_D1"]["is_confident"] is False
    assert units["L48_2024_QH15_D6"]["is_confident"] is False


# ---------------------------------------------------------------- L15: dấu ngoặc lạ do OCR nuốt các Điều phía sau

def articles_of(units: dict) -> list[str]:
    return [unit_id for unit_id, unit in units.items() if unit["clause"] is None and unit["article"] is not None]


def test_stray_quotes_inside_words_do_not_hide_the_following_articles():
    # Tesseract chèn ngoặc vào giữa chữ (bảo "hiểm, ngân “hàng) và không bao giờ đóng (L108, ND145, ND181...)
    _, units = build([
        ("Điều 1. Một\n1. Khoản một.\n2. Người nộp thuế thuộc “trường hợp phải nộp tiền chậm nộp quy định tại\n"
        "khoản 1 Điều 59 Luật này.\n3. Tham gia bảo \"hiểm xã hội theo quy định.\n"
        "Điều 2. Hai\n1. Khoản một.\n2. Khoản hai.\nĐiều 3. Ba\nNội dung.")
    ])
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D3"]
    assert {"L48_2024_QH15_D1_K3", "L48_2024_QH15_D2_K2"} <= set(units)
    assert units["L48_2024_QH15_D2"]["is_confident"] is False  # nhận tiêu đề khi ngoặc chưa đóng: người soát xem lại
    assert units["L48_2024_QH15_D3"]["is_confident"] is True


def test_unclosed_quote_in_a_sentence_does_not_swallow_the_next_clause():
    _, units = build([
        ("Điều 1. Một\n1. Hộ kinh doanh do một cá nhân hoặc các thành viên hộ gia\n"
        "\"dân cư, chủ hộ kinh doanh đăng ký.\n2. Khoản hai.\n3. Khoản ba.\nĐiều 2. Hai\nNội dung.")
    ])
    assert ["L48_2024_QH15_D1_K1", "L48_2024_QH15_D1_K2", "L48_2024_QH15_D1_K3"] == [
        u for u in units if "_K" in u]
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]


def test_unclosed_quote_in_a_sentence_expires_after_a_few_lines():
    filler = "".join(f"dòng văn bản thứ {i}\n" for i in range(10))
    _, units = build([f"Điều 1. Một\n1. Thay cụm từ “abc bằng cụm từ khác\n{filler}3. Khoản ba.\nĐiều 2. Hai\nNội dung."])
    assert "L48_2024_QH15_D1_K3" in units  # Khoản 3 (thiếu Khoản 2) vẫn được nhận sau khi ngoặc lạ hết hạn
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]


def test_lone_curly_quote_without_text_after_it_is_ocr_noise():
    _, units = build(["Điều 1. Một\nNội dung văn bản thay thế. “\n“®\n“%\nĐiều 2. Hai\nNội dung.\nĐiều 3. Ba\nNội dung."])
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2", "L48_2024_QH15_D3"]
    assert all(unit["is_confident"] for unit in units.values())


def test_stray_quote_or_mark_before_a_real_article_heading_is_recognised_and_flagged():
    # "Điều 37. ..." mở bằng ngoặc lạ nhưng dòng trước không kết thúc bằng ':' thì không phải trích dẫn sửa đổi
    _, units = build([
        ("Điều 1. Một\nNội dung.\n\"Điều 2. Hai\nNội dung.\n” Điều 3. Ba\nNội dung.\n'Điều 4. Bốn\nNội dung.\n"
        "„ Điều 5. Năm\nNội dung.\n- Điều 6. Sáu\nNội dung.\nĐiều 7. Bảy\nNội dung.")
    ])
    assert articles_of(units) == [f"L48_2024_QH15_D{n}" for n in range(1, 8)]
    assert [units[f"L48_2024_QH15_D{n}"]["is_confident"] for n in range(2, 8)] == [False] * 5 + [True]


def test_misread_article_heading_with_the_next_number_is_recognised_and_flagged():
    # chữ OCR thật: "Điều. 51." (ND168), "Điều, 31." (TT90), "Điều 166:" (L59), "Điền 147." (L45)
    _, units = build([
        ("Điều 1. Một\nNội dung.\nĐiều. 2. Hai\nNội dung.\nĐiều, 3. Ba\nNội dung.\nĐiều 4: Bốn\nNội dung.\n"
         "Điền 5. Năm\nNội dung.\nĐiều 6. Sáu\nNội dung.")
    ])
    assert articles_of(units) == [f"L48_2024_QH15_D{n}" for n in range(1, 7)]
    assert units["L48_2024_QH15_D4"]["title"] == "Bốn"
    assert [units[f"L48_2024_QH15_D{n}"]["is_confident"] for n in range(2, 7)] == [False] * 4 + [True]


def test_misread_article_heading_needs_the_next_number_and_a_capitalised_title():
    _, units = build([
        ("Điều 1. Một\nquy định tại\nĐiều 2, khoản 3 của Luật này.\nĐiền 5. Không phải Điều kế tiếp\n"
         "theo quy định tại\nĐiều 2, Điều 3, Điều 4 Thông tư này và lập báo cáo.\n"  # TT58 Điều 4, lớp chữ thật
         "Điều 2. Hai\nNội dung.")
    ])
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]
    assert units["L48_2024_QH15_D2"]["title"] == "Hai"


def test_quoted_article_of_an_amended_law_is_still_quoted_when_it_has_the_next_number():
    # trích dẫn khối sau "như sau:" vẫn là trích dẫn, kể cả khi số Điều được trích trùng với số Điều kế tiếp
    _, units = build([
        ("Điều 1. Sửa đổi\n1. Sửa đổi Điều 2 như sau:\n“Điều 2. Không phải Điều mới\n1. Khoản được trích.\n"
        "Điều 3. Vẫn được trích dẫn.”\n2. Khoản hai.\nĐiều 2. Hai\nNội dung.")
    ])
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]
    assert [u for u in units if "_K" in u] == ["L48_2024_QH15_D1_K1", "L48_2024_QH15_D1_K2"]


@pytest.mark.parametrize("lead_in", ["1. Sửa đổi Điều 2 như sau", "1. Sửa đổi Điều 2 như sau.",
                                     "1. Sửa đổi, bổ sung Điều 2 như sau;"])
def test_quoted_next_article_after_a_lead_in_whose_colon_was_lost_is_still_quoted(lead_in):
    # OCR làm mất hoặc đọc lệch dấu ":" của "như sau:"
    _, units = build([
        (f"Điều 1. Sửa đổi\n{lead_in}\n“Điều 2. Không phải Điều mới\n1. Khoản được trích.”\n"
         "2. Khoản hai.\nĐiều 2. Hai\nNội dung.")
    ])
    assert articles_of(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]
    assert units["L48_2024_QH15_D2"]["title"] == "Hai"


def test_quoted_clause_after_an_amendment_line_without_colon_is_still_quoted():
    # L48 Điều 17: câu dẫn sửa đổi luật khác không có "như sau:", trích dẫn mở ngay bằng “1.
    _, units = build([
        ("Điều 1. Sửa đổi luật khác\nSửa đổi, bổ sung khoản 1 Điều 3 của Luật Thuế thu nhập cá nhân số 04/2007/QH12\n"
        "“1. Thu nhập từ kinh doanh, bao gồm:\na) Thu nhập A;\nb) Thu nhập B.”\nĐiều 2. Hai\nNội dung.")
    ])
    assert list(units) == ["L48_2024_QH15_D1", "L48_2024_QH15_D2"]


def test_common_ocr_misreads_of_point_and_clause_markers_are_recognised_and_flagged():
    _, units = build(["Điều 1. Một\n1.Tổ chức bán hàng:\na) A;\nb) B;\n©) C;\nđ) D;\nđ) Đ;\ne) E;\n,ø) G.\n2. Khoản hai.\n"])
    prefix = "L48_2024_QH15_D1_K1"
    assert list(units) == ["L48_2024_QH15_D1", prefix] + [f"{prefix}_{x}" for x in "abcdđeg"] + ["L48_2024_QH15_D1_K2"]
    assert units[f"{prefix}_d"]["content"] == "đ) D;"
    assert [x for x in "abcdđeg" if not units[f"{prefix}_{x}"]["is_confident"]] == ["c", "d", "g"]
