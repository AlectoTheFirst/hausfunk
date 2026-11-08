"""Hausfunk configuration.

Sources, later ones win:
    built-in defaults -> YAML file -> ``HAUSFUNK_*_FILE`` secret files -> ``.env`` -> environment

Env vars use the prefix ``HAUSFUNK_`` and ``__`` for nesting, e.g. ``HAUSFUNK_JELLYFIN__URL``.
Secrets (``SecretStr`` fields) are rejected in the YAML file; set them via env or ``*_FILE``.
"""

from __future__ import annotations

import os
import types
import typing
from datetime import time
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    ValidationError,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

ENV_PREFIX = "HAUSFUNK_"
ENV_NESTED = "__"
CONFIG_PATH_ENV = "HAUSFUNK_CONFIG"
DEFAULT_CONFIG_PATH = Path("config/hausfunk.yaml")

Language = Literal["en", "de"]
Role = Literal["host", "news", "storyteller"]
Slug = typing.Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Services ----------------------------------------------------------------------------------


class JellyfinSettings(Strict):
    url: HttpUrl
    api_key: SecretStr
    user_id: str | None = None


class AnthropicSettings(Strict):
    api_key: SecretStr
    base_url: HttpUrl | None = None


class ElevenLabsSettings(Strict):
    api_key: SecretStr | None = None
    base_url: HttpUrl | None = None


class IcecastSettings(Strict):
    url: HttpUrl = HttpUrl("http://localhost:8000")
    source_password: SecretStr
    admin_password: SecretStr | None = None


class LiquidsoapSettings(Strict):
    host: str = "localhost"
    port: int = Field(default=1234, ge=1, le=65535)


class TTSSettings(Strict):
    provider: Literal["piper", "elevenlabs"] = "piper"
    url: HttpUrl | None = None


class HomeAssistantSettings(Strict):
    url: HttpUrl | None = None
    token: SecretStr | None = None

    @model_validator(mode="after")
    def _both_or_neither(self) -> HomeAssistantSettings:
        if (self.url is None) != (self.token is None):
            raise ValueError("set both url and token, or neither")
        return self


class DirectorSettings(Strict):
    model: str = "claude-sonnet-5-5"
    bulk_model: str = "claude-haiku-5-5"


# --- Domain ------------------------------------------------------------------------------------


class Profile(Strict):
    id: Slug
    name: str
    banned_genres: list[str] = []
    banned_artists: list[str] = []
    banned_tracks: list[str] = []
    preferred_genres: dict[str, float] = {}


class Voice(Strict):
    provider: Literal["piper", "elevenlabs"]
    voice: str


class Persona(Strict):
    id: Slug
    role: Role
    voices: dict[Language, Voice]
    style: str = ""


class TasteSource(Strict):
    """No profiles = format-only channel driven by ``genres``."""

    profiles: list[str] = []
    genres: list[str] = []

    @model_validator(mode="after")
    def _not_empty(self) -> TasteSource:
        if not self.profiles and not self.genres:
            raise ValueError("needs at least one profile or genre")
        return self


class TimeWindow(Strict):
    start: time
    end: time


class Programme(Strict):
    name: str
    window: TimeWindow
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] | None = None
    genres: list[str] = []
    persona: str | None = None
    language: Language | None = None
    story: bool = False


class Channel(Strict):
    slug: Slug
    name: str
    language: Language = "en"
    taste: TasteSource
    personas: dict[Role, str] = {}
    news_topics: list[str] = []
    silence: TimeWindow | None = None
    programmes: list[Programme] = []

    @property
    def mount(self) -> str:
        return f"/{self.slug}"


class NewsFeed(Strict):
    topic: str
    url: HttpUrl
    language: Language = "en"


# --- Root --------------------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED,
        env_file=".env",
        # Ignore unrelated .env entries (e.g. LIQUIDSOAP_CHANNELS for the containers).
        dotenv_filtering="only_existing",
        extra="forbid",
        nested_model_default_partial_update=True,
    )

    # Read by config_path() before the sources are built; declared so HAUSFUNK_CONFIG is accepted.
    config: Path | None = None

    jellyfin: JellyfinSettings
    anthropic: AnthropicSettings
    icecast: IcecastSettings
    elevenlabs: ElevenLabsSettings = ElevenLabsSettings()
    liquidsoap: LiquidsoapSettings = LiquidsoapSettings()
    tts: TTSSettings = TTSSettings()
    homeassistant: HomeAssistantSettings = HomeAssistantSettings()
    director: DirectorSettings = DirectorSettings()

    profiles: list[Profile] = []
    personas: list[Persona] = []
    channels: list[Channel] = []
    news_feeds: list[NewsFeed] = []

    @model_validator(mode="after")
    def _check_references(self) -> Settings:
        _unique("profile id", [p.id for p in self.profiles])
        _unique("persona id", [p.id for p in self.personas])
        _unique("channel slug", [c.slug for c in self.channels])
        _unique("channel name", [c.name.casefold() for c in self.channels])

        profiles = {p.id for p in self.profiles}
        personas = {p.id: p for p in self.personas}
        topics = {f.topic for f in self.news_feeds}
        uses_elevenlabs = self.tts.provider == "elevenlabs"

        for ch in self.channels:
            for pid in ch.taste.profiles:
                if pid not in profiles:
                    raise ValueError(f"channel '{ch.slug}': unknown profile '{pid}'")
            refs = [(role, pid) for role, pid in ch.personas.items()]
            refs += [("programme", p.persona) for p in ch.programmes if p.persona]
            for role, pid in refs:
                persona = personas.get(pid)
                if persona is None:
                    raise ValueError(f"channel '{ch.slug}': unknown persona '{pid}'")
                if role != "programme" and persona.role != role:
                    raise ValueError(
                        f"channel '{ch.slug}': persona '{pid}' has role '{persona.role}', "
                        f"not '{role}'"
                    )
            for topic in ch.news_topics:
                if topic not in topics:
                    raise ValueError(f"channel '{ch.slug}': no news feed for topic '{topic}'")

        for persona in self.personas:
            if any(v.provider == "elevenlabs" for v in persona.voices.values()):
                uses_elevenlabs = True
        if uses_elevenlabs and self.elevenlabs.api_key is None:
            raise ValueError(
                f"ElevenLabs is used but {ENV_PREFIX}ELEVENLABS{ENV_NESTED}API_KEY is not set"
            )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            NoFileVarsEnvSource(settings_cls),
            NoFileVarsDotEnvSource(settings_cls),
            EnvFileSecretsSource(settings_cls),
            NoSecretsYamlSource(settings_cls, yaml_file=config_path()),
        )


