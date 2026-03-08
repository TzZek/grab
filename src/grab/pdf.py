"""Download PDFs and extract text + metadata.

Handles both URL downloads and local PDF files. Uses pymupdf for
text extraction with page markers for LLM context.

Run directly: grab pdf <url-or-file>
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse

import httpx

from grab import log


@dataclass
class PDFInfo:
    path: str
    text: str
    title: str
    author: str
    pages: int
    size_bytes: int
    text_path: str | None = None
    metadata: dict | None = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d.pop("text", None)  # don't dump full text in JSON output
        d["text_chars"] = len(self.text)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


MAX_TEXT_CHARS = 150_000


def _normalize_url(url: str) -> str:
    """Convert hosting platform URLs to direct download links."""
    parsed = urlparse(url)

    # GitHub: /blob/ → /raw/
    if parsed.netloc in ("github.com", "www.github.com") and "/blob/" in parsed.path:
        return url.replace("/blob/", "/raw/", 1)

    # GitLab: append ?inline=false for raw download
    if "gitlab" in parsed.netloc and "/blob/" in parsed.path:
        raw_url = url.replace("/blob/", "/raw/", 1)
        return raw_url

    return url


def download_pdf(url: str, output_dir: Path) -> Path:
    """Download a PDF from a URL. Returns the local file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    url = _normalize_url(url)

    # Try to get filename from URL path, decode percent-encoding
    from urllib.parse import unquote
    url_path = unquote(urlparse(url).path)
    filename = Path(url_path).name if url_path else "document.pdf"
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    log("downloading pdf...")
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as stream:
        # Override filename from Content-Disposition if available
        cd = stream.headers.get("content-disposition", "")
        if "filename=" in cd:
            fn = cd.split("filename=")[-1].strip('" ')
            if fn:
                filename = fn

        out_path = output_dir / filename
        with open(out_path, "wb") as f:
            for chunk in stream.iter_bytes(chunk_size=65536):
                f.write(chunk)

    # Validate the download is actually a PDF
    with open(out_path, "rb") as f:
        header = f.read(5)
    if header != b"%PDF-":
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file is not a PDF (got HTML or error page from {url})")

    log(f"downloaded: {out_path.name} ({out_path.stat().st_size / 1024:.0f} KB)")
    return out_path


def extract_text(pdf_path: Path) -> tuple[str, dict]:
    """Extract text and metadata from a PDF. Returns (text, metadata)."""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise RuntimeError("pymupdf not installed. Install: uv pip install pymupdf")

    doc = fitz.open(str(pdf_path))
    meta = doc.metadata or {}

    pages = []
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        if text:
            pages.append(f"--- Page {i} ---\n{text}")

    doc.close()

    full_text = "\n\n".join(pages)
    if len(full_text) > MAX_TEXT_CHARS:
        log(f"text truncated from {len(full_text)} to {MAX_TEXT_CHARS} chars")
        full_text = full_text[:MAX_TEXT_CHARS] + "\n\n[... truncated ...]"

    # Warn if PDF appears to be scanned (image-only)
    total_pages = i if pages else 0
    if total_pages > 0 and len(full_text) < total_pages * 50:
        log("warning: very little text extracted — PDF may be scanned/image-only")

    metadata = {
        "title": meta.get("title") or pdf_path.stem,
        "author": meta.get("author") or "",
        "pages": total_pages,
        "subject": meta.get("subject") or "",
        "creator": meta.get("creator") or "",
        "creation_date": meta.get("creationDate") or "",
    }
    return full_text, metadata


def process_pdf(url_or_path: str, output_dir: Path) -> PDFInfo:
    """Download (if URL) and extract text from a PDF."""
    path = Path(url_or_path)
    is_local = path.exists() and path.suffix.lower() == ".pdf"

    if is_local:
        pdf_path = path
    else:
        pdf_path = download_pdf(url_or_path, output_dir)

    text, metadata = extract_text(pdf_path)

    # Save extracted text alongside PDF
    text_path = pdf_path.with_suffix(".txt")
    text_path.write_text(text)
    log(f"extracted {len(text)} chars from {metadata['pages']} pages")

    # Save metadata sidecar
    meta_path = pdf_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(metadata, indent=2))

    return PDFInfo(
        path=str(pdf_path),
        text=text,
        title=metadata["title"],
        author=metadata["author"],
        pages=metadata["pages"],
        size_bytes=pdf_path.stat().st_size,
        text_path=str(text_path),
        metadata=metadata,
    )


def is_pdf_url(url: str) -> bool:
    """Check if a URL points to a PDF."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="grab-pdf", description="Download and extract text from PDFs")
    parser.add_argument("input", help="PDF URL or local file path")
    parser.add_argument("--output-dir", "-d", help="Output directory")
    args = parser.parse_args()

    if args.output_dir:
        out_dir = Path(args.output_dir)
        info = process_pdf(args.input, out_dir)
    else:
        # Local files use parent dir; URLs need a temp dir
        input_path = Path(args.input)
        if input_path.exists() and input_path.suffix.lower() == ".pdf":
            info = process_pdf(args.input, input_path.parent)
        else:
            from grab.util import temp_dir
            with temp_dir("grab_pdf_") as d:
                info = process_pdf(args.input, d)
    print(info.to_json())


if __name__ == "__main__":
    main()
