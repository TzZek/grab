# grab

Download and compress media for target platforms. Modular Python CLI — each module is under 300 lines and independently runnable.

## Install

```bash
git clone https://github.com/TzZek/grab.git
cd grab
uv venv && source .venv/bin/activate
uv pip install -e .
```

## Usage

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

# Pipe URLs
echo "https://youtube.com/watch?v=xyz" | grab --batch -
```

## Config

```bash
grab config show                                    # show all settings
grab config set output_dir ~/Pictures/content       # set default save directory
grab config set cobalt_api https://your-instance     # set cobalt API
grab config set default_preset discord               # always compress for discord
grab config set filename_template "{source}_{title}" # customize naming
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
python -m grab.probe video.mp4          # file metadata → JSON
python -m grab.download <url>           # download only → JSON
python -m grab.compress file.mp4 --preset discord  # compress video
python -m grab.image photo.jpg --max-bytes 1048576 # optimize image
python -m grab.gif video.mp4 --fps 15 --width 480  # video → GIF
python -m grab.naming <url>             # generate clean filename
python -m grab.presets                  # list all presets
python -m grab.config show              # show config
```

## Dependencies

- **Python 3.11+**
- **ffmpeg/ffprobe** — video compression and probing
- **yt-dlp** — media downloading
- **ImageMagick** — image processing
- **httpx** — HTTP client (for cobalt API)

Optionally uses [cobalt](https://github.com/imputnet/cobalt) API for downloading. This project is not affiliated with cobalt.

## License

MIT
