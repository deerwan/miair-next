"""JWT 签发/校验、密码哈希、登录失败限速"""

import time
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings

ALGORITHM = "HS256"

# bcrypt 仅处理前 72 字节, 超长口令需截断 (直接使用 bcrypt, 规避 passlib 与新版
# bcrypt 的版本探测不兼容问题)
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        pw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(subject: str) -> str:
    settings = get_settings()
    payload = {
        "sub": subject,
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.token_expire_hours),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """校验 token, 返回用户名; 无效返回 None"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


class LoginRateLimiter:
    """登录失败限速 (内存记录, 按客户端 IP)"""

    def __init__(self, max_failures: int, lockout_seconds: int):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}

    def is_locked(self, key: str) -> int:
        """返回剩余锁定秒数, 0 表示未锁定"""
        now = time.time()
        window_start = now - self.lockout_seconds
        records = [t for t in self._failures.get(key, []) if t > window_start]
        self._failures[key] = records
        if len(records) >= self.max_failures:
            return int(records[0] + self.lockout_seconds - now) + 1
        return 0

    def record_failure(self, key: str):
        self._failures.setdefault(key, []).append(time.time())

    def reset(self, key: str):
        self._failures.pop(key, None)


_settings = get_settings()
login_limiter = LoginRateLimiter(_settings.login_max_failures, _settings.login_lockout_seconds)
