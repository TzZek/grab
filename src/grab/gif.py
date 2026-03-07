"""Convert video clips to high-quality GIFs using ffmpeg palette generation.

Run directly: python -m grab.gif video.mp4 -o output.gif
              python -m grab.gif video.mp4 --fps 15 --width 480
              python -m grab.gif video.mp4 --max-bytes 10485760
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from grab import MediaInfo, log
from grab.probe import probe as probe_file


def to_gif(
    input_path: str | Path,
    output_path: str | Path | None = None,
    fps: int = 15,
    width: int = 480,
    max_bytes: int | None = None,
    start: float | None = None,
    duration: float | None = None,
) -> MediaInfo:
    """Convert video to GIF using ffmpeg two-step palette method.

    Args:
        fps: Frames per second (default 15, lower = smaller file)
        width: Output width in pixels (default 480, height auto-scales)
        max_bytes: Target max size — will reduce fps/width to fit
        start: Start time in seconds (trim)
        duration: Duration in seconds (trim)
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    if output_path is None:
        output_path = input_path.with_suffix(".gif")
    else:
        output_path = Path(output_path)

    if max_bytes:
        return _compress_gif(input_path, output_path, max_bytes, fps, width, start, duration)

    _generate_gif(input_path, output_path, fps, width, start, duration)
    info = probe_file(output_path)
    log(f"gif: {_h(info.size_bytes)}, {info.width}x{info.height}")
    return info


def _generate_gif(
    input_path: Path,
    output_path: Path,
    fps: int,
    width: int,
    start: float | None,
    duration: float | None,
) -> None:
    """Two-pass GIF generation: palette → final output."""
    palette = input_path.with_suffix(".palette.png")

    time_args = []
    if start is not None:
        time_args += ["-ss", str(start)]
    if duration is not None:
        time_args += ["-t", str(duration)]

    filters = f"fps={fps},scale={width}:-1:flags=lanczos"

    # Pass 1: generate palette
    _run_ffmpeg([
        *time_args, "-i", str(input_path),
        "-vf", f"{filters},palettegen=stats_mode=diff",
        "-y", str(palette),
    ])

    # Pass 2: apply palette
    _run_ffmpeg([
        *time_args, "-i", str(input_path),
        "-i", str(palette),
        "-lavfi", f"{filters} [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=5",
        "-y", str(output_path),
    ])

    palette.unlink(missing_ok=True)


def _compress_gif(
    input_path: Path,
    output_path: Path,
    max_bytes: int,
    fps: int,
    width: int,
    start: float | None,
    duration: float | None,
) -> MediaInfo:
    """Iteratively reduce quality to hit target size."""
    attempts = [
        (fps, width),
        (fps, int(width * 0.75)),
        (max(fps - 5, 5), int(width * 0.75)),
        (max(fps - 5, 5), int(width * 0.5)),
        (8, int(width * 0.5)),
        (5, int(width * 0.25)),
    ]

    for try_fps, try_width in attempts:
        _generate_gif(input_path, output_path, try_fps, try_width, start, duration)
        size = output_path.stat().st_size
        if size <= max_bytes:
            log(f"gif fits at {try_fps}fps, {try_width}px wide")
            return probe_file(output_path)
        log(f"gif too large at {try_fps}fps {try_width}px: {_h(size)}, trying smaller...")

    log("warning: could not reach target GIF size")
    return probe_file(output_path)


def _run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")


def _h(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Convert video to GIF")
    parser.add_argument("file", help="Input video file")
    parser.add_argument("-o", "--output", help="Output GIF path")
    parser.add_argument("--fps", type=int, default=15, help="Frames per second (default: 15)")
    parser.add_argument("--width", type=int, default=480, help="Output width (default: 480)")
    parser.add_argument("--max-bytes", type=int, help="Target max file size")
    parser.add_argument("--preset", help="Use a grab preset for max size")
    parser.add_argument("--start", type=float, help="Start time in seconds")
    parser.add_argument("--duration", type=float, help="Duration in seconds")
    args = parser.parse_args()

    max_bytes = args.max_bytes
    if args.preset:
        from grab.presets import resolve_preset
        max_bytes = resolve_preset(args.preset).max_bytes

    result = to_gif(
        input_path=args.file,
        output_path=args.output,
        fps=args.fps,
        width=args.width,
        max_bytes=max_bytes,
        start=args.start,
        duration=args.duration,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
