"""
chathaiku.py — Public command-line chat client.

Connects to a running public agent server and lets you talk to Haiku from
your terminal.

Usage:
  python chathaiku.py

Type messages and press Enter. Slash commands:
  /clear     Start a fresh conversation
  /save FILE Save the conversation to a text file
  /help      Show this help
  /quit      Exit

Press Ctrl-C any time to stop a reply or exit.
"""

import os
import sys
import json
import time
import argparse
import threading
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Optional


# ──────────────────────────────────────────────────────────
#  Banner & UI
# ──────────────────────────────────────────────────────────

BANNER = r"""
 ██████╗ ██╗  ██╗  █████╗  ████████╗██╗  ██╗  █████╗  ██████╗  ██╗  ██╗ ██╗  ██╗
██╔════╝ ██║  ██║ ██╔══██╗ ╚══██╔══╝██║  ██║ ██╔══██╗ ╚═██╔═╝  ██║ ██╔╝ ██║  ██║
██║      ███████║ ███████║    ██║   ███████║ ███████║   ██║    █████╔╝  ██║  ██║
██║      ██╔══██║ ██╔══██║    ██║   ██╔══██║ ██╔══██║   ██║    ██╔═██╗  ██║  ██║
╚██████╗ ██║  ██║ ██║  ██║    ██║   ██║  ██║ ██║  ██║ ██████╗  ██║  ██╗ ╚██████╔╝
 ╚═════╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝    ╚═╝   ╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚═════╝  ╚═╝  ╚═╝  ╚═════╝ 
                                                                     ( v 1 . 2 )
"""

# Optional color (ANSI). Disable with --no-color or NO_COLOR env var.
class Color:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"

    @classmethod
    def disable(cls):
        for name in dir(cls):
            if name.isupper():
                setattr(cls, name, "")


def print_banner(server_url: str):
    print(Color.CYAN + BANNER + Color.RESET)
    print(Color.DIM + f"  Connected to {server_url}" + Color.RESET)
    print(Color.DIM + "  Type a message and press Enter. /help for commands. /quit to exit." + Color.RESET)
    print()


# ──────────────────────────────────────────────────────────
#  HTTP client
# ──────────────────────────────────────────────────────────

DEFAULT_ENDPOINT = "https://chathaiku.com/api/haiku.php"
BACKEND_FALLBACK_ENDPOINT = "http://haiku.rootcomputer.dev/api/chat"
APP_NAME = "chathaiku"
APP_VERSION = "1.2.0"
UPDATE_MANIFEST_URL = "https://rootcomputer.dev/software/chathaikucli/update/chathaiku_cli_updates.json"


