"""Main CLI entry point for grab.

Orchestrates download → probe → compress pipeline.
Run: grab <url> [options]

Outputs JSON to stdout with final file metadata.
All progress/status goes to stderr.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from grab import log
from grab.download import download
from grab.probe import probe
from grab.compress import compress
from grab.presets import resolve_preset, PRESETS

DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "content"


def get_output_dir(cli_dir: str | None = None) -> Path:
    """Resolve output directory: --dir flag > GRAB_OUTPUT_DIR env > ~/Videos/grab."""
    if cli_dir:
        d = Path(cli_dir)
    elif os.environ.get("GRAB_OUTPUT_DIR"):
        d = Path(os.environ["GRAB_OUTPUT_DIR"])
    else:
        d = DEFAULT_OUTPUT_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grab",
        description="Download and compress media for target platforms",
    )
    p.add_argument("url", help="URL to download")
    p.add_argument(
        "-p", "--preset",
        help=f"Target preset: {', '.join(PRESETS.keys())}, or a number in MB",
    )
    p.add_argument(
        "-q", "--quality", default="1080",
        help="Video quality: max, 2160, 1440, 1080, 720, 480, 360 (default: 1080)",
    )
    p.add_argument("-a", "--audio-only", action="store_true", help="Download audio only")
    p.add_argument("-o", "--output", help="Output file path")
    p.add_argument(
        "-d", "--dir",
        help="Output directory (default: GRAB_OUTPUT_DIR or ~/Videos/grab)",
    )
    p.add_argument("-k", "--keep", action="store_true", help="Keep original after compression")
    p.add_argument("--cobalt", help="Cobalt API URL (or set GRAB_COBALT_API env var)")
    p.add_argument("--no-compress", action="store_true", help="Skip compression")
    p.add_argument("--json", action="store_true", help="Output JSON only (no human summary)")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    out_dir = get_output_dir(args.dir)
    log(f"output dir: {out_dir}")

    # Download
    info = download(
        url=args.url,
        cobalt_api=args.cobalt,
        quality=args.quality,
        audio_only=args.audio_only,
    )

    downloaded_path = Path(info.path)
    final_path = downloaded_path

    # Compress if preset is set
    if args.preset and not args.no_compress:
        preset = resolve_preset(args.preset)

        if info.size_bytes > preset.max_bytes:
            if args.output:
                output = args.output
            else:
                output = out_dir / (downloaded_path.stem + ".mp4")

            result = compress(
                input_path=downloaded_path,
                preset=preset,
                output_path=output,
            )
            final_path = Path(result.path)

            if args.keep:
                keep_name = final_path.with_stem(final_path.stem + "_original")
                keep_name = keep_name.with_suffix(downloaded_path.suffix)
                shutil.copy2(downloaded_path, keep_name)
                log(f"original kept: {keep_name}")
        else:
            if args.output:
                final_path = Path(args.output)
            else:
                final_path = out_dir / downloaded_path.name
            shutil.copy2(downloaded_path, final_path)
            log("already within size limit, no compression needed")

    elif args.output:
        final_path = Path(args.output)
        shutil.copy2(downloaded_path, final_path)
    else:
        final_path = out_dir / downloaded_path.name
        if final_path != downloaded_path:
            shutil.copy2(downloaded_path, final_path)

    # Final probe for accurate output
    final_info = probe(final_path)

    if args.json:
        print(final_info.to_json())
    else:
        print(final_info.to_json())
        log(f"saved: {final_path}")


if __name__ == "__main__":
    main()
