"""Summarize transcript text via LLMs. Multi-backend via BACKENDS dict.

Backends:
  ollama             — local Ollama (default)
  anthropic          — Claude API
  openai             — OpenAI API
  openai-compatible  — any OpenAI-compatible API with custom base_url

Run directly: python -m grab.summarize transcript.txt --backend ollama --model llama3.2
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

from grab import log, vlog
from grab.util import parse_srt

DEFAULT_PROMPT = """/no_think
You are summarizing a video transcript into a thorough reference note.

Rules:
- Start with a 1-2 sentence TL;DR
- Then use "## Key Ideas" with bullet points for the main concepts
- Use "## Details" for supporting facts, names, numbers, examples
  - Be thorough — capture all significant points, not just the headline
- Use "## Resources" if any tools, links, books, or people are mentioned worth following up on
- Do NOT repeat information across sections
- Do NOT include a heading for the note title (it's handled externally)
- Use markdown formatting: **bold** for emphasis, `code` for technical terms
- Aim for ~400-800 words. Cover all key points without padding or redundancy.

Transcript:
{text}"""

DEFAULT_PDF_PROMPT = """/no_think
You are summarizing a document into a detailed reference note.

Rules:
- Start with a 1-2 sentence TL;DR
- Then use "## Key Points" with bullet points for the main arguments, findings, or provisions
- Use "## Details" for supporting facts, data, definitions, exceptions, and specifics
  - Be thorough — capture all significant details, not just the top-level points
  - For policy/legal documents, note specific requirements, thresholds, dates, and scope
  - For technical documents, note methodologies, parameters, and caveats
- Use "## Context" for background information, related documents, or historical context mentioned
- Use "## Resources" if any references, citations, or links are mentioned
- Do NOT repeat information across sections
- Do NOT include a heading for the note title (it's handled externally)
- Use markdown formatting: **bold** for emphasis, `code` for technical terms
- Be comprehensive. A 10-page document should be ~600-1200 words. Do not omit key details.

Document text:
{text}"""

DEFAULT_ARTICLE_PROMPT = """/no_think
You are summarizing a web article into a detailed reference note.

Rules:
- Start with a 1-2 sentence TL;DR
- Then use "## Key Points" with bullet points for the main arguments or findings
- Use "## Details" for supporting facts, quotes, data, and specifics
  - Be thorough — capture all significant details, not just the headline
  - Include notable quotes, statistics, or examples from the article
- Use "## Context" for background info, related events, or historical context
- Use "## Resources" if any links, tools, people, or references are mentioned
- Do NOT repeat information across sections
- Do NOT include a heading for the note title (it's handled externally)
- Use markdown formatting: **bold** for emphasis, `code` for technical terms
- Aim for ~400-800 words. Cover all key points without padding or redundancy.

Article text:
{text}"""

DEFAULT_PODCAST_PROMPT = """/no_think
You are summarizing a podcast episode transcript into a detailed reference note.

Rules:
- Start with a 1-2 sentence TL;DR of the episode
- Then use "## Key Ideas" with bullet points for the main topics discussed
- Use "## Details" for supporting points, stories, examples, and specifics
  - Be thorough — capture all significant discussion points
  - Note any disagreements, nuances, or caveats mentioned
- Use "## Quotes" for notable or memorable quotes from speakers (with attribution if possible)
- Use "## Resources" if any tools, books, links, or people are mentioned
- Do NOT repeat information across sections
- Do NOT include a heading for the note title (it's handled externally)
- Use markdown formatting: **bold** for emphasis, `code` for technical terms
- Aim for ~400-800 words. Cover all key points without padding or redundancy.

Transcript:
{text}"""

_PROMPTS = {
    "video": DEFAULT_PROMPT,
    "document": DEFAULT_PDF_PROMPT,
    "article": DEFAULT_ARTICLE_PROMPT,
    "podcast": DEFAULT_PODCAST_PROMPT,
}


def get_default_prompt(content_type: str = "video") -> str:
    """Return the default summarization prompt for a content type."""
    return _PROMPTS.get(content_type, DEFAULT_PROMPT)


_BANNED_TAGS = {
    "technology", "information", "analysis", "research", "discussion",
    "overview", "important", "update", "news", "general", "misc",
    "article", "video", "podcast", "summary", "document", "content",
    "introduction", "conclusion", "review", "report",
}

MAX_TAGS = 5
MAX_TAG_LEN = 50


def _tag_instruction(taxonomy: str) -> str:
    """Build the tag instruction to append to summarization prompts."""
    if taxonomy:
        taxonomy_clause = (
            f"Pick tags from this list: {taxonomy}. "
            "You may add 1 tag not on this list if the content clearly warrants it."
        )
    else:
        taxonomy_clause = "Choose tags that describe the main topics covered."
    return (
        f"\n\nAfter your summary, on the very last line, output exactly one line "
        f"starting with \"Tags:\" followed by 2-5 comma-separated lowercase topic tags.\n"
        f"{taxonomy_clause}\n"
        f"Do not include generic tags like \"article\", \"video\", \"summary\", or \"technology\". "
        f"Only topical/subject tags.\n"
        f"Example: Tags: kubernetes, container-security, supply-chain-attacks"
    )


def _normalize_tag(tag: str) -> str:
    """Normalize a tag: lowercase, strip, replace spaces/slashes with hyphens."""
    tag = tag.lower().strip().strip("#")
    tag = re.sub(r"[^a-z0-9-]", "-", tag)
    tag = re.sub(r"-+", "-", tag).strip("-")
    return tag[:MAX_TAG_LEN]


def _parse_tags_from_summary(text: str) -> tuple[str, list[str]]:
    """Extract Tags: line from LLM output. Returns (clean_summary, tags)."""
    lines = text.rstrip().split("\n")
    tags = []
    clean_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("tags:"):
            raw = stripped[5:]
            for t in raw.split(","):
                normalized = _normalize_tag(t)
                if normalized and normalized not in _BANNED_TAGS and len(normalized) > 1:
                    tags.append(normalized)
        else:
            clean_lines.append(line)

    # Deduplicate preserving order, cap at MAX_TAGS
    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    tags = unique[:MAX_TAGS]

    summary = "\n".join(clean_lines).rstrip()
    if tags:
        vlog(f"auto-tags: {', '.join(tags)}")
    return summary, tags


@dataclass
class SummaryInfo:
    summary: str
    source: str
    model: str
    input_chars: int
    path: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if v is not None}
        if not d.get("tags"):
            d.pop("tags", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _summarize_ollama(text: str, model: str, prompt: str, **_) -> str:
    try:
        import ollama
    except ImportError:
        raise RuntimeError("ollama not installed. Install: pip install ollama")
    model = model or "llama3.2"
    log(f"summarizing with ollama ({model})...")
    resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt.format(text=text)}])
    return resp["message"]["content"]


def _summarize_anthropic(text: str, model: str, prompt: str, **_) -> str:
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic not installed. Install: pip install anthropic")
    model = model or "claude-sonnet-4-20250514"
    log(f"summarizing with anthropic ({model})...")
    client = anthropic.Anthropic()
    resp = client.messages.create(model=model, max_tokens=4096, messages=[{"role": "user", "content": prompt.format(text=text)}])
    return resp.content[0].text


def _summarize_openai(text: str, model: str, prompt: str, **_) -> str:
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai not installed. Install: pip install openai")
    model = model or "gpt-4o-mini"
    log(f"summarizing with openai ({model})...")
    client = openai.OpenAI()
    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt.format(text=text)}])
    return resp.choices[0].message.content


def _summarize_openai_compatible(text: str, model: str, prompt: str, api_base: str = "", api_key: str = "", **_) -> str:
    try:
        import openai
    except ImportError:
        raise RuntimeError("openai not installed. Install: pip install openai")
    base_url = api_base or "http://localhost:8080/v1"
    model = model or "default"
    log(f"summarizing with openai-compatible ({base_url}, {model})...")
    client = openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed")
    resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt.format(text=text)}])
    return resp.choices[0].message.content


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

BACKENDS = {
    "ollama": _summarize_ollama,
    "anthropic": _summarize_anthropic,
    "openai": _summarize_openai,
    "openai-compatible": _summarize_openai_compatible,
}


def summarize(
    text: str,
    backend: str = "ollama",
    model: str = "",
    prompt: str = "",
    output_path: str | Path | None = None,
    api_base: str = "",
    api_key: str = "",
    taxonomy: str = "",
    auto_tags: bool = True,
) -> SummaryInfo:
    """Summarize text using the specified LLM backend."""
    if not text.strip():
        raise ValueError("No text to summarize")
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}. Available: {', '.join(BACKENDS.keys())}")

    prompt = prompt or DEFAULT_PROMPT
    if auto_tags:
        prompt += _tag_instruction(taxonomy)

    fn = BACKENDS[backend]
    raw_summary = fn(text=text, model=model, prompt=prompt, api_base=api_base, api_key=api_key)

    tags = []
    if auto_tags:
        summary, tags = _parse_tags_from_summary(raw_summary)
    else:
        summary = raw_summary

    if output_path:
        output_path = Path(output_path)
        output_path.write_text(summary)
        log(f"summary saved: {output_path}")

    return SummaryInfo(
        summary=summary, source=backend, model=model or "(default)",
        input_chars=len(text), path=str(output_path) if output_path else None,
        tags=tags,
    )


def summarize_file(
    input_path: str | Path,
    backend: str = "ollama",
    model: str = "",
    prompt: str = "",
    api_base: str = "",
    api_key: str = "",
) -> SummaryInfo:
    """Read a transcript file and summarize it."""
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    content = input_path.read_text()
    if input_path.suffix == ".srt":
        text = parse_srt(content)
    elif input_path.suffix == ".json":
        try:
            text = json.loads(content).get("text", content)
        except json.JSONDecodeError:
            text = content
    else:
        text = content

    return summarize(
        text=text, backend=backend, model=model, prompt=prompt,
        output_path=input_path.with_suffix(".summary.md"),
        api_base=api_base, api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    from grab.config import load as load_config
    config = load_config()
    parser = argparse.ArgumentParser(prog="grab-summarize", description="Summarize a transcript file")
    parser.add_argument("input", help="Transcript file (.txt, .srt, .json)")
    parser.add_argument("--backend", default=config.get("summarize_backend", "ollama"), choices=list(BACKENDS.keys()), help="LLM backend")
    parser.add_argument("--model", default=config.get("summarize_model", ""), help="Model name")
    parser.add_argument("--prompt", default=config.get("summarize_prompt", ""), help="Custom prompt (use {text} placeholder)")
    parser.add_argument("--api-base", default=config.get("summarize_api_base", ""), help="API base URL (for openai-compatible)")
    parser.add_argument("--api-key", default=config.get("summarize_api_key", ""), help="API key (for openai-compatible)")
    args = parser.parse_args()

    result = summarize_file(
        input_path=args.input, backend=args.backend, model=args.model,
        prompt=args.prompt, api_base=args.api_base, api_key=args.api_key,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
