"""Podcast episode support: resolve audio URLs, download, extract metadata.

Handles Apple Podcasts (iTunes API), Spotify (yt-dlp), and RSS feeds.

Run directly: grab podcast <url>
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

from grab import log
from grab.util import format_duration, sanitize_filename

_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass
class PodcastInfo:
    url: str
    title: str
    show: str
    path: str | None
    duration: str
    description: str
    date: str

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _resolve_apple_podcast(url: str) -> dict:
    """Resolve Apple Podcasts URL to episode metadata via iTunes API."""
    # Extract episode ID from URL like /id123456789?i=987654321
    match = re.search(r"[?&]i=(\d+)", url)
    if not match:
        raise RuntimeError(f"Cannot extract episode ID from Apple Podcasts URL: {url}")
    episode_id = match.group(1)

    log("resolving apple podcast episode...")
    resp = httpx.get(f"https://itunes.apple.com/lookup?id={episode_id}&entity=podcastEpisode",
                     timeout=15, headers=_HEADERS)
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"Apple Podcasts episode not found: {episode_id}")

    ep = results[0]
    return {
        "title": ep.get("trackName", ""),
        "show": ep.get("collectionName", ""),
        "audio_url": ep.get("episodeUrl", ""),
        "duration": ep.get("trackTimeMillis", 0) // 1000 if ep.get("trackTimeMillis") else 0,
        "description": ep.get("description", ""),
        "date": ep.get("releaseDate", ""),
    }


def _parse_rss_feed(url: str) -> list[dict]:
    """Parse an RSS feed and return episode metadata."""
    log("fetching RSS feed...")
    resp = httpx.get(url, follow_redirects=True, timeout=30, headers=_HEADERS)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    channel = root.find("channel")
    show_title = channel.findtext("title", "") if channel is not None else ""

    episodes = []
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url", "") if enclosure is not None else ""
        duration = item.findtext("itunes:duration", "", ns)

        episodes.append({
            "title": item.findtext("title", ""),
            "show": show_title,
            "audio_url": audio_url,
            "duration": duration,
            "description": item.findtext("description", ""),
            "date": item.findtext("pubDate", ""),
        })
    return episodes


def _download_audio(audio_url: str, output_dir: Path, filename: str = "") -> Path:
    """Download audio file from URL."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        url_path = urlparse(audio_url).path
        filename = Path(url_path).name or "episode.mp3"
    if not Path(filename).suffix:
        filename += ".mp3"

    out_path = output_dir / filename
    log(f"downloading audio: {filename}")

    with httpx.stream("GET", audio_url, follow_redirects=True, timeout=120, headers=_HEADERS) as stream:
        with open(out_path, "wb") as f:
            for chunk in stream.iter_bytes(chunk_size=65536):
                f.write(chunk)

    log(f"downloaded: {out_path.name} ({out_path.stat().st_size / 1024:.0f} KB)")
    return out_path


def _download_via_ytdlp(url: str, output_dir: Path) -> Path:
    """Download podcast audio via yt-dlp (for Spotify etc)."""
    import subprocess

    log("downloading via yt-dlp...")
    output_dir.mkdir(parents=True, exist_ok=True)
    template = str(output_dir / "%(title)s.%(ext)s")

    cmd = ["yt-dlp", "-x", "--audio-format", "mp3", "--write-info-json",
           "-o", template, url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    # Find the downloaded file
    for f in sorted(output_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.suffix in (".mp3", ".m4a", ".opus", ".ogg", ".wav"):
            return f
    raise RuntimeError("yt-dlp produced no audio file")


def process_podcast(url: str, output_dir: Path) -> PodcastInfo:
    """Download and process a podcast episode from a URL."""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().replace("www.", "")

    meta = None
    audio_path = None

    if domain == "podcasts.apple.com":
        meta = _resolve_apple_podcast(url)
        if meta.get("audio_url"):
            safe = sanitize_filename(meta["title"], fallback="episode")
            audio_path = _download_audio(meta["audio_url"], output_dir, f"{safe}.mp3")

    elif domain == "open.spotify.com":
        audio_path = _download_via_ytdlp(url, output_dir)
        # Try to get metadata from info.json
        for f in output_dir.glob("*.info.json"):
            try:
                data = json.loads(f.read_text())
                meta = {
                    "title": data.get("title", ""),
                    "show": data.get("series", data.get("album", "")),
                    "duration": data.get("duration", 0),
                    "description": data.get("description", ""),
                    "date": data.get("upload_date", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass
            break

    elif any(p in url.lower() for p in ("/feed", "/rss", ".rss", ".xml", "/atom")):
        episodes = _parse_rss_feed(url)
        if not episodes:
            raise RuntimeError(f"No episodes found in feed: {url}")
        # Get latest episode
        ep = episodes[0]
        meta = ep
        if ep.get("audio_url"):
            safe = sanitize_filename(ep["title"], fallback="episode")
            audio_path = _download_audio(ep["audio_url"], output_dir, f"{safe}.mp3")

    else:
        # Try yt-dlp as fallback
        audio_path = _download_via_ytdlp(url, output_dir)
        for f in output_dir.glob("*.info.json"):
            try:
                data = json.loads(f.read_text())
                meta = {
                    "title": data.get("title", ""),
                    "show": data.get("series", data.get("channel", "")),
                    "duration": data.get("duration", 0),
                    "description": data.get("description", ""),
                    "date": data.get("upload_date", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass
            break

    if meta is None:
        meta = {"title": "", "show": "", "duration": 0, "description": "", "date": ""}

    return PodcastInfo(
        url=url,
        title=meta.get("title", ""),
        show=meta.get("show", ""),
        path=str(audio_path) if audio_path else None,
        duration=format_duration(meta.get("duration", 0)),
        description=meta.get("description", ""),
        date=meta.get("date", ""),
    )


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="grab-podcast", description="Download podcast episodes")
    parser.add_argument("url", help="Podcast episode URL or RSS feed URL")
    parser.add_argument("--output-dir", "-d", help="Output directory")
    args = parser.parse_args()

    if args.output_dir:
        out_dir = Path(args.output_dir)
        info = process_podcast(args.url, out_dir)
    else:
        from grab.util import temp_dir
        with temp_dir("grab_podcast_") as d:
            info = process_podcast(args.url, d)
    print(info.to_json())


if __name__ == "__main__":
    main()
