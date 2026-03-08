"""Compress media files to a target size using ffmpeg.

Run directly: python -m grab.compress <file> --target-bytes 10485760
              python -m grab.compress <file> --preset discord
Outputs JSON to stdout with the compressed file path and metadata.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from grab import MediaInfo, log
from grab.probe import probe
from grab.presets import Preset, resolve_preset
from grab.util import human_size


def compress(
    input_path: str | Path,
    target_bytes: int | None = None,
    preset: Preset | None = None,
    output_path: str | Path | None = None,
) -> MediaInfo:
    """Compress a media file to fit within target size.

    Provide either target_bytes or a Preset. Uses two-pass x264 encoding
    to maximize quality within the size budget.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    if preset is not None:
        target_bytes = preset.max_bytes
        max_w = preset.max_width
        max_h = preset.max_height
        audio_kbps = preset.audio_bitrate_kbps
    else:
        if target_bytes is None:
            raise ValueError("Must provide either target_bytes or preset")
        max_w = 1920
        max_h = 1080
        audio_kbps = 128

    info = probe(input_path)

    # Already small enough?
    if info.size_bytes <= target_bytes:
        log(f"file already within target ({human_size(info.size_bytes)} <= {human_size(target_bytes)})")
        if output_path:
            import shutil
            output_path = Path(output_path)
            shutil.copy2(input_path, output_path)
            return probe(output_path)
        return info

    if output_path is None:
        output_path = input_path.with_stem(input_path.stem + "_compressed").with_suffix(".mp4")
    else:
        output_path = Path(output_path)

    duration = info.duration_seconds
    if not duration or duration <= 0:
        raise ValueError("Cannot compress: unable to determine duration")

    # Calculate target video bitrate
    # Budget: total bits - audio bits - 10% overhead margin for container/VBR variance
    total_bits = target_bytes * 8 * 0.90
    audio_bits = audio_kbps * 1000 * duration
    video_bits = total_bits - audio_bits

    if video_bits <= 0:
        log("target too small for video+audio, producing audio-only")
        _run_ffmpeg([
            "-i", str(input_path),
            "-vn", "-c:a", "aac", "-b:a", f"{audio_kbps}k",
            str(output_path),
        ])
        return probe(output_path)

    video_kbps = max(50, int(video_bits / duration / 1000))

    scale_filter = (
        f"scale='min({max_w},iw)':'min({max_h},ih)'"
        f":force_original_aspect_ratio=decrease"
        f":force_divisible_by=2"
    )

    log(f"compressing: {video_kbps}k video, {audio_kbps}k audio, {duration:.0f}s")

    common = [
        "-i", str(input_path),
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-vf", scale_filter,
    ]

    # Pass 1
    _run_ffmpeg([*common, "-pass", "1", "-an", "-f", "null", "/dev/null"])

    # Pass 2
    _run_ffmpeg([
        *common,
        "-pass", "2",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        str(output_path),
    ])

    # Clean up two-pass logs
    for f in Path(".").glob("ffmpeg2pass-*"):
        f.unlink(missing_ok=True)

    result = probe(output_path)
    log(f"compressed: {human_size(result.size_bytes)} (target: {human_size(target_bytes)})")
    return result


def _run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Compress media to target size")
    parser.add_argument("file", help="Input media file")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target-bytes", type=int, help="Target size in bytes")
    group.add_argument("--preset", help="Preset name or size in MB")
    parser.add_argument("-o", "--output", help="Output file path")
    args = parser.parse_args()

    preset = resolve_preset(args.preset) if args.preset else None

    result = compress(
        input_path=args.file,
        target_bytes=args.target_bytes,
        preset=preset,
        output_path=args.output,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
