"""On-demand cobalt container management for media downloading.

Automatically spins up a cobalt Docker container when downloading from
platforms that need it (Instagram, Twitter/X, TikTok, etc.), and stops
it when the process exits.

Cookies are extracted automatically from the user's browser when
cookies_from_browser is configured.

Run directly: python -m grab.cobalt status|start|stop|cookies
"""

from __future__ import annotations

import atexit
import json
import shutil
import sqlite3
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

# Map cobalt cookie keys to domains we extract from browser
_COOKIE_DOMAINS = {
    "twitter": [".x.com", ".twitter.com"],
    "instagram": [".instagram.com"],
    "reddit": [".reddit.com"],
    "youtube": [".youtube.com"],
}

_auto_started = False


def needs_cobalt(url: str) -> bool:
    """Check if URL is from a platform that benefits from cobalt."""
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return any(domain == d or domain.endswith("." + d) for d in COBALT_DOMAINS)


# ---------------------------------------------------------------------------
# Browser cookie extraction
# ---------------------------------------------------------------------------

# Browser name → (cookie DB paths, is_firefox_format)
_BROWSER_PATHS: dict[str, tuple[list[str], bool]] = {
    "brave": ([
        "~/.config/BraveSoftware/Brave-Browser/Default/Cookies",
        "~/.config/BraveSoftware/Brave-Browser/Profile 1/Cookies",
    ], False),
    "chrome": ([
        "~/.config/google-chrome/Default/Cookies",
        "~/.config/google-chrome/Profile 1/Cookies",
    ], False),
    "chromium": ([
        "~/.config/chromium/Default/Cookies",
    ], False),
    "vivaldi": ([
        "~/.config/vivaldi/Default/Cookies",
    ], False),
    "firefox": ([
        "~/.mozilla/firefox/*.default-release/cookies.sqlite",
        "~/.mozilla/firefox/*.default/cookies.sqlite",
    ], True),
    "zen": ([
        "~/.zen/*.default-release/cookies.sqlite",
        "~/.zen/*.default/cookies.sqlite",
        "~/.zen/*/cookies.sqlite",
    ], True),
}


def _find_cookie_db(browser: str) -> tuple[Path, bool] | None:
    """Find the cookie database for a browser. Returns (path, is_firefox)."""
    import glob
    info = _BROWSER_PATHS.get(browser)
    if not info:
        return None
    paths, is_firefox = info
    for pattern in paths:
        expanded = Path(pattern).expanduser()
        matches = glob.glob(str(expanded))
        for m in matches:
            p = Path(m)
            if p.exists() and p.stat().st_size > 0:
                return p, is_firefox
    return None


def _copy_db(db_path: Path) -> Path:
    """Copy a DB to a temp file to avoid locks and path issues."""
    import tempfile, shutil as sh
    tmp = Path(tempfile.mktemp(suffix=".sqlite"))
    sh.copy2(db_path, tmp)
    return tmp


def _extract_cookies_firefox(db_path: Path, domains: list[str]) -> list[tuple[str, str]]:
    """Extract cookies from Firefox/Zen SQLite DB (unencrypted)."""
    placeholders = ",".join("?" for _ in domains)
    query = f"SELECT name, value FROM moz_cookies WHERE baseDomain IN ({placeholders}) OR host IN ({placeholders})"
    base_domains = [d.lstrip(".") for d in domains]
    params = base_domains + domains
    tmp = None
    try:
        tmp = _copy_db(db_path)
        conn = sqlite3.connect(str(tmp))
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return rows
    except Exception as e:
        log(f"cookie extraction failed for {db_path}: {e}")
        return []
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def _extract_cookies_chromium(db_path: Path, domains: list[str], browser: str = "chrome") -> list[tuple[str, str]]:
    """Extract cookies from Chromium-based browser.

    Decrypts v10/v11 cookies using PBKDF2 key from the system keyring
    (or 'peanuts' fallback). Handles DB version 24+ SHA256 domain prefix.
    """
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
    except ImportError:
        log("cryptography package required for Chromium cookie decryption: uv pip install cryptography")
        return []

    # Get encryption key from keyring, fall back to 'peanuts'
    password = _get_chromium_key(browser) or b"peanuts"
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1).derive(password)
    db_version = _get_cookie_db_version(db_path)

    placeholders = ",".join("?" for _ in domains)
    query = f"SELECT name, value, encrypted_value FROM cookies WHERE host_key IN ({placeholders})"
    tmp = None
    try:
        tmp = _copy_db(db_path)
        conn = sqlite3.connect(str(tmp))
        rows = conn.execute(query, domains).fetchall()
        conn.close()

        result = []
        for name, value, enc_value in rows:
            if value:
                result.append((name, value))
            elif enc_value:
                decrypted = _decrypt_chromium_cookie(enc_value, key, db_version)
                if decrypted:
                    result.append((name, decrypted))
        return result
    except Exception as e:
        log(f"cookie extraction failed for {db_path}: {e}")
        return []
    finally:
        if tmp:
            tmp.unlink(missing_ok=True)


