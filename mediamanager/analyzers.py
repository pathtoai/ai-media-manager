"""Image analyzers. Each turns one JPEG into a structured Analysis (description,
category, tags, ...). Claude runs through the Anthropic API; Qwen2.5-VL (or any
vision model) runs locally through Ollama."""

import base64
import json
import os
from pathlib import Path
from typing import Literal

import anthropic
import ollama
from pydantic import BaseModel, ValidationError

from .config import Config, ModelConfig

FALLBACK_BETA = "server-side-fallback-2026-07-01"

PROMPT = """You analyze photos (and single frames from videos) for a personal photo library.
Describe what is actually visible; do not guess identities of real people.
- description: one or two plain sentences about the image.
- category: the single best folder for it, from this list:
{categories}
- tags: 5-15 short lowercase search tags (1-3 words each, no "#"), most relevant first.
  Cover subject, setting, activity, style and notable objects. No near-duplicates.
- objects: the main distinct things in the frame.
- scene: "indoor", "outdoor" or "unknown" when it cannot be told (e.g. screenshots, text).
- colors: 1-5 dominant color names (e.g. "beige", "navy").
- mood: one or two words for the overall feel (e.g. "cozy", "energetic", "minimal").
- people_count: how many people are visible (0 if none).
- text: visible text transcribed verbatim, or "" if none.
Use the filename only as a weak hint."""


class AnalyzerError(Exception):
    pass


def make_analysis_model(categories: list[str]) -> type[BaseModel]:
    class Analysis(BaseModel):
        description: str
        category: Literal[tuple(categories)]
        tags: list[str]
        objects: list[str]
        scene: Literal["indoor", "outdoor", "unknown"]
        colors: list[str]
        mood: str
        people_count: int
        text: str

    return Analysis


def clean_tag(tag: str) -> str:
    return " ".join(tag.lower().lstrip("#").split())


def clean_tags(tags: list[str], limit: int = 15) -> list[str]:
    out: list[str] = []
    for t in map(clean_tag, tags):
        if t and t not in out:
            out.append(t)
    return out[:limit]


def _user_text(filename: str | None, kind: str) -> str:
    label = "Video frame" if kind == "video" else "Photo"
    return f"{label}{f' — filename: {filename}' if filename else ''}. Analyze and tag it."


class ClaudeAnalyzer:
    def __init__(self, model: ModelConfig, cfg: Config):
        self.model = model
        self.schema = make_analysis_model(list(cfg.categories))
        self.system = PROMPT.format(categories="\n".join(f"  - {k}: {v}" for k, v in cfg.categories.items()))
        self._client: anthropic.Anthropic | None = None

    def status(self) -> tuple[bool, str]:
        # The SDK also reads an `ant auth login` profile from ~/.config/anthropic.
        env = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID")
        if any(os.environ.get(k) for k in env) or (Path.home() / ".config" / "anthropic").is_dir():
            return True, ""
        return False, "Set ANTHROPIC_API_KEY (or run `ant auth login`) and restart to use Claude."

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def analyze(self, jpeg: bytes, filename: str | None, kind: str) -> BaseModel:
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if self.model.fallbacks else {}
        try:
            response = self.client.beta.messages.parse(
                model=self.model.id,
                max_tokens=4000,
                system=self.system,
                output_config={"effort": self.model.effort},
                output_format=self.schema,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(jpeg)}},
                            {"type": "text", "text": _user_text(filename, kind)},
                        ],
                    }
                ],
                **extra,
            )
        except anthropic.AuthenticationError:
            raise AnalyzerError("Claude rejected the API key — check ANTHROPIC_API_KEY")
        except anthropic.RateLimitError:
            raise AnalyzerError("Claude rate limit hit — lower `concurrency` in config.toml or retry later")
        except anthropic.APIStatusError as e:
            raise AnalyzerError(f"Claude API error {e.status_code}: {e.message}")
        except anthropic.APIConnectionError:
            raise AnalyzerError("could not reach the Claude API")

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise AnalyzerError(f"Claude declined this image ({category or 'no category'})")
        if response.parsed_output is None:
            raise AnalyzerError(f"Claude returned no analysis (stop_reason: {response.stop_reason})")
        return response.parsed_output


class OllamaAnalyzer:
    def __init__(self, model: ModelConfig, cfg: Config):
        self.model = model
        self.categories = list(cfg.categories)
        self.schema = make_analysis_model(self.categories)
        self.system = PROMPT.format(categories="\n".join(f"  - {k}: {v}" for k, v in cfg.categories.items()))
        self.client = ollama.Client(host=model.host, timeout=600)

    def status(self) -> tuple[bool, str]:
        try:
            installed = {m.model for m in ollama.Client(host=self.model.host, timeout=2).list().models}
        except Exception:
            return False, f"Ollama is not running at {self.model.host}. Install it from ollama.com, then run `ollama serve`."
        wanted = self.model.id if ":" in self.model.id else f"{self.model.id}:latest"
        if wanted not in installed:
            return False, f"Model not downloaded yet. Run: ollama pull {self.model.id}"
        return True, ""

    def analyze(self, jpeg: bytes, filename: str | None, kind: str) -> BaseModel:
        try:
            response = self.client.chat(
                model=self.model.id,
                messages=[
                    {"role": "system", "content": self.system},
                    {"role": "user", "content": _user_text(filename, kind), "images": [jpeg]},
                ],
                format=self.schema.model_json_schema(),
                options={"temperature": 0},
                keep_alive="10m",
            )
        except ollama.ResponseError as e:
            raise AnalyzerError(f"Ollama error: {e.error}")
        except Exception as e:
            raise AnalyzerError(f"could not reach Ollama at {self.model.host}: {e}")
        return self._parse(response.message.content or "")

    def _parse(self, content: str) -> BaseModel:
        # Small local models occasionally drift from the schema; repair what we can.
        try:
            return self.schema.model_validate_json(content)
        except ValidationError:
            pass
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            raise AnalyzerError("local model returned invalid JSON")
        if data.get("category") not in self.categories:
            data["category"] = "Other"
        if data.get("scene") not in ("indoor", "outdoor", "unknown"):
            data["scene"] = "unknown"
        defaults = {"description": "", "tags": [], "objects": [], "colors": [], "mood": "", "people_count": 0, "text": ""}
        data = {**defaults, **{k: v for k, v in data.items() if v is not None}}
        try:
            return self.schema.model_validate(data)
        except ValidationError as e:
            raise AnalyzerError(f"local model returned an unexpected shape: {e.errors()[0]['msg']}")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


_analyzers: dict[str, ClaudeAnalyzer | OllamaAnalyzer] = {}


def get_analyzer(model_id: str, cfg: Config) -> ClaudeAnalyzer | OllamaAnalyzer:
    if model_id not in _analyzers:
        model = cfg.models[model_id]
        cls = ClaudeAnalyzer if model.provider == "anthropic" else OllamaAnalyzer
        _analyzers[model_id] = cls(model, cfg)
    return _analyzers[model_id]
