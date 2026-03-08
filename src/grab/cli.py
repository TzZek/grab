"""Main CLI entry point for grab.

Orchestrates smart URL routing → download → process → summarize → obsidian.
Run: grab <url> [options]
     grab config show|set|get
     grab gif <file> [options]
     grab transcribe <file> [options]
     grab summarize <file> [options]

Outputs JSON to stdout with final file metadata.
All progress/status goes to stderr.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import grab
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
    if cli_dir:
        d = Path(cli_dir)
    else:
        d = Path(config["output_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="grab", description="Download and compress media for target platforms")
    p.add_argument("url", nargs="?", help="URL to download")
    p.add_argument("-p", "--preset", help=f"Target preset: {', '.join(PRESETS.keys())}, or a number in MB")
    p.add_argument("-q", "--quality", help="Video quality: max, 2160, 1440, 1080, 720, 480, 360")
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
    p.add_argument("--batch", metavar="FILE", help="Read URLs from file (one per line, - for stdin)")
    p.add_argument("--transcribe", action="store_true", help="Transcribe audio/video to text")
    p.add_argument("--transcribe-backend", help="Transcription backend")
    p.add_argument("--transcribe-model", help="Transcription model name")
    p.add_argument("--summarize", action="store_true", help="Summarize content (implies --transcribe for media)")
    p.add_argument("--summarize-backend", help="Summarization backend")
    p.add_argument("--summarize-model", help="Summarization model name")
    p.add_argument("--language", default="", help="Language code for transcription (e.g. en, ja)")
    p.add_argument("--vault", action="store_true", help="Save summary as Obsidian note (implies --summarize)")
    p.add_argument("-v", "--verbose", action="store_true", help="Verbose output (debug logging to stderr)")
    return p


def make_output_path(url: str, downloaded_path: Path, out_dir: Path, config: dict, ext: str | None = None) -> Path:
    template = config.get("filename_template", "{source}_{title}_{date}")
    suffix = ext or downloaded_path.suffix
    name = generate_filename(url, title=downloaded_path.stem, template=template, ext=suffix)
    return deduplicate(out_dir / name)


def process_media(info, downloaded_path: Path, out_dir: Path, args: argparse.Namespace, config: dict) -> Path:
    preset_name = args.preset or config.get("default_preset") or None
    if not preset_name or args.no_compress:
        return None
    preset = resolve_preset(preset_name)
    if info.size_bytes <= preset.max_bytes:
        log("already within size limit, no compression needed")
        return None
    if is_image(downloaded_path):
        out = args.output or make_output_path(args.url, downloaded_path, out_dir, config, ext=downloaded_path.suffix)
        return Path(resize_image(input_path=downloaded_path, output_path=out, max_bytes=preset.max_bytes,
                                 max_width=preset.max_width, max_height=preset.max_height).path)
    out = args.output or make_output_path(args.url, downloaded_path, out_dir, config, ext=".mp4")
    return Path(compress(input_path=downloaded_path, preset=preset, output_path=out).path)


def _summarize_to_vault(text: str, url: str, args: argparse.Namespace, config: dict,
                        content_type: str, folder_key: str, default_folder: str,
                        output_path: Path, meta: dict | None = None,
                        media_path: Path | None = None) -> None:
    """Shared summarize → obsidian pipeline for all content types."""
    from grab.summarize import summarize as run_summarize, get_default_prompt

    prompt_type = {"pdf-note": "document", "article-note": "article",
                   "podcast-note": "podcast", "video-note": "video"}.get(content_type, "video")
    prompt = config.get("summarize_prompt") or get_default_prompt(prompt_type)
    s_info = run_summarize(
        text=text,
        backend=args.summarize_backend or config.get("summarize_backend", "ollama"),
        model=args.summarize_model or config.get("summarize_model", ""),
        prompt=prompt, output_path=output_path,
        api_base=config.get("summarize_api_base", ""),
        api_key=config.get("summarize_api_key", ""),
    )
    if not args.json:
        log(f"summary: {s_info.path}")

    vault_path = config.get("obsidian_vault", "")
    if args.vault or vault_path:
        if not vault_path:
            log("error: no obsidian_vault configured. Run: grab config set obsidian_vault /path/to/vault")
            return
        from grab.obsidian import write_note, print_link
        vault = Path(vault_path)
        note_meta = dict(meta or {})
        note_meta.setdefault("source", url)
        note_path = write_note(
            summary=s_info.summary, vault_path=vault,
            folder=config.get(folder_key, default_folder),
            media_path=media_path, meta=note_meta,
            transcript=text, content_type=content_type,
        )
        if not args.json:
            print_link(vault, note_path)


def _run_transcribe_summarize(final_path: Path, url: str, args: argparse.Namespace, config: dict) -> None:
    """Run the transcribe → summarize → obsidian pipeline for media."""
    do_summarize = args.summarize or args.vault
    do_transcribe = args.transcribe or do_summarize
    if not do_transcribe:
        return

    from grab.transcribe import transcribe as run_transcribe
    t_info = run_transcribe(
        input_path=final_path, url=url,
        backend=args.transcribe_backend or config.get("transcribe_backend", "faster-whisper"),
        model=args.transcribe_model or config.get("transcribe_model", "base"),
        language=args.language or config.get("transcribe_language", ""),
        output_dir=final_path.parent,
    )
    if not args.json:
        log(f"transcript: {len(t_info.text)} chars via {t_info.source}")

    if do_summarize:
        _summarize_to_vault(
            text=t_info.text, url=url, args=args, config=config,
            content_type="video-note", folder_key="obsidian_folder",
            default_folder="reference/videos", output_path=final_path.with_suffix(".summary.md"),
            media_path=final_path,
        )


def run_pdf(url: str, args: argparse.Namespace, config: dict) -> None:
    """Pipeline for PDF URLs: download → extract text → summarize → obsidian."""
    from grab.pdf import process_pdf

    out_dir = get_output_dir(args.dir, config)
    pdf_info = process_pdf(url, out_dir)
    final_path = Path(pdf_info.path)

    if args.summarize or args.vault:
        meta = pdf_info.metadata or {}
        meta["source"] = url
        _summarize_to_vault(
            text=pdf_info.text, url=url, args=args, config=config,
            content_type="pdf-note", folder_key="obsidian_pdf_folder",
            default_folder="reference/documents", output_path=final_path.with_suffix(".summary.md"),
            meta=meta, media_path=final_path,
        )

    print(pdf_info.to_json())
    if not args.json:
        log(f"saved: {final_path}")


def run_article(url: str, args: argparse.Namespace, config: dict) -> None:
    """Pipeline for articles: fetch → extract → summarize → obsidian."""
    from grab.article import process_article

    out_dir = get_output_dir(args.dir, config)
    cookies = config.get("cookies_from_browser", "")
    article_info = process_article(url, out_dir, cookies_from_browser=cookies)

    if args.summarize or args.vault:
        meta = {
            "title": article_info.title, "author": article_info.author,
            "date": article_info.date, "sitename": article_info.sitename,
            "source": url,
        }
        output_path = Path(article_info.path).with_suffix(".summary.md") if article_info.path else None
        _summarize_to_vault(
            text=article_info.text, url=url, args=args, config=config,
            content_type="article-note", folder_key="obsidian_article_folder",
            default_folder="reference/articles", output_path=output_path,
            meta=meta,
        )

    print(article_info.to_json())
    if not args.json:
        log(f"extracted: {article_info.title or url}")


def run_podcast(url: str, args: argparse.Namespace, config: dict) -> None:
    """Pipeline for podcasts: resolve → download audio → transcribe → summarize."""
    from grab.podcast import process_podcast

    out_dir = get_output_dir(args.dir, config)
    pod_info = process_podcast(url, out_dir)

    if pod_info.path and (args.transcribe or args.summarize or args.vault):
        audio_path = Path(pod_info.path)
        _run_transcribe_summarize(audio_path, url, args, config)

    print(pod_info.to_json())
    if not args.json:
        log(f"podcast: {pod_info.title or url}")


def run_single(url: str, args: argparse.Namespace, config: dict) -> None:
    from grab.detect import detect, DetectionError, PDF, ARTICLE, PODCAST, MEDIA

    try:
        content_type = detect(url)
    except DetectionError as e:
        log(str(e))
        sys.exit(1)
    log(f"detected: {content_type}")

    if content_type == PDF:
        return run_pdf(url, args, config)
    if content_type == ARTICLE:
        return run_article(url, args, config)
    if content_type == PODCAST:
        return run_podcast(url, args, config)

    # MEDIA — original download + compress pipeline
    out_dir = get_output_dir(args.dir, config)
    quality = args.quality or config.get("default_quality", "1080")
    cobalt = args.cobalt or config.get("cobalt_api") or None
    cookies = config.get("cookies_from_browser", "")
    info = download(url=url, cobalt_api=cobalt, quality=quality, audio_only=args.audio_only, cookies_from_browser=cookies)
    downloaded_path = Path(info.path)

    compressed = process_media(info, downloaded_path, out_dir, args, config)
    if compressed:
        final_path = compressed
        if args.keep:
            keep_path = final_path.with_stem(final_path.stem + "_original").with_suffix(downloaded_path.suffix)
            shutil.copy2(downloaded_path, keep_path)
            log(f"original kept: {keep_path}")
    else:
        final_path = Path(args.output) if args.output else make_output_path(url, downloaded_path, out_dir, config)
        if final_path != downloaded_path:
            shutil.copy2(downloaded_path, final_path)

    if args.gif:
        gif_path = final_path.with_suffix(".gif")
        preset_name = args.preset or config.get("default_preset") or None
        max_bytes = resolve_preset(preset_name).max_bytes if preset_name else None
        gif_info = to_gif(input_path=final_path, output_path=gif_path, fps=args.gif_fps,
                          width=args.gif_width, max_bytes=max_bytes)
        print(gif_info.to_json())
        if not args.json:
            log(f"saved gif: {gif_path}")
        return

    _run_transcribe_summarize(final_path, url, args, config)

    final_info = probe(final_path)
    print(final_info.to_json())
    if not args.json:
        log(f"saved: {final_path}")


def run_batch(batch_file: str, args: argparse.Namespace, config: dict) -> None:
    lines = sys.stdin.read().splitlines() if batch_file == "-" else Path(batch_file).read_text().splitlines()
    urls = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
    log(f"batch: {len(urls)} URLs to process")
    for i, url in enumerate(urls, 1):
        log(f"[{i}/{len(urls)}] {url}")
        try:
            run_single(url, args, config)
        except Exception as e:
            log(f"error processing {url}: {e}")


# Subcommand dispatch table
_SUBCOMMANDS = {
    "config": ("grab.config", "main", "grab-config"),
    "gif": ("grab.gif", "main", "grab-gif"),
    "pdf": ("grab.pdf", "main", "grab-pdf"),
    "article": ("grab.article", "main", "grab-article"),
    "podcast": ("grab.podcast", "main", "grab-podcast"),
    "detect": ("grab.detect", "main", "grab-detect"),
    "transcribe": ("grab.transcribe", "main", "grab-transcribe"),
    "summarize": ("grab.summarize", "main", "grab-summarize"),
    "cobalt": ("grab.cobalt", "main", "grab-cobalt"),
}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMANDS:
        mod_name, func_name, prog = _SUBCOMMANDS[sys.argv[1]]
        from importlib import import_module
        sys.argv = [prog] + sys.argv[2:]
        getattr(import_module(mod_name), func_name)()
        return

    parser = build_parser()
    args = parser.parse_args()
    grab.set_verbose(args.verbose)
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
