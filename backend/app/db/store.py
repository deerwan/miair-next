"""SQLite 存储: 用户 (单管理员场景, 预留多用户扩展) + 面板登录日志"""

import sqlite3
import threading
from datetime import datetime

from app.core.config import get_settings

_lock = threading.Lock()

# 登录日志保留条数上限 (超出后淘汰最旧记录)
LOGIN_LOG_MAX_ROWS = 100


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                ip TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL
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


def add_login_log(username: str, ip: str, success: bool):
    """记录一次面板登录尝试, 并淘汰超出上限的最旧记录"""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO login_logs (username, ip, success, created_at) VALUES (?, ?, ?, ?)",
            (username, ip, int(success), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.execute(
            "DELETE FROM login_logs WHERE id NOT IN "
            "(SELECT id FROM login_logs ORDER BY id DESC LIMIT ?)",
            (LOGIN_LOG_MAX_ROWS,),
        )


def get_login_logs(limit: int = 50) -> list[dict]:
    """最近的登录日志 (新的在前)"""
    with _lock, _connect() as conn:
        rows = conn.execute(
            "SELECT username, ip, success, created_at FROM login_logs "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "username": r["username"],
                "ip": r["ip"],
                "success": bool(r["success"]),
                "time": r["created_at"],
            }
            for r in rows
        ]
