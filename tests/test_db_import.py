"""Nhập văn bản vào PostgreSQL thật và gọi API. Cần PostgreSQL và biến môi trường TEST_DATABASE_URL.

Ví dụ (PowerShell, DB do docker compose chạy, thêm `ports: ["5432:5432"]` cho dịch vụ db khi cần):
    $env:TEST_DATABASE_URL = "postgresql://review:<mật khẩu>@localhost:5432/review_test"
    pytest tests/test_db_import.py

CẢNH BÁO: test XÓA các bảng review_event, document, legal_unit, schema_migrations trong DB đó. Dùng một DB riêng, đừng trỏ vào DB đang có dữ liệu thật.
"""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
psycopg = pytest.importorskip("psycopg")

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="đặt TEST_DATABASE_URL để chạy test PostgreSQL")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

DOC = "ND254_2026"


@pytest.fixture
def client(tmp_path):
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS reviewer_progress, reviewer_account, review_event, legal_unit, document, schema_migrations CASCADE")
    storage = tmp_path / "storage"
    app = create_app(Settings(data_dir=tmp_path / "none", storage_dir=storage, conninfo=URL, auto_import_dir=None))
    with TestClient(app) as test_client:
        test_client.storage = storage
        yield test_client


def upload(client, pdf, js, replace=False):
    data = {"replace": "true"} if replace else {}
    return client.post("/api/documents", files={"pdf": ("source.pdf", pdf), "document": ("document.json", js)}, data=data)


def count(table):
    with psycopg.connect(URL) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_migration_created_tables(client):
    assert count("schema_migrations") >= 2 and count("document") == 0 and count("review_event") == 0


def test_import_then_api_returns_tree_pages_and_units(client, sample_pdf, sample_json):
    response = upload(client, sample_pdf, sample_json)
    assert response.status_code == 201
    assert response.json() == {"document_id": DOC, "status": "created", "unit_count": 7, "page_count": 3}
    assert sorted(p.name for p in (client.storage / DOC / "pages").glob("p*.png")) == ["p001.png", "p002.png", "p003.png"]

    listing = client.get("/api/documents").json()
    assert [(d["document_id"], d["unit_count"], d["page_count"]) for d in listing] == [(DOC, 7, 3)]

    tree = client.get(f"/api/documents/{DOC}").json()["tree"]
    assert [n["unit_id"] for n in tree] == ["ND254_2026_D9", "ND254_2026_D10"]
    k1 = tree[0]["children"][0]
    assert [c["unit_id"] for c in k1["children"]] == ["ND254_2026_D9_K1_a", "ND254_2026_D9_K1_b"]
    assert k1["children"][0]["source_pages"] == [1] and k1["children"][0]["is_confident"] is False

    unit = client.get(f"/api/documents/{DOC}/units/ND254_2026_D9_K1_a").json()
    assert unit["parent_unit_id"] == "ND254_2026_D9_K1" and "Điểm a" in unit["content"]

    page = client.get(f"/api/documents/{DOC}/pages/2")
    assert page.status_code == 200 and page.content[:8] == b"\x89PNG\r\n\x1a\n"

    # giao diện HTML đọc cùng nguồn
    assert client.get(f"/documents/{DOC}/units/ND254_2026_D10_K1").text.count(f"/documents/{DOC}/pages/3") >= 1


def test_tree_order_follows_file_even_when_unit_ids_sort_differently(client, sample_pdf, sample_json):
    """Điều 10 sắp trước Điều 9 theo chữ cái; cây phải giữ thứ tự trong file (Điều 9 rồi Điều 10)."""
    upload(client, sample_pdf, sample_json)
    ids = [n["unit_id"] for n in client.get(f"/api/documents/{DOC}").json()["tree"]]
    assert ids == ["ND254_2026_D9", "ND254_2026_D10"] and ids != sorted(ids)


def test_reimport_same_content_is_a_noop(client, sample_pdf, sample_json):
    assert upload(client, sample_pdf, sample_json).status_code == 201
    again = upload(client, sample_pdf, sample_json)
    assert again.status_code == 200 and again.json()["status"] == "unchanged"
    reformatted = json.dumps(json.loads(sample_json), ensure_ascii=False, indent=4).encode("utf-8")
    assert upload(client, sample_pdf, reformatted).json()["status"] == "unchanged"
    assert (count("document"), count("legal_unit")) == (1, 7)


