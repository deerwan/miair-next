"""应用级配置 (区别于 engine.config 的业务配置)

- 数据目录: 环境变量 MIAIR_DATA, 默认 ./data
  - data/conf/       引擎业务配置 (config.json / .mi.token / miair.log)
  - data/miair.db    SQLite (管理员账号)
  - data/secret.key  JWT 密钥 (首次启动随机生成并持久化)
"""

import os
import secrets
from functools import lru_cache


class AppSettings:
    def __init__(self):
        self.data_dir = os.path.abspath(os.environ.get("MIAIR_DATA", "data"))
        os.makedirs(self.data_dir, exist_ok=True)

        self.conf_path = os.path.join(self.data_dir, "conf")
        self.db_path = os.path.join(self.data_dir, "miair.db")
        self.token_expire_hours = int(os.environ.get("MIAIR_TOKEN_EXPIRE_HOURS", "24"))

        # 版本更新检查: GitHub 仓库坐标 (owner/repo), 用于查询最新 Release
        self.github_repo = os.environ.get("MIAIR_GITHUB_REPO", "deerwan/miair-next")

        # 登录失败限速: 连续失败 N 次锁定 M 秒
        self.login_max_failures = 5
        self.login_lockout_seconds = 600

        # 仅在面板部署于可信反向代理后时置为 1/true,
        # 直连部署下信任 X-Forwarded-For 会被伪造头绕过登录限速
        self.trust_proxy = os.environ.get("MIAIR_TRUST_PROXY", "").lower() in (
            "1",
            "true",
            "yes",
        )

        self.secret_key = self._load_or_create_secret()

    def _load_or_create_secret(self) -> str:
        """SECRET_KEY 首次启动随机生成并持久化, 不硬编码"""
        path = os.path.join(self.data_dir, "secret.key")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                key = f.read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return key


@lru_cache
def get_settings() -> AppSettings:
    return AppSettings()
