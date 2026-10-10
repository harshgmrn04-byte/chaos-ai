"""Repeatable seed without duplicate auth requests or external uploads."""

import docker
import httpx
from docker.models.containers import Container

from chaosai.config import Settings
from chaosai.models import AuthResponse, Category, Product, SeedResult
from chaosai.target import TargetError, require_scope, service_container
from chaosai.target_api import (
    all_products,
    categories,
    login,
    product_body,
    register_body,
    request_json,
)


def auth_sql(container: Container, settings: Settings, sql: str) -> str:
    container.reload()
    require_scope(container, settings.compose_project)
    if container.status != "running" or container.attrs.get("State", {}).get("Paused", False):
        raise TargetError("Seed: scoped PostgreSQL is not running or is paused.")
    result = container.exec_run(
        [
            "psql",
            "-U",
            settings.postgres_user,
            "-d",
            settings.auth_database,
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            sql,
        ],
    )
    if result.exit_code:
        raise TargetError("Seed: auth database SQL failed; verify ecommerce_auth schema/startup.")
    return result.output.decode("utf-8")


def seed_target(
    settings: Settings,
    docker_client: docker.DockerClient,
    client: httpx.Client,
    users: int | None = None,
) -> SeedResult:
    count = settings.seed_users if users is None else users
    if count < 0:
        raise TargetError("Seed users must be non-negative.")
    if len(settings.seed_password.get_secret_value()) < 6:
        raise TargetError("SEED_PASSWORD must contain at least six characters.")
    postgres = service_container(docker_client, settings, "postgres")
    # The target throws duplicate/login errors from unwrapped async controllers. A scoped
    # existence query avoids relying on an HTTP 409/401 that might never be delivered.
    existing = set(auth_sql(postgres, settings, 'SELECT email FROM "User";').splitlines())
    result = SeedResult()
    accounts = [("ChaosPilot Admin", settings.admin_email)] + [
        (f"Load User {index}", settings.load_email_template.format(index=index))
        for index in range(1, count + 1)
    ]
    for name, email in accounts:
        if email in existing:
            result.users_existing += 1
            continue
        registered = request_json(
            client,
            "POST",
            settings.auth_url + "/register",
            AuthResponse,
            stage=f"Register {email}",
            expected=201,
            body=register_body(name, email, settings.seed_password.get_secret_value()),
        )
        if registered.user.email != email:
            raise TargetError("Seed: registration returned a different account email.")
        result.users_created += 1
    # Use the exact documented promotion, then mint a fresh token with the ADMIN role.
    auth_sql(postgres, settings, "UPDATE \"User\" SET role='ADMIN' WHERE email='admin@test.com';")
    auth = login(client, settings)
    if auth.user.role != "ADMIN":
        raise TargetError("Seed: admin promotion did not produce an ADMIN token after login.")
    by_category = {c.slug: c for c in categories(client, settings)}
    by_product = {p.slug: p for p in all_products(client, settings)}
    for category_name, category_slug, names in settings.catalog:
        category = by_category.get(category_slug)
        if category is None:
            category = request_json(
                client,
                "POST",
                settings.product_url + "/categories",
                Category,
                stage=f"Create category {category_slug}",
                expected=201,
                token=auth.accessToken,
                body={"name": category_name, "slug": category_slug},
            )
            by_category[category_slug] = category
            result.categories_created += 1
        for name in names:
            slug = "chaospilot-" + name.lower().replace(" ", "-")
            product = by_product.get(slug)
            if product is None:
                product = request_json(
                    client,
                    "POST",
                    settings.product_url + "/products",
                    Product,
                    stage=f"Create product {slug}",
                    expected=201,
                    token=auth.accessToken,
                    body=product_body(name, slug, category.id),
                )
                by_product[slug] = product
                result.products_created += 1
            if product.stock < 100000 or product.categoryId != category.id:
                raise TargetError(
                    f"Seed: existing product {slug} has stock below 100000 or wrong category. "
                    "Correct it via the target admin UI; no product-update contract is documented."
                )
    return result
