"""Văn bản thật data/ND254_2026 (bản nháp từ chép chữ scan): phải nạp được và khớp với script dựng.

Các con số dưới đây là kết quả đã kiểm tra khi dựng (45 Điều in trong văn bản). Chúng bảo vệ khỏi việc sửa script
làm đổi cấu trúc mà không ai để ý; chúng KHÔNG chứng minh bản chép chữ đúng 100%, việc đó là của người soát.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.contracts import LegalDocument
from app.services.store import check_source_pages

ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT / "data" / "ND254_2026"

pytestmark = pytest.mark.skipif(not (DOC_DIR / "document.json").is_file(), reason="chưa có data/ND254_2026")


def load():
    return LegalDocument.model_validate(json.loads((DOC_DIR / "document.json").read_text(encoding="utf-8")))


def test_loads_and_source_pages_valid_for_55_pages():
    doc = load()
    assert check_source_pages(doc, 55) == []


def test_structure_counts():
    units = load().legal_units
    articles = [u for u in units if u.article is not None and u.clause is None]
    clauses = [u for u in units if u.clause is not None and u.point is None]
    points = [u for u in units if u.point is not None]
    assert [a.article for a in articles] == list(range(1, 46))
    assert (len(clauses), len(points)) == (181, 189)
    assert [u.unit_id for u in units if u.article is None] == ["ND254_2026_PL1"]


def test_units_are_flagged_for_review_where_unsure():
    flagged = {u.unit_id for u in load().legal_units if not u.is_confident}
    assert flagged == {"ND254_2026_D9_K4_đ", "ND254_2026_PL1"}


def test_content_has_no_ocr_markers_and_excludes_article_heading():
    for unit in load().legal_units:
        assert "[?]" not in unit.content and "[không đọc được]" not in unit.content
        if unit.article is not None and unit.clause is None:
            assert not unit.content.startswith(f"Điều {unit.article}.")


def test_committed_json_matches_script_output(tmp_path):
    out = tmp_path / "document.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_legal_document.py"),
         "--pages-dir", str(DOC_DIR / "ocr_pages"), "--out", str(out)],
        check=True, capture_output=True, cwd=ROOT,
    )
    assert out.read_text(encoding="utf-8") == (DOC_DIR / "document.json").read_text(encoding="utf-8")
