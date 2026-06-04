"""
chathaiku_dev.py — Developer chat client

Like chathaiku.py but with the controls you'd want as a developer:
  - Per-request sampling parameters (temp, top_p, top_k, etc.)
  - DPO/SFT-positive data collection on /good /bad /rewrite
  - Hot-swap server endpoints with /endpoint
  - Latency and token-count display per request
  - Health check command

Usage:
  python chathaiku_dev.py
  python chathaiku_dev.py --server https://chathaiku.com/api/haiku.php
  python chathaiku_dev.py --dpo-out my_pairs.jsonl

Type /help inside the chat for the full command list.
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
                                                  dev
"""


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
    GREY = "\033[90m"

    @classmethod
    def disable(cls):
        for name in dir(cls):
            if name.isupper():
                setattr(cls, name, "")


def print_banner(server_url: str, health: Optional[dict]):
    print(Color.CYAN + BANNER + Color.RESET)
    line = f"  Endpoint: {server_url}"
    print(Color.DIM + line + Color.RESET)
    if health:
        params = health.get("params")
        if isinstance(params, int):
            params_text = f"{params:,}"
        elif params is None:
            params_text = "?"
        else:
            params_text = str(params)

        print(Color.DIM +
              f"  Model: {health.get('model','?')}  "
              f"({params_text} params, {health.get('device','?')})"
              + Color.RESET)
        if health.get("health") == "not_available":
            print(Color.YELLOW + "  No health endpoint exposed; chat POST will be tested on first message." + Color.RESET)
    else:
        print(Color.RED + "  Server offline." + Color.RESET)
    print(Color.DIM + "  /help for commands. /quit to exit." + Color.RESET)
    print()


# ──────────────────────────────────────────────────────────
#  HTTP client
# ──────────────────────────────────────────────────────────

DEFAULT_ENDPOINT = "https://chathaiku.com/api/haiku.php"
BACKEND_FALLBACK_ENDPOINT = "http://haiku.rootcomputer.dev/api/chat"


