"""SQLite 连接与迁移运行器。

迁移按 migrations/ 目录下文件名排序依次应用，并在 schema_migrations 中留痕。
"""

import os
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def default_database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "data/app.sqlite3"))


def connect(database_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(database_path) if database_path else default_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def applied_versions(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchall()
    if not rows:
        return set()
    return {
        row["version"]
        for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
    }


def migrate(connection: sqlite3.Connection) -> list[str]:
    """应用所有待执行迁移，返回本次应用的版本名。"""
    already = applied_versions(connection)
    newly_applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in already:
            continue
        connection.executescript(path.read_text(encoding="utf-8"))
        already.add(version)
        newly_applied.append(version)
    if not newly_applied:
        connection.commit()
    return newly_applied
