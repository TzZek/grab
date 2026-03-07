"""Main CLI entry point for grab.

Orchestrates download → probe → compress/image/gif pipeline.
Run: grab <url> [options]
     grab config show|set|get
     grab gif <file> [options]

Outputs JSON to stdout with final file metadata.
All progress/status goes to stderr.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from grab import log
from grab.config import load as load_config
from grab.download import download
from grab.probe import probe
from grab.compress import compress
from grab.image import is_image, resize_image
from grab.gif import to_gif
from grab.naming import generate_filename, deduplicate
from grab.presets import resolve_preset, PRESETS


def get_output_dir(cli_dir: str | None, config: dict) -> Path:
    """Resolve output directory: --dir flag > config file."""
    if cli_dir:
        d = Path(cli_dir)
    else:
        d = Path(config["output_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grab",
        description="Download and compress media for target platforms",
    )
    p.add_argument("url", nargs="?", help="URL to download")
    p.add_argument(
        "-p", "--preset",
        help=f"Target preset: {', '.join(PRESETS.keys())}, or a number in MB",
    )
    p.add_argument(
        "-q", "--quality",
        help="Video quality: max, 2160, 1440, 1080, 720, 480, 360",
    )
    p.add_argument("-a", "--audio-only", action="store_true", help="Download audio only")
    p.add_argument("-o", "--output", help="Output file path")
    p.add_argument("-d", "--dir", help="Output directory (overrides config)")
    p.add_argument("-k", "--keep", action="store_true", help="Keep original after compression")
    p.add_argument("--cobalt", help="Cobalt API URL (overrides config)")
    p.add_argument("--no-compress", action="store_true", help="Skip compression")
    p.add_argument("--gif", action="store_true", help="Convert output to GIF")
    p.add_argument("--gif-fps", type=int, default=15, help="GIF fps (default: 15)")
    p.add_argument("--gif-width", type=int, default=480, help="GIF width (default: 480)")
    p.add_argument("--json", action="store_true", help="Output JSON only (no human summary)")
    p.add_argument(
        "--batch", metavar="FILE",
        help="Read URLs from file (one per line, - for stdin)",
    )
    return p


def make_output_path(
    url: str, downloaded_path: Path, out_dir: Path,
    config: dict, ext: str | None = None,
) -> Path:
    """Generate a clean output path using naming template."""
    template = config.get("filename_template", "{source}_{title}_{date}")
    suffix = ext or downloaded_path.suffix
    name = generate_filename(url, title=downloaded_path.stem, template=template, ext=suffix)
    return deduplicate(out_dir / name)


def process_media(
    info, downloaded_path: Path, out_dir: Path,
    args: argparse.Namespace, config: dict,
) -> Path:
    """Route to image or video compression based on media type."""
    preset_name = args.preset or config.get("default_preset") or None

    if not preset_name or args.no_compress:
        return None  # no compression needed

    preset = resolve_preset(preset_name)

    if info.size_bytes <= preset.max_bytes:
        log("already within size limit, no compression needed")
        return None

    if is_image(downloaded_path):
        out = args.output or make_output_path(
            args.url, downloaded_path, out_dir, config,
            ext=downloaded_path.suffix,
        )
        result = resize_image(
            input_path=downloaded_path,
            output_path=out,
            max_bytes=preset.max_bytes,
            max_width=preset.max_width,
            max_height=preset.max_height,
        )
        return Path(result.path)
    else:
        out = args.output or make_output_path(
            args.url, downloaded_path, out_dir, config, ext=".mp4",
        )
        result = compress(
            input_path=downloaded_path,
            preset=preset,
            output_path=out,
        )
        return Path(result.path)


def run_single(url: str, args: argparse.Namespace, config: dict) -> None:
    """Download and optionally compress a single URL."""
    out_dir = get_output_dir(args.dir, config)
    quality = args.quality or config.get("default_quality", "1080")
    cobalt = args.cobalt or config.get("cobalt_api") or None

    info = download(
        url=url,
        cobalt_api=cobalt,
        quality=quality,
        audio_only=args.audio_only,
    )

    downloaded_path = Path(info.path)

    # Try compression/optimization
    compressed = process_media(info, downloaded_path, out_dir, args, config)

    if compressed:
        final_path = compressed
        if args.keep:
            keep_path = final_path.with_stem(final_path.stem + "_original")
            keep_path = keep_path.with_suffix(downloaded_path.suffix)
            shutil.copy2(downloaded_path, keep_path)
            log(f"original kept: {keep_path}")
    else:
        if args.output:
            final_path = Path(args.output)
        else:
            final_path = make_output_path(url, downloaded_path, out_dir, config)
        if final_path != downloaded_path:
            shutil.copy2(downloaded_path, final_path)

    # GIF conversion
    if args.gif:
        gif_path = final_path.with_suffix(".gif")
        preset_name = args.preset or config.get("default_preset") or None
        max_bytes = resolve_preset(preset_name).max_bytes if preset_name else None
        gif_info = to_gif(
            input_path=final_path,
            output_path=gif_path,
            fps=args.gif_fps,
            width=args.gif_width,
            max_bytes=max_bytes,
        )
        print(gif_info.to_json())
        if not args.json:
            log(f"saved gif: {gif_path}")
        return

    final_info = probe(final_path)
    print(final_info.to_json())
    if not args.json:
        log(f"saved: {final_path}")


def run_batch(batch_file: str, args: argparse.Namespace, config: dict) -> None:
    """Process multiple URLs from a file or stdin."""
    if batch_file == "-":
        lines = sys.stdin.read().splitlines()
    else:
        lines = Path(batch_file).read_text().splitlines()

    urls = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    log(f"batch: {len(urls)} URLs to process")

    for i, url in enumerate(urls, 1):
        log(f"[{i}/{len(urls)}] {url}")
        try:
            run_single(url, args, config)
        except Exception as e:
            log(f"error processing {url}: {e}")


def handle_config(argv: list[str]) -> None:
    """Handle 'grab config' subcommand."""
    from grab.config import main as config_main
    sys.argv = ["grab-config"] + argv
    config_main()


def handle_gif_subcommand(argv: list[str]) -> None:
    """Handle 'grab gif <file>' subcommand."""
    from grab.gif import main as gif_main
    sys.argv = ["grab-gif"] + argv
    gif_main()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        handle_config(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "gif":
        handle_gif_subcommand(sys.argv[2:])
        return

    parser = build_parser()
    args = parser.parse_args()
    config = load_config()

    if args.batch:
        run_batch(args.batch, args, config)
    elif args.url:
        run_single(args.url, args, config)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
