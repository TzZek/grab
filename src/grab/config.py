"""Configuration management for grab.

Config file: ~/.config/grab/config.toml
Run directly: python -m grab.config show
              python -m grab.config set output_dir ~/Pictures/content
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "grab"
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULTS = {
    "output_dir": str(Path.home() / "Pictures" / "content"),
    "cobalt_api": "",
    "default_preset": "",
    "default_quality": "1080",
    "filename_template": "{source}_{title}_{date}",
    "cookies_from_browser": "",
    "transcribe_backend": "faster-whisper",
    "transcribe_model": "base",
    "transcribe_language": "",
    "summarize_backend": "ollama",
    "summarize_model": "",
    "summarize_prompt": "",
    "summarize_api_base": "http://localhost:8080/v1",
    "summarize_api_key": "",
    "obsidian_vault": "",
    "obsidian_folder": "reference/videos",
}


def load() -> dict:
    """Load config from file, merged with defaults."""
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            user = tomllib.load(f)
        config.update(user)
    return config


def save(config: dict) -> None:
    """Write config dict to TOML file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, bool):
            lines.append(f"{key} = {'true' if value else 'false'}")
        elif isinstance(value, int):
            lines.append(f"{key} = {value}")
        else:
            lines.append(f'{key} = "{value}"')
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


def get(key: str) -> str:
    """Get a single config value."""
    config = load()
    if key not in config:
        raise KeyError(f"Unknown config key: {key}. Valid keys: {', '.join(DEFAULTS.keys())}")
    return config[key]


def set_value(key: str, value: str) -> None:
    """Set a single config value."""
    if key not in DEFAULTS:
        raise KeyError(f"Unknown config key: {key}. Valid keys: {', '.join(DEFAULTS.keys())}")
    config = load()
    config[key] = value
    save(config)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m grab.config show|set|get", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "show":
        config = load()
        for key, value in config.items():
            print(f"{key} = {value}")
    elif cmd == "get" and len(sys.argv) == 3:
        print(get(sys.argv[2]))
    elif cmd == "set" and len(sys.argv) == 4:
        key, value = sys.argv[2], sys.argv[3]
        set_value(key, value)
        print(f"{key} = {value}")
    else:
        print("usage: python -m grab.config show|get <key>|set <key> <value>", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
