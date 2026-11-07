from app.contracts import LegalDocument
from app.services.tree import build_tree, unit_label, unit_snippet


def unit(unit_id, **kwargs):
    base = {"unit_id": unit_id, "content": "nội dung", "source_pages": [1]}
    base.update(kwargs)
    return base


def doc(units):
    return LegalDocument.model_validate(
        {"document_id": "ND254_2026", "document_no": "254/2026/NĐ-CP", "document_type": "Nghị định", "title": "t", "legal_units": units}
    )


def sample():
    return doc([
        unit("ND254_2026_D9", article=9, title="Tên Điều 9", content="1. a\n2. b"),
        unit("ND254_2026_D9_K1", article=9, clause=1, content="Khoản một\na) x"),
        unit("ND254_2026_D9_K1_a", article=9, clause=1, point="a", content="a) x", is_confident=False),
        unit("ND254_2026_D9_K1_đ", article=9, clause=1, point="đ", content="đ) y"),
        unit("ND254_2026_D9_K2", article=9, clause=2, content="Khoản hai"),
        unit("ND254_2026_D10", article=10, title="Tên Điều 10", content="1. z"),
        unit("ND254_2026_PL1", title="Phụ lục: Nội dung của hóa đơn", content="Nội dung phụ lục"),
    ])


def test_tree_nests_by_parent_and_keeps_file_order():
    tree = build_tree(sample().legal_units)
    assert [n["unit_id"] for n in tree] == ["ND254_2026_D9", "ND254_2026_D10", "ND254_2026_PL1"]
    d9 = tree[0]
    assert [c["unit_id"] for c in d9["children"]] == ["ND254_2026_D9_K1", "ND254_2026_D9_K2"]
    assert [c["unit_id"] for c in d9["children"][0]["children"]] == ["ND254_2026_D9_K1_a", "ND254_2026_D9_K1_đ"]
    assert d9["children"][1]["children"] == []


def test_labels_snippets_and_flags():
    d = sample()
    assert [unit_label(u) for u in d.legal_units] == [
        "Điều 9", "Khoản 1", "Điểm a", "Điểm đ", "Khoản 2", "Điều 10", "Phụ lục: Nội dung của hóa đơn",
    ]
    assert unit_snippet(d.legal_units[0]) == "Tên Điều 9"  # Điều: tên Điều, không lặp nội dung các Khoản
    assert unit_snippet(d.legal_units[1]) == "Khoản một a) x"  # xuống dòng gộp thành khoảng trắng
    assert len(unit_snippet(doc([unit("ND254_2026_PL1", content="x" * 500)]).legal_units[0])) == 80
    point_a = build_tree(d.legal_units)[0]["children"][0]["children"][0]
    assert point_a["is_confident"] is False and point_a["review_status"] == "unreviewed"
    assert point_a["review_status_label"] == "Chưa soát"


def test_node_carries_source_pages_for_page_jump():
    d = doc([unit("ND254_2026_D9", article=9, title="t", source_pages=[3, 4])])
    assert build_tree(d.legal_units)[0]["source_pages"] == [3, 4]
