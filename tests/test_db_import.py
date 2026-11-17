"""Nhập văn bản vào PostgreSQL thật và gọi API. Cần PostgreSQL và biến môi trường TEST_DATABASE_URL.

Ví dụ (PowerShell, DB do docker compose chạy, thêm `ports: ["5432:5432"]` cho dịch vụ db khi cần):
    $env:TEST_DATABASE_URL = "postgresql://review:<mật khẩu>@localhost:5432/review_test"
    pytest tests/test_db_import.py

CẢNH BÁO: test XÓA mọi bảng của ứng dụng (review_event, document, legal_unit, category…) trong DB đó. Dùng một DB riêng, đừng trỏ vào DB đang có dữ liệu thật.
"""
import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
psycopg = pytest.importorskip("psycopg")

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="đặt TEST_DATABASE_URL để chạy test PostgreSQL")

from app.config import Settings  # noqa: E402
from app.services.models import ImportProblem  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

DOC = "ND254_2026"
ADMIN_PW = "admin-pass-for-tests"
USER_PW = "user-pass-for-tests"


@pytest.fixture
def client(tmp_path):
    # Hàm review_event_append_only() KHÔNG bị xóa ở đây: migration phải chạy lại được khi hàm đã có sẵn.
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS workflow_event, page_words, document_category, category, reviewer_progress, reviewer_account, "
                     "review_event, document_pdf, "
                     "legal_unit, document, schema_migrations CASCADE")
    storage = tmp_path / "storage"
    app = create_app(Settings(data_dir=tmp_path / "none", storage_dir=storage, conninfo=URL, auto_import_dir=None,
                              admin_password=ADMIN_PW))
    with TestClient(app) as test_client:
        test_client.storage = storage
        test_client.auth = ("admin", ADMIN_PW)  # mặc định gọi API bằng HTTP Basic của admin
        yield test_client


def add_account(client, username, display_name=None, role="reviewer", approved=True):
    client.app.state.accounts.create(username, display_name or username, USER_PW, role=role, is_approved=approved)
    return username, USER_PW


def assign(client, *editors, approver=None, document_id=None):
    """Admin giao văn bản cho người chỉnh sửa (và người phê duyệt), như trang /admin/assign."""
    catalog, admin = client.app.state.catalog, {"username": "admin", "role": "admin"}
    for name in editors:
        catalog.record_workflow(document_id or DOC, "assign", admin, target_user=name, target_role="editor")
    if approver:
        catalog.record_workflow(document_id or DOC, "assign", admin, target_user=approver, target_role="approver")


def workflow(client, action, auth=None, **extra):
    kwargs = {"auth": auth} if auth else {}
    return client.post(f"/api/documents/{DOC}/workflow", json={"action": action, **extra}, **kwargs)


def login(client, username, password):
    return client.post("/account/login", data={"username": username, "password": password}, follow_redirects=False)


def upload(client, pdf, js, replace=False, data=None, **kwargs):
    data = {**(data or {}), **({"replace": "true"} if replace else {})}
    return client.post("/api/documents", files={"pdf": ("source.pdf", pdf), "document": ("document.json", js)}, data=data,
                       **kwargs)


def count(table):
    with psycopg.connect(URL) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def stored_pdf(document_id):
    with psycopg.connect(URL) as conn:
        row = conn.execute(
            "SELECT p.pdf FROM document_pdf p JOIN document d ON d.id = p.document_pk WHERE d.document_id = %s",
            (document_id,),
        ).fetchone()
    return bytes(row[0]) if row else None


def blank_pdf(pages: int) -> bytes:
    import io

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            pdf.new_page(595, 842)
        buffer = io.BytesIO()
        pdf.save(buffer)
    finally:
        pdf.close()
    return buffer.getvalue()


def test_migration_created_tables(client):
    assert count("schema_migrations") >= 2 and count("document") == 0 and count("review_event") == 0


def test_import_then_api_returns_tree_pages_and_units(client, sample_pdf, sample_json):
    response = upload(client, sample_pdf, sample_json)
    assert response.status_code == 201
    assert response.json() == {"document_id": DOC, "status": "created", "unit_count": 7, "page_count": 3}
    # PDF gốc nằm trong PostgreSQL; lúc nhập không ghi gì ra đĩa (ảnh trang chỉ dựng khi có người xem)
    assert stored_pdf(DOC) == sample_pdf
    assert not (client.storage / DOC).exists()

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


def test_page_cache_is_rebuilt_from_postgresql(client, sample_pdf, sample_json):
    """Thư mục STORAGE_DIR chỉ là bộ đệm: xóa đi thì ảnh trang được dựng lại từ PDF trong PostgreSQL."""
    upload(client, sample_pdf, sample_json)
    assert client.get(f"/api/documents/{DOC}/pages/2").status_code == 200
    assert (client.storage / DOC / "pages" / "p002.png").is_file()

    shutil.rmtree(client.storage / DOC)
    page = client.get(f"/api/documents/{DOC}/pages/2")
    assert page.status_code == 200 and page.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (client.storage / DOC / "source.pdf").read_bytes() == sample_pdf


def test_replacing_a_document_refreshes_the_page_cache(client, sample_pdf, sample_json):
    upload(client, sample_pdf, sample_json)
    client.get(f"/api/documents/{DOC}/pages/1")  # bộ đệm của bản cũ
    other_pdf = blank_pdf(3)
    assert upload(client, other_pdf, sample_json, replace=True).json()["status"] == "replaced"
    assert stored_pdf(DOC) == other_pdf
    assert client.get(f"/api/documents/{DOC}/pages/1").status_code == 200
    assert (client.storage / DOC / "source.pdf").read_bytes() == other_pdf


def test_reimport_stores_the_pdf_of_a_document_imported_before_pdfs_lived_in_postgresql(client, sample_pdf, sample_json):
    upload(client, sample_pdf, sample_json)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DELETE FROM document_pdf")  # như dòng nhập từ phiên bản cũ, chỉ có PDF trên đĩa
    assert upload(client, sample_pdf, sample_json).json()["status"] == "unchanged"
    assert stored_pdf(DOC) == sample_pdf


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