def _unique(what: str, values: list[str]) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise ValueError(f"duplicate {what} '{v}'")
        seen.add(v)


# --- Sources -----------------------------------------------------------------------------------


def config_path() -> Path:
    """``HAUSFUNK_CONFIG`` from the environment, then from ``.env``, else the default path."""
    value = os.environ.get(CONFIG_PATH_ENV) or dotenv_values(".env").get(CONFIG_PATH_ENV)
    return Path(value) if value else DEFAULT_CONFIG_PATH


def _is_secret(annotation: Any) -> bool:
    if annotation is SecretStr:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return any(_is_secret(a) for a in typing.get_args(annotation))
    return False


def _model_of(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def secret_paths(model: type[BaseModel], prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Dotted paths of all ``SecretStr`` fields in nested service models."""
    paths: list[tuple[str, ...]] = []
    for name, field in model.model_fields.items():
        path = (*prefix, name)
        if _is_secret(field.annotation):
            paths.append(path)
        elif sub := _model_of(field.annotation):
            paths.extend(secret_paths(sub, path))
    return paths


def env_name(path: tuple[str, ...] | list[str | int]) -> str:
    return ENV_PREFIX + ENV_NESTED.join(str(p).upper() for p in path)


class NoSecretsYamlSource(YamlConfigSettingsSource):
    """YAML source that refuses secret fields, so they never end up in a config file."""

    def __call__(self) -> dict[str, Any]:
        data = super().__call__()
        for path in secret_paths(self.settings_cls):
            node: Any = data
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if node is not None:
                raise ConfigError(
                    f"{'.'.join(path)} must not be set in {config_path()}; "
                    f"use {env_name(path)} or {env_name(path)}_FILE"
                )
        return data


def _drop_file_vars(env: Any) -> dict[str, str | None]:
    return {k: v for k, v in env.items() if not k.lower().endswith("_file")}


class NoFileVarsEnvSource(EnvSettingsSource):
    """Env source that leaves ``*_FILE`` variables to :class:`EnvFileSecretsSource`."""

    def _load_env_vars(self) -> dict[str, str | None]:
        return _drop_file_vars(super()._load_env_vars())


class NoFileVarsDotEnvSource(DotEnvSettingsSource):
    def _load_env_vars(self) -> dict[str, str | None]:
        return _drop_file_vars(super()._load_env_vars())


class EnvFileSecretsSource(PydanticBaseSettingsSource):
    """Reads ``HAUSFUNK_X__Y_FILE=/path`` and sets ``x.y`` to the file's content."""

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key, file in os.environ.items():
            if not (key.startswith(ENV_PREFIX) and key.endswith("_FILE")) or key == CONFIG_PATH_ENV:
                continue
            path = key[len(ENV_PREFIX) : -len("_FILE")].lower().split(ENV_NESTED)
            try:
                value = Path(file).read_text().strip()
            except OSError as e:
                raise ConfigError(f"{key}: cannot read {file}: {e.strerror}") from e
            node = data
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = value
        return data


# --- Loading -----------------------------------------------------------------------------------


class ConfigError(Exception):
    """Configuration is missing or invalid; the message is meant for humans."""


def load_settings(**overrides: Any) -> Settings:
    try:
        return Settings(**overrides)
    except ValidationError as e:
        lines = []
        for err in e.errors():
            loc = list(err["loc"])
            label = ".".join(str(p) for p in loc) or "config"
            hint = ""
            if err["type"] == "missing" and loc and all(isinstance(p, str) for p in loc):
                hint = f" (set {', '.join(_required_env_names(tuple(loc)))})"
            lines.append(f"- {label}: {err['msg']}{hint}")
        raise ConfigError("Invalid configuration:\n" + "\n".join(lines)) from None


def _required_env_names(loc: tuple[str, ...]) -> list[str]:
    """Env var names for a missing field, expanding a missing section to its required fields."""
    model: type[BaseModel] = Settings
    for part in loc[:-1]:
        sub = _model_of(model.model_fields[part].annotation)
        if sub is None:
            return [env_name(loc)]
        model = sub
    section = _model_of(model.model_fields[loc[-1]].annotation)
    if section is None:
        return [env_name(loc)]
    return [env_name((*loc, n)) for n, f in section.model_fields.items() if f.is_required()]
