"""Hướng dẫn sử dụng: trang /help (static/guide/), tour trong app (static/tour.js, static/tours.json), menu Hướng dẫn.
Chạy ở chế độ thư mục file như test_routes.py. Tự bỏ qua nếu chưa cài fastapi/httpx."""
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from app.config import Settings  # noqa: E402
from app.services.store import DocumentStore  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

APP_DIR = Path(__file__).resolve().parent.parent / "app"
STATIC = APP_DIR / "static"
GUIDE = STATIC / "guide"
ADMIN_PW = "admin-pass-for-tests"
SCREENS = {"home", "library", "review", "assign", "users", "stats"}
ROLES = {"admin", "approver", "reviewer"}


@pytest.fixture
def anonymous(data_dir, tmp_path):
    settings = Settings(data_dir=data_dir, storage_dir=tmp_path / "storage", conninfo=None, auto_import_dir=None,
                        admin_password=ADMIN_PW)
    with TestClient(create_app(settings, catalog=DocumentStore(data_dir))) as test_client:
        yield test_client


@pytest.fixture
def client(anonymous):
    login = anonymous.post("/account/login", data={"username": "admin", "password": ADMIN_PW}, follow_redirects=False)
    assert login.status_code == 303
    return anonymous


def test_help_redirects_without_login(anonymous):
    response = anonymous.get("/help", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/static/guide/index.html"


@pytest.mark.parametrize("path", [
    "/static/guide/index.html", "/static/guide/guide.js", "/static/guide/guide.css", "/static/guide/data.js",
    "/static/tour.js", "/static/tours.json",
])
def test_guide_files_are_public(anonymous, path):
    assert anonymous.get(path).status_code == 200


def test_login_page_links_to_guide(anonymous):
    html = anonymous.get("/login").text
    assert 'href="/help"' in html
    assert 'data-screen="login"' in html and 'data-role="none"' in html


def test_pages_carry_screen_role_and_help_menu(client):
    html = client.get("/documents").text
    assert 'data-screen="library"' in html and 'data-role="admin"' in html
    assert 'class="menu help-menu"' in html and "data-tour-start" in html
    assert 'id="shortcuts-dialog"' in html
    assert "/static/tour.js" in html
    review = client.get("/documents/ND254_2026").text
    assert 'data-screen="review"' in review


def _templates_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in (APP_DIR / "templates").rglob("*.html"))


def _selector_tokens(selector: str) -> list[str]:
    selector = re.sub(r'"[^"]*"', "", selector)  # giá trị thuộc tính có thể ghép trong template ({{ id }}-title)
    return re.findall(r"#([\w-]+)", selector) + re.findall(r"\.([\w-]+)", selector) + re.findall(r"\[([\w-]+)", selector)


def test_tours_point_at_elements_that_exist_in_templates():
    """Sửa template mà quên tour: bước tour sẽ trỏ vào phần tử không còn (tour.js bỏ qua lặng lẽ). Test bắt việc này."""
    data = json.loads((STATIC / "tours.json").read_text(encoding="utf-8"))
    tours = {t["id"]: t for t in data["tours"]}
    templates = _templates_text()
    missing = []
    for tour in data["tours"]:
        assert tour.get("screen", "") in SCREENS | {""}, tour["id"]
        assert set(tour.get("roles", [])) <= ROLES, tour["id"]
        assert tour["steps"], tour["id"]
        for step in tour["steps"]:
            if "include" in step:
                assert step["include"] in tours, f'{tour["id"]}: không có tour {step["include"]}'
                continue
            assert step.get("title") and step.get("body"), f'{tour["id"]}: bước thiếu tiêu đề hoặc nội dung'
            assert set(step.get("roles", [])) <= ROLES
            if step.get("advanceOn"):
                assert step["advanceOn"]["event"] and step["advanceOn"]["selector"]
            for token in _selector_tokens(step.get("selector", "")):
                if token not in templates:
                    missing.append(f'{tour["id"]}: {step["selector"]} ({token})')
    assert not missing, "Selector không còn trong templates:\n" + "\n".join(missing)


def test_every_screen_with_tour_has_one_tour_per_role():
    data = json.loads((STATIC / "tours.json").read_text(encoding="utf-8"))
    seen = set()
    for tour in data["tours"]:
        if not tour.get("screen"):
            continue
        for role in tour.get("roles", sorted(ROLES)):
            key = (tour["screen"], role)
            assert key not in seen, f"Hai tour cho {key}: tour.js chỉ chạy tour đầu tiên"
            seen.add(key)


def test_guide_images_exist():
    data_js = (GUIDE / "data.js").read_text(encoding="utf-8")
    images = set(re.findall(r"img/[\w.-]+\.webp", data_js))
    assert images, "data.js không tham chiếu ảnh nào"
    assert not [img for img in images if not (GUIDE / img).is_file()]
    exercises = sorted(img for img in images if img.startswith("img/ex-"))
    script = (APP_DIR.parent / "scripts" / "build_guide_exercises.py").read_text(encoding="utf-8")
    assert len(exercises) == len(re.findall(r'^\s+\(\d+, "', script, re.M)), "Số bài trong data.js khác EXERCISES"