def test_cli_import_pdfs_imports_a_dataset_folder(client, tmp_path, monkeypatch, capsys, sample_pdf):
    """Bộ dữ liệu chỉ có PDF (như 'bo du lieu'): tự tách cấu trúc, bỏ bản trùng, từ chối bản scan khi không có OCR."""
    from app.cli import main
    from app.services import pdf_parser

    folder = tmp_path / "bo du lieu"
    (folder / "I. Thuế").mkdir(parents=True)
    (folder / "II. Hóa đơn").mkdir()
    (folder / "I. Thuế" / "ND254_2026.pdf").write_bytes(sample_pdf)
    (folder / "II. Hóa đơn" / "ND254_2026.pdf").write_bytes(sample_pdf)  # cùng một tệp ở hai chủ đề
    (folder / "II. Hóa đơn" / "SCAN_2026.pdf").write_bytes(blank_pdf(2))
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setattr(pdf_parser, "ocr_available", lambda: False)

    assert main(["import-pdfs", str(folder)]) == 1  # có tệp bị từ chối
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("I. Thuế/ND254_2026.pdf: created (7 đơn vị, 3 trang")
    assert lines[0].endswith(" · danh mục I. Thuế")
    assert lines[1] == "II. Hóa đơn/ND254_2026.pdf: trùng với I. Thuế/ND254_2026.pdf, bỏ qua · danh mục II. Hóa đơn"
    assert lines[2].startswith("II. Hóa đơn/SCAN_2026.pdf: TỪ CHỐI: ") and "OCR" in lines[2]
    assert (count("document"), count("document_pdf")) == (1, 1)

    # Chạy lại (ví dụ sau khi bị ngắt): văn bản đã có thì bỏ qua TRƯỚC khi tách chữ/OCR
    built = []

    def recording_builder(pdf_bytes, filename, words_out=None):
        built.append(filename)
        raise ImportProblem(filename, ["dừng ở đây"])

    monkeypatch.setattr(pdf_parser, "auto_build_json_from_pdf", recording_builder)
    main(["import-pdfs", str(folder)])
    assert capsys.readouterr().out.splitlines()[0] == (
        "I. Thuế/ND254_2026.pdf: đã có ND254_2026, bỏ qua (dùng --replace để nhập lại) · danh mục I. Thuế")
    assert built == ["SCAN_2026.pdf"]


# ---- danh mục (thư mục chủ đề của bộ dữ liệu) ----


def variant(pdf: bytes, tag: str) -> bytes:
    """Cùng nội dung, khác byte (khác sha256): chú thích PDF sau %%EOF, pdfium vẫn đọc bình thường."""
    return pdf + f"\n% {tag}\n".encode()


@pytest.fixture
def dataset(tmp_path, monkeypatch, sample_pdf, sample_json):
    """Bộ dữ liệu như 'bo du lieu': thư mục "I. …", "II. …", tệp danh mục Excel; mọi PDF là bản mẫu ND254 đổi mã."""
    from app.services import pdf_parser
    from tests.test_categories import HEADER, write_xlsx

    def fake_builder(pdf_bytes, filename, words_out=None):  # không OCR: lấy JSON mẫu, đổi mã theo tên tệp
        fake_builder.calls.append(filename)
        return sample_json.decode().replace(DOC, pdf_parser.document_id_from_filename(filename)).encode()

    fake_builder.calls = []
    monkeypatch.setattr(pdf_parser, "auto_build_json_from_pdf", fake_builder)
    monkeypatch.setenv("DATABASE_URL", URL)

    root = tmp_path / "bo du lieu"
    files = {
        "I. Quản lý thuế/ND125_2020.pdf": variant(sample_pdf, "nd125"),
        "I. Quản lý thuế/L108_2025_QH15.pdf": variant(sample_pdf, "l108"),
        "I. Quản lý thuế/TT99_2025_BTC.pdf": variant(sample_pdf, "tt99"),  # không có trong danh mục Excel
        "II. Hóa đơn điện tử/ND254_2026.pdf": sample_pdf,
        "VI. Hộ kinh doanh/TT152_2025_BTC.pdf": variant(sample_pdf, "tt152"),
        "VIII. Chế độ kế toán/TT52_2025_BTC.pdf": variant(sample_pdf, "tt152"),  # trùng byte với TT152
        "ND20_2026.pdf": variant(sample_pdf, "nd20"),  # nằm ngoài mọi thư mục chủ đề
    }
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    write_xlsx(root / "Danh_muc_van_ban_phap_luat.xlsx", [
        HEADER,
        [1, "I. Quản lý thuế", "Luật Quản lý thuế", "108/2025/QH15", "L108_2025_QH15.pdf", ""],
        [2, "I. Quản lý thuế", "Nghị định xử phạt", "125/2020/NĐ-CP", "ND125_2020.pdf", ""],
        [3, "II. Hóa đơn điện tử", "Nghị định hóa đơn", "254/2026/NĐ-CP", "ND254_2026.pdf", ""],
    ])
    return root, fake_builder


def folders_of(client):
    """{mã văn bản: [mã danh mục theo thứ tự]} qua API."""
    return {row["document_id"]: [c["code"] for c in row.get("categories", [])] for row in client.get("/api/documents").json()}


def test_import_pdfs_files_each_document_into_its_dataset_folder(client, dataset, capsys):
    from app.cli import main

    root, builder = dataset
    assert main(["import-pdfs", str(root)]) == 0
    out = capsys.readouterr().out
    assert "VIII. Chế độ kế toán/TT52_2025_BTC.pdf: trùng với VI. Hộ kinh doanh/TT152_2025_BTC.pdf, bỏ qua · danh mục VIII. Chế độ kế toán" in out
    loose = next(line for line in out.splitlines() if line.startswith("ND20_2026.pdf: "))
    assert loose.startswith("ND20_2026.pdf: created") and loose.endswith("→ ND20_2026")  # không thuộc thư mục nào

    assert folders_of(client) == {
        "L108_2025_QH15": ["I"], "ND125_2020": ["I"], "TT99_2025_BTC": ["I"], "ND254_2026": ["II"],
        "TT152_2025_BTC": ["VI", "VIII"],  # một văn bản, hai chủ đề; không có bản TT52 riêng
        "ND20_2026": [],
    }
    categories = client.get("/api/categories").json()
    assert [(c["code"], c["name"], c["document_count"]) for c in categories] == [
        ("I", "Quản lý thuế", 3), ("II", "Hóa đơn điện tử", 1), ("VI", "Hộ kinh doanh", 1), ("VIII", "Chế độ kế toán", 1)]
    # trong thư mục: theo STT của tệp danh mục, văn bản ngoài danh mục đứng sau
    assert categories[0]["documents"] == ["L108_2025_QH15", "ND125_2020", "TT99_2025_BTC"]


