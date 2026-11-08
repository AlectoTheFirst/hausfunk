from pathlib import Path

import pytest
import yaml

from hausfunk.config import ConfigError, load_settings

EXAMPLE = Path(__file__).parent.parent / "config" / "hausfunk.example.yaml"

REQUIRED_ENV = {
    "HAUSFUNK_JELLYFIN__URL": "http://jellyfin.test:8096",
    "HAUSFUNK_JELLYFIN__API_KEY": "jf-key",
    "HAUSFUNK_ANTHROPIC__API_KEY": "sk-test",
    "HAUSFUNK_ICECAST__SOURCE_PASSWORD": "source-pw",
}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Run each test in an empty directory with no HAUSFUNK_* variables set."""
    import os

    for key in list(os.environ):
        if key.startswith("HAUSFUNK_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def write_config(tmp_path, monkeypatch, data) -> Path:
    path = tmp_path / "hausfunk.yaml"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.setenv("HAUSFUNK_CONFIG", str(path))
    return path


def set_required(monkeypatch, **skip):
    for key, value in REQUIRED_ENV.items():
        if key not in skip:
            monkeypatch.setenv(key, value)


def test_example_config_is_valid(monkeypatch):
    monkeypatch.setenv("HAUSFUNK_CONFIG", str(EXAMPLE))
    set_required(monkeypatch)
    settings = load_settings()
    assert [c.mount for c in settings.channels] == ["/hausfunk", "/nachtschicht-fm"]
    assert settings.jellyfin.api_key.get_secret_value() == "jf-key"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        monkeypatch,
        {"director": {"model": "from-yaml"}, "jellyfin": {"url": "http://yaml:8096"}},
    )
    set_required(monkeypatch, HAUSFUNK_JELLYFIN__URL=True)
    monkeypatch.setenv("HAUSFUNK_DIRECTOR__MODEL", "from-env")
    settings = load_settings()
    assert settings.director.model == "from-env"
    # Values only in YAML survive the partial env override of the same section.
    assert str(settings.jellyfin.url) == "http://yaml:8096/"
    assert settings.director.bulk_model == "claude-haiku-5-5"


def test_dotenv_file_is_read(tmp_path):
    lines = [f"{k}={v}" for k, v in REQUIRED_ENV.items()] + ["LIQUIDSOAP_CHANNELS=a,b"]
    (tmp_path / ".env").write_text("\n".join(lines))
    settings = load_settings()
    assert settings.anthropic.api_key.get_secret_value() == "sk-test"


def test_config_path_from_dotenv(tmp_path):
    cfg = tmp_path / "custom.yaml"
    cfg.write_text(yaml.safe_dump({"director": {"model": "custom"}}))
    lines = [f"{k}={v}" for k, v in REQUIRED_ENV.items()] + [f"HAUSFUNK_CONFIG={cfg}"]
    (tmp_path / ".env").write_text("\n".join(lines))
    assert load_settings().director.model == "custom"


def test_secret_in_yaml_is_rejected(tmp_path, monkeypatch):
    write_config(tmp_path, monkeypatch, {"anthropic": {"api_key": "leaked"}})
    set_required(monkeypatch)
    with pytest.raises(ConfigError, match="HAUSFUNK_ANTHROPIC__API_KEY"):
        load_settings()


def test_secret_from_file(tmp_path, monkeypatch):
    secret = tmp_path / "anthropic_key"
    secret.write_text("sk-from-file\n")
    set_required(monkeypatch, HAUSFUNK_ANTHROPIC__API_KEY=True)
    monkeypatch.setenv("HAUSFUNK_ANTHROPIC__API_KEY_FILE", str(secret))
    assert load_settings().anthropic.api_key.get_secret_value() == "sk-from-file"


def test_env_beats_secret_file(tmp_path, monkeypatch):
    secret = tmp_path / "anthropic_key"
    secret.write_text("from-file")
    set_required(monkeypatch)
    monkeypatch.setenv("HAUSFUNK_ANTHROPIC__API_KEY_FILE", str(secret))
    assert load_settings().anthropic.api_key.get_secret_value() == "sk-test"


def test_missing_secret_names_env_var(monkeypatch):
    set_required(monkeypatch, HAUSFUNK_JELLYFIN__API_KEY=True)
    with pytest.raises(ConfigError, match="HAUSFUNK_JELLYFIN__API_KEY"):
        load_settings()


def test_secrets_are_masked(monkeypatch):
    set_required(monkeypatch)
    assert "sk-test" not in repr(load_settings())


def test_unknown_profile_reference(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        monkeypatch,
        {"channels": [{"slug": "x", "name": "X", "taste": {"profiles": ["nobody"]}}]},
    )
    set_required(monkeypatch)
    with pytest.raises(ConfigError, match="unknown profile 'nobody'"):
        load_settings()


def test_persona_role_mismatch(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        monkeypatch,
        {
            "personas": [
                {
                    "id": "anchor",
                    "role": "news",
                    "voices": {"en": {"provider": "piper", "voice": "v"}},
                }
            ],
            "channels": [
                {
                    "slug": "x",
                    "name": "X",
                    "taste": {"genres": ["jazz"]},
                    "personas": {"host": "anchor"},
                }
            ],
        },
    )
    set_required(monkeypatch)
    with pytest.raises(ConfigError, match="role 'news', not 'host'"):
        load_settings()


def test_elevenlabs_requires_key(monkeypatch):
    set_required(monkeypatch)
    monkeypatch.setenv("HAUSFUNK_TTS__PROVIDER", "elevenlabs")
    with pytest.raises(ConfigError, match="HAUSFUNK_ELEVENLABS__API_KEY"):
        load_settings()


@pytest.mark.parametrize("slug", ["Nacht FM", "-x", "x_y"])
def test_invalid_slug(tmp_path, monkeypatch, slug):
    write_config(
        tmp_path,
        monkeypatch,
        {"channels": [{"slug": slug, "name": "X", "taste": {"genres": ["jazz"]}}]},
    )
    set_required(monkeypatch)
    with pytest.raises(ConfigError, match="slug"):
        load_settings()


def test_duplicate_channel_slug(tmp_path, monkeypatch):
    ch = {"slug": "x", "taste": {"genres": ["jazz"]}}
    write_config(tmp_path, monkeypatch, {"channels": [{**ch, "name": "A"}, {**ch, "name": "B"}]})
    set_required(monkeypatch)
    with pytest.raises(ConfigError, match="duplicate channel slug 'x'"):
        load_settings()
