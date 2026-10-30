import json

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path

from app.services.store import DocumentStore, ImportProblem
from app.services.views import build_rows, first_page_of, nearest_zoom, viewer_context

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
DOC = "ND254_2026"


def render(name, **context):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))
    return env.get_template(name).render(**context)


def test_list_and_load_renders_pages_on_demand(data_dir):
    store = DocumentStore(data_dir)
    assert store.list_ids() == [DOC]
    loaded = store.get(DOC)
    assert loaded.page_count == 3
    assert not (data_dir / DOC / "pages").exists()  # nạp văn bản chưa render trang nào
    for page in (1, 2, 3):
        png = loaded.page_image(page)
        assert png.is_file()
        assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_replacing_pdf_discards_old_page_images(data_dir):
    """Chép PDF mới đè lên (ngày sửa có thể cũ hơn ảnh đã render) thì ảnh cũ không được dùng lại."""
    import os
    from reportlab.pdfgen import canvas

    loaded = DocumentStore(data_dir).get(DOC)
    before = loaded.page_image(1).read_bytes()

    pdf_path = data_dir / DOC / "source.pdf"
    pdf = canvas.Canvas(str(pdf_path))
    for number in range(5):
        pdf.drawString(100, 700, f"Trang khac hoan toan so {number + 1}")
        pdf.showPage()
    pdf.save()
    os.utime(pdf_path, (1_000_000_000, 1_000_000_000))  # giả lập chép file giữ ngày sửa rất cũ

    reloaded = DocumentStore(data_dir).get(DOC)
    assert reloaded.page_count == 5
    assert reloaded.page_image(1).read_bytes() != before


def test_unknown_and_unsafe_ids_are_not_found(data_dir):
    store = DocumentStore(data_dir)
    for bad in ("khong_co", "../ND254_2026", "ND254_2026/../x"):
        with pytest.raises(KeyError):
            store.get(bad)


def test_missing_source_pages_is_reported(data_dir):
    path = data_dir / DOC / "document.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["legal_units"][0]["source_pages"] = []
    raw["legal_units"][1]["source_pages"] = [99]
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ImportProblem) as exc:
        DocumentStore(data_dir).get(DOC)
    text = " | ".join(exc.value.problems)
    assert "ND254_2026_D9: thiếu source_pages" in text
    assert "ND254_2026_D9_K1: source_pages [99] nằm ngoài 1..3" in text


def test_invalid_json_and_contract_errors_are_reported(data_dir):
    path = data_dir / DOC / "document.json"
    path.write_text("{ khong phai json", encoding="utf-8")
    with pytest.raises(ImportProblem) as exc:
        DocumentStore(data_dir).get(DOC)
    assert "document.json" in exc.value.problems[0]

    raw = {"document_id": DOC, "document_no": "254/2026/NĐ-CP", "document_type": "Nghị định", "title": "t",
           "legal_units": [{"unit_id": "sai", "article": 9, "content": "x", "source_pages": [1]}]}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ImportProblem) as exc:
        DocumentStore(data_dir).get(DOC)
    assert "sai quy ước" in " ".join(exc.value.problems)


def test_tree_rows_and_depths(data_dir):
    loaded = DocumentStore(data_dir).get(DOC)
    rows = build_rows(loaded, "ND254_2026_D9_K1_a", 100)
    labels = [(r["label"], r["depth"]) for r in rows]
    assert labels[:5] == [("Điều 9", 0), ("Khoản 1", 1), ("Điểm a", 2), ("Điểm b", 2), ("Khoản 2", 1)]
    assert [r["unit_id"] for r in rows if r["selected"]] == ["ND254_2026_D9_K1_a"]
    assert rows[0]["url"] == "/documents/ND254_2026/units/ND254_2026_D9?zoom=100"


def test_viewer_context_clamps_and_links(data_dir):
    loaded = DocumentStore(data_dir).get(DOC)
    v = viewer_context(loaded, page=99, zoom=110, unit_id="ND254_2026_D9")
    assert v["page"] == 3 and v["next_url"] is None and v["prev_url"].startswith("/documents/ND254_2026/viewer?page=2")
    assert v["zoom"] == nearest_zoom(110) == 100
    assert [c["page"] for c in v["chips"]] == [1, 2]
    assert first_page_of(loaded.units_by_id["ND254_2026_D10"]) == 3


def test_click_unit_jumps_left_pane_to_its_page(data_dir):
    """Hành vi chính của buổi 30/10: chọn đơn vị ở trang 3 thì khung trái hiển thị trang 3."""
    loaded = DocumentStore(data_dir).get(DOC)
    selected = loaded.units_by_id["ND254_2026_D10_K1"]
    html = render(
        "unit_response.html",
        doc=loaded.doc, unit=selected, oob=True,
        rows=build_rows(loaded, selected.unit_id, 100),
        v=viewer_context(loaded, first_page_of(selected), 100, selected.unit_id),
    )
    assert 'src="/documents/ND254_2026/pages/3"' in html
    assert 'id="viewer"' in html and 'hx-swap-oob="true"' in html
    assert "Khoản 1, Điều 10" in html


def test_review_page_renders_tree_and_escapes_content(data_dir):
    loaded = DocumentStore(data_dir).get(DOC)
    loaded.doc.legal_units[0].content = "<script>alert(1)</script>"
    html = render(
        "review.html",
        doc=loaded.doc, unit=loaded.doc.legal_units[0], oob=False,
        rows=build_rows(loaded, "ND254_2026_D9", 100),
        v=viewer_context(loaded, 1, 100, "ND254_2026_D9"),
    )
    assert "Điều 9" in html and "Điểm a" in html and "Không chắc" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