def make_request_headers(url: str, *, has_json_body: bool = False) -> dict:
    """Build headers that look like the public browser frontend."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "close",
    }
    if has_json_body:
        headers["Content-Type"] = "application/json"

    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
    except Exception:
        host = ""

    if host.endswith("chathaiku.com"):
        headers.update({
            "Origin": "https://chathaiku.com",
            "Referer": "https://chathaiku.com/",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })

    return headers


def _is_local_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    return (
        host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
        or host.startswith("192.168.")
        or host.startswith("10.")
        or host.startswith("172.16.")
        or host.startswith("172.17.")
        or host.startswith("172.18.")
        or host.startswith("172.19.")
        or host.startswith("172.2")
        or host.startswith("172.30.")
        or host.startswith("172.31.")
    )


def normalize_server_url(raw_url: str) -> str:
    """Normalize a server base URL or direct chat/proxy endpoint."""
    raw_url = (raw_url or "").strip().rstrip("/")
    if not raw_url:
        raise ValueError("empty endpoint")

    # Allow the same relative endpoint used by the public website.
    if raw_url.startswith("/api/"):
        raw_url = "https://chathaiku.com" + raw_url

    # urllib requires a scheme. Prefer https for public hosts and http for local/dev hosts.
    if "://" not in raw_url:
        host = raw_url.split("/", 1)[0].split("@")[-1].split(":", 1)[0].lower()
        raw_url = ("http://" if _is_local_host(host) else "https://") + raw_url

    parsed = urllib.parse.urlsplit(raw_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported URL scheme: {parsed.scheme or 'none'}")
    if not parsed.netloc:
        raise ValueError("endpoint is missing a host")

    path = parsed.path.rstrip("/")
    normalized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return normalized.rstrip("/")


def resolve_endpoint(server_url: str) -> dict:
    """Return normalized display, chat, and health URLs.

    Supported input shapes:
      - https://chathaiku.com/api/haiku.php
      - https://chathaiku.com/api/tanka.php
      - https://chathaiku.com/api/haiku.php/api/chat
      - https://chathaiku.com/api/haiku.php/api/health
      - http://localhost:PORT or any plain server base
    """
    display_url = normalize_server_url(server_url)
    parsed = urllib.parse.urlsplit(display_url)
    path = parsed.path.rstrip("/")

    def with_path(new_path: str) -> str:
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, new_path.rstrip("/"), "", ""))

    if path.endswith("/api/chat"):
        base_path = path[:-len("/api/chat")].rstrip("/")
        base_url = with_path(base_path).rstrip("/")
        return {
            "display_url": base_url,
            "chat_url": display_url,
            "health_url": base_url + "/api/health",
            "kind": "direct-chat",
        }

    if path.endswith("/api/health"):
        base_path = path[:-len("/api/health")].rstrip("/")
        base_url = with_path(base_path).rstrip("/")
        return {
            "display_url": base_url,
            "chat_url": base_url + "/api/chat",
            "health_url": display_url,
            "kind": "server-base",
        }

    # PHP router/proxy base used by the public website.
    # Do not hardcode the model name; derive health/chat from the chosen PHP file.
    if path.endswith(".php"):
        base_url = display_url.rstrip("/")
        return {
            "display_url": base_url,
            "chat_url": base_url + "/api/chat",
            "health_url": base_url + "/api/health",
            "kind": "php-router",
        }

    base_url = display_url.rstrip("/")
    return {
        "display_url": base_url,
        "chat_url": base_url + "/api/chat",
        "health_url": base_url + "/api/health",
        "kind": "server-base",
    }


def ping_server(server_url: str, timeout: float = 5.0) -> Optional[dict]:
    """Check endpoint health/reachability."""
    try:
        ep = resolve_endpoint(server_url)

        if ep["health_url"]:
            req = urllib.request.Request(
                ep["health_url"],
                headers=make_request_headers(ep["health_url"]),
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("endpoint", ep["display_url"])
                data.setdefault("endpoint_type", ep["kind"])
                return data
            return None

    except (ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ConnectionRefusedError, OSError):
        return None


def _version_key(value: str) -> tuple:
    """Convert loose semver-ish strings into comparable tuples."""
    text = str(value or "").strip().lower()
    if text.startswith("v"):
        text = text[1:]
    text = text.replace("-", ".").replace("_", ".")
    parts = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
        elif chunk:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def _select_update_record(manifest: dict, app_name: str) -> Optional[dict]:
    """Support both {apps:{name:{...}}} and flat manifest shapes."""
    if not isinstance(manifest, dict):
        return None

    record = None
    apps = manifest.get("apps")
    if isinstance(apps, dict):
        record = apps.get(app_name)
        if record is None and app_name == "chathaiku_dev":
            record = apps.get("chathaiku-dev")
        if record is None and app_name == "chathaiku":
            record = apps.get("chathaiku_cli")

    if record is None:
        record = manifest.get(app_name)

    if record is None and any(k in manifest for k in ("latest", "version", "latest_version")):
        record = manifest

    if isinstance(record, str):
        record = {"latest": record}
    return record if isinstance(record, dict) else None


def check_for_update(app_name: str, current_version: str, manifest_url: str,
                     timeout: float = 4.0) -> Optional[dict]:
    """Fetch the update manifest and return a normalized status dictionary."""
    if not manifest_url:
        return None

    req = urllib.request.Request(
        manifest_url,
        headers=make_request_headers(manifest_url),
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        manifest = json.loads(resp.read().decode("utf-8"))

    record = _select_update_record(manifest, app_name)
    if not record:
        return None

    latest = (
        record.get("latest")
        or record.get("version")
        or record.get("latest_version")
    )
    if latest is None:
        return None
    latest = str(latest).strip()

    return {
        "app": app_name,
        "current": current_version,
        "latest": latest,
        "up_to_date": _version_key(latest) <= _version_key(current_version),
        "download_url": record.get("download_url") or record.get("url"),
        "notes_url": record.get("notes_url") or record.get("changelog_url"),
        "message": record.get("message") or record.get("notes"),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def start_update_check_loop(state: dict, app_name: str, current_version: str,
                            manifest_url: str, interval: float) -> None:
    """Start a quiet background update loop.

    The worker never prints while input() is active. It only caches status;
    the main loop prints notices at safe prompt boundaries.
    """
    if not manifest_url or interval <= 0:
        return

    lock = state.setdefault("update_lock", threading.Lock())
    stop_event = threading.Event()
    state["update_stop_event"] = stop_event
    state["update_url"] = manifest_url
    state["update_interval"] = interval

    def worker():
        while not stop_event.is_set():
            try:
                result = check_for_update(app_name, current_version, manifest_url)
                with lock:
                    state["update_last_result"] = result
                    state["update_last_error"] = None
                    if result and not result.get("up_to_date"):
                        state["update_notice"] = result
            except Exception as e:
                with lock:
                    state["update_last_error"] = str(e)
            stop_event.wait(max(1.0, float(interval)))

    thread = threading.Thread(target=worker, name="chathaiku-update-check", daemon=True)
    state["update_thread"] = thread
    thread.start()


def print_update_notice_if_needed(state: dict, force: bool = False) -> None:
    lock = state.get("update_lock")
    if lock is None:
        return

    with lock:
        notice = state.get("update_notice")
        if not notice:
            return
        notice_key = f"{notice.get('app')}:{notice.get('latest')}"
        if not force and state.get("update_notice_shown") == notice_key:
            return
        state["update_notice_shown"] = notice_key

    print(Color.YELLOW +
          f"  Update available: {notice.get('app')} "
          f"{notice.get('current')} → {notice.get('latest')}" + Color.RESET)
    if notice.get("message"):
        print(Color.DIM + f"  {notice['message']}" + Color.RESET)
    if notice.get("download_url"):
        print(Color.DIM + f"  Download: {notice['download_url']}" + Color.RESET)
    if notice.get("notes_url"):
        print(Color.DIM + f"  Notes:    {notice['notes_url']}" + Color.RESET)
    print(Color.DIM + "  Run /update to check again." + Color.RESET)
    print()


def run_manual_update_check(state: dict, app_name: str, current_version: str) -> None:
    manifest_url = state.get("update_url") or UPDATE_MANIFEST_URL
    print(Color.DIM + f"  Checking for updates at {manifest_url}..." + Color.RESET, end="", flush=True)
    try:
        result = check_for_update(app_name, current_version, manifest_url)
    except Exception as e:
        print(Color.RED + " failed" + Color.RESET)
        print(Color.YELLOW + f"  Update check error: {e}" + Color.RESET)
        return

    lock = state.setdefault("update_lock", threading.Lock())
    with lock:
        state["update_last_result"] = result
        state["update_last_error"] = None
        if result and not result.get("up_to_date"):
            state["update_notice"] = result
            state["update_notice_shown"] = None

    if result and not result.get("up_to_date"):
        print(Color.YELLOW + " update available" + Color.RESET)
        print_update_notice_if_needed(state, force=True)
    elif result:
        print(Color.GREEN + " up to date" + Color.RESET)
        print(Color.DIM + f"  Current: {current_version}  Latest: {result.get('latest')}" + Color.RESET)
    else:
        print(Color.YELLOW + " no app entry found" + Color.RESET)
        print(Color.DIM + "  Manifest loaded, but no matching version entry was found." + Color.RESET)


def post_chat(server_url: str, history: List[dict], timeout: float = 120.0) -> tuple:
    """POST to the configured chat endpoint. Returns (reply_text, error_message).
    On success: (str, None). On failure: (None, str)."""
    try:
        ep = resolve_endpoint(server_url)
        url = ep["chat_url"]
    except ValueError as e:
        return None, f"Invalid endpoint: {e}"

    payload = json.dumps({
        "history": history,
        "temperature": 0.85,
        "top_p": 0.92,
        "max_new_tokens": 200,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers=make_request_headers(url, has_json_body=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        reply = data.get("reply", "") if isinstance(data, dict) else ""
        if not isinstance(reply, str) or not reply.strip():
            return None, "Haiku didn't have anything to say."
        return reply, None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:500]
        except Exception:
            pass

        # Shared-host ModSecurity can block Python-origin POSTs to the public PHP
        # proxy. For CLI/dev use, fall back to the same backend haiku.php proxies to.
        if e.code == 412 and ep.get("kind") in {"php-proxy", "php-router"}:
            fallback_payload = json.dumps({
                "history": history,
                "temperature": 0.85,
                "top_p": 0.92,
                "max_new_tokens": 200,
            }).encode("utf-8")
            fallback_req = urllib.request.Request(
                BACKEND_FALLBACK_ENDPOINT,
                data=fallback_payload,
                headers=make_request_headers(BACKEND_FALLBACK_ENDPOINT, has_json_body=True),
                method="POST",
            )
            try:
                with urllib.request.urlopen(fallback_req, timeout=timeout) as resp:
                    fallback_body = resp.read().decode("utf-8")
                data = json.loads(fallback_body)
                reply = data.get("reply", "") if isinstance(data, dict) else ""
                if isinstance(reply, str) and reply.strip():
                    return reply, None
                return None, "The public endpoint was blocked, and the backend fallback returned an empty reply."
            except Exception as fallback_error:
                return None, (
                    f"Server returned error 412 from the public endpoint. "
                    f"Backend fallback also failed: {fallback_error}"
                )

        return None, f"Server returned error {e.code}. Try again in a moment."
    except urllib.error.URLError as e:
        return None, f"Couldn't reach the server ({e.reason}). Is it running?"
    except (TimeoutError, OSError):
        return None, "The server took too long to respond."
    except json.JSONDecodeError:
        return None, "Got a confused response from the server."


# ──────────────────────────────────────────────────────────
#  Conversation
# ──────────────────────────────────────────────────────────

class Conversation:
    def __init__(self):
        self.turns: List[dict] = []

    def add(self, role: str, content: str):
        self.turns.append({"role": role, "content": content})

    def clear(self):
        self.turns = []

    def to_history(self) -> List[dict]:
        """API expects role: 'user' or 'bot' — same as what we store."""
        return self.turns

    def to_transcript(self) -> str:
        lines = []
        for t in self.turns:
            who = "You" if t["role"] == "user" else "Haiku"
            lines.append(f"{who}: {t['content']}")
        return "\n\n".join(lines)


# ──────────────────────────────────────────────────────────
#  Slash commands
# ──────────────────────────────────────────────────────────

def handle_slash(cmd: str, args: List[str], conv: Conversation, state: dict) -> bool:
    """Return False to quit, True to continue."""
    if cmd in ("/quit", "/exit", "/q", "/bye"):
        print(Color.DIM + "  Goodbye." + Color.RESET)
        return False

    elif cmd == "/clear":
        conv.clear()
        print(Color.DIM + "  Cleared." + Color.RESET)

    elif cmd == "/save":
        if not args:
            print(Color.YELLOW + "  Usage: /save <filename.txt>" + Color.RESET)
        else:
            path = args[0]
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(conv.to_transcript())
                print(Color.DIM + f"  Saved to {path}" + Color.RESET)
            except OSError as e:
                print(Color.RED + f"  Couldn't save: {e}" + Color.RESET)

    elif cmd == "/help":
        print(Color.DIM + """
  Commands:
    /clear         Start a fresh conversation
    /save FILE     Save the conversation to a text file
    /update        Check for CLI updates now
    /help          Show this help
    /quit          Exit (or just press Ctrl-C)

  Just type a message and press Enter to talk to Haiku.
