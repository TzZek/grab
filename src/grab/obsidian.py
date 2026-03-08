"""Obsidian vault integration for grab.

Writes summary notes with YAML frontmatter to an Obsidian vault.
Generates obsidian:// URIs for clickable terminal links.

Run directly: python -m grab.obsidian --help
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from grab import log
from grab.util import format_duration, sanitize_filename


def load_video_metadata(media_path: Path) -> dict:
    """Load metadata from .info.json sidecar.

    Searches: exact match, stem match, base stem (strip .en etc), any in dir.
    """
    candidates = [
        media_path.with_suffix(".info.json"),
        media_path.parent / f"{media_path.stem}.info.json",
    ]
    base_stem = media_path.stem.split(".")[0]
    if base_stem != media_path.stem:
        candidates.append(media_path.parent / f"{base_stem}.info.json")

    for c in candidates:
        if c.exists():
            try:
                return json.loads(c.read_text())
            except (json.JSONDecodeError, OSError):
                continue

    for f in media_path.parent.glob("*.info.json"):
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
    return {}


def _build_tags(meta: dict, content_type: str = "video-note",
                auto_tags: list[str] | None = None) -> list[str]:
    tags = [content_type]
    for cat in (meta.get("categories") or []):
        tag = cat.lower().replace(" ", "-").replace("&", "and")
        if tag and tag != content_type:
            tags.append(tag)
    author = meta.get("channel") or meta.get("uploader") or meta.get("author") or ""
    if author:
        slug = re.sub(r"[^a-z0-9]+", "-", author.lower()).strip("-")
        if slug:
            tags.append(slug)
    # Merge auto-tags, deduplicating
    if auto_tags:
        seen = set(tags)
        for t in auto_tags:
            if t not in seen:
                tags.append(t)
                seen.add(t)
    return tags


def _resolve_meta(media_path: Path | None, meta: dict | None) -> tuple[dict, str]:
    """Resolve metadata and generate a safe title stem."""
    if meta is None:
        meta = load_video_metadata(media_path) if media_path else {}
    title = meta.get("title") or (media_path.stem if media_path else "Untitled")
    return meta, sanitize_filename(title, max_len=120)


def write_transcript(
    transcript: str,
    vault_path: Path,
    folder: str,
    media_path: Path | None = None,
    meta: dict | None = None,
) -> Path:
    """Write the full transcript as a vault note. Returns the note path."""
    meta, safe_title = _resolve_meta(media_path, meta)

    title = meta.get("title") or safe_title
    url = meta.get("webpage_url") or meta.get("original_url") or ""
    author = meta.get("channel") or meta.get("uploader") or ""

    frontmatter = f"""---
tags:
  - transcript
