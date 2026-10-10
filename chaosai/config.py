"""Environment configuration; offline operation never loads a dotenv file."""

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_root: Path = PROJECT_ROOT
    target_dir: Path = Path("../ecommerce-microservices")
    compose_project: str = "ecommerce-microservices"
    llm_provider: Literal["gemini", "mock", "none"] = "none"
    gemini_model: str | None = None

    @property
    def runs_dir(self) -> Path:
        return self.project_root / "runs"


def load_config(*, offline: bool = False, project_root: Path = PROJECT_ROOT) -> Settings:
    if not offline:
        load_dotenv(project_root / ".env", override=False)
    provider = os.getenv("LLM_PROVIDER") or (
        "gemini" if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") else "none"
    )
    return Settings(
        project_root=project_root,
        target_dir=Path(os.getenv("TARGET_DIR") or "../ecommerce-microservices"),
        compose_project=os.getenv("COMPOSE_PROJECT") or "ecommerce-microservices",
        llm_provider="none" if offline else provider,
        gemini_model=os.getenv("GEMINI_MODEL") or None,
    )
