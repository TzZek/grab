"""Download media from URLs using cobalt API or yt-dlp.

Run directly: python -m grab.download <url> [--cobalt <api_url>] [--quality 720] [--audio-only]
Outputs JSON to stdout with the downloaded file path and metadata.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from grab import MediaInfo, log
from grab.probe import probe


def download_cobalt(
    url: str,
    api_url: str,
    output_dir: Path,
    quality: str = "1080",
    audio_only: bool = False,
) -> Path | None:
    """Try downloading via cobalt API. Returns file path or None on failure."""
    body = {
        "url": url,
        "downloadMode": "audio" if audio_only else "auto",
        "videoQuality": quality.rstrip("p"),
        "filenameStyle": "basic",
    }

    try:
        resp = httpx.post(
            api_url,
            json=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        data = resp.json()
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        log(f"cobalt request failed: {e}")
        return None

    status = data.get("status")

    if status in ("redirect", "tunnel"):
        dl_url = data["url"]
    elif status == "picker":
        dl_url = data["picker"][0]["url"]
    elif status == "error":
        code = data.get("error", {}).get("code", "unknown")
        log(f"cobalt error: {code}")
        return None
    else:
        log(f"cobalt unexpected response: {data}")
        return None

    log("downloading via cobalt...")
    out_path = output_dir / "cobalt_download"
    with httpx.stream("GET", dl_url, follow_redirects=True, timeout=300) as stream:
        # Try to get extension from content-type or URL
        ct = stream.headers.get("content-type", "")
        ext = _ext_from_content_type(ct) or _ext_from_url(dl_url) or ".mp4"
        out_path = out_path.with_suffix(ext)
        with open(out_path, "wb") as f:
            for chunk in stream.iter_bytes(chunk_size=65536):
                f.write(chunk)

    return out_path


def download_ytdlp(
    url: str,
    output_dir: Path,
    quality: str = "1080",
    audio_only: bool = False,
    cookies_from_browser: str = "",
) -> Path:
    """Download via yt-dlp. Returns file path."""
    out_template = str(output_dir / "%(title).60s.%(ext)s")

    cmd = ["yt-dlp", "--no-playlist", "--write-info-json", "-o", out_template]

    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]

    if audio_only:
        cmd += ["-x", "--audio-format", "mp3"]
    else:
        cmd += [
            "-f", f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best",
            "--merge-output-format", "mp4",
        ]

    cmd.append(url)
    log("downloading via yt-dlp...")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed:\n{result.stderr}")

    # Find the downloaded file
    files = sorted(output_dir.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError("yt-dlp produced no output file")
    return files[0]


def download(
    url: str,
    output_dir: str | Path | None = None,
    cobalt_api: str | None = None,
    quality: str = "1080",
    audio_only: bool = False,
    cookies_from_browser: str = "",
) -> MediaInfo:
    """Download media and return its metadata.

    Tries cobalt first (if api url provided), falls back to yt-dlp.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="grab_"))
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    cobalt_api = cobalt_api or os.environ.get("GRAB_COBALT_API")
    path = None

    # Auto-start cobalt for platforms that need it
    if not cobalt_api:
        from grab.cobalt import needs_cobalt
        if needs_cobalt(url):
            try:
                from grab.cobalt import ensure_running
                cobalt_api = ensure_running(cookies_from_browser)
            except Exception as e:
                log(f"cobalt auto-start failed: {e}, trying yt-dlp...")

    if cobalt_api:
        path = download_cobalt(url, cobalt_api, output_dir, quality, audio_only)
        if path is None:
            log("cobalt failed, falling back to yt-dlp...")

    if path is None:
        path = download_ytdlp(url, output_dir, quality, audio_only, cookies_from_browser)

    info = probe(path)
    log(f"downloaded: {path.name} ({_human_size(info.size_bytes)})")
    return info


def _ext_from_content_type(ct: str) -> str | None:
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "audio/mpeg": ".mp3",
        "audio/ogg": ".ogg",
        "audio/wav": ".wav",
        "audio/mp4": ".m4a",
    }
    for key, ext in mapping.items():
        if key in ct:
            return ext
    return None


def _ext_from_url(url: str) -> str | None:
    from urllib.parse import urlparse
    path = urlparse(url).path
    if "." in path.split("/")[-1]:
        return "." + path.split(".")[-1].split("?")[0][:5]
    return None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Download media from a URL")
    parser.add_argument("url", help="URL to download")
    parser.add_argument("--cobalt", help="Cobalt API URL")
    parser.add_argument("--quality", default="1080", help="Video quality (default: 1080)")
    parser.add_argument("--audio-only", action="store_true")
    parser.add_argument("--output-dir", help="Directory to save to (default: temp dir)")
    args = parser.parse_args()

    info = download(
        url=args.url,
        output_dir=args.output_dir,
        cobalt_api=args.cobalt,
        quality=args.quality,
        audio_only=args.audio_only,
    )
    print(info.to_json())


if __name__ == "__main__":
    main()
