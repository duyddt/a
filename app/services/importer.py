"""Nhập một văn bản (PDF gốc + JSON LegalDocument): kiểm tra, render ảnh trang, lưu vào PostgreSQL.

Nhập lại cùng nội dung thì không tạo bản trùng (trả "unchanged"). Cùng document_id nhưng nội dung khác thì báo
AlreadyImported, trừ khi replace=True (xóa bản cũ, nhập bản mới).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import ValidationError

from app import repository
from app.contracts import LegalDocument

from .models import DOC_ID_RE, ImportProblem, check_parents_exist, check_source_pages
from .render_pdf import render_all

UPLOAD_NAME = "(tệp tải lên)"


class AlreadyImported(Exception):
    def __init__(self, document_id: str):
        super().__init__(document_id)
        self.document_id = document_id


@dataclass
class Prepared:
    doc: LegalDocument
    page_count: int
    content_hash: str
    pdf_bytes: bytes


@dataclass
class ImportResult:
    document_id: str
    status: str  # created | unchanged | replaced
    unit_count: int
    page_count: int


def content_hash_of(pdf_bytes: bytes, raw_json: object) -> str:
    """sha256 của PDF và JSON đã chuẩn hóa (khoảng trắng, thứ tự khóa không làm đổi mã băm)."""
    canonical = json.dumps(raw_json, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256()
    digest.update(pdf_bytes)
    digest.update(b"\0")
    digest.update(canonical.encode("utf-8"))
    return digest.hexdigest()


def prepare_import(pdf_bytes: bytes, json_bytes: bytes) -> Prepared:
    """Kiểm tra toàn bộ đầu vào; sai thì raise ImportProblem với danh sách lỗi cụ thể. Chưa ghi gì."""
    try:
        raw = json.loads(json_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportProblem(UPLOAD_NAME, [f"JSON không hợp lệ: {exc}"]) from exc

    try:
        doc = LegalDocument.model_validate(raw)
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in e['loc']) or '(văn bản)'}: {e['msg']}" for e in exc.errors()]
        raise ImportProblem(UPLOAD_NAME, problems) from exc

    name = doc.document_id
    if not DOC_ID_RE.match(name):
        raise ImportProblem(name, [f"document_id {name!r} chỉ được gồm chữ, số, gạch dưới, gạch ngang (tối đa 64 ký tự)"])

    if not pdf_bytes.startswith(b"%PDF"):
        raise ImportProblem(name, ["Tệp PDF không hợp lệ (không bắt đầu bằng %PDF)"])
    try:
        pdf = pdfium.PdfDocument(pdf_bytes)
        try:
            page_count = len(pdf)
        finally:
            pdf.close()
    except Exception as exc:
        raise ImportProblem(name, [f"Không đọc được PDF: {exc}"]) from exc

    problems = check_source_pages(doc, page_count) + check_parents_exist(doc)
    if not doc.legal_units:
        problems.append("Văn bản không có đơn vị nào trong legal_units")
    if problems:
        raise ImportProblem(name, problems)

    return Prepared(doc=doc, page_count=page_count, content_hash=content_hash_of(pdf_bytes, raw), pdf_bytes=pdf_bytes)


def stage_files(prepared: Prepared, storage_dir: Path) -> Path:
    """Ghi source.pdf và render mọi trang vào một thư mục tạm trong storage_dir. Trả về thư mục tạm."""
    storage_dir.mkdir(parents=True, exist_ok=True)
    staging = storage_dir / f".staging-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        (staging / "source.pdf").write_bytes(prepared.pdf_bytes)
        rendered = render_all(staging / "source.pdf", staging / "pages")
        if rendered != prepared.page_count:
            raise RuntimeError(f"render {rendered} trang, mong đợi {prepared.page_count}")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def publish_files(staging: Path, storage_dir: Path, document_id: str) -> Path:
    """Đưa thư mục tạm vào chỗ chính thức storage_dir/<document_id>, thay bản cũ nếu có."""
    final = storage_dir / document_id
    if final.exists():
        shutil.rmtree(final)
    os.replace(staging, final)
    return final


class Importer:
    def __init__(self, pool, storage_dir: Path):
        self.pool = pool
        self.storage_dir = Path(storage_dir)

    def import_bytes(self, pdf_bytes: bytes, json_bytes: bytes, replace: bool = False) -> ImportResult:
        prepared = prepare_import(pdf_bytes, json_bytes)
        document_id = prepared.doc.document_id
        unit_count = len(prepared.doc.legal_units)

        # Kiểm tra sớm để nhập lại cùng nội dung không tốn thời gian render
        with self.pool.connection() as conn:
            existing = repository.find_hash(conn, document_id)
        if existing == prepared.content_hash:
            return ImportResult(document_id, "unchanged", unit_count, prepared.page_count)
        if existing is not None and not replace:
            raise AlreadyImported(document_id)

        staging = stage_files(prepared, self.storage_dir)
        try:
            with self.pool.connection() as conn:  # một giao dịch: lỗi ở đâu cũng rollback hết
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (document_id,))
                current = repository.find_hash(conn, document_id)  # kiểm tra lại: có thể có người nhập cùng lúc
                if current == prepared.content_hash:
                    return ImportResult(document_id, "unchanged", unit_count, prepared.page_count)
                if current is not None and not replace:
                    raise AlreadyImported(document_id)
                if current is not None:
                    repository.delete_document(conn, document_id)
                pk = repository.insert_document(conn, prepared.doc, prepared.page_count, prepared.content_hash)
                repository.insert_units(conn, pk, prepared.doc.legal_units)
                publish_files(staging, self.storage_dir, document_id)
                status = "replaced" if current is not None else "created"
            return ImportResult(document_id, status, unit_count, prepared.page_count)
        finally:
            shutil.rmtree(staging, ignore_errors=True)  # đã đưa đi rồi thì không còn gì để xóa

    def import_dir(self, directory: Path, replace: bool = False) -> ImportResult:
        """Nhập một thư mục có source.pdf và document.json."""
        pdf_path, json_path = Path(directory) / "source.pdf", Path(directory) / "document.json"
        missing = [p.name for p in (pdf_path, json_path) if not p.is_file()]
        if missing:
            raise ImportProblem(Path(directory).name, [f"Thiếu tệp: {', '.join(missing)}"])
        return self.import_bytes(pdf_path.read_bytes(), json_path.read_bytes(), replace=replace)

    def import_all(self, root: Path, replace: bool = False) -> list[tuple[str, str]]:
        """Nhập mọi thư mục con của root. Trả về [(tên thư mục, kết quả hoặc lỗi)]; không dừng ở lỗi đầu tiên."""
        outcomes: list[tuple[str, str]] = []
        for child in sorted(Path(root).iterdir()) if Path(root).is_dir() else []:
            if not (child.is_dir() and (child / "source.pdf").is_file() and (child / "document.json").is_file()):
                continue
            try:
                result = self.import_dir(child, replace=replace)
                outcomes.append((child.name, f"{result.status} ({result.unit_count} đơn vị, {result.page_count} trang)"))
            except ImportProblem as problem:
                outcomes.append((child.name, "LỖI: " + "; ".join(problem.problems[:3])))
            except AlreadyImported:
                outcomes.append((child.name, "LỖI: đã có bản khác cùng document_id (dùng --replace để thay)"))
        return outcomes
