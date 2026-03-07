"""grab - download and compress media for target platforms."""

from dataclasses import dataclass, asdict
import json
import sys

__version__ = "0.1.0"


@dataclass
class MediaInfo:
    path: str
    size_bytes: int
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    audio_codec: str | None = None
    format: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def log(msg: str) -> None:
    """Log to stderr so stdout stays clean for structured output."""
    print(msg, file=sys.stderr)
