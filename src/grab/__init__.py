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
    media_type: str | None = None  # "video", "image", or "audio"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


_verbose: bool = False


def set_verbose(v: bool) -> None:
    global _verbose
    _verbose = v


def vlog(msg: str) -> None:
    if _verbose:
        print(f"  [{msg}]", file=sys.stderr)


def log(msg: str) -> None:
    """Log to stderr so stdout stays clean for structured output."""
    print(msg, file=sys.stderr)
