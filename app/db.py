"""Kết nối PostgreSQL (psycopg 3 + pool) và chạy migration."""
from __future__ import annotations

from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_LOCK = 727274  # khóa tư vấn: hai tiến trình khởi động cùng lúc không chạy migration song song


def create_pool(conninfo: str, wait_seconds: int = 30) -> ConnectionPool:
    pool = ConnectionPool(conninfo, min_size=1, max_size=5, kwargs={"row_factory": dict_row}, open=False)
    pool.open(wait=True, timeout=wait_seconds)
    return pool


def migrate(pool: ConnectionPool, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Chạy các file *.sql chưa áp dụng, theo thứ tự tên, trong một giao dịch. Trả về tên các file vừa chạy."""
    applied: list[str] = []
    with pool.connection() as conn:  # thoát khối with = commit; lỗi = rollback toàn bộ
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied
