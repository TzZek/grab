"""Target presets for compression.

Each preset defines a max file size and optional encoding hints.
Run directly to list all presets: python -m grab.presets
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, asdict

MB = 1_048_576


@dataclass
class Preset:
    name: str
    max_bytes: int
    max_width: int = 1920
    max_height: int = 1080
    audio_bitrate_kbps: int = 128
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


PRESETS: dict[str, Preset] = {
    "discord": Preset(
        name="discord",
        max_bytes=10 * MB,
        description="Discord free tier (10 MB)",
    ),
    "discord-nitro": Preset(
        name="discord-nitro",
        max_bytes=50 * MB,
        description="Discord Nitro (50 MB)",
    ),
    "telegram": Preset(
        name="telegram",
        max_bytes=50 * MB,
        description="Telegram (50 MB)",
    ),
    "email": Preset(
        name="email",
        max_bytes=25 * MB,
        description="Email attachment (25 MB)",
    ),
    "twitter": Preset(
        name="twitter",
        max_bytes=512 * MB,
        max_width=1920,
        max_height=1200,
        description="Twitter/X video upload (512 MB)",
    ),
}


def resolve_preset(name_or_mb: str) -> Preset:
    """Resolve a preset by name or interpret as a size in MB."""
    if name_or_mb in PRESETS:
        return PRESETS[name_or_mb]
    try:
        size_mb = int(name_or_mb)
        return Preset(
            name=f"custom-{size_mb}mb",
            max_bytes=size_mb * MB,
            description=f"Custom target ({size_mb} MB)",
        )
    except ValueError:
        raise ValueError(
            f"Unknown preset '{name_or_mb}'. "
            f"Available: {', '.join(PRESETS.keys())} or a number in MB"
        )


def main() -> None:
    out = {name: p.to_dict() for name, p in PRESETS.items()}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
