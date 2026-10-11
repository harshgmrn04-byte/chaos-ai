"""Explicit local auth sandbox patch; third-party checkout stays read-only."""

import hashlib
import subprocess
from pathlib import Path

import docker

from chaosai.config import Settings
from chaosai.models import JournalData
from chaosai.runs import read_json, write_json
from chaosai.safety import SafetyError, file_lock, kill_requested
from chaosai.target import get_docker_client, service_container


def assert_idle(settings: Settings) -> None:
    if kill_requested(settings):
        raise SafetyError("KILL is set; review rollback before explicitly resuming.")
    for path in settings.runs_dir.glob("*/journal.json"):
        data = read_json(path, object)
        journal = JournalData() if data == [] else JournalData.model_validate(data)
        if any(not r.done for r in journal.records):
            raise SafetyError("Pending undo blocks sandbox setup; run chaosai rollback.")


def patched_auth(source: str) -> str:
    old = "  max: 100,"
    if source.count(old) != 1:
        raise SafetyError("Auth source differs from the audited rate limiter; patch refused.")
    return source.replace(
        old,
        "  max: Math.min(10000, Math.max(100, Number(process.env.AUTH_RATE_LIMIT_MAX) || 100)),",
    )


def sandbox_overlay(settings: Settings) -> Path | None:
    marker = settings.safety_dir / "sandbox.json"
    if not marker.exists():
        return None
    data = read_json(marker, object)
    overlay = settings.safety_dir / "sandbox.compose.yaml"
    if data.get("project") != settings.compose_project:
        raise SafetyError("Sandbox configuration belongs to another Compose project.")
    if not overlay.is_file():
        raise SafetyError("Sandbox overlay missing; rerun chaosai sandbox.")
    if hashlib.sha256(overlay.read_bytes()).hexdigest() != data.get("overlay_sha256"):
        raise SafetyError("Sandbox overlay changed; rerun chaosai sandbox to review it.")
    return overlay


def setup_sandbox(settings: Settings, *, restore: bool = False) -> None:
    """Recreate only scoped auth; preserve image/config rollback and database volumes."""
    root = settings.safety_dir
    root.mkdir(parents=True, exist_ok=True)
    with (
        file_lock(settings.active_lock_path, blocking=False),
        file_lock(settings.operation_lock_path),
    ):
        assert_idle(settings)
        marker = root / "sandbox.json"
        previous = read_json(marker, object) if marker.exists() else {}
        if previous and previous.get("project") != settings.compose_project:
            raise SafetyError("Sandbox configuration belongs to another Compose project.")
        client = get_docker_client()
        try:
            auth = service_container(client, settings, "auth-service")
            if restore:
                marker = root / "sandbox.json"
                if not marker.exists():
                    raise SafetyError("No sandbox patch is active.")
                old = read_json(marker, object)
                if old.get("project") != settings.compose_project:
                    raise SafetyError("Sandbox patch belongs to another project.")
                overlay = root / "sandbox.restore.yaml"
                overlay.write_text(
                    "services:\n  auth-service:\n    image: " + old["original_image"] + "\n",
                    encoding="utf-8",
                )
            else:
                source = settings.resolved_target_dir / "auth-service/src/index.ts"
                patched = patched_auth(source.read_text(encoding="utf-8"))
                owned = root / "sandbox-auth"
                owned.mkdir(exist_ok=True)
                (owned / "index.ts").write_text(patched, encoding="utf-8")
                (owned / "Dockerfile").write_text(
                    "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nCOPY index.ts /app/src/index.ts\n"
                    "RUN npm run build\n",
                    encoding="utf-8",
                )
                image = (
                    f"chaospilot-sandbox-auth:{hashlib.sha256(patched.encode()).hexdigest()[:12]}"
                )
                base = auth.attrs["Image"]
                try:
                    client.images.get(base)
                except docker.errors.ImageNotFound:
                    snapshot = client.api.commit(
                        auth.id, repository="chaospilot-auth-base", tag="preserved", pause=False
                    )
                    base = snapshot["Id"]
                base_tag = f"chaospilot-auth-base:{base.removeprefix('sha256:')[:12]}"
                _run(["docker", "image", "tag", base, base_tag], settings)
                _run(
                    [
                        "docker",
                        "build",
                        "--build-arg",
                        f"BASE_IMAGE={base_tag}",
                        "--tag",
                        image,
                        str(owned),
                    ],
                    settings,
                )
                overlay = root / "sandbox.compose.yaml"
                overlay.write_text(
                    f"services:\n  auth-service:\n    image: {image}\n"
                    f"    build:\n      context: {owned}\n      args:\n"
                    f"        BASE_IMAGE: {base_tag}\n"
                    '    environment:\n      AUTH_RATE_LIMIT_MAX: "6000"\n',
                    encoding="utf-8",
                )
                # Durable restoration information exists BEFORE container recreation.
                write_json(
                    root / "sandbox.json",
                    {
                        "project": settings.compose_project,
                        "original_image": previous.get("original_image", base),
                        "patched_image": image,
                        "auth_limit_per_15m": 6000,
                        "overlay_sha256": hashlib.sha256(overlay.read_bytes()).hexdigest(),
                        "source_sha256": hashlib.sha256(patched.encode()).hexdigest(),
                    },
                )
            _run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    settings.compose_project,
                    "--file",
                    str(settings.resolved_target_dir / settings.compose_file),
                    "--file",
                    str(overlay),
                    "up",
                    "-d",
                    "--no-deps",
                    "--no-build",
                    "auth-service",
                ],
                settings,
            )
            if restore:
                (root / "sandbox.json").unlink()
        finally:
            client.close()


def _run(command: list[str], settings: Settings) -> None:
    result = subprocess.run(command, capture_output=True, timeout=settings.build_timeout_s)
    if result.returncode:
        from chaosai.evidence import redact_line

        safe = [
            redact_line(line, settings)
            for line in result.stderr.decode("utf-8", errors="replace").splitlines()
        ]
        settings.safety_dir.mkdir(parents=True, exist_ok=True)
        (settings.safety_dir / "sandbox-build.log").write_text("\n".join(safe))
        raise SafetyError(
            "Sandbox build/start failed; original target files/volumes retained. "
            "Build output withheld because dependencies may print credentials."
        )
