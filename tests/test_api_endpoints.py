"""Kiểm thử toàn bộ các API endpoint và luồng thao tác người dùng qua TestClient."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts import LegalDocument
from app.main import create_app

DOC = "ND254_2026"


@pytest.fixture
def client(data_dir):
    # create_app(data_dir) tự tạo SQLite review.db trong thư mục data_dir tạm
    app = create_app(data_dir)
    return TestClient(app)


def test_switch_user(client):
    """Kiểm tra đổi tài khoản người thẩm định."""
    resp = client.get("/switch-user/dung.nguyen", follow_redirects=False)
    assert resp.status_code == 303
    assert "reviewer_username=dung.nguyen" in resp.headers.get("set-cookie", "")


def test_approve_flow(client):
    """Kiểm tra thao tác Duyệt qua HTMX và REST API."""
    unit_id = "ND254_2026_D9"

    # 1. Thao tác HTMX
    resp = client.post(f"/documents/{DOC}/units/{unit_id}/approve", data={"zoom": 100})
    assert resp.status_code == 200
    assert "Đã duyệt" in resp.text
    assert 'hx-swap-oob="true"' in resp.text

    # 2. REST API
    resp_api = client.post(f"/api/documents/{DOC}/units/{unit_id}/approve")
    assert resp_api.status_code == 200
    data = resp_api.json()
    assert data["status"] == "success"
    assert data["decision"] == "approved"


def test_edit_text_flow(client):
    """Kiểm tra thao tác Sửa chữ (Append-only)."""
    unit_id = "ND254_2026_D9_K1"
    new_text = "Nội dung Khoản 1 Điều 9 đã được chuẩn hóa chính tả."

    # 1. Thao tác HTMX
    resp = client.post(f"/documents/{DOC}/units/{unit_id}/edit", data={"new_content": new_text, "zoom": 100})
    assert resp.status_code == 200
    assert "lần sửa chữ" in resp.text
    assert new_text in resp.text

    # 2. REST API sửa tiếp
    newer_text = "Nội dung Khoản 1 Điều 9 sửa lần hai."
    resp_api = client.post(f"/api/documents/{DOC}/units/{unit_id}/edit-text", json={"new_content": newer_text})
    assert resp_api.status_code == 200
    data = resp_api.json()
    assert data["content_after"] == newer_text


def test_flag_flow(client):
    """Kiểm tra thao tác Gắn cờ."""
    unit_id = "ND254_2026_D9_K2"
    note = "Cần kiểm tra lại biểu phí"

    # 1. Thao tác HTMX
    resp = client.post(f"/documents/{DOC}/units/{unit_id}/flag", data={"note": note, "zoom": 100})
    assert resp.status_code == 200
    assert "Gắn cờ chưa chắc chắn" in resp.text

    # 2. REST API
    resp_api = client.post(f"/api/documents/{DOC}/units/{unit_id}/flag", json={"note": "Cờ qua API"})
    assert resp_api.status_code == 200
    assert resp_api.json()["decision"] == "flagged"


def test_structural_error_flow(client):
    """Kiểm tra thao tác Báo lỗi cấu trúc."""
    unit_id = "ND254_2026_D10"

    # 1. Thao tác HTMX
    resp = client.post(
        f"/documents/{DOC}/units/{unit_id}/structural-error",
        data={"error_type": "wrong_level", "note": "Khoản bị nhầm thành Điều", "zoom": 100},
    )
    assert resp.status_code == 200
    assert "Báo lỗi cấu trúc" in resp.text

    # 2. REST API
    resp_api = client.post(
        f"/api/documents/{DOC}/units/{unit_id}/structural-error",
        json={"error_type": "need_split", "note": "Hai đoạn dính liền"},
    )
    assert resp_api.status_code == 200
    assert resp_api.json()["error_type"] == "need_split"


def test_history_endpoint(client):
    """Kiểm tra xem lịch sử sửa chữ và quyết định."""
    unit_id = "ND254_2026_D9_K1"

    # Sửa chữ và duyệt
    client.post(f"/documents/{DOC}/units/{unit_id}/edit", data={"new_content": "Bản sửa kiểm tra", "zoom": 100})
    client.post(f"/documents/{DOC}/units/{unit_id}/approve", data={"zoom": 100})

    # 1. View HTML
    resp = client.get(f"/documents/{DOC}/units/{unit_id}/history")
    assert resp.status_code == 200
    assert "Bản sửa kiểm tra" in resp.text

    # 2. API JSON
    resp_api = client.get(f"/api/documents/{DOC}/units/{unit_id}/history")
    assert resp_api.status_code == 200
    data = resp_api.json()
    assert len(data["revisions"]) >= 1
    assert data["revisions"][-1]["content_after"] == "Bản sửa kiểm tra"


def test_progress_endpoint(client):
    """Kiểm tra API trả về tiến độ thẩm định."""
    client.post(f"/api/documents/{DOC}/units/ND254_2026_D9/approve")
    client.post(f"/api/documents/{DOC}/units/ND254_2026_D10/flag", json={"note": "Cờ"})

    resp = client.get(f"/api/documents/{DOC}/progress")
    assert resp.status_code == 200
    data = resp.json()
    assert data["approved_units"] >= 1
    assert data["flagged_units"] >= 1
    assert data["total_units"] > 0


def test_export_zip(client):
    """Kiểm tra xuất file ZIP chứa văn bản thẩm định và báo cáo lỗi parser."""
    unit_id = "ND254_2026_D9"
    client.post(f"/api/documents/{DOC}/units/{unit_id}/edit-text", json={"new_content": "Nội dung sửa để xuất"})
    client.post(f"/api/documents/{DOC}/units/ND254_2026_D10/flag", json={"note": "Cờ xuất"})

    resp = client.get(f"/documents/{DOC}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    # Đọc nội dung file zip
    buffer = io.BytesIO(resp.content)
    with zipfile.ZipFile(buffer) as zf:
        namelist = zf.namelist()
        assert f"{DOC}_verified.json" in namelist
        assert f"{DOC}_parser_feedback.json" in namelist

        doc_json = json.loads(zf.read(f"{DOC}_verified.json").decode("utf-8"))
        feedback_json = json.loads(zf.read(f"{DOC}_parser_feedback.json").decode("utf-8"))

        # Xác thực nạp lại được bằng model Pydantic LegalDocument
        parsed = LegalDocument.model_validate(doc_json)
        assert parsed.document_id == DOC

        # Kiểm tra nội dung sửa đã được áp dụng
        d1 = next(u for u in parsed.legal_units if u.unit_id == unit_id)
        assert d1.content == "Nội dung sửa để xuất"

        # Kiểm tra feedback báo cáo cờ
        assert len(feedback_json["flagged_units"]) >= 1
