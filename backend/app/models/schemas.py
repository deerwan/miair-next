"""API 请求/响应模型"""

from pydantic import BaseModel, Field

# ---- 认证 ----

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SetupRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


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
    auto_play_on_set_uri: bool | None = None
    auto_resume_on_interrupt: bool | None = None
    resume_delay_seconds: int | None = Field(default=None, ge=1, le=15)
    default_volume: int | None = Field(default=None, ge=1, le=100)
    follow_device_volume: bool | None = None
    auto_restart: bool | None = None
    notify_type: str | None = None
    notify_feishu_webhook: str | None = None
    notify_feishu_secret: str | None = None
    notify_wxpusher_spt: str | None = None
    speakers: dict[str, SpeakerSettingPayload] | None = None


# ---- 音箱 ----

class RenameRequest(BaseModel):
    dlna_name: str = Field(min_length=1, max_length=64)


class PlayUrlRequest(BaseModel):
    url: str = Field(min_length=1)


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)
