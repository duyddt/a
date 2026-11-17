"""Nhập một văn bản (PDF gốc + JSON LegalDocument): kiểm tra rồi lưu cả hai vào PostgreSQL.

Nhập lại cùng nội dung thì không tạo bản trùng (trả "unchanged"). Cùng document_id nhưng nội dung khác thì báo
AlreadyImported, trừ khi replace=True (xóa bản cũ, nhập bản mới).
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from pydantic import ValidationError

from app import repository
from app.contracts import LegalDocument

from . import pdf_parser
from .categories import Category, category_from_folder, position_in_folder, read_catalog_order
from .models import DOC_ID_RE, ImportProblem, check_parents_exist, check_source_pages
from .render_pdf import PDFIUM_LOCK

UPLOAD_NAME = "(tệp tải lên)"
log = logging.getLogger("review-app")


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
        with PDFIUM_LOCK:
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


class Importer:
    """Nhập văn bản vào PostgreSQL: đơn vị và PDF gốc nằm cùng một giao dịch, không ghi gì ra đĩa."""

    def __init__(self, pool):
        self.pool = pool

    def import_bytes(self, pdf_bytes: bytes, json_bytes: bytes, replace: bool = False,
                     category: str | None = None, page_words: dict | None = None) -> ImportResult:
        """`category`: mã danh mục đã có ("VI") để xếp văn bản vào; mã chưa có thì từ chối và không lưu gì.
        `page_words`: {số trang: toạ độ từng từ} có được khi OCR lúc dựng JSON, lưu lại để tô sáng trên bản scan."""
        prepared = prepare_import(pdf_bytes, json_bytes)
        document_id = prepared.doc.document_id
        unit_count = len(prepared.doc.legal_units)

        with self.pool.connection() as conn:  # một giao dịch: lỗi ở đâu cũng rollback hết
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (document_id,))  # chống hai người nhập cùng lúc
            if category and not repository.category_exists(conn, category):
                raise ImportProblem(document_id, [f"Không có danh mục {category!r}"])
            current = repository.find_hash(conn, document_id)
            if current == prepared.content_hash:
                if not repository.has_pdf(conn, document_id):  # nhập từ phiên bản cũ, khi PDF chỉ nằm trên đĩa
                    repository.store_pdf(conn, document_id, pdf_bytes)
                status = "unchanged"
            else:
                if current is not None and not replace:
                    raise AlreadyImported(document_id)
                kept = []
                if current is not None:  # xóa rồi tạo lại dòng document: giữ văn bản trong các danh mục cũ
                    kept = repository.get_category_links(conn, document_id)
                    repository.delete_document(conn, document_id)
                pk = repository.insert_document(conn, prepared.doc, prepared.page_count, prepared.content_hash)
                repository.insert_units(conn, pk, prepared.doc.legal_units)
                repository.store_pdf(conn, document_id, pdf_bytes)
                for link in kept:
                    repository.link_category(conn, document_id, link["code"], link["rank"])
                status = "replaced" if current is not None else "created"
            if category:
                repository.link_category(conn, document_id, category, position_in_folder(document_id, {}))
            if page_words:
                from .page_words import ENGINE, to_json

                digest = hashlib.sha256(pdf_bytes).hexdigest()
                for page, words in page_words.items():
                    repository.put_page_words(conn, digest, page, ENGINE, to_json(words))
        return ImportResult(document_id, status, unit_count, prepared.page_count)

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

    def import_pdfs(self, root: Path, replace: bool = False) -> Iterator[tuple[str, str]]:
        """Nhập mọi PDF trong root (kể cả thư mục con) mà không cần document.json: tự tách cấu trúc, OCR bản scan.

        Trả về từng (đường dẫn tương đối, kết quả) ngay khi xong một tệp. Không dừng ở tệp lỗi. Tệp trùng byte với tệp
        đã gặp, hoặc với PDF của văn bản đã có trong DB, thì bỏ qua. Văn bản đã có trong DB (theo mã lấy từ tên tệp)
        thì bỏ qua TRƯỚC khi tách chữ/OCR, nên chạy lại sau khi bị ngắt không phải OCR lại; dùng replace=True để nhập lại.

        Danh mục: thư mục con cấp 1 ("I. Quản lý thuế") là danh mục của mọi PDF bên trong; nếu root chính là một thư mục
        đánh số thì PDF nằm thẳng trong root thuộc danh mục đó. Văn bản (kể cả bản trùng, bản đã có) được xếp THÊM vào
        danh mục, không bị gỡ khỏi danh mục cũ. Thứ tự trong danh mục lấy từ tệp danh mục .xlsx ở root (nếu có).
        """
        root = Path(root)
        catalog = next((order for book in sorted(root.glob("*.xlsx")) if (order := read_catalog_order(book))), {})
        seen: dict[str, tuple[str, str | None]] = {}  # sha256 -> (tệp gặp đầu tiên, mã văn bản của nó khi đã vào DB)
        for path in sorted(root.rglob("*.pdf")):
            name = path.relative_to(root).as_posix()
            folder = _folder_of(root, path)
            pdf_bytes = path.read_bytes()
            digest = hashlib.sha256(pdf_bytes).hexdigest()
            if digest in seen:
                first, document_id = seen[digest]
                yield name, f"trùng với {first}, bỏ qua" + self._file_into(document_id, folder, catalog)
                continue
            seen[digest] = (name, None)

            document_id = pdf_parser.document_id_from_filename(path.name)
            if not replace:
                with self.pool.connection() as conn:
                    exists = repository.find_hash(conn, document_id) is not None
                    same_pdf = None if exists else repository.find_document_by_pdf(conn, digest)
                if exists:
                    seen[digest] = (name, document_id)
                    yield name, (f"đã có {document_id}, bỏ qua (dùng --replace để nhập lại)"
                                 + self._file_into(document_id, folder, catalog))
                    continue
                if same_pdf:
                    seen[digest] = (name, same_pdf)
                    yield name, f"trùng nội dung với {same_pdf} đã có, bỏ qua" + self._file_into(same_pdf, folder, catalog)
                    continue

            started = time.perf_counter()
            try:
                words: dict = {}
                json_bytes = pdf_parser.auto_build_json_from_pdf(pdf_bytes, path.name, words_out=words)
                result = self.import_bytes(pdf_bytes, json_bytes, replace=replace, page_words=words)
            except ImportProblem as problem:
                yield name, "TỪ CHỐI: " + "; ".join(problem.problems[:3])
                continue
            except AlreadyImported as exc:
                yield name, f"LỖI: đã có bản khác của {exc.document_id} (dùng --replace để thay)"
                continue
            except Exception as exc:  # một PDF hỏng không được làm dừng cả lô (lỗi đã ghi log đầy đủ)
                log.exception("Không nhập được %s", name)
                yield name, f"LỖI: {type(exc).__name__}: {exc}"
                continue
            seen[digest] = (name, result.document_id)
            units = json.loads(json_bytes)["legal_units"]
            unsure = sum(1 for unit in units if not unit.get("is_confident", True))
            yield name, (
                f"{result.status} ({result.unit_count} đơn vị, {result.page_count} trang, {unsure} cần soát kỹ, "
                f"{time.perf_counter() - started:.1f} giây) → {result.document_id}"
                + self._file_into(result.document_id, folder, catalog)
            )

    def _file_into(self, document_id: str | None, folder: Category | None, catalog: dict[str, int]) -> str:
        """Xếp văn bản vào danh mục của thư mục; trả phần ghi thêm vào dòng kết quả ("" nếu không xếp)."""
        if document_id is None or folder is None:
            return ""
        with self.pool.connection() as conn:
            repository.upsert_category(conn, folder.code, folder.name, folder.position)
            repository.link_category(conn, document_id, folder.code, position_in_folder(document_id, catalog))
        return f" · danh mục {folder.label}"


def _folder_of(root: Path, path: Path) -> Category | None:
    """Danh mục của một PDF: thư mục con cấp 1 của root, hoặc chính root nếu root là một thư mục đánh số."""
    parts = path.relative_to(root).parts
    if len(parts) > 1:
        return category_from_folder(parts[0])
    own = category_from_folder(root.resolve().name)
    return own if own.numbered else None
