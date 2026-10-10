"""Scoped PostgreSQL reads and the documented admin promotion."""

import docker
from docker.models.containers import Container
from pydantic import TypeAdapter, ValidationError

from chaosai.config import Settings
from chaosai.models import DatabaseUser, ProductCounts
from chaosai.target import TargetError, require_scope


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def postgres_sql(container: Container, settings: Settings, database: str, sql: str) -> str:
    container.reload()
    require_scope(container, settings.compose_project)
    if container.status != "running" or container.attrs.get("State", {}).get("Paused", False):
        raise TargetError("Scoped PostgreSQL is not running or is paused.")
    try:
        result = container.exec_run(
            [
                "psql",
                "-U",
                settings.postgres_user,
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
                "-c",
                sql,
            ],
        )
    except (docker.errors.DockerException, OSError) as exc:
        raise TargetError("PostgreSQL query unavailable; stop and check DB readiness.") from exc
    if result.exit_code:
        raise TargetError("PostgreSQL query failed; verify application databases/schema startup.")
    return result.output.decode("utf-8").strip()


def database_users(
    container: Container, settings: Settings, emails: list[str]
) -> list[DatabaseUser]:
    selected = ", ".join(sql_literal(email) for email in emails)
    raw = postgres_sql(
        container,
        settings,
        settings.auth_database,
        "SELECT COALESCE(json_agg(json_build_object('id', id, 'email', email, 'role', role)), "
        f"'[]'::json) FROM \"User\" WHERE email IN ({selected});",
    )
    try:
        return TypeAdapter(list[DatabaseUser]).validate_json(raw)
    except ValidationError as exc:
        raise TargetError(
            "PostgreSQL account-state response is invalid; no auth request sent."
        ) from exc


def database_user(container: Container, settings: Settings, email: str) -> DatabaseUser | None:
    users = database_users(container, settings, [email])
    if any(user.email != email for user in users) or len(users) > 1:
        raise TargetError(
            "PostgreSQL returned unexpected account identities; no auth request sent."
        )
    return users[0] if users else None


def product_counts(container: Container, settings: Settings, slugs: list[str]) -> ProductCounts:
    selected = ", ".join(sql_literal(slug) for slug in slugs)
    raw = postgres_sql(
        container,
        settings,
        settings.products_database,
        "SELECT json_build_object('total', COUNT(*), 'seed', COUNT(*) FILTER "
        f"(WHERE slug IN ({selected})), 'high_stock_seed', COUNT(*) FILTER "
        f'(WHERE slug IN ({selected}) AND stock >= 100000)) FROM "Product";',
    )
    try:
        return ProductCounts.model_validate_json(raw)
    except ValidationError as exc:
        raise TargetError(
            "PostgreSQL product-count response is invalid; seed not verified."
        ) from exc
