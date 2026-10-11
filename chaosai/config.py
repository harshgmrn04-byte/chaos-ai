"""Environment configuration; offline operation never loads a dotenv file."""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_root: Path = PROJECT_ROOT
    target_dir: Path = Path("../ecommerce-microservices")
    compose_project: str = "ecommerce-microservices"
    compose_file: str = "docker-compose.yml"
    overrides_file: Path = Path("overrides.yaml")
    state_dir: Path = Path(".chaosai")
    llm_provider: Literal["gemini", "mock", "none"] = "none"
    gemini_model: str | None = None

    frontend_url: str = "http://localhost:3000"
    load_duration_s: float = Field(default=30, gt=0)
    load_users: int = Field(default=10, ge=1, le=150)
    load_timeout_s: float = Field(default=5, gt=0, le=5)
    checkout_rps: float = Field(default=1, gt=0)
    login_rate_cap: float = Field(default=1, gt=0)
    token_refresh_s: float = Field(default=600, gt=0, le=600)
    think_min_s: float = Field(default=0.2, ge=0.2, le=1)
    think_max_s: float = Field(default=1, ge=0.2, le=1)
    warmup_s: float = Field(default=30, ge=0)
    baseline_duration_s: float = Field(default=120, gt=0)
    quick_warmup_s: float = Field(default=10, ge=0)
    quick_duration_s: float = Field(default=40, gt=0)
    precheck_timeout_s: float = Field(default=60, gt=0, le=60)
    recovery_timeout_s: float = Field(default=120, gt=0, le=120)
    experiment_log_line_chars: int = Field(default=300, ge=1, le=1000)
    metrics_interval_s: float = Field(default=5, gt=0)

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
    admin_password: SecretStr | None = None
    auth_timeout_s: float = Field(default=4, ge=3, le=5)
    auth_workdir: str = "/app"
    products_database: str = "ecommerce_products"
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

    @model_validator(mode="after")
    def validate_think_time(self) -> "Settings":
        if self.think_max_s < self.think_min_s:
            raise ValueError("THINK_MAX_S must be at least THINK_MIN_S")
        return self

    @property
    def admin_login_password(self) -> SecretStr:
        return self.admin_password or self.seed_password

    @property
    def resolved_target_dir(self) -> Path:
        return (self.project_root / self.target_dir).resolve()

    @property
    def resolved_overrides_file(self) -> Path:
        return self.project_root / self.overrides_file

    @property
    def llm_key_present(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    @property
    def runs_dir(self) -> Path:
        return self.project_root / "runs"

    @property
    def safety_dir(self) -> Path:
        return self.project_root / self.state_dir

    @property
    def kill_path(self) -> Path:
        return self.safety_dir / "KILL"

    @property
    def operation_lock_path(self) -> Path:
        return self.safety_dir / "operation.lock"

    @property
    def active_lock_path(self) -> Path:
        return self.safety_dir / "active.lock"

    def journal_path(self, run_dir: Path) -> Path:
        return run_dir / "journal.json"


def load_config(*, offline: bool = False, project_root: Path = PROJECT_ROOT) -> Settings:
    if not offline:
        load_dotenv(project_root / ".env", override=False)
    provider = os.getenv("LLM_PROVIDER") or (
        "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") else "none"
    )
    target_options: dict[str, str] = {}
    for field, variable in (
        ("frontend_url", "FRONTEND_URL"),
        ("load_duration_s", "LOAD_DURATION_S"),
        ("load_users", "LOAD_USERS"),
        ("think_min_s", "THINK_MIN_S"),
        ("think_max_s", "THINK_MAX_S"),
        ("metrics_interval_s", "METRICS_INTERVAL_S"),
        ("load_timeout_s", "LOAD_TIMEOUT_S"),
        ("checkout_rps", "CHECKOUT_RPS"),
        ("login_rate_cap", "LOGIN_RATE_CAP"),
        ("token_refresh_s", "TOKEN_REFRESH_S"),
        ("warmup_s", "WARMUP_S"),
        ("baseline_duration_s", "BASELINE_DURATION_S"),
        ("auth_url", "AUTH_URL"),
        ("product_url", "PRODUCT_URL"),
        ("order_url", "ORDER_URL"),
        ("rabbitmq_url", "RABBITMQ_MANAGEMENT_URL"),
        ("rabbitmq_user", "RABBITMQ_USER"),
        ("rabbitmq_password", "RABBITMQ_PASSWORD"),
        ("rabbitmq_vhost", "RABBITMQ_VHOST"),
        ("seed_users", "SEED_USERS"),
        ("seed_password", "SEED_PASSWORD"),
        ("admin_password", "ADMIN_PASSWORD"),
        ("auth_timeout_s", "AUTH_TIMEOUT_S"),
        ("request_timeout_s", "REQUEST_TIMEOUT_S"),
        ("notification_interval_s", "NOTIFICATION_INTERVAL_S"),
    ):
        value = os.getenv(variable)
        if value is not None and (value or field == "seed_password"):
            target_options[field] = value
    return Settings(
        **target_options,
        project_root=project_root,
        target_dir=Path(os.getenv("TARGET_DIR") or "../ecommerce-microservices"),
        compose_project=os.getenv("COMPOSE_PROJECT") or "ecommerce-microservices",
        llm_provider="none" if offline else provider,
        gemini_model=os.getenv("GEMINI_MODEL") or None,
    )
