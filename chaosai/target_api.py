"""HTTP requests using only the contracts in docs/target-notes.md."""

from typing import TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from chaosai.config import Settings
from chaosai.models import Category, Product, ProductPage
from chaosai.target import TargetError

T = TypeVar("T")


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    shape: type[T],
    *,
    stage: str,
    expected: int = 200,
    token: str | None = None,
    body: dict[str, object] | None = None,
    params: dict[str, str | int] | None = None,
    timeout: float | None = None,
) -> T:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request_options: dict[str, object] = {}
    if timeout is not None:
        request_options["timeout"] = timeout
    try:
        response = client.request(
            method, url, json=body, params=params, headers=headers, **request_options
        )
    except httpx.HTTPError as exc:
        raise TargetError(
            f"{stage}: request unavailable or timed out; check service readiness. "
            "Auth duplicate/invalid-credential errors may hang in this target."
        ) from exc
    if response.status_code != expected:
        hint = (
            " Retry after the auth rate-limit window (15 minutes)."
            if response.status_code == 429
            else " Check credentials/admin role."
            if response.status_code in (401, 403)
            else ""
        )
        raise TargetError(f"{stage}: expected HTTP {expected}, got {response.status_code}.{hint}")
    try:
        return TypeAdapter(shape).validate_python(response.json())
    except (ValueError, ValidationError) as exc:
        raise TargetError(
            f"{stage}: response does not match the documented JSON contract."
        ) from exc


def register_body(name: str, email: str, password: str) -> dict[str, object]:
    return {"name": name, "email": email, "password": password}


def product_body(name: str, slug: str, category_id: str) -> dict[str, object]:
    return {
        "name": name,
        "slug": slug,
        "description": "ChaosPilot load-test seed item",
        "price": "12.50",
        "stock": "100000",
        "categoryId": category_id,
        "featured": "true",
    }


def order_body(product: Product) -> dict[str, object]:
    return {
        "items": [
            {"productId": product.id, "name": product.name, "price": product.price, "quantity": 1}
        ],
        "shippingAddress": {
            "street": "1 Test Street",
            "city": "Test City",
            "state": "TS",
            "zip": "12345",
            "country": "US",
        },
        "paymentMethod": "stripe",
    }


def categories(client: httpx.Client, settings: Settings) -> list[Category]:
    return request_json(
        client,
        "GET",
        settings.product_url + "/categories",
        list[Category],
        stage="List categories",
    )


def product_page(client: httpx.Client, settings: Settings, page: int = 1) -> ProductPage:
    return request_json(
        client,
        "GET",
        settings.product_url + "/products",
        ProductPage,
        stage="Browse products",
        params={"page": page, "limit": 100},
    )


def all_products(client: httpx.Client, settings: Settings) -> list[Product]:
    first = product_page(client, settings)
    products = list(first.products)
    for page in range(2, first.pagination.totalPages + 1):
        result = product_page(client, settings, page)
        if result.pagination.page != page:
            raise TargetError("Browse products: pagination did not advance.")
        products.extend(result.products)
    return products
