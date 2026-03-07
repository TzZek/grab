"""Probe media files with ffprobe and return structured metadata.

Run directly: python -m grab.probe <file>
Outputs JSON to stdout with file metadata.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from grab import MediaInfo, log


def probe(path: str | Path) -> MediaInfo:
    """Run ffprobe on a file and return structured MediaInfo."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video = next((s for s in streams if s["codec_type"] == "video"), None)
    audio = next((s for s in streams if s["codec_type"] == "audio"), None)

    duration = None
    if "duration" in fmt:
        duration = float(fmt["duration"])

    # Detect media type
    from grab.image import IMAGE_EXTENSIONS
    if path.suffix.lower() in IMAGE_EXTENSIONS:
        media_type = "image"
    elif video and duration and duration > 0:
        media_type = "video"
    elif audio:
        media_type = "audio"
    else:
        media_type = "video"  # default assumption

    return MediaInfo(
        path=str(path.resolve()),
        size_bytes=path.stat().st_size,
        duration_seconds=duration,
        width=int(video["width"]) if video and "width" in video else None,
        height=int(video["height"]) if video and "height" in video else None,
        codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        format=fmt.get("format_name"),
        media_type=media_type,
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m grab.probe <file>", file=sys.stderr)
        sys.exit(1)

    info = probe(sys.argv[1])
    print(info.to_json())


if __name__ == "__main__":
    main()
