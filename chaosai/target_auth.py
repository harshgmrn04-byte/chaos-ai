"""Avoid the target's unsafe duplicate/invalid-credential HTTP paths."""

import docker
import httpx
from docker.models.containers import Container
from pydantic import SecretStr, ValidationError

from chaosai.config import Settings
from chaosai.models import AuthResponse, CredentialCheck
from chaosai.target import TargetError, require_scope, service_container
from chaosai.target_api import TargetRequestError, register_body, request_json
from chaosai.target_db import database_user

# Reuse the target's installed comparison implementation. Never emit hashes/passwords,
# persist a helper file, load dotenv, or invoke the HTTP error path as a credential probe.
CREDENTIAL_CHECK = """
const { PrismaClient } = require('@prisma/client');
const bcrypt = require('bcrypt');
const prisma = new PrismaClient();
const deadline = setTimeout(() => process.exit(3), Number(process.env.CHAOSAI_AUTH_TIMEOUT_MS));
(async () => {
  try {
    const user = await prisma.user.findUnique({
      where: { email: process.env.CHAOSAI_AUTH_EMAIL }, select: { password: true }
    });
    const matches = !!user && await bcrypt.compare(
      process.env.CHAOSAI_AUTH_PASSWORD, user.password
    );
    process.stdout.write(JSON.stringify({ exists: !!user, password_matches: matches }));
  } catch (_) { process.exitCode = 2; }
  finally { await prisma.$disconnect(); clearTimeout(deadline); }
})();
"""


def credential_check(
    docker_client: docker.DockerClient, settings: Settings, email: str, password: SecretStr
) -> CredentialCheck:
    auth = service_container(docker_client, settings, "auth-service")
    require_scope(auth, settings.compose_project)
    try:
        result = auth.exec_run(
            ["node", "-e", CREDENTIAL_CHECK],
            workdir=settings.auth_workdir,
            environment={
                "CHAOSAI_AUTH_EMAIL": email,
                "CHAOSAI_AUTH_PASSWORD": password.get_secret_value(),
                "CHAOSAI_AUTH_TIMEOUT_MS": str(int(settings.auth_timeout_s * 1000)),
            },
        )
    except (docker.errors.DockerException, OSError) as exc:
        raise TargetError(
            "Credential check unavailable; inspect auth/PostgreSQL. "
            "No further auth request will be sent."
        ) from exc
    if result.exit_code:
        raise TargetError(
            "Credential check failed/timed out; inspect auth/PostgreSQL. "
            "No further auth request will be sent."
        )
    try:
        return CredentialCheck.model_validate_json(result.output)
    except ValidationError as exc:
        raise TargetError(
            "Credential check returned invalid evidence. No further auth request will be sent."
        ) from exc


def require_credentials(
    docker_client: docker.DockerClient,
    postgres: Container,
    settings: Settings,
    email: str,
    password: SecretStr,
    *,
    request_sent: bool = False,
) -> None:
    action = "No login retry sent." if request_sent else "No login sent."
    if database_user(postgres, settings, email) is None:
        raise TargetError(f"{email} is absent from ecommerce_auth; run chaosai seed. {action}")
    check = credential_check(docker_client, settings, email, password)
    if not check.exists:
        raise TargetError(f"{email} disappeared during credential verification. {action}")
    if not check.password_matches:
        variable = "ADMIN_PASSWORD" if email == settings.admin_email else "SEED_PASSWORD"
        raise TargetError(
            f"{email} exists but the password does not match; set its existing password "
            f"in {variable} in chaos-ai/.env. {action} Existing password was not changed."
        )


def register_absent(
    docker_client: docker.DockerClient,
    postgres: Container,
    settings: Settings,
    client: httpx.Client,
    name: str,
    email: str,
    password: SecretStr,
) -> tuple[bool, str | None]:
    if database_user(postgres, settings, email) is not None:
        require_credentials(docker_client, postgres, settings, email, password)
        return False, None
    if len(password.get_secret_value()) < 6:
        raise TargetError(
            f"Register {email}: configured password must have at least six characters."
        )
    warning = None
    try:
        registered = request_json(
            client,
            "POST",
            settings.auth_url + "/register",
            AuthResponse,
            stage=f"Register {email}",
            expected=201,
            timeout=settings.auth_timeout_s,
            body=register_body(name, email, password.get_secret_value()),
        )
        if registered.user.email != email:
            raise TargetError("Registration returned a different account email.")
    except TargetError as exc:
        # Never blindly retry: a lost response can follow a committed registration or a
        # concurrent duplicate. Only a fresh DB read plus password match permits reuse.
        if database_user(postgres, settings, email) is None:
            raise TargetError(
                f"Register {email} failed; database recheck confirms user is absent. "
                "Check auth readiness/rate limit, then rerun seed; registration was not retried."
            ) from exc
        require_credentials(docker_client, postgres, settings, email, password)
        warning = (
            f"Register {email} response failed; database recheck confirms account exists "
            "and configured credentials match. No registration retry."
        )
        return True, warning
    require_credentials(docker_client, postgres, settings, email, password)
    return True, warning


def safe_admin_login(
    docker_client: docker.DockerClient,
    postgres: Container,
    settings: Settings,
    client: httpx.Client,
) -> AuthResponse:
    email = settings.admin_email
    password = settings.admin_login_password
    require_credentials(docker_client, postgres, settings, email, password)
    try:
        auth = request_json(
            client,
            "POST",
            settings.auth_url + "/login",
            AuthResponse,
            stage="Login admin",
            timeout=settings.auth_timeout_s,
            body={"email": email, "password": password.get_secret_value()},
        )
    except TargetError as exc:
        # Re-evaluate after a disconnect, timeout, or unexpected response; existence alone
        # cannot distinguish bad credentials from service/network failure.
        require_credentials(docker_client, postgres, settings, email, password, request_sent=True)
        status = f" Observed HTTP {exc.status}." if isinstance(exc, TargetRequestError) else ""
        raise TargetError(
            f"Login admin failed; database recheck confirms {email} exists and credentials match. "
            "Check auth restart/readiness/rate limit, then rerun; "
            f"no automatic login retry.{status}"
        ) from exc
    if auth.user.email != email:
        raise TargetError("Login returned a different account identity.")
    return auth
