"""Tô sáng vùng nội dung của đơn vị trên ảnh trang (app/services/highlight.py)."""
import random

import pytest
from PIL import Image, ImageDraw

from app.contracts import LegalUnit
from app.services import highlight
from app.services.highlight import NO_HIGHLIGHT, locate
from app.services.store import DocumentStore

DOC = "ND254_2026"


@pytest.fixture
def loaded(data_dir):
    return DocumentStore(data_dir).get(DOC)


def _top(rects):
    return min(r[1] for r in rects)


def _bottom(rects):
    return max(r[3] for r in rects)


def test_normalize_ignores_case_diacritics_and_punctuation():
    text, origin = highlight._normalize("Điều 1. Phạm vi")
    assert text == "dieu1phamvi"
    assert origin[0] == 0 and origin[-1] == len("Điều 1. Phạm vi") - 1


def test_span_prefers_heading_and_first_end_anchor():
    page = "chuong2dieu9tendieu9khoan1noidungxkhoan2noidungy" + "noidungx"
    span = highlight._span_on_page(page, "khoan1noidungx", "dieu9tendieu9", starts_here=True, ends_here=True)
    assert page[span[0]:span[1]] == "dieu9tendieu9khoan1noidungx"
    # đơn vị bắt đầu ở trang trước: lấy từ đầu trang tới lần xuất hiện ĐẦU TIÊN của phần cuối nội dung
    span = highlight._span_on_page(page, "phantruoc" + "khoan1noidungx", "", starts_here=False, ends_here=True)
    assert span == (0, page.index("noidungx") + len("noidungx"))


def test_text_layer_pdf_highlights_exact_lines(loaded):
    point = locate(loaded, "ND254_2026_D9_K1_a", 1)
    article = locate(loaded, "ND254_2026_D9", 1)
    assert point.method == "text" and article.method == "text"
    assert all(0 <= v <= 1 for rect in point.rects for v in rect)
    # Điều 9 gồm cả dòng tiêu đề "Điều 9. ..." nên bắt đầu cao hơn Điểm a và bao trọn Điểm a
    assert _top(article.rects) < _top(point.rects)
    assert _bottom(article.rects) >= _bottom(point.rects) - 0.005


def test_unit_spanning_pages_is_highlighted_on_each_page(loaded):
    first, second = locate(loaded, "ND254_2026_D9_K1", 1), locate(loaded, "ND254_2026_D9_K1", 2)
    assert first.rects and second.rects
    # trang 2 chỉ có phần cuối (Điểm b): không được lan sang Khoản 2 nằm ngay dưới
    clause2 = locate(loaded, "ND254_2026_D9_K2", 2)
    assert _bottom(second.rects) <= _top(clause2.rects) + 0.005


def test_no_highlight_outside_unit_pages_or_for_unknown_unit(loaded):
    assert locate(loaded, "ND254_2026_D9_K1_a", 2) is NO_HIGHLIGHT
    assert locate(loaded, "khong_co", 1) is NO_HIGHLIGHT
    assert locate(loaded, None, 1) is NO_HIGHLIGHT


# ---- bản scan không có lớp chữ: ghép khối đoạn văn trên ảnh với thứ tự đoạn văn của đơn vị ----

def _draw_paragraph(draw, y, lines, rng, indent=40, left=100, right=900):
    """Vẽ một đoạn "chữ" giả: các từ là hình chữ nhật đen. Trả về toạ độ y sau đoạn và khoảng y của đoạn."""
    top = y
    for index in range(lines):
        x = left + (indent if index == 0 else 0)
        end = right if index < lines - 1 else left + 300
        while x < end - 20:
            word = rng.randint(20, 70)
            draw.rectangle([x, y, min(x + word, end), y + 16], fill=0)
            x += word + 10
        y += 16 + 12
    return y + 22, (top, y - 12)