def _get_chromium_key(browser: str) -> bytes | None:
    """Get the encryption key for a Chromium-based browser from the system keyring."""
    try:
        import secretstorage
        bus = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(bus)
        label = f"{browser.title()} Safe Storage"
        for item in collection.get_all_items():
            if item.get_label() == label:
                return item.get_secret()
    except Exception:
        pass
    return None


def _get_cookie_db_version(db_path: Path) -> int:
    """Get the cookie database schema version."""
    try:
        tmp = _copy_db(db_path)
        conn = sqlite3.connect(str(tmp))
        row = conn.execute('SELECT value FROM meta WHERE key="version"').fetchone()
        conn.close()
        tmp.unlink(missing_ok=True)
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _decrypt_chromium_cookie(encrypted: bytes, key: bytes, db_version: int = 0) -> str | None:
    """Decrypt a Chromium cookie value on Linux using the derived AES key."""
    if not encrypted.startswith(b"v1"):
        return None
    version = encrypted[:3]
    if version not in (b"v10", b"v11"):
        return None

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        iv = b" " * 16
        payload = encrypted[3:]
        if len(payload) == 0 or len(payload) % 16 != 0:
            return None
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        dec = cipher.decryptor()
        plaintext = dec.update(payload) + dec.finalize()

        # DB version 24+: first 32 bytes are SHA256 hash of domain
        if db_version >= 24:
            plaintext = plaintext[32:]

        # Remove PKCS7 padding
        pad_len = plaintext[-1]
        if not (isinstance(pad_len, int) and 1 <= pad_len <= 16):
            return None
        if not all(b == pad_len for b in plaintext[-pad_len:]):
            return None
        plaintext = plaintext[:-pad_len]
        return plaintext.decode("utf-8")
    except Exception:
        return None


def extract_cookies(browser: str) -> dict[str, list[str]]:
    """Extract platform cookies from browser into cobalt cookies.json format."""
    result = _find_cookie_db(browser)
    if not result:
        log(f"could not find cookie database for {browser}")
        return {}

    db_path, is_firefox = result
    log(f"extracting cookies from {browser} ({db_path.name})...")
    cobalt_cookies: dict[str, list[str]] = {}
    for service, domains in _COOKIE_DOMAINS.items():
        rows = (_extract_cookies_firefox(db_path, domains) if is_firefox
                else _extract_cookies_chromium(db_path, domains, browser))
        if rows:
            cookie_str = "; ".join(f"{name}={value}" for name, value in rows)
            cobalt_cookies[service] = [cookie_str]

    return cobalt_cookies


def sync_cookies(browser: str) -> bool:
    """Extract browser cookies and write to cookies.json for cobalt."""
    cookies = extract_cookies(browser)
    if not cookies:
        log(f"no platform cookies found in {browser}")
        return False

    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies, indent=2))
    services = ", ".join(cookies.keys())
    log(f"synced cookies for: {services}")
    return True


# ---------------------------------------------------------------------------
# Container management
# ---------------------------------------------------------------------------

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


def start(cookies_from_browser: str = "") -> str:
    """Start the cobalt container. Returns API URL."""
    if is_running():
        log("cobalt container already running")
        return API_URL

    if not _has_docker():
        raise RuntimeError("Docker is not installed or not in PATH")

    # Auto-sync cookies from browser before starting
    if cookies_from_browser:
        sync_cookies(cookies_from_browser)

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
        cmd += ["-v", f"{COOKIES_PATH}:/cookies.json:ro", "-e", "COOKIE_PATH=/cookies.json"]
        log("mounting platform cookies")
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


def ensure_running(cookies_from_browser: str = "") -> str:
    """Start cobalt if not running. Registers auto-shutdown on process exit."""
    global _auto_started
    api_url = start(cookies_from_browser)
    if not _auto_started:
        _auto_started = True
        atexit.register(_auto_stop)
    return api_url


def _auto_stop() -> None:
    if _auto_started:
        stop()


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    usage = "Usage: grab cobalt {status|start|stop|pull|cookies}"
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
        browser = sys.argv[2] if len(sys.argv) > 2 else ""
        url = start(browser)
        print(f"cobalt running at {url}")
    elif cmd == "stop":
        stop()
        print("cobalt stopped")
    elif cmd == "pull":
        log("pulling cobalt image...")
        _run(["docker", "pull", IMAGE], check=True)
        print(f"pulled {IMAGE}")
    elif cmd == "cookies":
        from grab.config import load as load_config
        browser = sys.argv[2] if len(sys.argv) > 2 else load_config().get("cookies_from_browser", "")
        if not browser:
            print("Usage: grab cobalt cookies <browser>")
            print(f"Supported: {', '.join(_BROWSER_PATHS.keys())}")
            sys.exit(1)
        if sync_cookies(browser):
            print(f"cookies saved to {COOKIES_PATH}")
        else:
            print("no cookies found")
            sys.exit(1)
    else:
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
