"""Phần nhập không cần DB: kiểm tra đầu vào, mã băm, ghi và render tệp."""
import json

import pytest

from app.services.importer import content_hash_of, prepare_import, publish_files, stage_files
from app.services.models import ImportProblem


def problems_of(pdf, js):
    with pytest.raises(ImportProblem) as exc:
        prepare_import(pdf, js)
    return " | ".join(exc.value.problems)


def mutate(sample_json, fn):
    raw = json.loads(sample_json)
    fn(raw)
    return json.dumps(raw, ensure_ascii=False).encode("utf-8")


def test_prepare_accepts_sample(sample_pdf, sample_json):
    prepared = prepare_import(sample_pdf, sample_json)
    assert prepared.doc.document_id == "ND254_2026"
    assert (prepared.page_count, len(prepared.doc.legal_units)) == (3, 7)
    assert len(prepared.content_hash) == 64


def test_bad_json_and_contract_errors(sample_pdf, sample_json):
    assert "JSON không hợp lệ" in problems_of(sample_pdf, b"{ khong phai json")
    assert "JSON không hợp lệ" in problems_of(sample_pdf, "\xff\xfe".encode("latin-1"))
    bad_id = mutate(sample_json, lambda r: r["legal_units"][0].update(unit_id="ND254_2026_D9_K1_Pa"))
    assert "sai quy ước" in problems_of(sample_pdf, bad_id)
    no_title = mutate(sample_json, lambda r: r.pop("title"))
    assert "title" in problems_of(sample_pdf, no_title)


def test_document_id_must_be_safe_for_paths(sample_pdf, sample_json):
    def rename(raw):  # document_id hợp lệ với contract (unit_id khớp) nhưng không an toàn làm tên thư mục
        raw["document_id"] = "../x"
        for u in raw["legal_units"]:
            u["unit_id"] = u["unit_id"].replace("ND254_2026", "../x", 1)

    assert "chỉ được gồm chữ, số" in problems_of(sample_pdf, mutate(sample_json, rename))


def test_bad_pdf(sample_json):
    assert "không bắt đầu bằng %PDF" in problems_of(b"khong phai pdf", sample_json)
    assert "Không đọc được PDF" in problems_of(b"%PDF-1.4\nrac", sample_json)


def test_source_pages_and_orphan_parents(sample_pdf, sample_json):
    js = mutate(sample_json, lambda r: r["legal_units"][2].update(source_pages=[]))
    assert "ND254_2026_D9_K1_a: thiếu source_pages" in problems_of(sample_pdf, js)
    js = mutate(sample_json, lambda r: r["legal_units"][0].update(source_pages=[1, 4]))
    assert "nằm ngoài 1..3" in problems_of(sample_pdf, js)
    # Khoản 2 của Điều 9 mà thiếu Điều 9 trong file: cây bị đứt
    js = mutate(sample_json, lambda r: r.update(legal_units=[u for u in r["legal_units"] if u["unit_id"] != "ND254_2026_D9"]))
    assert "đơn vị cha ND254_2026_D9 không có trong văn bản" in problems_of(sample_pdf, js)
    js = mutate(sample_json, lambda r: r.update(legal_units=[]))
    assert "không có đơn vị nào" in problems_of(sample_pdf, js)


def test_hash_ignores_formatting_but_detects_changes(sample_pdf, sample_json):
    raw = json.loads(sample_json)
    reformatted = json.dumps(raw, ensure_ascii=False, indent=4, sort_keys=True).encode("utf-8")
    assert prepare_import(sample_pdf, sample_json).content_hash == prepare_import(sample_pdf, reformatted).content_hash

    changed = mutate(sample_json, lambda r: r["legal_units"][0].update(content="nội dung khác"))
    assert prepare_import(sample_pdf, changed).content_hash != prepare_import(sample_pdf, sample_json).content_hash
    assert content_hash_of(sample_pdf + b"x", raw) != content_hash_of(sample_pdf, raw)


def test_stage_then_publish_files(tmp_path, sample_pdf, sample_json):
    prepared = prepare_import(sample_pdf, sample_json)
    storage = tmp_path / "storage"
    staging = stage_files(prepared, storage)
    assert (staging / "source.pdf").read_bytes() == sample_pdf
    assert sorted(p.name for p in (staging / "pages").glob("p*.png")) == ["p001.png", "p002.png", "p003.png"]

    final = publish_files(staging, storage, "ND254_2026")
    assert final == storage / "ND254_2026" and not staging.exists()
    assert (final / "pages" / "p003.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # nhập bản thay thế: thư mục cũ bị thay, không lẫn trang cũ
    (final / "pages" / "p999.png").write_bytes(b"cu")
    publish_files(stage_files(prepared, storage), storage, "ND254_2026")
    assert not (final / "pages" / "p999.png").exists()
    assert [p.name for p in storage.iterdir()] == ["ND254_2026"]  # không còn thư mục .staging
