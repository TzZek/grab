"""Transcribe audio/video to text. Multi-backend via BACKENDS dict.

Backends:
  ytdlp-subs     — extract existing subtitles via yt-dlp (free, tried first)
  faster-whisper  — default local model (pip install faster-whisper)
  whisper         — original openai-whisper
  whisper.cpp     — via subprocess
  mlx-whisper     — Apple Silicon optimized

Run directly: python -m grab.transcribe video.mp4 --backend faster-whisper --model base
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from grab import log
from grab.util import parse_srt


@dataclass
class TranscriptInfo:
    text: str
    source: str
    language: str | None = None
    srt_path: str | None = None
    json_path: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _format_ts(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(segments: list, output_path: Path) -> None:
    """Write SRT from segment objects or dicts."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg["start"] if isinstance(seg, dict) else seg.start
        end = seg["end"] if isinstance(seg, dict) else seg.end
        text = (seg["text"] if isinstance(seg, dict) else seg.text).strip()
        lines += [str(i), f"{_format_ts(start)} --> {_format_ts(end)}", text, ""]
    output_path.write_text("\n".join(lines))


def _save_transcript(segments: list, lang: str | None, source: str,
                     input_path: Path, output_dir: Path) -> TranscriptInfo:
    """Common post-processing: write SRT + JSON, return TranscriptInfo."""
    srt_path = output_dir / (input_path.stem + ".srt")
    _write_srt(segments, srt_path)

    seg_dicts = [
        {"start": (s["start"] if isinstance(s, dict) else s.start),
         "end": (s["end"] if isinstance(s, dict) else s.end),
         "text": (s["text"] if isinstance(s, dict) else s.text).strip()}
        for s in segments
    ]
    text = " ".join(s["text"] for s in seg_dicts)

    json_path = output_dir / (input_path.stem + ".transcript.json")
    json_path.write_text(json.dumps({"text": text, "language": lang, "segments": seg_dicts}, indent=2))

    return TranscriptInfo(text=text, source=source, language=lang,
                          srt_path=str(srt_path), json_path=str(json_path))


# ---------------------------------------------------------------------------
# Backend: ytdlp-subs
# ---------------------------------------------------------------------------

def _get_url_from_sidecar(input_path: Path) -> str | None:
    for name in [input_path.with_suffix(".info.json"),
                 input_path.parent / f"{input_path.stem}.info.json"]:
        if name.exists():
            try:
                data = json.loads(name.read_text())
                return data.get("webpage_url") or data.get("original_url") or data.get("url")
            except (json.JSONDecodeError, OSError):
                pass
    return None


