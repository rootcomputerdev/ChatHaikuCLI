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
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Optional


# ──────────────────────────────────────────────────────────
#  Banner & UI
# ──────────────────────────────────────────────────────────

BANNER = r"""
   ____ _           _   _   _       _ _         _
  / ___| |__   __ _| |_| | | | __ _(_) | ___   _| |
 | |   | '_ \ / _` | __| |_| |/ _` | | |/ / | | | |
 | |___| | | | (_| | |_|  _  | (_| | |   <| |_| |_|
  \____|_| |_|\__,_|\__|_| |_|\__,_|_|_|\_\__,_(_)
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
    """Resolve either a base server URL or a direct POST endpoint."""
    display_url = normalize_server_url(server_url)
    parsed = urllib.parse.urlsplit(display_url)
    path = parsed.path.rstrip("/")

    # Direct Python server chat endpoint.
    if path.endswith("/api/chat"):
        base_path = path[:-len("/api/chat")].rstrip("/")
        base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")).rstrip("/")
        return {
            "display_url": display_url,
            "chat_url": display_url,
            "health_url": base_url + "/api/health",
            "kind": "direct-chat",
        }

    # Public website PHP proxy endpoint, e.g. /api/haiku.php.
    if path.endswith(".php"):
        return {
            "display_url": display_url,
            "chat_url": display_url,
            "health_url": None,
            "kind": "php-proxy",
        }

    # If a health URL is pasted, recover the base URL.
    if path.endswith("/api/health"):
        base_path = path[:-len("/api/health")].rstrip("/")
        base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")).rstrip("/")
        return {
            "display_url": base_url,
            "chat_url": base_url + "/api/chat",
            "health_url": display_url,
            "kind": "server-base",
        }

    # Plain base server URL.
    base_url = display_url.rstrip("/")
    return {
        "display_url": base_url,
        "chat_url": base_url + "/api/chat",
        "health_url": base_url + "/api/health",
        "kind": "server-base",
    }


def ping_server(server_url: str, timeout: float = 5.0) -> Optional[dict]:
    """Check health when available. Direct PHP proxy endpoints have no health route."""
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
            return data if isinstance(data, dict) else None

        # The public website does not health-check /api/haiku.php; it POSTs to it.
        # Treat it as configured and let the first chat request report real errors.
        return {
            "model": "Haiku public PHP proxy",
            "params": None,
            "device": "public",
            "health": "not_available",
        }

    except (ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ConnectionRefusedError, OSError):
        return None


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
        if e.code == 412 and ep.get("kind") == "php-proxy":
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

def handle_slash(cmd: str, args: List[str], conv: Conversation) -> bool:
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
    /help          Show this help
    /quit          Exit (or just press Ctrl-C)

  Just type a message and press Enter to talk to Haiku.
""" + Color.RESET)

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
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors (useful for log files or limited terminals)")
    args = p.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        Color.disable()

    try:
        server_url = normalize_server_url(args.server)
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

    while True:
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
            should_continue = handle_slash(parts[0], parts[1:], conv)
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
