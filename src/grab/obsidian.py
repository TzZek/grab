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


def _format_duration(seconds: int | float | None) -> str:
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def _build_tags(meta: dict) -> list[str]:
    tags = ["video-note"]
    for cat in (meta.get("categories") or []):
        tag = cat.lower().replace(" ", "-").replace("&", "and")
        if tag and tag != "video-note":
            tags.append(tag)
    channel = meta.get("channel") or meta.get("uploader") or ""
    if channel:
        slug = re.sub(r"[^a-z0-9]+", "-", channel.lower()).strip("-")
        if slug:
            tags.append(slug)
    return tags


def _resolve_meta(media_path: Path | None, meta: dict | None) -> tuple[dict, str]:
    """Resolve metadata and generate a safe title stem."""
    if meta is None:
        meta = load_video_metadata(media_path) if media_path else {}
    title = meta.get("title") or (media_path.stem if media_path else "Untitled")
    return meta, _sanitize_filename(title)[:120]


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
) -> Path:
    """Write a summary as an Obsidian note with YAML frontmatter.

    If transcript is provided, also writes a companion transcript note
    and adds a [[backlink]] to it from the summary.
    """
    meta, safe_title = _resolve_meta(media_path, meta)

    title = meta.get("title") or safe_title
    author = meta.get("channel") or meta.get("uploader") or ""
    url = meta.get("webpage_url") or meta.get("original_url") or ""
    upload_date = meta.get("upload_date") or ""
    duration = _format_duration(meta.get("duration"))
    tags = _build_tags(meta)

    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    # Write transcript companion note if provided
    transcript_link = ""
    if transcript:
        t_path = write_transcript(transcript, vault_path, folder, media_path, meta)
        transcript_link = f"\n**Full transcript:** [[{t_path.stem}]]\n"

    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    frontmatter = f"""---
tags:
{tags_yaml}
type: video-note
title: "{title}"
author: "{author}"
source: "{url}"
date: {upload_date}
duration: "{duration}"
---"""

    info_parts = []
    if author:
        info_parts.append(f"**Channel:** {author}")
    if url:
        info_parts.append(f"**URL:** {url}")
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
