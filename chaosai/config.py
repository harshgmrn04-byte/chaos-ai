"""Environment configuration; offline operation never loads a dotenv file."""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_root: Path = PROJECT_ROOT
    target_dir: Path = Path("../ecommerce-microservices")
    compose_project: str = "ecommerce-microservices"
    llm_provider: Literal["gemini", "mock", "none"] = "none"
    gemini_model: str | None = None

    auth_url: str = "http://localhost:3004"
    product_url: str = "http://localhost:3002"
    order_url: str = "http://localhost:3003"
    rabbitmq_url: str = "http://localhost:15672"
    rabbitmq_user: str = "ecommerce"
    rabbitmq_password: SecretStr = SecretStr("ecommerce123")
    rabbitmq_vhost: str = "/"
    notification_queue: str = "notifications"
    seed_users: int = Field(default=20, ge=0)
    seed_password: SecretStr = SecretStr("ChaosPilotTest123!")
    admin_email: str = "admin@test.com"
    load_email_template: str = "loaduser{index:03d}@test.com"
    postgres_user: str = "ecommerce"
    auth_database: str = "ecommerce_auth"
    request_timeout_s: float = Field(default=10, gt=0)
    build_timeout_s: float = Field(default=600, gt=0)
    notification_interval_s: float = Field(default=6, gt=0)
    notification_samples: int = Field(default=3, ge=2, le=10)
    notification_log_tail: int = Field(default=300, ge=1)
    catalog: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        ("ChaosPilot Electronics", "chaospilot-electronics", ("Test Headphones", "Test Keyboard")),
        ("ChaosPilot Home", "chaospilot-home", ("Test Lamp", "Test Mug")),
    )
    expected_services: tuple[str, ...] = (
        "postgres",
        "mongodb",
        "redis",
        "rabbitmq",
        "mailhog",
        "auth-service",
        "product-service",
        "order-service",
        "notification-service",
        "frontend",
    )
    published_services: tuple[str, ...] = (
        "postgres",
        "mongodb",
        "redis",
        "rabbitmq",
        "mailhog",
        "auth-service",
        "product-service",
        "order-service",
        "frontend",
    )

    @property
    def resolved_target_dir(self) -> Path:
        return (self.project_root / self.target_dir).resolve()

    @property
    def llm_key_present(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    @property
    def runs_dir(self) -> Path:
        return self.project_root / "runs"


def load_config(*, offline: bool = False, project_root: Path = PROJECT_ROOT) -> Settings:
    if not offline:
        load_dotenv(project_root / ".env", override=False)
    provider = os.getenv("LLM_PROVIDER") or (
        "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") else "none"
    )
    target_options: dict[str, str] = {}
    for field, variable in (
        ("auth_url", "AUTH_URL"),
        ("product_url", "PRODUCT_URL"),
        ("order_url", "ORDER_URL"),
        ("rabbitmq_url", "RABBITMQ_MANAGEMENT_URL"),
        ("rabbitmq_user", "RABBITMQ_USER"),
        ("rabbitmq_password", "RABBITMQ_PASSWORD"),
        ("rabbitmq_vhost", "RABBITMQ_VHOST"),
        ("seed_users", "SEED_USERS"),
        ("seed_password", "SEED_PASSWORD"),
        ("request_timeout_s", "REQUEST_TIMEOUT_S"),
        ("notification_interval_s", "NOTIFICATION_INTERVAL_S"),
    ):
        if value := os.getenv(variable):
            target_options[field] = value
    return Settings(
        **target_options,
        project_root=project_root,
        target_dir=Path(os.getenv("TARGET_DIR") or "../ecommerce-microservices"),
        compose_project=os.getenv("COMPOSE_PROJECT") or "ecommerce-microservices",
        llm_provider="none" if offline else provider,
        gemini_model=os.getenv("GEMINI_MODEL") or None,
    )
