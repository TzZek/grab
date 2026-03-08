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
from dataclasses import dataclass, asdict
from pathlib import Path

from grab import log

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

_PROMPTS = {
    "video": DEFAULT_PROMPT,
    "document": DEFAULT_PDF_PROMPT,
}


def get_default_prompt(content_type: str = "video") -> str:
    """Return the default summarization prompt for a content type."""
    return _PROMPTS.get(content_type, DEFAULT_PROMPT)


@dataclass
class SummaryInfo:
    summary: str
    source: str
    model: str
    input_chars: int
    output_path: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

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
# SRT parser
# ---------------------------------------------------------------------------

def _parse_srt(srt_text: str) -> str:
    """Strip timestamps from SRT, return plain text."""
    lines = []
    for line in srt_text.splitlines():
        line = line.strip()
        if not line or line.isdigit() or re.match(r"\d{2}:\d{2}:\d{2}", line):
            continue
        lines.append(line)
    return " ".join(lines)


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
) -> SummaryInfo:
    """Summarize text using the specified LLM backend."""
    if not text.strip():
        raise ValueError("No text to summarize")
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}. Available: {', '.join(BACKENDS.keys())}")

    prompt = prompt or DEFAULT_PROMPT
    fn = BACKENDS[backend]
    summary = fn(text=text, model=model, prompt=prompt, api_base=api_base, api_key=api_key)

    if output_path:
        output_path = Path(output_path)
        output_path.write_text(summary)
        log(f"summary saved: {output_path}")

    return SummaryInfo(
        summary=summary, source=backend, model=model or "(default)",
        input_chars=len(text), output_path=str(output_path) if output_path else None,
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
        text = _parse_srt(content)
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
