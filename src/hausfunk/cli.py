"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from pydantic import SecretStr

from hausfunk.config import ConfigError, Settings, config_path, load_settings


def _check_config(settings: Settings) -> None:
    print(f"Config file: {config_path()}")
    print("Services:")
    services = (
        "jellyfin",
        "anthropic",
        "icecast",
        "elevenlabs",
        "liquidsoap",
        "tts",
        "homeassistant",
        "director",
    )
    for name in services:
        values = []
        for key, value in getattr(settings, name):
            if isinstance(value, SecretStr):
                value = "set" if value.get_secret_value() else "empty"
            elif value is None:
                value = "-"
            values.append(f"{key}={value}")
        print(f"  {name}: {', '.join(values)}")
    print(f"Profiles: {', '.join(p.id for p in settings.profiles) or '-'}")
    print(f"Personas: {', '.join(p.id for p in settings.personas) or '-'}")
    print("Channels:")
    for ch in settings.channels or []:
        print(f"  {ch.name} ({ch.mount}, {ch.language})")
    if not settings.channels:
        print("  -")
    print("OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hausfunk")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check-config", help="validate configuration and print a masked summary")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2

    if args.command == "check-config":
        _check_config(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
