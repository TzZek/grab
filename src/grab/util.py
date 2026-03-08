"""Shared utility functions for grab.

Consolidates helpers that were duplicated across modules:
sanitize_filename, format_duration, human_size, parse_srt, temp_dir.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def sanitize_filename(name: str, max_len: int = 80, fallback: str = "untitled") -> str:
    """Remove filesystem-unsafe characters and truncate."""
    return re.sub(r'[\\/*?:"<>|]', "", name)[:max_len].strip() or fallback


def format_duration(seconds: int | float | str | None) -> str:
    """Format a duration as human-readable 'Xh XXm' or 'Xm XXs'.

    Accepts int/float seconds, a string (returned as-is if it contains ':',
    otherwise parsed as int), or None (returns '').
    """
    if isinstance(seconds, str):
        if ":" in seconds:
            return seconds
        try:
            seconds = int(seconds)
        except ValueError:
            return seconds
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def human_size(n: int | float) -> str:
    """Format a byte count as a human-readable string (KB/MB/GB)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def parse_srt(srt_text: str) -> str:
    """Strip SRT timestamps and sequence numbers, return plain text."""
    lines = []
    for line in srt_text.splitlines():
        line = line.strip()
        if not line or line.isdigit() or re.match(r"\d{2}:\d{2}:\d{2}", line):
            continue
        lines.append(line)
    return " ".join(lines)


@contextmanager
def temp_dir(prefix: str = "grab_") -> Iterator[Path]:
    """Create a temporary directory that is cleaned up on exit."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
