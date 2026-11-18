"""Nhập văn bản từ dòng lệnh.

    python -m app.cli import --pdf source.pdf --json document.json [--replace]
    python -m app.cli import-dir data/ND254_2026 [--replace]
    python -m app.cli import-all data [--replace]
    python -m app.cli import-pdfs ../data/sources [--replace]
    python -m app.cli page-words [--document ND254_2026 ...] [--workers 4] [--force]

import-pdfs nhận thư mục chỉ có PDF (kể cả thư mục con): tự tách Điều/Khoản/Điểm, OCR bản scan nếu máy có Tesseract
(OCR_WORKERS trang cùng lúc). Mọi thứ, kể cả PDF gốc, được lưu trong PostgreSQL.

page-words OCR các trang scan chưa có toạ độ từng từ (văn bản nhập trước đây, hoặc nhập kèm document.json), để khung
xem PDF tô vàng đúng vùng của Điều/Khoản/Điểm. Cần Tesseract kèm gói 'vie'; dừng giữa chừng thì lần sau làm tiếp.

Cần POSTGRES_HOST (hoặc DATABASE_URL), giống khi chạy API.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import load_settings
from app.services.importer import AlreadyImported, Importer
from app.services.models import ImportProblem


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Nhập văn bản vào PostgreSQL")
    sub = parser.add_subparsers(dest="command", required=True)
    p_one = sub.add_parser("import", help="một văn bản từ hai tệp")
    p_one.add_argument("--pdf", required=True, type=Path)
    p_one.add_argument("--json", required=True, type=Path)
    p_dir = sub.add_parser("import-dir", help="một thư mục có source.pdf và document.json")
    p_dir.add_argument("directory", type=Path)
    p_all = sub.add_parser("import-all", help="mọi thư mục con của một thư mục")
    p_all.add_argument("directory", type=Path)
    p_pdfs = sub.add_parser("import-pdfs", help="mọi PDF trong một thư mục (không cần document.json, tự OCR bản scan)")
    p_pdfs.add_argument("directory", type=Path)
    for p in (p_one, p_dir, p_all, p_pdfs):
        p.add_argument("--replace", action="store_true", help="thay bản cũ nếu đã có cùng document_id")
    p_words = sub.add_parser("page-words", help="OCR toạ độ từng từ cho các trang scan (để tô vàng đúng vùng)")
    p_words.add_argument("--document", action="append", help="chỉ văn bản này (lặp lại được); mặc định: mọi văn bản")
    p_words.add_argument("--workers", type=int, default=None, help="số trang OCR cùng lúc (mặc định OCR_WORKERS)")
    p_words.add_argument("--force", action="store_true", help="OCR lại cả các trang đã có toạ độ")
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings.conninfo is None:
        print("Chưa cấu hình PostgreSQL: đặt POSTGRES_HOST (và POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB) hoặc DATABASE_URL.", file=sys.stderr)
        return 2

    from app.db import create_pool, migrate

    pool = create_pool(settings.conninfo)
    try:
        migrate(pool)
        if args.command == "page-words":
            from app.services.page_words import backfill

            try:
                backfill(pool, args.document, args.workers, args.force, report=lambda line: print(line, flush=True))
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
                return 2
            return 0
        importer = Importer(pool)
        if args.command == "import-pdfs":
            failed = False
            for name, outcome in importer.import_pdfs(args.directory, replace=args.replace):
                print(f"{name}: {outcome}", flush=True)
                failed = failed or outcome.startswith(("TỪ CHỐI", "LỖI"))
            return 1 if failed else 0
        if args.command == "import-all":
            outcomes = importer.import_all(args.directory, replace=args.replace)
            for name, outcome in outcomes:
                print(f"{name}: {outcome}")
            return 1 if any(o.startswith("LỖI") for _, o in outcomes) else 0
        try:
            if args.command == "import":
                result = importer.import_bytes(args.pdf.read_bytes(), args.json.read_bytes(), replace=args.replace)
            else:
                result = importer.import_dir(args.directory, replace=args.replace)
        except ImportProblem as problem:
            print(f"Không nhập được {problem.document_id}:", file=sys.stderr)
            for line in problem.problems:
                print(f"  - {line}", file=sys.stderr)
            return 1
        except AlreadyImported as exc:
            print(f"Đã có văn bản {exc.document_id} với nội dung khác. Thêm --replace để thay bản cũ.", file=sys.stderr)
            return 1
        print(f"{result.document_id}: {result.status} ({result.unit_count} đơn vị, {result.page_count} trang)")
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
