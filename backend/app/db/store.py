"""SQLite 用户存储 (单管理员场景, 预留多用户扩展)"""

import sqlite3
import threading

from app.core.config import get_settings

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def is_initialized() -> bool:
    """是否已创建管理员账号"""
    with _lock, _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return row["c"] > 0


def create_user(username: str, password_hash: str) -> bool:
    with _lock, _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_password_hash(username: str) -> str | None:
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row["password_hash"] if row else None


def update_password(username: str, password_hash: str) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (password_hash, username),
        )
        return cur.rowcount > 0
