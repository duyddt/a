import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import LegalDocument, make_document_id, make_unit_id

SAMPLE = Path(__file__).resolve().parent / "fixtures" / "data" / "ND254_2026" / "document.json"


def sample_dict():
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def unit(**kwargs):
    base = {"unit_id": "X", "content": "nội dung", "source_pages": [1]}
    base.update(kwargs)
    return base


def doc(units):
    return {
        "document_id": "ND254_2026",
        "document_no": "254/2026/NĐ-CP",
        "document_type": "Nghị định",
        "title": "t",
        "legal_units": units,
    }


def test_identifiers_follow_contract_examples():
    assert make_document_id("Nghị định", "254/2026/NĐ-CP") == "ND254_2026"
    assert make_unit_id("ND254_2026", 9, 1, "a") == "ND254_2026_D9_K1_a"
    assert make_unit_id("ND254_2026", 9) == "ND254_2026_D9"
    with pytest.raises(ValueError):
        make_document_id("Thông tư", "15/VBHN-BTC")


def test_sample_loads_and_fills_document_no_and_parents():
    loaded = LegalDocument.model_validate(sample_dict())
    by_id = {u.unit_id: u for u in loaded.legal_units}
    assert all(u.document_no == "254/2026/NĐ-CP" for u in loaded.legal_units)
    assert by_id["ND254_2026_D9"].parent_unit_id is None
    assert by_id["ND254_2026_D9_K1"].parent_unit_id == "ND254_2026_D9"
    assert by_id["ND254_2026_D9_K1_a"].parent_unit_id == "ND254_2026_D9_K1"


def test_wrong_unit_id_convention_is_rejected():
    bad = doc([unit(unit_id="ND254_2026_D9_K1_Pa", article=9, clause=1, point="a")])
    with pytest.raises(ValidationError) as exc:
        LegalDocument.model_validate(bad)
    assert "sai quy ước" in str(exc.value)


def test_duplicate_unit_id_is_rejected():
    u = unit(unit_id="ND254_2026_D9", article=9)
    with pytest.raises(ValidationError) as exc:
        LegalDocument.model_validate(doc([u, dict(u)]))
    assert "trùng" in str(exc.value)


def test_parent_after_child_is_rejected():
    child = unit(unit_id="ND254_2026_D9_K1", article=9, clause=1)
    parent = unit(unit_id="ND254_2026_D9", article=9)
    with pytest.raises(ValidationError) as exc:
        LegalDocument.model_validate(doc([child, parent]))
    assert "đứng sau" in str(exc.value)


def test_document_no_mismatch_is_rejected():
    u = unit(unit_id="ND254_2026_D9", article=9, document_no="1/2020/NĐ-CP")
    with pytest.raises(ValidationError):
        LegalDocument.model_validate(doc([u]))


def test_appendix_unit_without_article_is_accepted():
    u = unit(unit_id="ND254_2026_PL1", title="Phụ lục I")
    loaded = LegalDocument.model_validate(doc([u]))
    assert loaded.legal_units[0].parent_unit_id is None
