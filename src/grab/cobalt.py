"""On-demand cobalt container management for media downloading.

Automatically spins up a cobalt Docker container when downloading from
platforms that need it (Instagram, Twitter/X, TikTok, etc.), and stops
it when the process exits.

Run directly: python -m grab.cobalt status|start|stop
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from grab import log

CONTAINER_NAME = "grab-cobalt"
IMAGE = "ghcr.io/imputnet/cobalt:latest"
PORT = 9000
API_URL = f"http://localhost:{PORT}"
COOKIES_PATH = Path("~/.config/grab/cookies.json").expanduser()

# Platforms where yt-dlp needs auth but cobalt handles natively
COBALT_DOMAINS = {
    "instagram.com",
    "twitter.com", "x.com",
    "tiktok.com",
    "facebook.com",
    "pinterest.com",
    "snapchat.com",
}

_auto_started = False


def needs_cobalt(url: str) -> bool:
    """Check if URL is from a platform that benefits from cobalt."""
    domain = urlparse(url).netloc.lower()
    # Strip www. prefix
    if domain.startswith("www."):
        domain = domain[4:]
    # Match domain or subdomain
    return any(domain == d or domain.endswith("." + d) for d in COBALT_DOMAINS)


def _has_docker() -> bool:
    return shutil.which("docker") is not None


def _run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check, **kwargs)


def is_running() -> bool:
    """Check if the cobalt container is running."""
    if not _has_docker():
        return False
    r = _run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME], check=False)
    return r.returncode == 0 and "true" in r.stdout.strip()


def _container_exists() -> bool:
    r = _run(["docker", "inspect", CONTAINER_NAME], check=False)
    return r.returncode == 0


def start() -> str:
    """Start the cobalt container. Returns API URL."""
    if is_running():
        log("cobalt container already running")
        return API_URL

    if not _has_docker():
        raise RuntimeError("Docker is not installed or not in PATH")

    # Remove stopped container if it exists
    if _container_exists():
        _run(["docker", "rm", "-f", CONTAINER_NAME], check=False)

    log("starting cobalt container...")
    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{PORT}:9000",
        "-e", f"API_URL={API_URL}",
    ]
    if COOKIES_PATH.exists():
        cmd += ["-v", f"{COOKIES_PATH}:/app/cookies.json:ro"]
        log(f"mounting cookies from {COOKIES_PATH}")
    cmd.append(IMAGE)
    _run(cmd)

    # Wait for it to be ready
    for i in range(30):
        try:
            import httpx
            r = httpx.get(API_URL, timeout=2)
            if r.status_code < 500:
                log("cobalt container ready")
                return API_URL
        except Exception:
            pass
        time.sleep(1)

    raise RuntimeError("cobalt container failed to start within 30 seconds")


def stop() -> None:
    """Stop and remove the cobalt container."""
    if not _has_docker():
        return
    if not _container_exists():
        return
    log("stopping cobalt container...")
    _run(["docker", "stop", CONTAINER_NAME], check=False)
    _run(["docker", "rm", CONTAINER_NAME], check=False)


def ensure_running() -> str:
    """Start cobalt if not running. Registers auto-shutdown on process exit.

    Returns the API URL.
    """
    global _auto_started
    api_url = start()
    if not _auto_started:
        _auto_started = True
        atexit.register(_auto_stop)
    return api_url


def _auto_stop() -> None:
    """atexit handler — only stops if we started it."""
    if _auto_started:
        stop()


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    usage = "Usage: python -m grab.cobalt {status|start|stop|pull}"
    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        if is_running():
            print(f"running at {API_URL}")
        elif _container_exists():
            print("stopped")
        elif _has_docker():
            print("not created")
        else:
            print("docker not available")
    elif cmd == "start":
        url = start()
        print(f"cobalt running at {url}")
    elif cmd == "stop":
        stop()
        print("cobalt stopped")
    elif cmd == "pull":
        log("pulling cobalt image...")
        _run(["docker", "pull", IMAGE], check=True)
        print(f"pulled {IMAGE}")
    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