""" + Color.RESET)

    elif cmd == "/update":
        run_manual_update_check(state, APP_NAME, APP_VERSION)

    else:
        print(Color.YELLOW + f"  Unknown command: {cmd}. Type /help for the list." + Color.RESET)

    return True


# ──────────────────────────────────────────────────────────
#  Main loop
# ──────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Chat with Haiku from your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--server", type=str, default=DEFAULT_ENDPOINT,
                   help=f"Server base URL or direct chat endpoint (default: {DEFAULT_ENDPOINT})")
    p.add_argument("--no-update-check", action="store_true",
                   help="Disable background CLI update checks")
    p.add_argument("--update-url", type=str, default=UPDATE_MANIFEST_URL,
                   help=f"Update manifest URL (default: {UPDATE_MANIFEST_URL})")
    p.add_argument("--update-interval", type=float, default=21600.0,
                   help="Seconds between background update checks (default: 21600 / 6 hours)")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors (useful for log files or limited terminals)")
    args = p.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        Color.disable()

    try:
        server_url = resolve_endpoint(args.server)["display_url"]
    except ValueError as e:
        print(Color.RED + f"  Invalid server URL: {e}" + Color.RESET)
        sys.exit(1)

    print_banner(server_url)

    # Friendly startup check — if the server isn't reachable, tell the user
    # before they type a message.
    print(Color.DIM + "  Checking connection..." + Color.RESET, end="", flush=True)
    health = ping_server(server_url)
    if health is None:
        print(Color.RED + " offline." + Color.RESET)
        print(Color.YELLOW + f"  Couldn't reach {server_url}." + Color.RESET)
        print(Color.DIM + "  Double-check the URL or try again when the server is back up.\n" + Color.RESET)
        sys.exit(1)
    print(Color.GREEN + " ready." + Color.RESET)
    print()

    conv = Conversation()
    state = {"update_url": args.update_url}
    if not args.no_update_check:
        start_update_check_loop(state, APP_NAME, APP_VERSION, args.update_url, args.update_interval)

    while True:
        print_update_notice_if_needed(state)
        # ─ Get input ─
        try:
            user_input = input(Color.BOLD + "you: " + Color.RESET).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(Color.DIM + "  Goodbye." + Color.RESET)
            break

        if not user_input:
            continue

        # ─ Slash commands ─
        if user_input.startswith("/"):
            parts = user_input.split()
            should_continue = handle_slash(parts[0], parts[1:], conv, state)
            if not should_continue:
                break
            continue

        # ─ Send to server ─
        conv.add("user", user_input)
        print(Color.MAGENTA + "haiku: " + Color.RESET, end="", flush=True)

        # Subtle waiting indicator without a spinner library
        t_start = time.time()
        try:
            reply, error = post_chat(server_url, conv.to_history())
        except KeyboardInterrupt:
            print(Color.DIM + "[cancelled]" + Color.RESET)
            # Drop the user message we just added so it isn't dangling
            conv.turns.pop()
            continue

        if error:
            print(Color.RED + error + Color.RESET)
            # Remove the unanswered user message so history stays clean
            conv.turns.pop()
        else:
            print(reply)
            conv.add("bot", reply)

        print()


if __name__ == "__main__":
    main()
