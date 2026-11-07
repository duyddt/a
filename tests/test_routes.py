"""Test lớp FastAPI ở chế độ thư mục file (không cần DB). Tự bỏ qua nếu chưa cài fastapi/httpx."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.store import DocumentStore  # noqa: E402

DOC = "ND254_2026"


@pytest.fixture
def client(data_dir, tmp_path):
    settings = Settings(data_dir=data_dir, storage_dir=tmp_path / "storage", conninfo=None, auto_import_dir=None)
    return TestClient(create_app(settings, catalog=DocumentStore(data_dir)))


def test_list_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Văn bản mẫu" in response.text and f"/documents/{DOC}" in response.text


def test_review_page_has_both_panes_and_nested_tree(client):
    response = client.get(f"/documents/{DOC}")
    assert response.status_code == 200
    assert 'id="viewer"' in response.text and 'id="tree"' in response.text and "<details" in response.text
    assert "Điều 9" in response.text and "Điều 10" in response.text


def test_page_image(client):
    response = client.get(f"/documents/{DOC}/pages/2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get(f"/documents/{DOC}/pages/4").status_code == 404
    assert client.get(f"/documents/{DOC}/pages/0").status_code == 404


def test_unit_click_returns_detail_and_jumps_page(client):
    response = client.get(f"/documents/{DOC}/units/ND254_2026_D10_K1")
    assert response.status_code == 200
    assert f"/documents/{DOC}/pages/3" in response.text
    assert 'hx-swap-oob="true"' in response.text


def test_viewer_partial_changes_page_and_zoom(client):
    response = client.get(f"/documents/{DOC}/viewer", params={"page": 2, "zoom": 150})
    assert response.status_code == 200
    assert f"/documents/{DOC}/pages/2" in response.text and "width: 150%" in response.text


def test_not_found_and_bad_ids(client):
    assert client.get("/documents/khong_co").status_code == 404
    assert client.get(f"/documents/{DOC}/units/khong_co").status_code == 404
    assert client.get(f"/documents/{DOC}?unit=khong_co").status_code == 404


def test_import_error_page(client, data_dir):
    path = data_dir / DOC / "document.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["legal_units"][0]["source_pages"] = []
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    response = client.get(f"/documents/{DOC}")
    assert response.status_code == 422
    assert "thiếu source_pages" in response.text


# ---- API JSON ----

def test_api_list_documents(client):
    response = client.get("/api/documents")
    assert response.status_code == 200
    assert response.json() == [{
        "document_id": DOC, "document_no": "254/2026/NĐ-CP", "document_type": "Nghị định",
        "title": "Văn bản mẫu để thử giao diện (dữ liệu minh họa, không phải nguyên văn pháp luật)",
        "page_count": 3, "unit_count": 7,
    }]


def test_api_document_detail_has_ordered_tree_with_source_pages(client):
    body = client.get(f"/api/documents/{DOC}").json()
    assert body["page_count"] == 3 and body["unit_count"] == 7
    d9, d10 = body["tree"]
    assert (d9["unit_id"], d9["label"], d9["source_pages"]) == ("ND254_2026_D9", "Điều 9", [1, 2])
    k1, k2 = d9["children"]
    assert [c["label"] for c in k1["children"]] == ["Điểm a", "Điểm b"]
    assert [c["source_pages"] for c in k1["children"]] == [[1], [2]]
    assert k1["children"][0]["is_confident"] is False
    assert (k2["label"], k2["source_pages"], k2["children"]) == ("Khoản 2", [2], [])
    assert d10["children"][0]["unit_id"] == "ND254_2026_D10_K1" and d10["source_pages"] == [3]


def test_api_unit_and_page_and_errors(client):
    unit = client.get(f"/api/documents/{DOC}/units/ND254_2026_D9_K1_b").json()
    assert unit["point"] == "b" and unit["parent_unit_id"] == "ND254_2026_D9_K1" and unit["source_pages"] == [2]
    assert unit["document_no"] == "254/2026/NĐ-CP"

    page = client.get(f"/api/documents/{DOC}/pages/3")
    assert page.status_code == 200 and page.content[:8] == b"\x89PNG\r\n\x1a\n"

    assert client.get("/api/documents/khong_co").status_code == 404
    assert client.get(f"/api/documents/{DOC}/units/khong_co").status_code == 404
    assert client.get(f"/api/documents/{DOC}/pages/4").status_code == 404


def test_api_import_needs_postgres_in_file_mode(client, sample_pdf, sample_json):
    response = client.post("/api/documents", files={"pdf": ("source.pdf", sample_pdf), "document": ("document.json", sample_json)})
    assert response.status_code == 501


def test_openapi_documents_every_endpoint(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) >= {
        "/api/documents", "/api/documents/{document_id}",
        "/api/documents/{document_id}/units/{unit_id}", "/api/documents/{document_id}/pages/{page}",
    }
    assert "post" in paths["/api/documents"]


# ---- thẩm định ở chế độ thư mục file: xem được, không lưu được ----

def test_file_mode_has_no_review_buttons_and_review_api_is_501(client):
    page = client.get(f"/documents/{DOC}")
    assert "Duyệt" not in page.text and "Xuất JSON đã duyệt" not in page.text and "PostgreSQL" in page.text
    body = {"action": "approve", "reviewer": "Duy"}
    assert client.post(f"/api/documents/{DOC}/units/ND254_2026_D9/reviews", json=body).status_code == 501
    assert client.get(f"/api/documents/{DOC}/export").status_code == 501
    form = client.post(f"/documents/{DOC}/units/ND254_2026_D9/review", data={"action": "approve", "reviewer": "Duy"})
    assert form.status_code == 200 and "PostgreSQL" in form.text


def test_file_mode_api_lists_have_no_progress_and_history_is_empty(client):
    assert client.get(f"/api/documents/{DOC}").json()["progress"] is None
    assert client.get(f"/api/documents/{DOC}/units/ND254_2026_D9/reviews").json() == []
