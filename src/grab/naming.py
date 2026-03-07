"""Clean filename generation from URLs and metadata.

Run directly: python -m grab.naming "https://instagram.com/reel/abc123"
Outputs the generated filename stem (no extension).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


SOURCE_PATTERNS = {
    "youtube": (r"(youtube\.com|youtu\.be)", "yt"),
    "instagram": (r"instagram\.com", "ig"),
    "twitter": (r"(twitter\.com|x\.com)", "x"),
    "tiktok": (r"tiktok\.com", "tt"),
    "reddit": (r"reddit\.com", "reddit"),
    "twitch": (r"twitch\.tv", "twitch"),
    "vimeo": (r"vimeo\.com", "vimeo"),
    "facebook": (r"facebook\.com", "fb"),
    "pinterest": (r"pinterest\.com", "pin"),
    "tumblr": (r"tumblr\.com", "tumblr"),
    "soundcloud": (r"soundcloud\.com", "sc"),
    "bilibili": (r"bilibili\.com", "bili"),
}


def detect_source(url: str) -> str:
    """Detect the source platform from a URL."""
    host = urlparse(url).netloc.lower()
    for name, (pattern, short) in SOURCE_PATTERNS.items():
        if re.search(pattern, host):
            return short
    return "web"


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a clean filename-safe slug."""
    text = text.lower()
    # Remove emojis and special unicode
    text = text.encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse multiple hyphens
    text = re.sub(r"-+", "-", text)
    # Strip leading/trailing hyphens
    text = text.strip("-")
    # Truncate
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def generate_filename(
    url: str,
    title: str | None = None,
    template: str = "{source}_{title}_{date}",
    ext: str = ".mp4",
) -> str:
    """Generate a clean filename from URL and metadata.

    Template variables: {source}, {title}, {date}, {timestamp}
    """
    source = detect_source(url)
    date_str = datetime.now().strftime("%Y%m%d")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if title:
        title_slug = slugify(title)
    else:
        # Extract something useful from the URL path
        path = urlparse(url).path.strip("/")
        last_segment = path.split("/")[-1] if path else "download"
        # Remove common prefixes like "reel", "watch", "video"
        if last_segment in ("watch", "video", "reel", "status", "p"):
            parts = path.split("/")
            last_segment = parts[-1] if len(parts) > 1 else "download"
        title_slug = slugify(last_segment)

    name = template.format(
        source=source,
        title=title_slug,
        date=date_str,
        timestamp=timestamp,
    )

    return name + ext


def deduplicate(path: Path) -> Path:
    """If path exists, append _1, _2, etc."""
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 1
    while True:
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m grab.naming <url> [title]", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    title = sys.argv[2] if len(sys.argv) > 2 else None
    print(generate_filename(url, title=title))


if __name__ == "__main__":
    main()
