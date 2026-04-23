from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


class DiscordSettings(BaseModel):
    token: str = ""
    guild_id: str = ""
    allowed_roles: str = "Admin,DevOps"


class FeatureSettings(BaseModel):
    enable_docker: bool = False
    power_control_enabled: bool = False
    power_control_password: str = ""

    @field_validator("enable_docker", "power_control_enabled", mode="before")
    @classmethod
    def validate_bool(cls, value: Any) -> bool:
        return _parse_bool(value)


class WebUISettings(BaseModel):
    enabled: bool = True
    password: str = ""

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_bool(cls, value: Any) -> bool:
        return _parse_bool(value)


class ServerSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    alias: str
    host: str = ""
    user: str = "root"
    port: int = 22
    auth_method: Literal["key", "password"] = "key"
    password: str = ""
    key: str = ""
    allowed_roles: str = ""
    backup_path: str = ""
    original_alias: str | None = Field(default=None, alias="_original_alias")


class AppConfig(BaseModel):
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    webui: WebUISettings = Field(default_factory=WebUISettings)
    servers: list[ServerSettings] = Field(default_factory=list)


class TestServerRequest(ServerSettings):
    trust_host: bool = False

    @field_validator("trust_host", mode="before")
    @classmethod
    def validate_trust_host(cls, value: Any) -> bool:
        return _parse_bool(value)


class SaveConfigRequest(AppConfig):
    model_config = ConfigDict(extra="allow")


class SetupRequest(BaseModel):
    password: str = Field(min_length=8)


class RestoreConfigResponse(BaseModel):
    status: str
    restored_servers: int
