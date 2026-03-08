# grab

Download, compress, transcribe, and summarize media. Modular Python CLI — each module is under 300 lines and independently runnable.

## Install

```bash
git clone https://github.com/TzZek/grab.git
cd grab
uv venv && source .venv/bin/activate
uv pip install -e .
```

### Optional dependencies

```bash
# Speech-to-text (local transcription)
uv pip install -e ".[transcribe]"    # installs faster-whisper

# LLM summarization (local via Ollama)
uv pip install -e ".[summarize]"     # installs ollama python client

# Everything
uv pip install -e ".[all]"
```

## Model setup

### Summarization model (Ollama)

grab uses [Ollama](https://ollama.com) for local LLM summarization. Install Ollama, then pull a model:

```bash
# Pick a model for your hardware:
ollama pull llama3.2          # ~2GB  — lightweight, works anywhere (default fallback)
ollama pull qwen3.5:9b       # ~7GB  — good balance for most machines
ollama pull qwen3.5:35b      # ~24GB — MoE, only ~3B active, beats models 7x its size
ollama pull qwen3.5:122b     # ~81GB — MoE, ~10B active, best quality (needs ~75GB RAM)
```

Set your default model:

```bash
grab config set summarize_model qwen3.5:9b    # or whichever you pulled
```

**Why Qwen3.5?** The MoE variants (35B, 122B) activate only a fraction of their parameters per token, giving high quality at lower compute cost. The default prompt uses `/no_think` to disable chain-of-thought reasoning, which is unnecessary for summarization and would only slow things down. If no model is configured, grab falls back to `llama3.2`.

**Other LLM backends** — if you don't want to run models locally:

```bash
# Claude API
uv pip install anthropic
grab config set summarize_backend anthropic
# Set ANTHROPIC_API_KEY in your environment

# OpenAI API
uv pip install openai
grab config set summarize_backend openai
# Set OPENAI_API_KEY in your environment

# Any OpenAI-compatible server (llama.cpp, vLLM, etc.)
uv pip install openai
grab config set summarize_backend openai-compatible
grab config set summarize_api_base http://localhost:8080/v1
```

### Transcription model

Transcription tries free subtitle extraction via yt-dlp first. If no subtitles exist, it falls back to a local whisper model.

```bash
# Install faster-whisper (default backend)
uv pip install faster-whisper

# Set whisper model size (trades speed for accuracy)
grab config set transcribe_model base      # fast, good enough for clear audio
grab config set transcribe_model small     # better accuracy
grab config set transcribe_model medium    # good balance
grab config set transcribe_model large-v3  # best accuracy, slower
```

**Transcription backends:**

| Backend | Install | Notes |
|---|---|---|
| `ytdlp-subs` | (included) | Extracts existing YouTube subtitles — free, fast, tried first |
| `faster-whisper` | `pip install faster-whisper` | Default model backend, fast on CPU |
| `whisper` | `pip install openai-whisper` | Original OpenAI whisper |
| `whisper.cpp` | binary install | Via subprocess, set `WHISPER_CPP_BIN` env var |
| `mlx-whisper` | `pip install mlx-whisper` | Apple Silicon optimized |

To change the backend:

```bash
grab config set transcribe_backend faster-whisper   # default
grab config set transcribe_backend whisper          # original openai-whisper
```

## Usage

### Download & compress

```bash
# Download a video
grab "https://youtube.com/watch?v=xyz"

# Download and compress for Discord (10MB)
grab "https://instagram.com/reel/abc123" -p discord

# Custom size target in MB
grab "https://tiktok.com/@user/video/123" -p 8

# Audio only
grab "https://youtube.com/watch?v=xyz" -a

# Convert to GIF
grab "https://twitter.com/user/status/123" --gif

# GIF from local file
grab gif video.mp4 --fps 10 --width 320

# Batch download
grab --batch urls.txt -p discord
```

### Transcribe

Extract speech-to-text from videos. Tries free subtitle extraction first, falls back to local whisper models.

```bash
# Transcribe a local file
grab transcribe video.mp4

# Choose backend and model
grab transcribe video.mp4 --backend faster-whisper --model large-v3

# Transcribe with a specific language
grab transcribe lecture.mp4 --language ja

# Skip subtitle extraction, go straight to whisper
grab transcribe video.mp4 --no-subs
```

Outputs: `.srt` subtitle file + `.transcript.json` with full text and segments.

### Summarize

Summarize a transcript using a local or remote LLM.

```bash
# Summarize an SRT file
grab summarize video.srt

# Summarize a transcript JSON
grab summarize video.transcript.json

# Use a different backend
grab summarize video.srt --backend anthropic

# Use a specific model
grab summarize video.srt --backend ollama --model qwen3.5:9b

# Override model for a single run
grab "https://youtube.com/watch?v=xyz" --summarize --summarize-model qwen3:32b
```

Outputs a `.summary.md` with structured notes: TL;DR, Key Ideas, Details, Resources.

### Full pipeline

Download + transcribe + summarize in one command:

```bash
# Download and transcribe
grab "https://youtube.com/watch?v=xyz" --transcribe

# Download, transcribe, and summarize (--summarize implies --transcribe)
grab "https://youtube.com/watch?v=xyz" --summarize

# Full pipeline with Obsidian integration (--vault implies --summarize --transcribe)
grab "https://youtube.com/watch?v=xyz" --vault
```

### Obsidian integration

Save summaries directly to your Obsidian vault as searchable notes with YAML frontmatter.

```bash
# One-time setup: point grab at your vault
grab config set obsidian_vault ~/obsidian-git-sync
grab config set obsidian_folder reference/videos   # default

# Now --vault does everything: download → transcribe → summarize → vault note
grab "https://youtube.com/watch?v=xyz" --vault
```

Each `--vault` run creates two notes in your vault:

1. **Summary note** — structured summary with YAML frontmatter, info callout, and a `[[backlink]]` to the transcript
2. **Transcript note** — full transcript text, tagged `transcript`, backlinked from the summary

Notes include:
- **YAML frontmatter**: tags (auto-generated from category + channel), title, author, source URL, date, duration
- **Info callout**: channel, URL, duration at a glance
- **Structured summary**: TL;DR → Key Ideas → Details → Resources
- **Transcript backlink**: `[[Video Title — Transcript]]` so you can always reference the source
- **Clickable terminal link**: click the note title in your terminal to open it in Obsidian

Example summary note:

```markdown
---
tags:
  - video-note
  - education
  - 3blue1brown
type: video-note
title: "But what is a neural network? | Deep learning chapter 1"
author: "3Blue1Brown"
source: "https://www.youtube.com/watch?v=aircAruvnKk"
date: 2017-10-05
duration: "18m40s"
---

# But what is a neural network? | Deep learning chapter 1

> [!info] Source
> **Channel:** 3Blue1Brown
> **URL:** https://www.youtube.com/watch?v=aircAruvnKk
> **Duration:** 18m40s

**Full transcript:** [[But what is a neural network  Deep learning chapter 1 — Transcript]]

**TL;DR** Neural networks recognize patterns by passing data through
layers of neurons with adjustable weights and biases...

## Key Ideas
- ...

## Details
- ...

## Resources
- ...
```

The companion transcript note (`Video Title — Transcript.md`) contains the full text with its own frontmatter:

```markdown
---
tags:
  - transcript
type: transcript
title: "But what is a neural network? | Deep learning chapter 1"
author: "3Blue1Brown"
source: "https://www.youtube.com/watch?v=aircAruvnKk"
---

# Transcript — But what is a neural network? | Deep learning chapter 1

This is a 3. It's sloppily written and rendered at an extremely low
resolution of 28x28 pixels, but your brain has no trouble recognizing
it as a 3...
```

## Config

All settings with their defaults:

```bash
grab config show                          # show all current settings
```

### Download settings

```bash
grab config set output_dir ~/Pictures/content       # default save directory
grab config set cobalt_api https://your-instance     # cobalt API URL
grab config set default_preset discord               # always compress for a preset
grab config set default_quality 1080                 # video quality
grab config set filename_template "{source}_{title}" # naming pattern
grab config set cookies_from_browser ""              # browser for cookies
```

### Transcription settings

```bash
grab config set transcribe_backend faster-whisper    # STT backend
grab config set transcribe_model base                # whisper model (base/small/medium/large-v3)
grab config set transcribe_language ""               # auto-detect, or set "en", "ja", etc.
```

### Summarization settings

```bash
grab config set summarize_backend ollama             # LLM backend
grab config set summarize_model qwen3.5:9b           # Ollama model name
grab config set summarize_prompt ""                  # custom prompt (use {text} placeholder)
grab config set summarize_api_base http://localhost:8080/v1  # for openai-compatible
grab config set summarize_api_key ""                 # for openai-compatible
```

### Obsidian settings

```bash
grab config set obsidian_vault ~/obsidian-git-sync   # vault path
grab config set obsidian_folder reference/videos     # subfolder for notes
```

Settings file: `~/.config/grab/config.toml`

## Presets

| Preset | Size |
|---|---|
| `discord` | 10 MB |
| `discord-nitro` | 50 MB |
| `telegram` | 50 MB |
| `email` | 25 MB |
| `twitter` | 512 MB |
| Any number | That many MB |

## Modules

Each module works standalone:

```bash
python -m grab.download <url>                    # download → JSON
python -m grab.probe video.mp4                   # file metadata → JSON
python -m grab.compress file.mp4 --preset discord  # compress video
python -m grab.image photo.jpg --max-bytes 1048576 # optimize image
python -m grab.gif video.mp4 --fps 15 --width 480  # video → GIF
python -m grab.transcribe video.mp4              # speech → text
python -m grab.summarize transcript.srt          # text → summary
python -m grab.obsidian summary.md --vault ~/vault  # summary → Obsidian note
python -m grab.naming <url>                      # generate clean filename
python -m grab.presets                           # list all presets
python -m grab.cobalt status                     # cobalt container status
python -m grab.config show                       # show config
```

## Cobalt integration

grab automatically spins up a [cobalt](https://github.com/imputnet/cobalt) Docker container when downloading from platforms where yt-dlp needs authentication (Instagram, Twitter/X, TikTok, etc.). The container stops automatically when grab exits.

```bash
# First time: pull the cobalt image
grab cobalt pull

# That's it — grab handles the rest automatically.
# When you run:
grab "https://www.instagram.com/reel/abc123"
# grab will: start cobalt → download → stop cobalt
```

### Platform cookies

Instagram, Twitter, and Reddit require cookies even through cobalt. Create `~/.config/grab/cookies.json`:

```json
{
    "instagram": ["mid=...; ig_did=...; csrftoken=...; ds_user_id=...; sessionid=..."],
    "twitter": ["auth_token=...; ct0=..."],
    "reddit": ["client_id=...; client_secret=...; refresh_token=..."]
}
```

The cookies file is automatically mounted into the cobalt container when it starts.

### Manual control

```bash
grab cobalt status    # check if container is running
grab cobalt start     # start manually (stays running)
grab cobalt stop      # stop and remove container
grab cobalt pull      # pull/update cobalt image
```

If you prefer a persistent cobalt instance, set the URL directly:

```bash
grab config set cobalt_api http://localhost:9000
```

When `cobalt_api` is set, grab uses that URL directly and skips auto container management.

## Dependencies

- **Python 3.11+**
- **ffmpeg/ffprobe** — video compression and probing
- **yt-dlp** — media downloading
- **ImageMagick** — image processing
- **httpx** — HTTP client (for cobalt API)
- **Docker** — (optional) for auto cobalt container management

This project is not affiliated with cobalt.

## License

MIT