def test_same_id_different_content_conflicts_unless_replace(client, sample_pdf, sample_json):
    upload(client, sample_pdf, sample_json)
    raw = json.loads(sample_json)
    raw["legal_units"][0]["content"] = "nội dung đã sửa"
    changed = json.dumps(raw, ensure_ascii=False).encode("utf-8")

    assert upload(client, sample_pdf, changed).status_code == 409
    assert client.get(f"/api/documents/{DOC}/units/ND254_2026_D9").json()["content"] != "nội dung đã sửa"

    replaced = upload(client, sample_pdf, changed, replace=True)
    assert replaced.status_code == 201 and replaced.json()["status"] == "replaced"
    assert client.get(f"/api/documents/{DOC}/units/ND254_2026_D9").json()["content"] == "nội dung đã sửa"
    assert (count("document"), count("legal_unit")) == (1, 7)  # không nhân đôi


def test_invalid_import_is_rejected_with_problems_and_stores_nothing(client, sample_pdf, sample_json):
    raw = json.loads(sample_json)
    raw["legal_units"][2]["source_pages"] = []
    response = upload(client, sample_pdf, json.dumps(raw, ensure_ascii=False).encode("utf-8"))
    assert response.status_code == 422
    assert any("thiếu source_pages" in p for p in response.json()["problems"])
    assert (count("document"), count("legal_unit")) == (0, 0)
    assert not (client.storage / DOC).exists()

    assert upload(client, b"khong phai pdf", sample_json).status_code == 422
    assert upload(client, sample_pdf, b"{ hong").status_code == 422


def test_unknown_document_is_404(client):
    assert client.get("/api/documents/khong_co").status_code == 404
    assert client.get("/documents/khong_co").status_code == 404


