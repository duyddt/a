"""Test lớp FastAPI. Tự bỏ qua nếu chưa cài fastapi/httpx (pip install -r requirements-dev.txt)."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

DOC = "ND254_2026"


@pytest.fixture
def client(data_dir):
    return TestClient(create_app(data_dir))


def test_list_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Văn bản mẫu" in response.text and f"/documents/{DOC}" in response.text


def test_review_page_has_both_panes(client):
    response = client.get(f"/documents/{DOC}")
    assert response.status_code == 200
    assert 'id="viewer"' in response.text and 'id="tree"' in response.text and "Điều 9" in response.text


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