def make_request_headers(url: str, *, has_json_body: bool = False) -> dict:
    
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

    raw_url = (raw_url or "").strip().rstrip("/")
    if not raw_url:
        raise ValueError("empty endpoint")

    # Allow CLI users to type the same relative endpoint shown in main_chat.js.
    if raw_url.startswith("/api/"):
        raw_url = "https://chathaiku.com" + raw_url

    # urllib requires a scheme. Use http for local/private dev hosts, https for public hosts.
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
    """Return normalized display, chat, and optional health URLs."""
    display_url = normalize_server_url(server_url)
    parsed = urllib.parse.urlsplit(display_url)
    path = parsed.path.rstrip("/")

    # Direct chat route on the Python server.
    if path.endswith("/api/chat"):
        base_path = path[:-len("/api/chat")].rstrip("/")
        base_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, base_path, "", "")).rstrip("/")
        return {
            "display_url": display_url,
            "chat_url": display_url,
            "health_url": base_url + "/api/health",
            "kind": "direct-chat",
        }

    # Direct PHP proxy endpoint used by the public website: /api/haiku.php, /api/tanka.php, etc.
    if path.endswith(".php"):
        return {
            "display_url": display_url,
            "chat_url": display_url,
            "health_url": None,
            "kind": "php-proxy",
        }

    # If a health route is pasted, recover the server base.
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
    try:
        ep = resolve_endpoint(server_url)

        if ep["health_url"]:
            req = urllib.request.Request(ep["health_url"], headers=make_request_headers(ep["health_url"]), method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                data.setdefault("endpoint", ep["display_url"])
                data.setdefault("endpoint_type", ep["kind"])
                return data
            return None

        # Public PHP proxy endpoints do not expose /api/health. The website does
        # not health-check them; it simply POSTs to /api/haiku.php. Some hosts/WAFs
        # also reject Python OPTIONS probes even when browser POSTs work, so do not
        # mark the model offline here. Actual failures will be reported by post_chat().
        return {
            "model": "Haiku public PHP proxy",
            "params": None,
            "device": "public",
            "endpoint": ep["display_url"],
            "endpoint_type": ep["kind"],
            "health": "not_available",
        }

    except (ValueError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ConnectionRefusedError, OSError):
        return None


def post_chat(server_url: str, history: List[dict], params: dict,
              timeout: float = 180.0) -> tuple:
    """POST to the configured chat endpoint. Returns (reply, error)."""
    try:
        ep = resolve_endpoint(server_url)
        url = ep["chat_url"]
    except ValueError as e:
        return None, f"Invalid endpoint: {e}"

    # Match the public frontend behavior: send only the recent model-visible tail.
    body = {"history": history[-10:], **params}
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers=make_request_headers(url, has_json_body=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if isinstance(data, dict) and data.get("error") and not data.get("reply"):
            return None, f"Server error: {data.get('error')}"

        reply = data.get("reply", "") if isinstance(data, dict) else ""
        if not isinstance(reply, str):
            return None, f"Server returned non-string reply: {type(reply).__name__}"
        if not reply.strip():
            return None, "Server returned an empty reply."
        return reply, None
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:500]
        except Exception:
            pass

        if e.code == 412 and ep.get("kind") == "php-proxy":
            fallback_url = BACKEND_FALLBACK_ENDPOINT
            fallback_body = {"history": history[-10:], **params}
            fallback_payload = json.dumps(fallback_body).encode("utf-8")
            fallback_req = urllib.request.Request(
                fallback_url,
                data=fallback_payload,
                headers=make_request_headers(fallback_url, has_json_body=True),
                method="POST",
            )
            try:
                with urllib.request.urlopen(fallback_req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("error") and not data.get("reply"):
                    return None, f"Public PHP proxy was blocked by ModSecurity; backend fallback returned: {data.get('error')}"
                reply = data.get("reply", "") if isinstance(data, dict) else ""
                if isinstance(reply, str) and reply.strip():
                    return reply, None
                return None, "Public PHP proxy was blocked by ModSecurity; backend fallback returned an empty reply."
            except Exception as fallback_error:
                return None, (
                    f"HTTP 412 from public PHP proxy: {body or e.reason}. "
                    f"Backend fallback also failed: {fallback_error}"
                )

        return None, f"HTTP {e.code}: {body or e.reason}"
    except urllib.error.URLError as e:
        return None, f"URLError: {e.reason}"
    except (TimeoutError, OSError) as e:
        return None, f"Connection error: {e}"
    except json.JSONDecodeError as e:
        return None, f"JSON decode error: {e}"


# ──────────────────────────────────────────────────────────
#  Sampling parameters (client-side state, sent with each request)
# ──────────────────────────────────────────────────────────

class SamplingParams:
    def __init__(self):
        # Defaults match the public Haiku Mini website's casual profile.
        self.temperature = 0.35
        self.top_p = 0.37
        self.top_k = 0
        self.max_new_tokens = 80
        self.repetition_penalty = 1.15
        self.no_repeat_ngram = 4

    def to_payload(self) -> dict:
        return {
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "max_new_tokens": self.max_new_tokens,
            "repetition_penalty": self.repetition_penalty,
            "no_repeat_ngram": self.no_repeat_ngram,
        }

    def show(self) -> str:
        return (
            f"  temperature:        {self.temperature}\n"
            f"  top_p:              {self.top_p}\n"
            f"  top_k:              {self.top_k} {'(off)' if self.top_k == 0 else ''}\n"
            f"  max_new_tokens:     {self.max_new_tokens}\n"
            f"  repetition_penalty: {self.repetition_penalty}\n"
            f"  no_repeat_ngram:    {self.no_repeat_ngram} {'(off)' if self.no_repeat_ngram == 0 else ''}"
        )


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
        return self.turns

    def transcript(self) -> str:
        lines = []
        for i, t in enumerate(self.turns, 1):
            who = "you" if t["role"] == "user" else "haiku"
            lines.append(f"[{i}] {who}: {t['content']}")
        return "\n".join(lines) if lines else "(empty)"

    def pop_last_bot(self) -> Optional[str]:
        """Remove last bot reply, return the user message that prompted it."""
        if self.turns and self.turns[-1].get("role") == "bot":
            self.turns.pop()
            if self.turns and self.turns[-1].get("role") == "user":
                return self.turns[-1]["content"]
        return None


# ──────────────────────────────────────────────────────────
#  DPO / SFT-positive collection (writes JSONL locally)
# ──────────────────────────────────────────────────────────

class PreferenceCollector:
    def __init__(self, dpo_path: str, sft_positive_path: str, server_url: str,
                 model_name: str = "unknown"):
        self.dpo_path = dpo_path
        self.sft_positive_path = sft_positive_path
        self.server_url = server_url
        self.model_name = model_name
        self.session_good = 0
        self.session_pairs = 0

    def _ensure_dir(self, path):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def _ts(self):
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    def record_good(self, prompt: str, reply: str, history_before: List[dict]):
        self._ensure_dir(self.sft_positive_path)
        rec = {
            "prompt": prompt, "chosen": reply,
            "history": history_before,
            "server": self.server_url, "model": self.model_name,
            "ts": self._ts(),
        }
        with open(self.sft_positive_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.session_good += 1

    def record_preference(self, prompt: str, chosen: str, rejected: str,
                          history_before: List[dict], source: str):
        self._ensure_dir(self.dpo_path)
        rec = {
            "prompt": prompt, "chosen": chosen, "rejected": rejected,
            "history": history_before, "source": source,
            "server": self.server_url, "model": self.model_name,
            "ts": self._ts(),
        }
        with open(self.dpo_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.session_pairs += 1


def count_jsonl_lines(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return sum(1 for line in f if line.strip())


def read_multiline_input(label: str) -> Optional[str]:
    print(Color.DIM + f"  Enter {label}. End with /end on its own line, /cancel to abort." + Color.RESET)
    lines: List[str] = []
    while True:
        try:
            line = input(Color.GREY + "  > " + Color.RESET)
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        stripped = line.strip()
        if stripped == "/end":
            break
        if stripped == "/cancel":
            return None
        lines.append(line)
    text = "\n".join(lines).strip()
    return text or None


# ──────────────────────────────────────────────────────────
#  Slash commands
# ──────────────────────────────────────────────────────────

HELP_TEXT = """
Chat commands:
  /clear              Reset conversation history
  /history            Show conversation transcript
  /save FILE          Save transcript to file
  /retry              Re-ask the last user message (drops last bot reply)
  /undo               Drop the last exchange

Sampling (sent with next request):
  /temp F             Temperature (default 0.85)
  /top-p F            Nucleus sampling (default 0.92)
  /top-k N            Top-k (0 = off, default 0)
  /max-new N          Max tokens per reply (default 200)
  /rep-penalty F      Repetition penalty (default 1.15)
  /no-repeat-ngram N  Block repeated n-grams (default 4, 0 = off)
  /params             Show current sampling params

Server:
  /endpoint URL       Switch endpoint/server. Accepts /api/haiku.php, full PHP URLs, or server bases
  /ping               Check endpoint health/reachability
  /info               Show last-known endpoint info

Feedback collection (writes JSONL files for offline DPO):
  /good               Save last reply as positive SFT example
  /bad                Mark last reply bad, prompt for rewrite, save DPO pair
  /rewrite            Rewrite last reply, save DPO pair
  /stats              Show counts collected this session and on disk

Misc:
  /help               This help
  /quit               Exit
"""


def handle_slash(cmd: str, args: List[str], state: dict) -> bool:
    conv: Conversation = state["conversation"]
    sp: SamplingParams = state["sampling"]
    pc: PreferenceCollector = state["preferences"]

    if cmd in ("/quit", "/exit", "/q"):
        return False

    elif cmd == "/help":
        print(Color.DIM + HELP_TEXT + Color.RESET)

    elif cmd == "/clear":
        conv.clear()
        print(Color.DIM + "  Cleared." + Color.RESET)

    elif cmd == "/history":
        print(Color.DIM + conv.transcript() + Color.RESET)

    elif cmd == "/save":
        if not args:
            print(Color.YELLOW + "  Usage: /save <file>" + Color.RESET)
        else:
            try:
                with open(args[0], "w", encoding="utf-8") as f:
                    f.write(conv.transcript())
                print(Color.DIM + f"  Saved to {args[0]}" + Color.RESET)
            except OSError as e:
                print(Color.RED + f"  Save failed: {e}" + Color.RESET)

    elif cmd == "/retry":
        last_user = conv.pop_last_bot()
        if last_user is None:
            print(Color.YELLOW + "  Nothing to retry." + Color.RESET)
        else:
            # Drop the user message too — main loop will re-add it via pending_retry
            conv.turns.pop()
            state["pending_retry"] = last_user

    elif cmd == "/undo":
        if conv.turns:
            conv.turns.pop()
            if conv.turns and conv.turns[-1].get("role") == "user":
                conv.turns.pop()
            print(Color.DIM + f"  Undone. {len(conv.turns)} turns remain." + Color.RESET)
        else:
            print(Color.YELLOW + "  Nothing to undo." + Color.RESET)

    # ── Sampling params ──
    elif cmd == "/temp":
        if not args: print(Color.DIM + f"  temperature: {sp.temperature}" + Color.RESET)
        else:
            try: sp.temperature = float(args[0]); print(Color.DIM + f"  temperature → {sp.temperature}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd in ("/top-p", "/topp"):
        if not args: print(Color.DIM + f"  top_p: {sp.top_p}" + Color.RESET)
        else:
            try: sp.top_p = float(args[0]); print(Color.DIM + f"  top_p → {sp.top_p}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd in ("/top-k", "/topk"):
        if not args: print(Color.DIM + f"  top_k: {sp.top_k}" + Color.RESET)
        else:
            try: sp.top_k = int(args[0]); print(Color.DIM + f"  top_k → {sp.top_k}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd in ("/max-new", "/maxnew"):
        if not args: print(Color.DIM + f"  max_new_tokens: {sp.max_new_tokens}" + Color.RESET)
        else:
            try: sp.max_new_tokens = int(args[0]); print(Color.DIM + f"  max_new_tokens → {sp.max_new_tokens}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd in ("/rep-penalty", "/reppenalty", "/repetition-penalty"):
        if not args: print(Color.DIM + f"  repetition_penalty: {sp.repetition_penalty}" + Color.RESET)
        else:
            try: sp.repetition_penalty = float(args[0]); print(Color.DIM + f"  repetition_penalty → {sp.repetition_penalty}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd in ("/no-repeat-ngram", "/norepeatngram", "/ngram"):
        if not args: print(Color.DIM + f"  no_repeat_ngram: {sp.no_repeat_ngram}" + Color.RESET)
        else:
            try: sp.no_repeat_ngram = int(args[0]); print(Color.DIM + f"  no_repeat_ngram → {sp.no_repeat_ngram}" + Color.RESET)
            except ValueError: print(Color.RED + f"  Invalid: {args[0]}" + Color.RESET)

    elif cmd == "/params":
        print(Color.DIM + sp.show() + Color.RESET)

    # ── Server ──
    elif cmd == "/endpoint":
        if not args:
            print(Color.DIM + f"  Current endpoint: {state['server_url']}" + Color.RESET)
        else:
            try:
                new_url = resolve_endpoint(args[0])["display_url"]
            except ValueError as e:
                print(Color.RED + f"  Invalid endpoint: {e}" + Color.RESET)
                return True

            print(Color.DIM + f"  Probing {new_url}..." + Color.RESET, end="", flush=True)
            health = ping_server(new_url)
            if health:
                if health.get("health") == "not_available":
                    print(Color.YELLOW + " configured (no health endpoint)" + Color.RESET)
                else:
                    print(Color.GREEN + " ok" + Color.RESET)
                state["server_url"] = new_url
                state["health"] = health
                pc.server_url = new_url
                pc.model_name = health.get("model", "unknown")
            else:
                print(Color.RED + " offline" + Color.RESET)
                print(Color.YELLOW + "  Keeping current endpoint." + Color.RESET)

    elif cmd == "/ping":
        print(Color.DIM + f"  Pinging {state['server_url']}..." + Color.RESET, end="", flush=True)
        health = ping_server(state["server_url"])
        if health:
            if health.get("health") == "not_available":
                print(Color.YELLOW + " no health endpoint" + Color.RESET)
            else:
                print(Color.GREEN + " ok" + Color.RESET)
            print(Color.DIM + f"  {json.dumps(health, indent=2)}" + Color.RESET)
            state["health"] = health
        else:
            print(Color.RED + " offline" + Color.RESET)

    elif cmd == "/info":
        h = state.get("health")
        if h:
            print(Color.DIM + json.dumps(h, indent=2) + Color.RESET)
        else:
            print(Color.YELLOW + "  No server info cached. Try /ping." + Color.RESET)

    # ── DPO / SFT collection ──
    elif cmd == "/good":
        if not conv.turns:
            print(Color.YELLOW + "  Nothing to mark." + Color.RESET)
        elif conv.turns[-1].get("role") != "bot":
            print(Color.YELLOW + "  Last turn isn't a bot reply." + Color.RESET)
        else:
            last = conv.turns[-1]
            # Find the user turn that prompted it
            prompt = ""
            for t in reversed(conv.turns[:-1]):
                if t.get("role") == "user":
                    prompt = t["content"]
                    break
            history_before = conv.turns[:-2] if len(conv.turns) >= 2 else []
            pc.record_good(prompt, last["content"], history_before)
            print(Color.GREEN + f"  Marked GOOD. ({pc.session_good} session, "
                  f"{count_jsonl_lines(pc.sft_positive_path)} total)" + Color.RESET)

    elif cmd == "/bad":
        if not conv.turns or conv.turns[-1].get("role") != "bot":
            print(Color.YELLOW + "  No recent bot reply to mark." + Color.RESET)
        else:
            last = conv.turns[-1]
            prompt = ""
            for t in reversed(conv.turns[:-1]):
                if t.get("role") == "user":
                    prompt = t["content"]
                    break
            history_before = conv.turns[:-2] if len(conv.turns) >= 2 else []
            print(Color.YELLOW + "  Marked BAD. What should the reply have been?" + Color.RESET)
            rewrite = read_multiline_input("the corrected reply")
            if rewrite is None:
                print(Color.DIM + "  Cancelled — no pair saved." + Color.RESET)
            else:
                pc.record_preference(prompt, rewrite, last["content"], history_before, "bad")
                print(Color.GREEN + f"  Saved DPO pair. ({pc.session_pairs} session, "
                      f"{count_jsonl_lines(pc.dpo_path)} total)" + Color.RESET)

    elif cmd == "/rewrite":
        if not conv.turns or conv.turns[-1].get("role") != "bot":
            print(Color.YELLOW + "  No recent bot reply to rewrite." + Color.RESET)
        else:
            last = conv.turns[-1]
            prompt = ""
            for t in reversed(conv.turns[:-1]):
                if t.get("role") == "user":
                    prompt = t["content"]
                    break
            history_before = conv.turns[:-2] if len(conv.turns) >= 2 else []
            print(Color.DIM + f"  Original: {last['content']!r}" + Color.RESET)
            rewrite = read_multiline_input("your rewrite")
            if rewrite is None:
                print(Color.DIM + "  Cancelled." + Color.RESET)
            else:
                pc.record_preference(prompt, rewrite, last["content"], history_before, "rewrite")
                print(Color.GREEN + f"  Saved DPO pair. ({pc.session_pairs} session, "
                      f"{count_jsonl_lines(pc.dpo_path)} total)" + Color.RESET)

    elif cmd in ("/stats", "/dpo-stats"):
        print(Color.DIM +
              f"  DPO pairs:    {pc.dpo_path}\n"
              f"    On disk:    {count_jsonl_lines(pc.dpo_path):,}\n"
              f"    Session:    {pc.session_pairs}\n"
              f"  SFT positive: {pc.sft_positive_path}\n"
              f"    On disk:    {count_jsonl_lines(pc.sft_positive_path):,}\n"
              f"    Session:    {pc.session_good}"
              + Color.RESET)

    else:
        print(Color.YELLOW + f"  Unknown command: {cmd}. Try /help." + Color.RESET)

    return True


# ──────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description="Developer chat client for the Haiku Mini v2 demo server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--server", type=str, default=DEFAULT_ENDPOINT,
                   help="Chat endpoint or server base URL. Default: https://chathaiku.com/api/haiku.php")
    p.add_argument("--dpo-out", type=str, default="data/dpo_pairs.jsonl",
                   help="JSONL file for /bad and /rewrite DPO triples")
    p.add_argument("--sft-positive-out", type=str, default="data/sft_positive.jsonl",
                   help="JSONL file for /good positive examples")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colors")
    args = p.parse_args()

    if args.no_color or os.environ.get("NO_COLOR"):
        Color.disable()

    try:
        server_url = resolve_endpoint(args.server)["display_url"]
    except ValueError as e:
        print(Color.RED + f"Invalid --server value: {e}" + Color.RESET)
        sys.exit(2)

    health = ping_server(server_url)

    print_banner(server_url, health)

    if health is None:
        print(Color.YELLOW +
              f"  Warning: server at {server_url} not reachable. You can /endpoint to switch.\n"
              + Color.RESET)

    sampling = SamplingParams()
    conv = Conversation()
    preferences = PreferenceCollector(
        dpo_path=args.dpo_out,
        sft_positive_path=args.sft_positive_out,
        server_url=server_url,
        model_name=(health or {}).get("model", "unknown"),
    )

    state = {
        "server_url": server_url,
        "health": health,
        "conversation": conv,
        "sampling": sampling,
        "preferences": preferences,
        "pending_retry": None,
    }

    while True:
        # Get input
        if state.get("pending_retry") is not None:
            user_input = state.pop("pending_retry")
            print(Color.DIM + f"[retry] " + Color.BOLD + "you: " + Color.RESET + user_input)
        else:
            try:
                user_input = input(Color.BOLD + "you: " + Color.RESET).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user_input:
                continue

        if user_input.startswith("/"):
            parts = user_input.split()
            if not handle_slash(parts[0], parts[1:], state):
                break
            continue

        # Send to server
        conv.add("user", user_input)
        print(Color.MAGENTA + "haiku: " + Color.RESET, end="", flush=True)

        t_start = time.time()
        try:
            reply, error = post_chat(
                state["server_url"],
                conv.to_history(),
                sampling.to_payload(),
            )
        except KeyboardInterrupt:
            print(Color.DIM + "[cancelled]" + Color.RESET)
            conv.turns.pop()
            continue
        elapsed = time.time() - t_start

        if error:
            print(Color.RED + error + Color.RESET)
            conv.turns.pop()
        else:
            print(reply)
            conv.add("bot", reply)
            # Latency footer
            n_chars = len(reply)
            print(Color.GREY + f"  ({n_chars} chars in {elapsed:.2f}s, "
                  f"{n_chars/max(0.01,elapsed):.0f} chars/s)" + Color.RESET)

        print()


if __name__ == "__main__":
    main()
