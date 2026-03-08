"""Smart URL type detection for routing.

Classifies URLs into: pdf, article, podcast, media.
Uses fast pattern matching first, then HEAD request for ambiguous URLs.

Run directly: grab detect <url>
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from grab import log, vlog


class DetectionError(Exception):
    pass

# Content types for routing
PDF = "pdf"
ARTICLE = "article"
PODCAST = "podcast"
MEDIA = "media"

MEDIA_DOMAINS = {
    "youtube.com", "youtu.be",
    "instagram.com", "twitter.com", "x.com",
    "tiktok.com", "facebook.com", "vimeo.com",
    "twitch.tv", "dailymotion.com", "reddit.com",
    "pinterest.com", "snapchat.com",
    "bilibili.com", "streamable.com", "rumble.com",
    "soundcloud.com", "bandcamp.com",
}

PODCAST_DOMAINS = {
    "podcasts.apple.com",
    "podcasts.google.com",
    "podbean.com",
    "anchor.fm",
}

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".flv",
    ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus",
}

_FEED_PATTERN = re.compile(r"(/feed|/rss|/atom|\.rss|\.atom)(/|$)", re.IGNORECASE)


def _strip_www(domain: str) -> str:
    return domain[4:] if domain.startswith("www.") else domain


def _sniff_feed(url: str) -> bool:
    """Fetch first 512 bytes to check if XML is an RSS/Atom feed."""
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-511"})
        chunk = resp.text[:512].lower()
        return "<rss" in chunk or "<feed" in chunk
    except Exception:
        return False


def detect_from_url(url: str) -> str | None:
    """Classify URL by pattern matching alone. Returns None if ambiguous."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    path = parsed.path.lower()
    domain = _strip_www(parsed.netloc.lower())
    result = None

    # PDF
    if path.endswith(".pdf"):
        result = PDF

    # Podcast platforms
    elif domain == "podcasts.apple.com":
        result = PODCAST
    elif domain == "open.spotify.com" and path.startswith("/episode"):
        result = PODCAST
    elif domain in PODCAST_DOMAINS:
        result = PODCAST

    # Media platforms
    elif any(domain == d or domain.endswith("." + d) for d in MEDIA_DOMAINS):
        result = MEDIA

    else:
        # Direct media files
        ext = "." + path.rsplit(".", 1)[-1] if "." in path.split("/")[-1] else ""
        if ext in MEDIA_EXTENSIONS:
            result = MEDIA

        # RSS/Atom feed patterns
        elif _FEED_PATTERN.search(path):
            result = PODCAST

    if result:
        vlog(f"pattern match: {result} for {domain}")
    return result


def detect_from_head(url: str) -> str | None:
    """Classify URL via HTTP HEAD request Content-Type."""
    try:
        resp = httpx.head(url, follow_redirects=True, timeout=10,
                          headers={"User-Agent": "Mozilla/5.0"})
        ct = resp.headers.get("content-type", "").lower()
    except httpx.TimeoutException:
        vlog("HEAD request timed out")
        return None
    except Exception as e:
        vlog(f"HEAD request failed: {e}")
        return None

    vlog(f"HEAD content-type: {ct}")

    if "application/pdf" in ct:
        return PDF
    if ct.startswith("audio/"):
        return MEDIA
    if ct.startswith("video/"):
        return MEDIA
    if "rss+xml" in ct or "atom+xml" in ct:
        return PODCAST
    if "xml" in ct:
        # Could be RSS/Atom served as generic XML — check the body
        if _sniff_feed(url):
            return PODCAST
    # text/html, text/plain, etc.
    return ARTICLE


def detect(url: str) -> str:
    """Detect content type for a URL. Returns: pdf, article, podcast, or media."""
    result = detect_from_url(url)
    if result:
        return result
    result = detect_from_head(url)
    if result:
        return result
    # Network failed and no pattern match — can't determine type
    raise DetectionError(f"Could not determine content type for {url}")


def main() -> None:
    import sys
    if len(sys.argv) < 2:
        print("usage: grab detect <url>", file=sys.stderr)
        sys.exit(1)
    url = sys.argv[1]
    try:
        content_type = detect(url)
    except DetectionError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(f"{content_type}: {url}")


if __name__ == "__main__":
    main()