def _transcribe_ytdlp_subs(url_or_path: str, output_dir: Path, language: str) -> TranscriptInfo | None:
    url = url_or_path
    if os.path.exists(url_or_path):
        url = _get_url_from_sidecar(Path(url_or_path))
        if not url:
            return None

    lang = language or "en"
    # Only grab manual/professional subs, not auto-generated garbage
    cmd = ["yt-dlp", "--write-subs", "--sub-lang", lang,
           "--sub-format", "srt", "--skip-download", "-o", str(output_dir / "subs"), url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None

    for ext in (f".{lang}.srt", f".{lang}.vtt"):
        srt_path = output_dir / f"subs{ext}"
        if srt_path.exists():
            text = parse_srt(srt_path.read_text())
            final = srt_path.with_suffix(".srt")
            if srt_path != final:
                srt_path.rename(final)
            return TranscriptInfo(text=text, source="ytdlp-subs", language=lang, srt_path=str(final))
    return None


# ---------------------------------------------------------------------------
# Backend: faster-whisper
# ---------------------------------------------------------------------------

def _transcribe_faster_whisper(input_path: Path, output_dir: Path, model: str, language: str) -> TranscriptInfo:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("faster-whisper not installed. Install: pip install faster-whisper")
    log(f"transcribing with faster-whisper (model={model})...")
    wmodel = WhisperModel(model, compute_type="int8")
    segments_iter, info = wmodel.transcribe(str(input_path), **({"language": language} if language else {}))
    return _save_transcript(list(segments_iter), info.language, "faster-whisper", input_path, output_dir)


# ---------------------------------------------------------------------------
# Backend: whisper (openai-whisper)
# ---------------------------------------------------------------------------

def _transcribe_whisper(input_path: Path, output_dir: Path, model: str, language: str) -> TranscriptInfo:
    try:
        import whisper
    except ImportError:
        raise RuntimeError("openai-whisper not installed. Install: pip install openai-whisper")
    log(f"transcribing with whisper (model={model})...")
    wmodel = whisper.load_model(model)
    result = wmodel.transcribe(str(input_path), **({"language": language} if language else {}))
    return _save_transcript(result.get("segments", []), result.get("language"), "whisper", input_path, output_dir)


# ---------------------------------------------------------------------------
# Backend: whisper.cpp
# ---------------------------------------------------------------------------

def _transcribe_whisper_cpp(input_path: Path, output_dir: Path, model: str, language: str) -> TranscriptInfo:
    whisper_bin = os.environ.get("WHISPER_CPP_BIN", "whisper-cpp")
    model_path = os.environ.get("WHISPER_CPP_MODEL",
                                str(Path.home() / ".local/share/whisper.cpp" / f"ggml-{model}.bin"))
    srt_out = output_dir / (input_path.stem + ".srt")
    cmd = [whisper_bin, "-m", model_path, "-osrt", "-of", str(srt_out.with_suffix(""))]
    if language:
        cmd += ["-l", language]
    cmd += ["-f", str(input_path)]

    log(f"transcribing with whisper.cpp (model={model})...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper.cpp failed:\n{result.stderr}")
    if not srt_out.exists():
        srt_out = srt_out.with_suffix(".srt")
    text = parse_srt(srt_out.read_text()) if srt_out.exists() else ""
    return TranscriptInfo(text=text, source="whisper.cpp", language=language or None, srt_path=str(srt_out))


# ---------------------------------------------------------------------------
# Backend: mlx-whisper
# ---------------------------------------------------------------------------

def _transcribe_mlx_whisper(input_path: Path, output_dir: Path, model: str, language: str) -> TranscriptInfo:
    try:
        import mlx_whisper
    except ImportError:
        raise RuntimeError("mlx-whisper not installed. Install: pip install mlx-whisper")
    log(f"transcribing with mlx-whisper (model={model})...")
    result = mlx_whisper.transcribe(str(input_path), path_or_hf_repo=model,
                                    **({"language": language} if language else {}))
    return _save_transcript(result.get("segments", []), result.get("language"), "mlx-whisper", input_path, output_dir)


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

BACKENDS = {
    "ytdlp-subs": _transcribe_ytdlp_subs,
    "faster-whisper": _transcribe_faster_whisper,
    "whisper": _transcribe_whisper,
    "whisper.cpp": _transcribe_whisper_cpp,
    "mlx-whisper": _transcribe_mlx_whisper,
}


def transcribe(
    input_path: str | Path, backend: str = "faster-whisper", model: str = "base",
    language: str = "", output_dir: str | Path | None = None,
    url: str | None = None, try_subs_first: bool = True,
) -> TranscriptInfo:
    """Transcribe audio/video to text."""
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    if output_dir is None:
        output_dir = input_path.parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    if try_subs_first:
        result = _transcribe_ytdlp_subs(url or str(input_path), output_dir, language or "en")
        if result:
            log(f"transcript extracted from subtitles ({result.language})")
            return result

    if backend == "ytdlp-subs":
        raise RuntimeError("No subtitles available for this video")
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}. Available: {', '.join(BACKENDS.keys())}")

    result = BACKENDS[backend](input_path, output_dir, model, language)
    log(f"transcribed with {backend}: {len(result.text)} chars")
    return result


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    from grab.config import load as load_config
    config = load_config()
    parser = argparse.ArgumentParser(prog="grab-transcribe", description="Transcribe audio/video to text")
    parser.add_argument("input", help="Audio/video file to transcribe")
    parser.add_argument("--backend", default=config.get("transcribe_backend", "faster-whisper"), choices=list(BACKENDS.keys()))
    parser.add_argument("--model", default=config.get("transcribe_model", "base"), help="Model name")
    parser.add_argument("--language", default=config.get("transcribe_language", ""), help="Language code (e.g. en, ja)")
    parser.add_argument("--output-dir", help="Output directory (default: same as input)")
    parser.add_argument("--url", help="Original URL (for subtitle extraction)")
    parser.add_argument("--no-subs", action="store_true", help="Skip subtitle extraction attempt")
    args = parser.parse_args()

    result = transcribe(
        input_path=args.input, backend=args.backend, model=args.model,
        language=args.language, output_dir=args.output_dir, url=args.url,
        try_subs_first=not args.no_subs,
    )
    print(result.to_json())


if __name__ == "__main__":
    main()
