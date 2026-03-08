"""Web article extraction using trafilatura.

Downloads HTML and extracts clean text + metadata from web articles,
blog posts, and news pages. Handles paywalls via browser cookies
and Google cache fallback.

Run directly: grab article <url>
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

from grab import log, vlog
from grab.util import sanitize_filename

MAX_TEXT_CHARS = 150_000
MIN_ARTICLE_CHARS = 200  # below this, article is likely truncated/paywalled

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@dataclass
class ArticleInfo:
    url: str
    title: str
    author: str
    text: str
    date: str
    sitename: str
    description: str
    path: str | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v}
        d.pop("text", None)
        d["text_chars"] = len(self.text)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _get_browser_cookies(browser: str, domain: str) -> dict[str, str]:
    """Extract cookies for a domain from the user's browser.

    Reuses cookie extraction from grab.cobalt (Chromium + Firefox support).
    Returns a name→value dict.
    """
    try:
        from grab.cobalt import _find_cookie_db, _extract_cookies_firefox, _extract_cookies_chromium
    except ImportError:
        return {}

    result = _find_cookie_db(browser)
    if not result:
        return {}
    db_path, is_firefox = result

    # Build domain variants: .example.com, example.com, www.example.com
    base = domain.lstrip(".")
    if base.startswith("www."):
        base = base[4:]
    domains = [base, f".{base}", f"www.{base}", f".www.{base}"]

    if is_firefox:
        pairs = _extract_cookies_firefox(db_path, domains)
    else:
        pairs = _extract_cookies_chromium(db_path, domains, browser)

    return {name: value for name, value in pairs}


def _fetch_google_cache(url: str) -> str | None:
    """Try fetching article from Google's webcache."""
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    try:
        resp = httpx.get(cache_url, follow_redirects=True, timeout=15, headers=_HEADERS)
        if resp.status_code == 200 and len(resp.text) > 1000:
            log("using google cache")
            return resp.text
    except Exception as e:
        vlog(f"google cache failed: {e}")
    return None


def fetch_html(url: str, cookies: dict[str, str] | None = None) -> str:
    """Download HTML content from a URL. Falls back to trafilatura's fetcher."""
    log("fetching article...")
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30,
                         headers=_HEADERS, cookies=cookies or {})
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError:
        # Some sites block generic requests; try trafilatura's fetcher
        try:
            import trafilatura
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                return downloaded
        except Exception as e:
            vlog(f"trafilatura fetch also failed: {e}")
        raise


def extract_article(html: str, url: str = "") -> ArticleInfo:
    """Extract article text and metadata from HTML."""
    try:
        import trafilatura
    except ImportError:
        raise RuntimeError("trafilatura not installed. Install: uv pip install trafilatura")

    doc = trafilatura.bare_extraction(html, url=url, include_comments=False,
                                      include_tables=True, favor_precision=False,
                                      with_metadata=True)
    if not doc or not doc.text:
        raise RuntimeError(f"Could not extract article content from {url or 'HTML'}")

    text = doc.text
    if len(text) > MAX_TEXT_CHARS:
        log(f"text truncated from {len(text)} to {MAX_TEXT_CHARS} chars")
        text = text[:MAX_TEXT_CHARS] + "\n\n[... truncated ...]"

    return ArticleInfo(
        url=url,
        title=doc.title or "",
        author=doc.author or "",
        text=text,
        date=doc.date or "",
        sitename=doc.sitename or "",
        description=doc.description or "",
    )


def process_article(url: str, output_dir: Path,
                    cookies_from_browser: str = "") -> ArticleInfo:
    """Fetch and extract an article from a URL.

    If the initial extraction looks truncated (paywall), tries:
    1. Re-fetch with browser cookies (if cookies_from_browser is set)
    2. Google webcache fallback
    """
    domain = urlparse(url).netloc.lower()

    # First attempt: plain fetch
    html = fetch_html(url)
    info = extract_article(html, url=url)

    # Check if content looks truncated (likely paywalled)
    if len(info.text) < MIN_ARTICLE_CHARS:
        log(f"warning: only {len(info.text)} chars extracted — may be paywalled")

        # Try with browser cookies
        if cookies_from_browser:
            cookies = _get_browser_cookies(cookies_from_browser, domain)
            if cookies:
                vlog(f"found {len(cookies)} cookies for {domain}")
                log(f"retrying with {len(cookies)} cookies from {cookies_from_browser}...")
                try:
                    html2 = fetch_html(url, cookies=cookies)
                    info2 = extract_article(html2, url=url)
                    if len(info2.text) > len(info.text):
                        log(f"cookies helped: {len(info.text)} → {len(info2.text)} chars")
                        info = info2
                except Exception as e:
                    log(f"cookie retry failed: {e}")

        # Still short? Try Google cache
        if len(info.text) < MIN_ARTICLE_CHARS:
            vlog("trying google cache")
            cached = _fetch_google_cache(url)
            if cached:
                try:
                    info3 = extract_article(cached, url=url)
                    if len(info3.text) > len(info.text):
                        log(f"cache helped: {len(info.text)} → {len(info3.text)} chars")
                        info = info3
                except Exception as e:
                    vlog(f"cache extraction failed: {e}")

        if len(info.text) < MIN_ARTICLE_CHARS:
            log("warning: article may be paywalled. try: grab config set cookies_from_browser <browser>")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Save extracted text
    safe_name = sanitize_filename(info.title or "article", fallback="article")
    text_path = output_dir / f"{safe_name}.txt"
    text_path.write_text(info.text)
    info.path = str(text_path)

    # Save metadata sidecar
    meta_path = output_dir / f"{safe_name}.meta.json"
    meta_path.write_text(json.dumps(info.to_dict(), indent=2))

    log(f"extracted {len(info.text)} chars from {info.sitename or url}")
    return info


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="grab-article", description="Extract text from web articles")
    parser.add_argument("url", help="Article URL")
    parser.add_argument("--output-dir", "-d", help="Output directory")
    parser.add_argument("--cookies", help="Browser to extract cookies from (e.g. chrome, firefox, zen)")
    args = parser.parse_args()

    if args.output_dir:
        out_dir = Path(args.output_dir)
        info = process_article(args.url, out_dir, cookies_from_browser=args.cookies or "")
    else:
        from grab.util import temp_dir
        with temp_dir("grab_article_") as d:
            info = process_article(args.url, d, cookies_from_browser=args.cookies or "")
    print(info.to_json())


if __name__ == "__main__":
    main()
