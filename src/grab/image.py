"""Image resizing, optimization, and format conversion.

Uses ImageMagick (magick CLI) for processing.
Run directly: python -m grab.image photo.png --max-bytes 10485760
              python -m grab.image photo.webp --convert png
              python -m grab.image photo.jpg --resize 1920x1080
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from grab import MediaInfo, log
from grab.util import human_size

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif", ".bmp", ".tiff"}


def is_image(path: str | Path) -> bool:
    """Check if a file is an image based on extension."""
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def image_info(path: str | Path) -> dict:
    """Get image dimensions and format using ImageMagick identify."""
    path = Path(path)
    result = subprocess.run(
        ["magick", "identify", "-format", "%w %h %m %B", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"identify failed: {result.stderr}")

    parts = result.stdout.strip().split()
    return {
        "width": int(parts[0]),
        "height": int(parts[1]),
        "format": parts[2].lower(),
        "size_bytes": int(parts[3]),
    }


def resize_image(
    input_path: str | Path,
    output_path: str | Path | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    max_bytes: int | None = None,
    convert_to: str | None = None,
) -> MediaInfo:
    """Resize and/or convert an image.

    Args:
        max_width: Maximum width (maintains aspect ratio)
        max_height: Maximum height (maintains aspect ratio)
        max_bytes: Target file size - iteratively reduces quality
        convert_to: Target format (png, jpg, webp)
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    if output_path is None:
        suffix = f".{convert_to}" if convert_to else input_path.suffix
        output_path = input_path.with_stem(input_path.stem + "_opt").with_suffix(suffix)
    else:
        output_path = Path(output_path)

    cmd = ["magick", str(input_path)]

    # Resize if dimensions specified
    if max_width or max_height:
        w = max_width or 999999
        h = max_height or 999999
        cmd += ["-resize", f"{w}x{h}>"]

    # Strip metadata for smaller files
    cmd += ["-strip"]

    if max_bytes:
        _compress_to_size(input_path, output_path, max_bytes, max_width, max_height)
    else:
        cmd.append(str(output_path))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"magick failed: {result.stderr}")

    info = image_info(output_path)
    log(f"image: {info['width']}x{info['height']}, {human_size(info['size_bytes'])}")

    return MediaInfo(
        path=str(output_path.resolve()),
        size_bytes=info["size_bytes"],
        width=info["width"],
        height=info["height"],
        format=info["format"],
    )


def _compress_to_size(
    input_path: Path,
    output_path: Path,
    max_bytes: int,
    max_width: int | None,
    max_height: int | None,
) -> None:
    """Iteratively reduce quality to hit target file size."""
    suffix = output_path.suffix.lower()
    # Only JPEG and WebP support quality control
    supports_quality = suffix in (".jpg", ".jpeg", ".webp")

    if not supports_quality:
        # Convert to webp for size optimization
        output_path = output_path.with_suffix(".webp")
        suffix = ".webp"

    for quality in range(90, 10, -5):
        cmd = ["magick", str(input_path)]
        if max_width or max_height:
            w = max_width or 999999
            h = max_height or 999999
            cmd += ["-resize", f"{w}x{h}>"]
        cmd += ["-strip", "-quality", str(quality), str(output_path)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"magick failed: {result.stderr}")

        size = output_path.stat().st_size
        if size <= max_bytes:
            log(f"image compressed at quality={quality}")
            return

    # If we still can't fit, progressively scale down
    for scale in (75, 50, 25):
        cmd = ["magick", str(input_path), "-resize", f"{scale}%",
               "-strip", "-quality", "20", str(output_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"magick failed: {result.stderr}")
        if output_path.stat().st_size <= max_bytes:
            log(f"image compressed at {scale}% scale, quality=20")
            return

    log("warning: could not reach target size")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Resize and optimize images")
    parser.add_argument("file", help="Input image file")
    parser.add_argument("-o", "--output", help="Output file path")
    parser.add_argument("--resize", help="Max dimensions WxH (e.g. 1920x1080)")
    parser.add_argument("--max-bytes", type=int, help="Target max file size in bytes")
    parser.add_argument("--preset", help="Use a grab preset for max size")
    parser.add_argument("--convert", help="Convert to format (png, jpg, webp)")
    args = parser.parse_args()

    max_w = max_h = None
    if args.resize:
        parts = args.resize.lower().split("x")
        max_w, max_h = int(parts[0]), int(parts[1])

    max_bytes = args.max_bytes
    if args.preset:
        from grab.presets import resolve_preset
        max_bytes = resolve_preset(args.preset).max_bytes

    result = resize_image(
        input_path=args.file,
        output_path=args.output,
        max_width=max_w,
        max_height=max_h,
        max_bytes=max_bytes,
        convert_to=args.convert,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
