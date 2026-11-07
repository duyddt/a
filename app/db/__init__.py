from pathlib import Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .models import Base, DecisionType, ReviewDecision, StructuralErrorType, UnitRevision, User, UserRole
from .session import SessionLocal, engine, get_db, init_db

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_MIGRATION_LOCK = 727274


def create_pool(conninfo: str, wait_seconds: int = 30) -> ConnectionPool:
    pool = ConnectionPool(conninfo, min_size=1, max_size=5, kwargs={"row_factory": dict_row}, open=False)
    pool.open(wait=True, timeout=wait_seconds)
    return pool


def migrate(pool: ConnectionPool, directory: Path = MIGRATIONS_DIR) -> list[str]:
    applied: list[str] = []
    with pool.connection() as conn:
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


__all__ = [
    "Base",
    "DecisionType",
    "ReviewDecision",
    "StructuralErrorType",
    "UnitRevision",
    "User",
    "UserRole",
    "engine",
    "SessionLocal",
    "get_db",
    "init_db",
    "create_pool",
    "migrate",
]
