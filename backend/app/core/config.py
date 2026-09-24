"""全局配置。

铁律（约束 C5 / NFR-S-02）：**所有可变参数一律来自环境变量或 .env，禁止硬编码**。
税率、费率、限流配额、平台凭证同理，均不得写死在代码里。
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_JWT_SECRET = "change-me-to-a-64-char-random-string"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ 运行环境
    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    debug: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # ------------------------------------------------------------------ 服务
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # ------------------------------------------------------------------ 主库
    database_url: str = "postgresql+asyncpg://crosspilot_app:crosspilot_app_pwd@localhost:5432/crosspilot"
    database_migration_url: str = "postgresql+asyncpg://crosspilot_owner:crosspilot_owner_pwd@localhost:5432/crosspilot"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    db_statement_timeout_ms: int = 30_000

    # ------------------------------------------------------------------ Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ------------------------------------------------------------------ 鉴权
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_alg: str = "HS256"
    jwt_access_ttl_minutes: int = 15
    jwt_refresh_ttl_days: int = 7
    login_max_failures: int = 5
    login_lock_minutes: int = 15

    # ------------------------------------------------------------------ 字段级加密
    credential_aes_key: str = ""

    # ------------------------------------------------------------------ 平台凭证
    platform_amazon_app_key: str = ""
    platform_amazon_app_secret: str = ""
    platform_shopee_app_key: str = ""
    platform_shopee_app_secret: str = ""
    platform_lazada_app_key: str = ""
    platform_lazada_app_secret: str = ""
    platform_tiktok_app_key: str = ""
    platform_tiktok_app_secret: str = ""
    oauth_redirect_base_url: str = "http://localhost:8000/api/v1/shops/oauth/callback"

    # ------------------------------------------------------------------ 对象存储
    s3_endpoint: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket_product_image: str = "crosspilot-product"
    s3_bucket_certificate: str = "crosspilot-cert"
    s3_bucket_export: str = "crosspilot-export"
    s3_use_ssl: bool = False

    # ------------------------------------------------------------------ 通知渠道
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    wecom_webhook: str = ""
    dingtalk_webhook: str = ""

    # ------------------------------------------------------------------ 派生属性
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def credential_aes_key_bytes(self) -> bytes:
        """返回 32 字节 AES-256 密钥；未配置时返回空串（由调用方决定是否强制）。"""
        if not self.credential_aes_key:
            return b""
        raw = self.credential_aes_key.strip()
        try:
            key = base64.urlsafe_b64decode(raw)
        except Exception:
            key = raw.encode()
        return key

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    def validate_for_runtime(self) -> None:
        """启动期自检：把「配置缺失」从运行期错误提前到启动期。

        开发环境只告警，生产环境直接拒绝启动 —— 明文密钥上生产是灾难级事故。
        """
        problems: list[str] = []
        if self.jwt_secret == _DEFAULT_JWT_SECRET:
            problems.append("JWT_SECRET 仍在用默认值")
        if len(self.credential_aes_key_bytes) != 32:
            problems.append("CREDENTIAL_AES_KEY 缺失或不是 32 字节（需 base64 编码的 32 字节随机数）")
        if any(o == "*" for o in self.cors_origin_list):
            problems.append("CORS_ORIGINS 不允许使用通配符 *")

        if not problems:
            return
        if self.is_prod:
            raise RuntimeError("配置自检失败，拒绝启动：\n  - " + "\n  - ".join(problems))
        import structlog

        structlog.get_logger(__name__).warning(
            "config_self_check_failed",
            problems=problems,
            hint="开发环境放行；生产环境将拒绝启动",
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例。测试中可用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()


settings = get_settings()
