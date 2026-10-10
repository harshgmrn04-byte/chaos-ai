"""Repeatable seed without duplicate auth requests or external uploads."""

import docker
import httpx
from docker.models.containers import Container

from chaosai.config import Settings
from chaosai.models import Category, Product, SeedResult
from chaosai.target import TargetError, service_container
from chaosai.target_api import (
    all_products,
    categories,
    product_body,
    request_json,
)
from chaosai.target_auth import register_absent, safe_admin_login
from chaosai.target_db import database_users, postgres_sql, product_counts


def auth_sql(container: Container, settings: Settings, sql: str) -> str:
    return postgres_sql(container, settings, settings.auth_database, sql)


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
    result = SeedResult()
    accounts = [("ChaosPilot Admin", settings.admin_email, settings.admin_login_password)] + [
        (
            f"Load User {index}",
            settings.load_email_template.format(index=index),
            settings.seed_password,
        )
        for index in range(1, count + 1)
    ]
    for name, email, password in accounts:
        created, warning = register_absent(
            docker_client, postgres, settings, client, name, email, password
        )
        result.users_created += int(created)
        result.users_existing += int(not created)
        if warning:
            result.warnings.append(warning)
    # Use the exact documented promotion, then mint a fresh token with the ADMIN role.
    auth_sql(postgres, settings, "UPDATE \"User\" SET role='ADMIN' WHERE email='admin@test.com';")
    auth = safe_admin_login(docker_client, postgres, settings, client)
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
    expected_emails = {email for _, email, _ in accounts}
    verified = database_users(postgres, settings, sorted(expected_emails))
    if {user.email for user in verified} != expected_emails:
        raise TargetError(
            "Seed database verification failed: missing/unexpected account identities."
        )
    if not any(user.email == settings.admin_email and user.role == "ADMIN" for user in verified):
        raise TargetError("Seed database verification failed: admin role is not ADMIN.")
    slugs = [
        "chaospilot-" + name.lower().replace(" ", "-")
        for _, _, names in settings.catalog
        for name in names
    ]
    counts = product_counts(postgres, settings, slugs)
    if counts.seed != len(slugs) or counts.high_stock_seed != len(slugs):
        raise TargetError(
            "Seed database verification failed: seed product count/high stock mismatch."
        )
    result.verified_users = len(verified)
    result.verified_admin = True
    result.verified_products = counts.seed
    result.product_count = counts.total
    return result