type: transcript
title: "{title}"
author: "{author}"
source: "{url}"
---"""

    content = f"{frontmatter}\n\n# Transcript — {title}\n\n{transcript}\n"

    dest_dir = vault_path / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    note_path = dest_dir / f"{safe_title} — Transcript.md"
    note_path.write_text(content)
    return note_path


def write_note(
    summary: str,
    vault_path: Path,
    folder: str,
    media_path: Path | None = None,
    meta: dict | None = None,
    transcript: str | None = None,
    content_type: str = "video-note",
    auto_tags: list[str] | None = None,
) -> Path:
    """Write a summary as an Obsidian note with YAML frontmatter.

    If transcript is provided, also writes a companion transcript note
    and adds a [[backlink]] to it from the summary.
    content_type: "video-note" or "pdf-note" — controls tags, frontmatter, callout.
    auto_tags: topic tags from LLM summarization to merge into frontmatter.
    """
    meta, safe_title = _resolve_meta(media_path, meta)

    title = meta.get("title") or safe_title
    author = meta.get("channel") or meta.get("uploader") or meta.get("author") or ""
    url = meta.get("webpage_url") or meta.get("original_url") or meta.get("source") or ""
    tags = _build_tags(meta, content_type, auto_tags=auto_tags)

    # Build frontmatter based on content type
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    fm_lines = [
        "---",
        "tags:",
        tags_yaml,
        f'type: {content_type}',
        f'title: "{title}"',
        f'author: "{author}"',
        f'source: "{url}"',
    ]

    if content_type == "pdf-note":
        pages = meta.get("pages") or ""
        fm_lines.append(f'pages: {pages}')
    elif content_type == "article-note":
        date = meta.get("date") or ""
        sitename = meta.get("sitename") or ""
        fm_lines.append(f'date: "{date}"')
        fm_lines.append(f'site: "{sitename}"')
    elif content_type == "podcast-note":
        date = meta.get("date") or ""
        show = meta.get("show") or ""
        duration = meta.get("duration") or ""
        fm_lines.append(f'date: "{date}"')
        fm_lines.append(f'show: "{show}"')
        fm_lines.append(f'duration: "{duration}"')
    else:
        upload_date = meta.get("upload_date") or ""
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        duration = format_duration(meta.get("duration"))
        fm_lines.append(f'date: {upload_date}')
        fm_lines.append(f'duration: "{duration}"')

    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)

    # Write transcript companion note if provided
    transcript_link = ""
    if transcript:
        t_path = write_transcript(transcript, vault_path, folder, media_path, meta)
        transcript_link = f"\n**Full transcript:** [[{t_path.stem}]]\n"

    # Build info callout
    info_parts = []
    if content_type == "pdf-note":
        if author:
            info_parts.append(f"**Author:** {author}")
        if meta.get("pages"):
            info_parts.append(f"**Pages:** {meta['pages']}")
        if url:
            info_parts.append(f"**Source:** {url}")
    elif content_type == "article-note":
        if author:
            info_parts.append(f"**Author:** {author}")
        sitename = meta.get("sitename") or ""
        if sitename:
            info_parts.append(f"**Site:** {sitename}")
        if url:
            info_parts.append(f"**URL:** {url}")
    elif content_type == "podcast-note":
        show = meta.get("show") or ""
        if show:
            info_parts.append(f"**Show:** {show}")
        if author:
            info_parts.append(f"**Host:** {author}")
        duration = meta.get("duration") or ""
        if duration:
            info_parts.append(f"**Duration:** {duration}")
        if url:
            info_parts.append(f"**URL:** {url}")
    else:
        if author:
            info_parts.append(f"**Channel:** {author}")
        if url:
            info_parts.append(f"**URL:** {url}")
        duration = format_duration(meta.get("duration"))
        if duration:
            info_parts.append(f"**Duration:** {duration}")

    callout = ""
    if info_parts:
        callout_body = "\n".join(f"> {p}" for p in info_parts)
        callout = f"\n> [!info] Source\n{callout_body}\n"

    note_content = f"{frontmatter}\n\n# {title}\n{callout}{transcript_link}\n{summary}\n"

    dest_dir = vault_path / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    note_path = dest_dir / f"{safe_title}.md"
    note_path.write_text(note_content)
    return note_path


def open_uri(vault_path: Path, note_path: Path) -> str:
    """Generate an obsidian:// URI for opening a note.

    Returns a clickable URI that most terminals will render as a link.
    """
    vault_name = vault_path.name
    rel = note_path.relative_to(vault_path)
    return f"obsidian://open?vault={quote(vault_name)}&file={quote(str(rel))}"


def print_link(vault_path: Path, note_path: Path) -> None:
    """Print a clickable obsidian:// link to stderr using OSC 8 hyperlinks."""
    uri = open_uri(vault_path, note_path)
    title = note_path.stem
    # OSC 8 terminal hyperlink: \e]8;;URL\e\\LABEL\e]8;;\e\\
    hyperlink = f"\033]8;;{uri}\033\\{title}\033]8;;\033\\"
    log(f"open in obsidian: {hyperlink}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="grab-obsidian",
        description="Write a markdown file as an Obsidian note with metadata",
    )
    parser.add_argument("input", help="Markdown summary file to import")
    parser.add_argument("--vault", required=True, help="Path to Obsidian vault")
    parser.add_argument("--folder", default="reference/videos", help="Vault subfolder")
    parser.add_argument("--media", help="Media file path (for .info.json metadata)")
    args = parser.parse_args()

    summary = Path(args.input).read_text()
    media = Path(args.media) if args.media else None
    vault = Path(args.vault)

    note_path = write_note(summary, vault, args.folder, media_path=media)
    log(f"note: {note_path}")
    print_link(vault, note_path)


if __name__ == "__main__":
    main()