def test_cli_import_dir_is_idempotent(client, tmp_path, monkeypatch, capsys, data_dir):
    from app.cli import main

    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "cli-storage"))
    assert main(["import-dir", str(data_dir / DOC)]) == 0
    assert "created" in capsys.readouterr().out
    assert main(["import-dir", str(data_dir / DOC)]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert count("document") == 1


# ---- thẩm định ----

UNIT = "ND254_2026_D9_K1_a"
REVIEWS = f"/api/documents/{DOC}/units/{UNIT}/reviews"


def review(client, action, reviewer="Duy", unit=UNIT, **extra):
    return client.post(f"/api/documents/{DOC}/units/{unit}/reviews", json={"action": action, "reviewer": reviewer, **extra})


@pytest.fixture
def loaded(client, sample_pdf, sample_json):
    assert upload(client, sample_pdf, sample_json).status_code == 201
    return client


def test_review_actions_update_status_progress_and_tree(loaded):
    client = loaded
    assert client.get(f"/api/documents/{DOC}").json()["progress"]["unreviewed"] == 7

    result = review(client, "approve")
    assert result.status_code == 201
    body = result.json()
    assert body["review_status"] == "approved" and body["progress"]["approved"] == 1 and body["progress"]["percent_approved"] == 14

    def node_status():
        tree = client.get(f"/api/documents/{DOC}").json()["tree"]
        return tree[0]["children"][0]["children"][0]["review_status"]

    assert node_status() == "approved"
    assert review(client, "flag", note="lệch với ảnh trang 1").json()["review_status"] == "flagged"
    assert node_status() == "flagged"
    assert review(client, "structure_error", note="đây là Khoản chứ không phải Điểm").json()["review_status"] == "structure_error"
    assert review(client, "reset").json()["review_status"] == "unreviewed"

    listing = client.get("/api/documents").json()[0]
    assert listing["progress"]["total"] == 7 and listing["progress"]["approved"] == 0


def test_review_is_append_only_and_history_is_newest_first(loaded):
    client = loaded
    review(client, "flag", reviewer="Dũng", note="nghi sai")
    review(client, "approve", reviewer="Duy")
    history = client.get(REVIEWS).json()
    assert [(h["action"], h["reviewer"]) for h in history] == [("approve", "Duy"), ("flag", "Dũng")]
    assert history[1]["note"] == "nghi sai" and history[0]["created_at"]
    assert count("review_event") == 2
    with psycopg.connect(URL) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE review_event SET reviewer = 'X'")
    with psycopg.connect(URL) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM review_event")
    assert count("review_event") == 2


def test_edit_text_persists_keeps_original_and_cancels_approval(loaded):
    client = loaded
    original = client.get(f"/api/documents/{DOC}/units/{UNIT}").json()["content"]
    review(client, "approve")
    result = review(client, "edit", content="Nội dung đã sửa cho đúng ảnh trang")
    assert result.status_code == 201 and result.json()["review_status"] == "unreviewed"
    assert client.get(f"/api/documents/{DOC}/units/{UNIT}").json()["content"] == "Nội dung đã sửa cho đúng ảnh trang"
    edit = client.get(REVIEWS).json()[0]
    assert edit["content_before"] == original and edit["content_after"] == "Nội dung đã sửa cho đúng ảnh trang"
    with psycopg.connect(URL) as conn:  # bản gốc trong legal_unit không bị đụng tới
        assert conn.execute("SELECT content FROM legal_unit WHERE unit_id = %s", (UNIT,)).fetchone()[0] == original


def test_review_rejects_bad_requests(loaded):
    client = loaded
    assert review(client, "flag").status_code == 422  # thiếu lý do
    assert "cần ghi chú" in " ".join(review(client, "structure_error").json()["problems"])
    assert review(client, "approve", reviewer="  ").status_code == 422
    assert review(client, "edit").status_code == 422  # thiếu nội dung
    original = client.get(f"/api/documents/{DOC}/units/{UNIT}").json()["content"]
    assert review(client, "edit", content=original).status_code == 422  # không đổi gì
    assert review(client, "xoa").status_code == 422
    assert review(client, "approve", unit="khong_co").status_code == 404
    assert count("review_event") == 0


def test_reviews_survive_reload_and_replace_import(loaded, sample_pdf, sample_json):
    client = loaded
    review(client, "approve")
    review(client, "flag", unit="ND254_2026_D10", note="x")
    again = client.get(f"/api/documents/{DOC}").json()  # "tải lại trang": đọc lại từ DB
    assert again["progress"]["approved"] == 1 and again["progress"]["flagged"] == 1

    raw = json.loads(sample_json)
    raw["legal_units"][0]["content"] = "bản nhập lại"
    replaced = upload(client, sample_pdf, json.dumps(raw, ensure_ascii=False).encode("utf-8"), replace=True)
    assert replaced.json()["status"] == "replaced"
    assert client.get(f"/api/documents/{DOC}").json()["progress"]["approved"] == 1  # lịch sử còn nguyên
    assert count("review_event") == 2


def test_export_contains_only_approved_units_with_edited_text(loaded):
    client = loaded
    review(client, "approve", unit="ND254_2026_D9")
    review(client, "edit", content="chữ đã sửa")
    review(client, "approve")
    review(client, "approve", unit="ND254_2026_D10")
    review(client, "flag", unit="ND254_2026_D10", note="x")  # gắn cờ sau khi duyệt: không xuất

    response = client.get(f"/api/documents/{DOC}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"] and "_approved.json" in response.headers["content-disposition"]
    assert response.headers["x-export-units"] == "2/7"
    data = response.json()
    assert [u["unit_id"] for u in data["legal_units"]] == ["ND254_2026_D9", UNIT]
    assert data["legal_units"][1]["content"] == "chữ đã sửa" and data["document_id"] == DOC


def test_html_review_flow(loaded):
    client = loaded
    url = f"/documents/{DOC}/units/{UNIT}/review"
    bad = client.post(url, data={"action": "flag", "reviewer": "Duy", "note": ""})
    assert bad.status_code == 200 and "cần ghi chú" in bad.text and count("review_event") == 0

    good = client.post(url, data={"action": "approve", "reviewer": "Dũng", "note": ""})
    assert good.status_code == 200
    assert f'id="dot-{UNIT}"' in good.text and "st-approved" in good.text and 'id="progress"' in good.text
    assert "reviewer=" in good.headers["set-cookie"]

    page = client.get(f"/documents/{DOC}?unit={UNIT}")
    assert "Đã duyệt" in page.text and "Dũng" in page.text and "Xuất JSON đã duyệt" in page.text
    assert client.get("/").text.count("Đã duyệt 1/7") == 1
