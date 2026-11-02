"""Loads config.toml: app settings, sort categories and the model list."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
# Secrets (ANTHROPIC_API_KEY) and env overrides live in .env; real env vars win.
load_dotenv(ROOT / ".env")
CONFIG_FILE = Path(os.environ.get("PICS_CONFIG", ROOT / "config.toml"))
PROVIDERS = ("anthropic", "ollama")


@dataclass(frozen=True)
class ModelConfig:
    id: str
    label: str
    provider: str
    effort: str = "low"
    fallbacks: bool = False
    host: str = "http://localhost:11434"
    concurrency: int = 4


@dataclass(frozen=True)
class Config:
    port: int
    sort_dir: Path
    default_model: str
    max_edge: int
    categories: dict[str, str]  # name -> description for the prompt
    models: dict[str, ModelConfig]


def load() -> Config:
    raw = tomllib.loads(CONFIG_FILE.read_text())
    app = raw.get("app", {})

    models = {}
    for entry in raw.get("models", []):
        m = ModelConfig(**entry)
        if m.provider not in PROVIDERS:
            raise ValueError(f"{CONFIG_FILE}: model {m.id!r} has unknown provider {m.provider!r} (use {' or '.join(PROVIDERS)})")
        models[m.id] = m
    if not models:
        raise ValueError(f"{CONFIG_FILE}: add at least one [[models]] entry")

    categories = dict(raw.get("categories", {}))
    categories.setdefault("Other", "anything that fits none of the above")

    default_model = os.environ.get("PICS_MODEL") or app.get("default_model") or next(iter(models))
    if default_model not in models:
        raise ValueError(f"{CONFIG_FILE}: default_model {default_model!r} is not one of the [[models]] ids: {', '.join(models)}")

    return Config(
        port=int(os.environ.get("PORT") or app.get("port", 8769)),
        sort_dir=Path(os.environ.get("SORT_DIR") or app.get("sort_dir", "~/Downloads")).expanduser(),
        default_model=default_model,
        max_edge=int(app.get("max_edge", 1024)),
        categories=categories,
        models=models,
    )