def test_scanned_page_layout_alignment(tmp_path):
    rng = random.Random(7)
    image = Image.new("L", (1000, 1400), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle([490, 40, 510, 58], fill=0)  # số trang ở giữa, đầu trang
    y, _ = _draw_paragraph(draw, 110, 1, rng)  # tiêu đề "Điều 5. ..."
    y, clause1 = _draw_paragraph(draw, y, 3, rng)
    y, point_a = _draw_paragraph(draw, y, 2, rng)
    y, point_b = _draw_paragraph(draw, y, 4, rng)
    path = tmp_path / "p001.png"
    image.save(path)

    def unit(unit_id, parent, clause, point, content):
        return LegalUnit(unit_id=unit_id, parent_unit_id=parent, article=5, clause=clause, point=point,
                         title="Tên điều", content=content, source_pages=[1])

    a_text, b_text = "a" * 150, "b" * 330
    whole = f"{'k' * 230}\n{a_text}\n{b_text}"  # nội dung Điều/Khoản gồm cả các Điểm con
    units = [
        unit("X_D5", None, None, None, whole),
        unit("X_D5_K1", "X_D5", 1, None, whole),
        unit("X_D5_K1_a", "X_D5_K1", 1, "a", a_text),
        unit("X_D5_K1_b", "X_D5_K1", 1, "b", b_text),
    ]
    by_id = {u.unit_id: u for u in units}

    def span(unit_id):
        (rect,) = highlight._layout_rects(units, by_id[unit_id], 1, path)
        return rect[1] * 1400, rect[3] * 1400

    top, bottom = span("X_D5_K1_a")
    assert top <= point_a[0] and bottom >= point_a[1]
    assert top > clause1[0] + 30 and bottom < point_b[0]
    top, bottom = span("X_D5_K1_b")
    assert top <= point_b[0] and bottom >= point_b[1] and top > point_a[1]
    top, bottom = span("X_D5")  # cả Điều: từ dòng tiêu đề tới hết Điểm b, không gồm số trang
    assert 100 <= top <= 112 and bottom >= point_b[1]


# ---- giao diện ----

def test_viewer_draws_highlight_for_selected_unit(client_factory):
    client = client_factory()
    page = client.get(f"/documents/{DOC}/viewer", params={"page": 1, "unit": "ND254_2026_D9_K1_a"})
    assert page.status_code == 200
    assert 'class="page-highlight"' in page.text and "Vùng tô vàng" in page.text
    plain = client.get(f"/documents/{DOC}/viewer", params={"page": 1})
    assert "page-highlight" not in plain.text
    other_page = client.get(f"/documents/{DOC}/viewer", params={"page": 3, "unit": "ND254_2026_D9_K1_a"})
    assert "page-highlight" not in other_page.text


def test_unit_click_highlights_on_first_page(client_factory):
    response = client_factory().get(f"/documents/{DOC}/units/ND254_2026_D10_K1")
    assert response.status_code == 200
    assert f"/documents/{DOC}/pages/3" in response.text and 'class="page-highlight"' in response.text


def test_unit_click_shows_first_page_whole_with_highlight(client_factory):
    html = client_factory().get(f"/documents/{DOC}/units/ND254_2026_D9_K1").text  # đơn vị nằm ở trang 1–2
    assert 'class="page-stack fit"' in html and html.count('class="page-frame"') == 1
    assert f"/documents/{DOC}/pages/1" in html and 'class="page-highlight"' in html
    assert f"/documents/{DOC}/viewer?page=2&amp;zoom=0&amp;unit=ND254_2026_D9_K1" in html  # nút sang trang 2
    # khung trái không giữ mức phóng to cũ: chọn đơn vị luôn quay về "Vừa trang"
    assert 'class="page-stack fit"' in client_factory().get(f"/documents/{DOC}/units/ND254_2026_D9_K1?zoom=200").text


@pytest.fixture
def client_factory(data_dir, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    clients = []

    def make():
        settings = Settings(data_dir=data_dir, storage_dir=tmp_path / "storage", conninfo=None, auto_import_dir=None)
        client = TestClient(create_app(settings, catalog=DocumentStore(data_dir)))
        client.__enter__()  # chạy lifespan: tạo tài khoản admin
        clients.append(client)
        client.post("/account/login", data={"username": "admin", "password": "admin123"})
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
