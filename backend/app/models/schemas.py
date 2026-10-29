"""API 请求/响应模型"""

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

# ---- 认证 ----

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _require_password_strength(value: str) -> str:
    """面板可暴露公网, 阻止纯数字/纯字母弱口令"""
    if len(value.encode("utf-8")) > 72:
        raise ValueError("密码 UTF-8 编码长度不能超过 72 字节")
    if not any(c.isalpha() for c in value) or not any(c.isdigit() for c in value):
        raise ValueError("密码需同时包含字母和数字")
    return value


class SetupRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        return _require_password_strength(v)

    @model_validator(mode="after")
    def _password_differs_from_username(self):
        """用户名即密码是最常见的弱口令组合 (admin/admin), 直接拒绝"""
        if self.password == self.username:
            raise ValueError("密码不能与用户名相同")
        return self


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, v: str) -> str:
        return _require_password_strength(v)


class LoginStatusResponse(BaseModel):
    initialized: bool


# ---- 设置 ----

class SpeakerSettingPayload(BaseModel):
    dlna_name: str | None = None
    compatibility_mode: bool | None = None


class SettingsPayload(BaseModel):
    """保存设置 (字段均可选, 只更新提交的字段)"""
    account: str | None = None
    password: str | None = None
    cookie: str | None = None
    mi_did: str | None = None
    dlna_port: int | None = Field(default=None, ge=1, le=65535)
    airplay_mode: Literal["airplay1", "airplay2"] | None = None
    airplay2_target_did: str | None = None
    airplay2_port: int | None = Field(default=None, ge=1, le=65535)
    airplay2_output_latency_ms: int | None = Field(default=None, ge=0, le=4000)
    auto_play_on_set_uri: bool | None = None
    auto_resume_on_interrupt: bool | None = None
    resume_delay_seconds: int | None = Field(default=None, ge=1, le=15)
    default_volume: int | None = Field(default=None, ge=1, le=100)
    follow_device_volume: bool | None = None
    auto_restart: bool | None = None
    default_cover_url: str | None = None
    default_audio_id: str | None = None
    touchscreen_lyrics: bool | None = None
    notify_type: str | None = None
    notify_feishu_webhook: str | None = None
    notify_feishu_secret: str | None = None
    notify_wxpusher_spt: str | None = None
    spotify_enabled: bool | None = None
    spotify_device_name: str | None = None
    spotify_did: str | None = None
    spotify_api_key: str | None = None
    speakers: dict[str, SpeakerSettingPayload] | None = None


# ---- 音箱 ----

class RenameRequest(BaseModel):
    dlna_name: str = Field(min_length=1, max_length=64)


class PlayUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def _require_http_scheme(cls, v: str) -> str:
        """投送的 URL 最终由音箱/后端拉流, 仅允许 http/https"""
        if urlparse(v).scheme.lower() not in ("http", "https"):
            raise ValueError("仅支持 http/https URL")
        return v


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


# ---- 小米账号交互式登录 ----


class PasswordLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginCaptchaRequest(BaseModel):
    session_id: str = Field(min_length=1)
    captcha: str = Field(min_length=1)


class LoginVerifyRequest(BaseModel):
    session_id: str = Field(min_length=1)
    code: str = Field(min_length=1)


class ManualTokenRequest(BaseModel):
    user_id: str = Field(min_length=1)
    pass_token: str = Field(min_length=1)