def test_rerunning_import_pdfs_only_adds_folders_without_parsing_again(client, dataset, capsys, sample_pdf):
    from app.cli import main

    root, builder = dataset
    main(["import-pdfs", str(root)])
    builder.calls.clear()
    (root / "X. Lao động").mkdir()
    (root / "X. Lao động" / "ND125_2020.pdf").write_bytes(variant(sample_pdf, "nd125"))
    (root / "I. Quản lý thuế").rename(root / "I. Quản lý thuế và hóa đơn")  # đổi tên thư mục: cùng danh mục I

    assert main(["import-pdfs", str(root)]) == 0
    assert builder.calls == []  # mọi văn bản đã có: không tách/OCR lại
    assert "X. Lao động/ND125_2020.pdf: trùng với I. Quản lý thuế và hóa đơn/ND125_2020.pdf" in capsys.readouterr().out
    assert folders_of(client)["ND125_2020"] == ["I", "X"]
    names = {c["code"]: c["name"] for c in client.get("/api/categories").json()}
    assert names["I"] == "Quản lý thuế và hóa đơn" and count("category") == 5


def test_a_pdf_identical_to_an_imported_document_joins_that_document(client, dataset, capsys, sample_pdf):
    """TT52 trùng byte TT152 nhưng nằm trong lần nhập khác: nhận ra qua sha256 PDF đã lưu, không nhập văn bản mới."""
    from app.cli import main

    root, builder = dataset
    (root / "VIII. Chế độ kế toán" / "TT52_2025_BTC.pdf").unlink()
    main(["import-pdfs", str(root)])
    later = root.parent / "bo sung" / "VIII. Chế độ kế toán"
    later.mkdir(parents=True)
    (later / "TT52_2025_BTC.pdf").write_bytes(variant(sample_pdf, "tt152"))
    builder.calls.clear()

    assert main(["import-pdfs", str(later.parent)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == (
        "VIII. Chế độ kế toán/TT52_2025_BTC.pdf: trùng nội dung với TT152_2025_BTC đã có, bỏ qua · danh mục VIII. Chế độ kế toán")
    assert builder.calls == [] and "TT52_2025_BTC" not in folders_of(client)
    assert folders_of(client)["TT152_2025_BTC"] == ["VI", "VIII"]


def test_importing_one_numbered_folder_files_its_pdfs_into_that_folder(client, dataset, capsys):
    from app.cli import main

    root, _ = dataset
    assert main(["import-pdfs", str(root / "II. Hóa đơn điện tử")]) == 0
    assert capsys.readouterr().out.startswith("ND254_2026.pdf: created")
    assert folders_of(client) == {"ND254_2026": ["II"]}


def test_replacing_or_deleting_a_document_keeps_folders_consistent(client, dataset, sample_pdf, sample_json):
    from app.cli import main

    root, _ = dataset
    main(["import-pdfs", str(root)])
    changed = sample_json.decode().replace("Văn bản mẫu", "Văn bản mẫu (sửa)").encode()
    assert upload(client, variant(sample_pdf, "moi"), changed, replace=True).json()["status"] == "replaced"
    assert folders_of(client)["ND254_2026"] == ["II"]  # nhập lại không làm văn bản rơi khỏi thư mục

    client.post(f"/documents/{DOC}/delete")
    assert DOC not in folders_of(client)
    with psycopg.connect(URL) as conn:
        assert conn.execute("SELECT count(*) FROM document_category dc JOIN category c ON c.id = dc.category_id "
                            "WHERE c.code = 'II'").fetchone()[0] == 0
    assert any(c["code"] == "II" and c["document_count"] == 0 for c in client.get("/api/categories").json())


def test_upload_can_put_the_new_document_into_a_folder(client, dataset, sample_pdf, sample_json):
    from app.cli import main

    root, _ = dataset
    main(["import-pdfs", str(root / "VI. Hộ kinh doanh")])
    other = sample_json.decode().replace(DOC, "ND68_2026").encode()

    response = upload(client, variant(sample_pdf, "nd68"), other, data={"category": "VI"})
    assert response.status_code == 201 and folders_of(client)["ND68_2026"] == ["VI"]
    unknown = upload(client, variant(sample_pdf, "x"), sample_json, data={"category": "XX"})
    assert unknown.status_code == 422 and "XX" in unknown.text and count("document") == 2  # không lưu gì

    page = client.get("/")
    assert '<option value="VI">VI. Hộ kinh doanh</option>' in page.text
    form = client.post("/documents/upload", data={"category": "VI"}, follow_redirects=False,
                       files={"pdf": ("ND254_2026.pdf", sample_pdf), "document": ("document.json", sample_json)})
    assert form.status_code == 303 and folders_of(client)[DOC] == ["VI"]
    in_folder = client.get("/?folder=VI")
    assert '<option value="VI" selected>VI. Hộ kinh doanh</option>' in in_folder.text  # tải lên ngay trong thư mục đang mở


def test_list_page_browses_documents_folder_by_folder(client, dataset):
    from app.cli import main

    root, _ = dataset
    main(["import-pdfs", str(root)])

    home = client.get("/").text
    # gốc: các thư mục theo thứ tự I → VIII (không theo bảng chữ cái), kèm số văn bản; văn bản lẻ nằm ở gốc
    rows = [home.index(f'href="/?folder={code}"') for code in ("I", "II", "VI", "VIII")]
    assert rows == sorted(rows)
    assert 'data-count="3"' in home and "I. Quản lý thuế" in home
    assert 'href="/documents/ND20_2026' in home.split('id="folder-table"')[1]  # văn bản không thuộc thư mục nào
    assert 'aria-current="page"' in home.split('class="address-box"')[1].split("</nav>")[0]  # đang ở gốc

    folder = client.get("/?folder=I").text
    listing = folder.split('id="document-table"')[1]
    order = [listing.index(f'href="/documents/{doc}?folder=I"') for doc in ("L108_2025_QH15", "ND125_2020", "TT99_2025_BTC")]
    assert order == sorted(order) and "ND254_2026" not in listing and 'href="/?folder=' not in listing.split("<tbody>")[0]
    crumbs = folder.split('class="address-box"')[1].split("</nav>")[0]
    assert 'href="/"' in crumbs and "I. Quản lý thuế" in crumbs and 'aria-current="page"' in crumbs

    shared = client.get("/?folder=VIII").text.split('id="document-table"')[1]
    assert "TT152_2025_BTC" in shared and 'href="/?folder=VI"' in shared  # văn bản ở nhiều thư mục: chỉ ra thư mục kia

    unknown = client.get("/?folder=XX", follow_redirects=False)
    assert unknown.status_code == 303 and "err=" in unknown.headers["location"]

    deleted = client.post("/documents/ND125_2020/delete", data={"folder": "I"}, follow_redirects=False)
    assert deleted.headers["location"].startswith("/?folder=I&msg=")  # xóa xong vẫn ở thư mục đang xem


def test_review_page_links_back_to_the_folder_it_was_opened_from(client, dataset):
    from app.cli import main

    root, _ = dataset
    main(["import-pdfs", str(root)])
    page = client.get("/documents/TT152_2025_BTC?folder=VIII").text
    assert 'href="/?folder=VIII"' in page and "VIII. Chế độ kế toán" in page
    assert 'href="/?folder=' not in client.get("/documents/TT152_2025_BTC").text.split("</header>")[0]


# ---- thẩm định ----

UNIT ="ND254_2026_D9_K1_a"
REVIEWS = f"/api/documents/{DOC}/units/{UNIT}/reviews"


_CLIENT_AUTH = object()


def review(client, action, unit=UNIT, auth=_CLIENT_AUTH, **extra):
    """Gửi thao tác thẩm định; `auth` = (tài khoản, mật khẩu), None = không xác thực, mặc định = auth của client."""
    kwargs = {} if auth is _CLIENT_AUTH else {"auth": auth}
    return client.post(f"/api/documents/{DOC}/units/{unit}/reviews", json={"action": action, **extra}, **kwargs)


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
    dung, duy = add_account(client, "dung", "Dũng"), add_account(client, "duy", "Duy")
    assign(client, "dung", "duy")
    review(client, "flag", auth=dung, note="nghi sai")
    review(client, "approve", auth=duy)
    history = client.get(REVIEWS + "?version=all").json()
    assert [(h["action"], h["reviewer"]) for h in history] == [("approve", "duy"), ("flag", "dung")]
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
    add_account(client, "dung", "Dũng")
    assign(client, "dung")
    client.auth = None  # giao diện web: chỉ dùng cookie phiên sau khi đăng nhập
    url = f"/documents/{DOC}/units/{UNIT}/review"
    anonymous = client.post(url, data={"action": "approve", "reviewer": "admin", "note": ""}, follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"].startswith("/login")
    assert count("review_event") == 0

    assert login(client, "dung", USER_PW).status_code == 303
    bad = client.post(url, data={"action": "flag", "note": ""})
    assert bad.status_code == 200 and "cần ghi chú" in bad.text and count("review_event") == 0

    good = client.post(url, data={"action": "approve", "reviewer": "admin", "note": ""})  # tên tự khai bị bỏ qua
    assert good.status_code == 200
    assert f'id="dot-{UNIT}"' in good.text and "st-approved" in good.text and 'id="progress"' in good.text
    assert "reviewer=" not in good.headers.get("set-cookie", "")
    assert client.get(REVIEWS).json()[0]["reviewer"] == "dung"

    page = client.get(f"/documents/{DOC}?unit={UNIT}")
    assert "Đã duyệt" in page.text and "dung" in page.text and "Bản của bạn" in page.text
    assert client.get("/").text.count("Đã duyệt 1/7") == 2  # khối Việc được giao + bảng; đếm trên bản của người xem


# ---- bản riêng của từng người: OCR gốc, mỗi người chỉnh sửa một bản, bản chốt của người phê duyệt / Admin ----

def unit_text(client, auth=_CLIENT_AUTH, version=None):
    kwargs = {} if auth is _CLIENT_AUTH else {"auth": auth}
    query = f"?version={version}" if version else ""
    return client.get(f"/api/documents/{DOC}/units/{UNIT}{query}", **kwargs).json()["content"]


def test_two_reviewers_edit_the_same_unit_independently_and_the_approver_picks_one(loaded):
    client = loaded
    bt1, bt2, pd1 = add_account(client, "bt1"), add_account(client, "bt2"), add_account(client, "pd1", role="approver")
    assign(client, "bt1", "bt2", approver="pd1")
    original = unit_text(client)
    assert review(client, "edit", auth=bt1, content="Bản sửa của bt1").status_code == 201
    assert review(client, "edit", auth=bt2, content="Bản sửa của bt2").status_code == 201
    assert review(client, "approve", auth=bt1).json()["review_status"] == "approved"

    # mỗi người chỉ thấy bản OCR gốc và bản của mình; bản chốt chưa đổi
    assert unit_text(client, auth=bt1) == "Bản sửa của bt1" and unit_text(client, auth=bt2) == "Bản sửa của bt2"
    assert unit_text(client, auth=bt1, version="bt2") == "Bản sửa của bt1"  # không xem trộm được bản người khác
    assert unit_text(client) == original and unit_text(client, auth=pd1) == original
    assert unit_text(client, auth=pd1, version="bt2") == "Bản sửa của bt2"  # người phê duyệt xem được từng bản
    assert client.get(f"/api/documents/{DOC}/progress", auth=bt1).json()["approved"] == 1
    assert client.get(f"/api/documents/{DOC}/progress").json()["approved"] == 0
    assert [e["reviewer"] for e in client.get(REVIEWS, auth=bt1).json()] == ["bt1", "bt1"]
    assert client.get(REVIEWS).json() == []  # bản chốt chưa có thao tác nào
    everything = client.get(REVIEWS + "?version=all").json()
    assert [(e["reviewer"], e["track"]) for e in everything] == [("bt1", "editor"), ("bt2", "editor"), ("bt1", "editor")]

    versions = client.get(f"/api/documents/{DOC}/units/{UNIT}/versions", auth=pd1).json()
    assert [(v["version"], v["content"], v["status"]) for v in versions] == [
        ("ocr", original, None), ("bt1", "Bản sửa của bt1", "approved"), ("bt2", "Bản sửa của bt2", "unreviewed"),
        ("final", original, "unreviewed")]
    assert [v["color"] for v in versions] == [None, 1, 2, None]
    mine = client.get(f"/api/documents/{DOC}/units/{UNIT}/versions", auth=bt1).json()
    assert [v["version"] for v in mine] == ["ocr", "bt1"]
    assert workflow(client, "submit", bt1).status_code == 200 and workflow(client, "submit", bt2).status_code == 200

    # người phê duyệt chọn bản của bt2 rồi duyệt; không sửa chữ được
    assert "không được sửa chữ" in " ".join(review(client, "edit", auth=pd1, content="chữ khác").json()["problems"])
    assert review(client, "adopt", auth=bt1, source="bt2").status_code == 422  # người chỉnh sửa không chọn bản
    assert review(client, "adopt", auth=pd1, source="ai_do").status_code == 422
    adopted = review(client, "adopt", auth=pd1, source="bt2")
    assert adopted.status_code == 201
    event = adopted.json()["event"]
    assert event["track"] == "final" and event["content_after"] == "Bản sửa của bt2" and "bt2" in event["note"]
    assert review(client, "adopt", auth=pd1, source="bt2").status_code == 422  # đã giống bản chốt
    assert review(client, "approve", auth=pd1).json()["review_status"] == "approved"
    assert unit_text(client) == "Bản sửa của bt2" and unit_text(client, auth=bt1) == "Bản sửa của bt1"
    exported = client.get(f"/api/documents/{DOC}/export").json()
    assert [u["content"] for u in exported["legal_units"]] == ["Bản sửa của bt2"]

    # Admin vẫn sửa được chữ trên bản chốt; chọn lại bản OCR gốc cũng được
    assert review(client, "edit", content="Admin chốt lại").json()["event"]["track"] == "final"
    assert review(client, "adopt", source="ocr").json()["event"]["content_after"] == original


def test_review_page_shows_each_version_in_its_own_colour_to_the_approver(loaded):
    client = loaded
    bt1, bt2, _pd1 = add_account(client, "bt1"), add_account(client, "bt2"), add_account(client, "pd1", role="approver")
    assign(client, "bt1", "bt2", approver="pd1")
    review(client, "edit", auth=bt1, content="Bản sửa của bt1")
    review(client, "flag", auth=bt2, note="ảnh trang mờ")
    client.auth = None
    login(client, "bt2", USER_PW)
    mine = client.get(f"/documents/{DOC}?unit={UNIT}").text
    assert "Bản của bạn" in mine and "So sánh các bản" not in mine and "bt1" not in mine.split('id="detail"')[1]
    assert "✎ Sửa chữ" in mine and "Xuất JSON đã duyệt" not in mine  # xuất JSON lấy bản chốt: không dành cho người chỉnh sửa
    client.cookies.clear()
    workflow(client, "submit", bt1), workflow(client, "submit", bt2)
    login(client, "pd1", USER_PW)
    page = client.get(f"/documents/{DOC}?unit={UNIT}").text
    assert "So sánh các bản" in page and "Bản OCR gốc" in page and "Bản chốt" in page
    assert 'class="person-chip person-1"' in page and ">bt1<" in page and '<ins class="diff-ins"' in page
    assert 'class="person-chip person-2"' in page and "ảnh trang mờ" in page
    assert '"action": "adopt", "source": "bt1"' in page and "✎ Sửa chữ" not in page  # người phê duyệt không sửa chữ
    assert 'class="person-mark person-1"' in page  # vạch màu trên cây: bt1 đã thao tác đơn vị này


# ---- quy trình: Admin phân công → người chỉnh sửa Hoàn thành → người phê duyệt Trả lại / Hoàn tất ----

def test_a_document_goes_to_the_approver_as_soon_as_its_editors_finish(loaded):
    client = loaded
    bt1, bt2, pd1 = add_account(client, "bt1"), add_account(client, "bt2"), add_account(client, "pd1", role="approver")
    pd2 = add_account(client, "pd2", role="approver")
    state = client.get(f"/api/documents/{DOC}/workflow").json()
    assert state["status"] == "unassigned" and state["editors"] == []

    # chỉ Admin phân công; người được giao phải đúng vai trò
    assert workflow(client, "assign", bt1, target_user="bt2", target_role="editor").status_code == 422
    assert "không phải người chỉnh sửa" in " ".join(
        workflow(client, "assign", target_user="pd1", target_role="editor").json()["problems"])
    for name in ("bt1", "bt2"):
        assert workflow(client, "assign", target_user=name, target_role="editor").status_code == 200
    state = workflow(client, "assign", target_user="pd1", target_role="approver").json()
    assert state["status"] == "editing" and state["approver"] == "pd1"
    assert [(e["username"], e["submitted"], e["color"]) for e in state["editors"]] == [("bt1", False, 1), ("bt2", False, 2)]

    # người phê duyệt chưa làm được gì khi người chỉnh sửa chưa xong
    assert "chưa chuyển sang phê duyệt" in " ".join(review(client, "approve", auth=pd1).json()["problems"])
    assert review(client, "edit", auth=bt1, content="Chữ của bt1").status_code == 201
    assert workflow(client, "submit", bt1).json()["status"] == "editing"
    assert "Hoàn thành" in " ".join(review(client, "approve", auth=bt1).json()["problems"])  # đã nộp: chỉ còn đọc
    assert workflow(client, "submit", bt2).json()["status"] == "pending"  # văn bản chuyển sang phê duyệt ngay

    assert "người phê duyệt khác" in " ".join(review(client, "approve", auth=pd2).json()["problems"])
    assert "Còn 7 đơn vị" in " ".join(workflow(client, "finalize", pd1).json()["problems"])
    assert "ghi chú" in " ".join(workflow(client, "return", pd1).json()["problems"])
    returned = workflow(client, "return", pd1, note="Điểm a còn thiếu dấu").json()
    assert returned["status"] == "returned" and returned["returned_note"] == "Điểm a còn thiếu dấu"
    assert review(client, "edit", auth=bt1, content="Chữ của bt1, đã sửa lại").status_code == 201  # sửa tiếp được
    workflow(client, "submit", bt1), workflow(client, "submit", bt2)

    assert review(client, "adopt", auth=pd1, source="bt1").status_code == 201
    with psycopg.connect(URL) as conn:
        units = [r[0] for r in conn.execute("SELECT unit_id FROM legal_unit ORDER BY unit_id")]
    for unit in units:
        assert review(client, "approve", unit=unit, auth=pd1).status_code == 201
    done = workflow(client, "finalize", pd1).json()
    assert done["status"] == "approved" and done["finalized_by"] == "pd1"
    assert "đã được phê duyệt" in " ".join(review(client, "flag", auth=pd1, note="x").json()["problems"])
    exported = {u["unit_id"]: u["content"] for u in client.get(f"/api/documents/{DOC}/export").json()["legal_units"]}
    assert len(exported) == 7 and exported[UNIT] == "Chữ của bt1, đã sửa lại"

    # Admin mở lại; lịch sử quy trình chỉ ghi thêm
    assert workflow(client, "reopen").json()["status"] == "pending"
    assert count("workflow_event") == 10
    with psycopg.connect(URL) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM workflow_event")


def test_list_page_shows_my_assignments_and_the_approval_queue(loaded):
    client = loaded
    add_account(client, "bt1"), add_account(client, "bt2"), add_account(client, "pd1", role="approver")
    assign(client, "bt1", approver="pd1")
    client.auth = None
    login(client, "bt1", USER_PW)
    page = client.get("/").text
    assert "Việc được giao cho tôi" in page and "Đang chỉnh sửa" in page and f'href="/documents/{DOC}' in page
    detail = client.get(f"/documents/{DOC}?unit={UNIT}").text
    assert "✔ Hoàn thành văn bản" in detail
    assert client.post(f"/documents/{DOC}/workflow", data={"action": "submit"}, follow_redirects=False).status_code == 303
    detail = client.get(f"/documents/{DOC}?unit={UNIT}").text
    assert "✔ Hoàn thành văn bản" not in detail and "✎ Sửa chữ" not in detail and "đã bấm Hoàn thành" in detail

    login(client, "bt2", USER_PW)
    assert "Việc được giao cho tôi" in client.get("/").text and "Chưa có văn bản nào" in client.get("/").text
    assert "chưa được giao" in client.get(f"/documents/{DOC}?unit={UNIT}").text

    login(client, "pd1", USER_PW)
    queue = client.get("/").text
    assert "Chờ tôi phê duyệt" in queue and "Chờ phê duyệt" in queue
    detail = client.get(f"/documents/{DOC}?unit={UNIT}").text
    assert "↩ Trả lại" in detail and "Hoàn tất phê duyệt" in detail
    back = client.post(f"/documents/{DOC}/workflow", data={"action": "return", "note": ""}, follow_redirects=False)
    assert "err=" in back.headers["location"]  # thiếu ghi chú: không ghi gì
    client.post(f"/documents/{DOC}/workflow", data={"action": "return", "note": "Xem lại Điều 9"})
    login(client, "bt1", USER_PW)
    assert "Xem lại Điều 9" in client.get(f"/documents/{DOC}?unit={UNIT}").text


def test_admin_assigns_a_whole_folder_and_can_unassign(client, sample_pdf, sample_json):
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("INSERT INTO category (code, name, position) VALUES ('II', 'Hóa đơn điện tử', 2)")
    assert upload(client, sample_pdf, sample_json, data={"category": "II"}).status_code == 201
    add_account(client, "bt1"), add_account(client, "pd1", role="approver")
    page = client.get("/admin/assign?folder=II").text
    assert "Phân công" in page and DOC in page and "bt1" in page and "pd1" in page
    assert "Chọn ít nhất" in client.post("/admin/assign", data={"editors": ["bt1"]}).text  # chưa chọn văn bản nào
    done = client.post("/admin/assign", data={"folder": "II", "whole_folder": "1", "editors": ["bt1"], "approver": "pd1"},
                       follow_redirects=False)
    assert done.status_code == 303 and "msg=" in done.headers["location"]
    state = client.get(f"/api/documents/{DOC}/workflow").json()
    assert [e["username"] for e in state["editors"]] == ["bt1"] and state["approver"] == "pd1"
    again = client.post("/admin/assign", data={"documents": [DOC], "editors": ["bt1"]}, follow_redirects=False)
    assert "msg=" in again.headers["location"]  # giao lại người đã có: bỏ qua, không lỗi
    assert count("workflow_event") == 2
    client.post("/admin/assign/remove", data={"document_id": DOC, "username": "bt1", "role": "editor"})
    assert client.get(f"/api/documents/{DOC}/workflow").json()["editors"] == []
    reviewer = add_account(client, "bt2")
    denied = client.post("/admin/assign", data={"documents": [DOC], "editors": ["bt2"]}, auth=reviewer, follow_redirects=False)
    assert denied.status_code in (303, 403) and count("workflow_event") == 3


def test_tracks_migration_keeps_earlier_events_as_the_final_version(client):
    with psycopg.connect(URL, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):  # chọn bản chỉ có trên bản chốt
            conn.execute("INSERT INTO review_event (document_id, unit_id, action, reviewer, content_after, track) "
                         "VALUES ('X', 'X_D1', 'adopt', 'bt1', 'chữ', 'editor')")
        with pytest.raises(psycopg.errors.NotNullViolation):  # phải nêu rõ bản
            conn.execute("INSERT INTO review_event (document_id, unit_id, action, reviewer) VALUES ('X', 'X_D1', 'approve', 'a')")
        conn.execute("ALTER TABLE review_event DROP COLUMN track")
        conn.execute("ALTER TABLE review_event DROP CONSTRAINT review_event_action_check")
        conn.execute("ALTER TABLE review_event DROP CONSTRAINT review_event_content_check")
        conn.execute("ALTER TABLE review_event ADD CHECK (action IN ('approve', 'edit', 'flag', 'structure_error', 'reset'))")
        conn.execute("ALTER TABLE review_event ADD CHECK ((action = 'edit') = (content_after IS NOT NULL))")
        conn.execute("INSERT INTO review_event (document_id, unit_id, action, reviewer) VALUES ('X', 'X_D1', 'approve', 'cu')")
        conn.execute((Path(__file__).resolve().parent.parent / "migrations" / "009_review_tracks.sql").read_text(encoding="utf-8"))
        assert conn.execute("SELECT track FROM review_event WHERE reviewer = 'cu'").fetchone()[0] == "final"
        conn.execute("INSERT INTO review_event (document_id, unit_id, action, reviewer, content_before, content_after, track) "
                     "VALUES ('X', 'X_D1', 'adopt', 'pd1', 'a', 'b', 'final')")
        checks = conn.execute("SELECT count(*) FROM pg_constraint WHERE conrelid = 'review_event'::regclass "
                              "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%action%'").fetchone()[0]
    assert checks == 3  # action, content, track: không còn ràng buộc cũ nào sót lại


# ---- bảo mật ----

def test_api_writes_require_an_active_account_and_only_admin_uploads(client, sample_pdf, sample_json):
    assert upload(client, sample_pdf, sample_json, auth=None).status_code == 401
    locked = add_account(client, "bi_khoa", approved=False)
    reviewer = add_account(client, "duy")
    approver = add_account(client, "pd1", role="approver")
    for who in (locked, reviewer, approver):
        assert upload(client, sample_pdf, sample_json, auth=who).status_code == 403  # chỉ Admin thêm văn bản
    assert upload(client, sample_pdf, sample_json).status_code == 201

    raw = json.loads(sample_json)
    raw["legal_units"][0]["content"] = "nội dung khác"
    changed = json.dumps(raw, ensure_ascii=False).encode("utf-8")
    assert upload(client, sample_pdf, changed, replace=True, auth=reviewer).status_code == 403  # chỉ Admin được ghi đè
    assert client.get(f"/api/documents/{DOC}/units/ND254_2026_D9").json()["content"] != "nội dung khác"
    assert client.get(f"/api/documents/{DOC}", auth=None).status_code == 401  # đọc cũng phải đăng nhập

    assert review(client, "approve", auth=None).status_code == 401
    assert review(client, "approve", auth=locked).status_code == 403
    forged = client.post(REVIEWS, json={"action": "approve", "reviewer": "admin"}, headers={"cookie": "reviewer=admin"},
                         auth=None)
    assert forged.status_code == 401 and count("review_event") == 0
    assert "chưa được giao" in " ".join(review(client, "approve", auth=reviewer).json()["problems"])
    assign(client, "duy")
    accepted = review(client, "approve", auth=reviewer, reviewer="admin")
    assert accepted.status_code == 201 and accepted.json()["event"]["reviewer"] == "duy"


def test_html_upload_and_delete_permissions(client, sample_pdf, sample_json):
    files = {"pdf": ("source.pdf", sample_pdf), "document": ("document.json", sample_json)}
    anonymous = client.post("/documents/upload", files=files, auth=None, follow_redirects=False)
    assert anonymous.status_code == 303 and anonymous.headers["location"].startswith("/login") and count("document") == 0
    uploaded = client.post("/documents/upload", files=files, follow_redirects=False)
    assert uploaded.status_code == 303 and uploaded.headers["location"] == f"/documents/{DOC}" and count("document") == 1

    reviewer = add_account(client, "duy")
    denied = client.post(f"/documents/{DOC}/delete", auth=reviewer, follow_redirects=False)
    assert "err=" in denied.headers["location"] and count("document") == 1
    assert client.get(f"/api/documents/{DOC}/pages/1").status_code == 200  # có bộ đệm trên đĩa để kiểm tra việc xóa
    traversal = client.post("/documents/%2E%2E/delete", follow_redirects=False)
    assert traversal.status_code == 404 and (client.storage / DOC / "source.pdf").is_file() and count("document") == 1

    deleted = client.post(f"/documents/{DOC}/delete", follow_redirects=False)
    assert deleted.status_code == 303 and "msg=" in deleted.headers["location"]
    assert count("document") == 0 and count("document_pdf") == 0
    assert not (client.storage / DOC).exists() and client.storage.is_dir()


def test_roles_migration_turns_old_viewers_into_locked_reviewers(client):
    with psycopg.connect(URL, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO reviewer_account (username, display_name, password_hash, role) "
                         "VALUES ('la', 'la', '', 'viewer')")
        conn.execute("ALTER TABLE reviewer_account DROP CONSTRAINT reviewer_account_role_check")
        conn.execute("INSERT INTO reviewer_account (username, display_name, password_hash, role, is_approved) "
                     "VALUES ('cu_xem', 'Tài khoản cũ', '', 'viewer', false), ('cu_soat', 'Cũ', '', 'reviewer', true)")
        conn.execute((Path(__file__).resolve().parent.parent / "migrations" / "007_roles.sql").read_text(encoding="utf-8"))
        rows = conn.execute("SELECT username, role, is_approved FROM reviewer_account "
                            "WHERE username LIKE 'cu_%%' ORDER BY username").fetchall()
        default = conn.execute("SELECT column_default FROM information_schema.columns "
                               "WHERE table_name = 'reviewer_account' AND column_name = 'role'").fetchone()[0]
    assert rows == [("cu_soat", "reviewer", True), ("cu_xem", "reviewer", False)]
    assert default.startswith("'reviewer'")
    accounts = client.app.state.accounts
    assert accounts.create("pd1", "Phê duyệt", "matkhau-12345", role="approver")["role"] == "approver"
    assert accounts.get("pd1")["is_approved"]


def test_admin_password_follows_setting_on_every_startup(client, tmp_path):
    """Không đặt ADMIN_PASSWORD: admin đăng nhập bằng admin123 (kể cả khi CSDL đang lưu mật khẩu khác)."""
    storage = tmp_path / "storage2"
    app = create_app(Settings(data_dir=tmp_path / "none", storage_dir=storage, conninfo=URL, auto_import_dir=None))
    with TestClient(app) as second:
        assert login(second, "admin", "admin123").status_code == 303
        assert login(second, "admin", ADMIN_PW).status_code == 401


# ---- tô vàng trên bản scan: toạ độ từng từ do OCR trả về, lưu trong PostgreSQL ----

SCAN_LINES = ["1", "CHÍNH PHỦ", "NGHỊ ĐỊNH", "Điều 1. Phạm vi điều chỉnh", "1. Nghị định này quy định về thuế.",
              "2. Khoản hai quy định về phí.", "Điều 2. Hiệu lực thi hành", "Nghị định có hiệu lực từ ngày ký."]


def scanned_pdf(pages=1):
    """PDF chỉ có ảnh (như bản scan / ảnh chụp), không có lớp chữ."""
    import io

    from PIL import Image

    images = [Image.new("RGB", (850, 1100), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


@pytest.fixture
def fake_tesseract(monkeypatch):
    """Tesseract giả: mỗi trang trả các dòng SCAN_LINES, từ cách đều nhau (dòng i ở y = 0,05 + 0,04·i)."""
    from app.services import page_words, pdf_parser
    from app.services.page_words import Word

    calls = []

    def ocr(image):
        calls.append(image.size)
        words = []
        for index, line in enumerate(SCAN_LINES):
            x, y = 0.15, 0.05 + 0.04 * index
            for text in line.split():
                words.append(Word(text, (x, y, x + 0.012 * len(text), y + 0.02), index))
                x += 0.012 * len(text) + 0.01
        return "\n".join(SCAN_LINES), tuple(words)

    monkeypatch.setattr(pdf_parser, "_tesseract_ready", lambda: True)
    monkeypatch.setattr(page_words, "ocr_image", ocr)
    return calls


def highlight_tops(html):
    import re

    return [float(top) for top in re.findall(r'class="page-highlight[^"]*" style="left: [\d.]+%; top: ([\d.]+)%', html)]


def test_uploaded_scan_keeps_word_boxes_and_highlights_the_exact_lines(client, fake_tesseract):
    response = client.post("/api/documents", files={"pdf": ("ND9_2026.pdf", scanned_pdf())})
    assert response.status_code == 201 and response.json()["document_id"] == "ND9_2026"
    assert len(fake_tesseract) == 1 and count("page_words") == 1  # một lần nhận dạng ra cả chữ lẫn toạ độ

    viewer = client.get("/documents/ND9_2026/viewer", params={"page": 1, "unit": "ND9_2026_D1_K2"}).text
    assert "ước lượng" not in viewer and highlight_tops(viewer) == [pytest.approx(25.0 - 0.4, abs=0.2)]  # dòng 5
    article = client.get("/documents/ND9_2026/viewer", params={"page": 1, "unit": "ND9_2026_D2"}).text
    assert highlight_tops(article) == [pytest.approx(29.0 - 0.4, abs=0.2)]  # từ dòng "Điều 2. ..." (dòng 6)
    assert len(fake_tesseract) == 1  # xem trang không OCR lại

    client.post("/documents/ND9_2026/delete", follow_redirects=False)
    assert count("page_words") == 0  # xóa văn bản thì toạ độ chữ của PDF đó cũng đi theo


def test_backfill_ocrs_only_scanned_pages_without_word_boxes(client, sample_pdf, sample_json, fake_tesseract):
    from app.services.page_words import backfill

    assert upload(client, sample_pdf, sample_json).status_code == 201  # PDF có lớp chữ: không cần OCR
    assert client.post("/api/documents", files={"pdf": ("ND9_2026.pdf", scanned_pdf(2))}).status_code == 201
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute("DELETE FROM page_words")  # như văn bản nhập trước khi có tính năng này
    pool, lines = client.app.state.catalog.pool, []
    fake_tesseract.clear()

    assert backfill(pool, workers=2, report=lines.append) == {"ND254_2026": 0, "ND9_2026": 2}
    assert count("page_words") == 2 and len(fake_tesseract) == 2 and len(lines) == 2
    assert backfill(pool, ["ND9_2026"], report=lines.append) == {"ND9_2026": 0}  # đã có: không OCR lại
    assert backfill(pool, ["ND9_2026"], force=True, report=lines.append) == {"ND9_2026": 2}
    assert len(fake_tesseract) == 4


def test_admin_sees_progress_per_person_and_per_document(loaded):
    client = loaded
    bt1, _bt2, pd1 = add_account(client, "bt1", "Bình"), add_account(client, "bt2"), add_account(client, "pd1", role="approver")
    assign(client, "bt1", "bt2", approver="pd1")
    review(client, "approve", auth=bt1)
    workflow(client, "submit", bt1)
    data = client.get("/api/stats").json()
    people = {p["username"]: p for p in data["people"]}
    assert (people["bt1"]["assigned"], people["bt1"]["completed"], people["bt1"]["percent"]) == (1, 1, 100)
    assert (people["bt2"]["completed"], people["bt2"]["percent"]) == (0, 0)
    assert (people["pd1"]["assigned"], people["pd1"]["waiting"]) == (1, 0)
    assert people["bt1"]["units_done"] == 1 and people["bt1"]["units_total"] == 7
    assert data["status_counts"]["editing"] == 1 and data["total_documents"] == 1
    assert client.get("/api/stats?folder=XX").json()["total_documents"] == 0

    page = client.get("/admin/stats").text
    assert "Thống kê tiến độ" in page and "Bình" in page and "100%" in page and "1/1 văn bản" in page
    assert 'class="person-chip person-1"' in page and "Đang chỉnh sửa" in page
    assert client.get("/api/stats", auth=pd1).status_code == 403  # chỉ Admin
    denied = client.get("/admin/stats", auth=bt1, follow_redirects=False)
    assert denied.status_code == 303 and denied.headers["location"].startswith("/login")
