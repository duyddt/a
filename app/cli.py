"""Nhập văn bản từ dòng lệnh.

    python -m app.cli import --pdf source.pdf --json document.json [--replace]
    python -m app.cli import-dir data/ND254_2026 [--replace]
    python -m app.cli import-all data [--replace]

Cần POSTGRES_HOST (hoặc DATABASE_URL) và STORAGE_DIR, giống khi chạy API.
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
    for p in (p_one, p_dir, p_all):
        p.add_argument("--replace", action="store_true", help="thay bản cũ nếu đã có cùng document_id")
    args = parser.parse_args(argv)

    settings = load_settings()
    if settings.conninfo is None:
        print("Chưa cấu hình PostgreSQL: đặt POSTGRES_HOST (và POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB) hoặc DATABASE_URL.", file=sys.stderr)
        return 2

    from app.db import create_pool, migrate

    pool = create_pool(settings.conninfo)
    try:
        migrate(pool)
        importer = Importer(pool, settings.storage_dir)
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
