![ChatHaikuCLI preview](assets/banner.png)

# ChatHaikuCLI

A dependency-free command-line interface for chatting with Rootcomputer models from a terminal.

ChatHaikuCLI ships with two Python clients:

| File | Purpose |
|---|---|
| `chathaiku.py` | Public CLI client for everyday terminal chat. |
| `chathaiku_dev.py` | Developer CLI client with endpoint switching, public model selection, sampling controls and presets, health checks, plugin support, evaluator tooling, AutoDPO support, latency output, and local preference-data collection. |

Both clients use only the Python standard library. No `pip install` step is required.

---

## Highlights

- Works with Rootcomputer-compatible `/api/chat` endpoints.
- Supports public PHP router endpoints such as `https://chathaiku.com/api/haiku.php`.
- Includes a developer `/models` board for public Rootcomputer endpoints: Haiku H2 and Tanka 3.5.
- Includes developer sampling presets through `/preset` and `/presets`.
- Infers `/api/chat` and `/api/health` routes from base URLs, direct chat URLs, health URLs, and PHP router URLs.
- Includes notify-only update checks using the Rootcomputer update manifest.
- Includes a developer plugin system for tools like `/autodpo` and `/evaluator`.
- Caches health metadata in the developer client so plugins can record the active model name correctly.
- Collects local SFT-positive and DPO preference examples as JSONL.
- Runs on Windows, macOS, and Linux with Python 3.8+.

---

## Table of contents

- [Requirements](#requirements)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Running the public client](#running-the-public-client)
- [Running the developer client](#running-the-developer-client)
- [Update checks](#update-checks)
- [Endpoint formats](#endpoint-formats)
- [Public model registry](#public-model-registry)
- [Public client commands](#public-client-commands)
- [Developer client commands](#developer-client-commands)
- [Plugin system](#plugin-system)
- [Plugin model metadata](#plugin-model-metadata)
- [Sampling controls](#sampling-controls)
- [Sampling presets](#sampling-presets)
- [Collecting SFT and DPO data](#collecting-sft-and-dpo-data)
- [Expected server API](#expected-server-api)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [License](#license)

---

## Requirements

- Python 3.8 or newer
- An internet connection for the public endpoint and update checks
- A Rootcomputer-compatible server if using a local or custom endpoint

The clients use only standard-library modules including:

- `argparse`
- `json`
- `os`
- `sys`
- `time`
- `threading`
- `urllib`
- `typing`

No package installation is required.

---

## Repository layout

Recommended layout:

```text
ChatHaikuCLI/
├─ chathaiku.py
├─ chathaiku_dev.py
├─ README.md
├─ plugins/

```

Only the two client files are required for basic chat. The `plugins/` folder is only needed for developer plugins.

If downloaded files have names like `chathaiku(1).py` or `chathaiku_dev(1).py`, rename them before publishing or running examples:

```bash
mv "chathaiku(1).py" chathaiku.py
mv "chathaiku_dev(1).py" chathaiku_dev.py
```

Windows Command Prompt:

```bat
rename chathaiku(1).py chathaiku.py
rename chathaiku_dev(1).py chathaiku_dev.py
```

---

## Quick start

Run the public client:

```bash
python chathaiku.py
```

Send a message:

```text
you: Hello, what can you do?
haiku: ...
```

Exit:

```text
/quit
```

or press `Ctrl-C`.

---

## Running the public client

The public client is the simple user-facing CLI.

```bash
python chathaiku.py
```

Run with a custom endpoint:

```bash
python chathaiku.py --server https://chathaiku.com/api/haiku.php
python chathaiku.py --server http://localhost:8000
python chathaiku.py --server http://localhost:8000/api/chat
```

Disable ANSI colors:

```bash
python chathaiku.py --no-color
```

Disable update checks:

```bash
python chathaiku.py --no-update-check
```

Use a custom update manifest:

```bash
python chathaiku.py --update-url https://example.com/chathaiku_cli_updates.json
```

---

## Running the developer client

The developer client adds endpoint switching, sampling controls, plugins, health checks, retry/undo, latency reporting, and preference-data collection.

```bash
python chathaiku_dev.py
```

Run against a local server:

```bash
python chathaiku_dev.py --server http://localhost:8000
```

Run against a PHP router endpoint:

```bash
python chathaiku_dev.py --server https://chathaiku.com/api/haiku.php
python chathaiku_dev.py --server https://chathaiku.com/api/tanka.php
```

Use custom output files for preference collection:

```bash
python chathaiku_dev.py ^
  --dpo-out data/my_dpo_pairs.jsonl ^
  --sft-positive-out data/my_sft_positive.jsonl
```

Use a custom plugin folder:

```bash
python chathaiku_dev.py --plugins-dir plugins
```

Disable update checks:

```bash
python chathaiku_dev.py --no-update-check
```

---

## Update checks

Both clients include a notify-only update checker.

Default manifest URL:

```text
https://rootcomputer.dev/software/chathaikucli/update/chathaiku_cli_updates.json
```

The update checker:

1. checks in a quiet background loop,
2. caches the result in memory,
3. prints an update notice only at safe prompt boundaries,
4. never rewrites or replaces files automatically.

Manual check:

```text
/update
```

Startup/background flags:

| Flag | Description |
|---|---|
| `--no-update-check` | Disable background update checks. |
| `--update-url URL` | Use a custom update manifest URL. |
| `--update-interval SECONDS` | Set background check interval. Default: `21600` seconds / 6 hours. |

---

## Endpoint formats

Both clients accept `--server`.

The value can be a public PHP router endpoint, a server base URL, a direct chat URL, a health URL, or a relative `/api/...` path.

Examples:

```bash
python chathaiku.py --server https://chathaiku.com/api/haiku.php
python chathaiku.py --server https://chathaiku.com/api/tanka.php
python chathaiku.py --server http://localhost:8000
python chathaiku.py --server http://localhost:8000/api/chat
python chathaiku.py --server http://localhost:8000/api/health
python chathaiku.py --server /api/haiku.php
```

If no scheme is supplied, the client adds one:

- local/private hosts use `http://`
- public hosts use `https://`

Example:

```bash
python chathaiku.py --server localhost:8000
```

is treated as:

```text
http://localhost:8000
```

### Server base URL

Input:

```text
http://localhost:8000
```

Resolved routes:

```text
display: http://localhost:8000
chat:    http://localhost:8000/api/chat
health:  http://localhost:8000/api/health
```

### Direct chat URL

Input:

```text
http://localhost:8000/api/chat
```

Resolved routes:

```text
display: http://localhost:8000
chat:    http://localhost:8000/api/chat
health:  http://localhost:8000/api/health
```

### Health URL

Input:

```text
http://localhost:8000/api/health
```

Resolved routes:

```text
display: http://localhost:8000
chat:    http://localhost:8000/api/chat
health:  http://localhost:8000/api/health
```

---

## Public model registry

`chathaiku_dev.py` includes a public Rootcomputer model registry for quick endpoint selection.

| Key | Model | Endpoint |
|---|---|---|
| `haiku` | Haiku H2 | `https://chathaiku.com/api/haiku.php` |
| `tanka` | Tanka 3.5 | `https://chathaiku.com/api/tanka.php` |

List public models and probe their current status:

```text
/models
```

Switch models:

```text
/models use haiku
/models use tanka
```

The same keys can be passed to `/endpoint`:

```text
/endpoint haiku
/endpoint tanka
```

When a model is selected, the developer client probes the endpoint first. If the endpoint is offline, the current endpoint is kept.

## Public client commands

| Command | Description |
|---|---|
| `/clear` | Clear conversation history. |
| `/save FILE` | Save the conversation transcript to a text file. |
| `/update` | Check for CLI updates immediately. |
| `/help` | Show command help. |
| `/quit` | Exit. |
| `/exit` | Exit alias. |
| `/q` | Exit alias. |
| `/bye` | Exit alias. |

---

## Developer client commands

### Conversation

| Command | Description |
|---|---|
| `/clear` | Reset conversation history. |
| `/history` | Show the current transcript. |
| `/save FILE` | Save the transcript to a file. |
| `/retry` | Drop the last bot reply and re-ask the last user message. |
| `/undo` | Drop the last exchange. |
| `/help` | Show the full command list. |
| `/quit` | Exit. |
| `/exit` | Exit alias. |
| `/q` | Exit alias. |

### Server and updates

| Command | Description |
|---|---|
| `/models` | Show public Rootcomputer model endpoints and live status. |
| `/models use KEY` | Switch to a public model key: `haiku` or `tanka`. |
| `/endpoint URL` | Switch endpoint/server without restarting. Accepts normal endpoints and public model keys. |
| `/ping` | Check endpoint health/reachability and refresh cached model metadata. |
| `/info` | Show last-known endpoint information. |
| `/update` | Check for CLI updates immediately. |

### Sampling

| Command | Description |
|---|---|
| `/preset` | List available sampling presets. |
| `/presets` | Alias for `/preset`. |
| `/preset NAME` | Apply a sampling preset: `balanced`, `precise`, `creative`, `long`, or `eval`. |
| `/temp F` | Set temperature. |
| `/top-p F` | Set nucleus sampling value. |
| `/top-k N` | Set top-k sampling value. `0` disables top-k. |
| `/max-new N` | Set maximum generated tokens per reply. |
| `/rep-penalty F` | Set repetition penalty. |
| `/no-repeat-ngram N` | Block repeated n-grams of size `N`. `0` disables this. |
| `/params` | Show current sampling parameters. |

### Feedback collection

| Command | Description |
|---|---|
| `/good` | Save the last bot reply as a positive SFT example. |
| `/bad` | Mark the last bot reply as bad, enter a corrected answer, and save a DPO pair. |
| `/rewrite` | Rewrite the last bot reply and save a DPO pair. |
| `/stats` | Show collected JSONL counts for this session and on disk. |
| `/dpo-stats` | Alias for `/stats`. |

### Plugins

| Command | Description |
|---|---|
| `/plugin` | List loaded plugins. |
| `/plugin reload` | Reload plugins from the plugin directory. |
| `/plugin help <name>` | Show help for one plugin. |

---

## Plugin system

Plugins are Python files in the configured plugin directory, usually:

```text
plugins/
```

Each plugin defines a subclass of the host `Plugin` class and registers one or more slash commands.

Example layout:

```text
ChatHaikuCLI/
├─ chathaiku_dev.py
└─ plugins/
   ├─ autodpo.py
   └─ evaluator.py
```

Start the developer client and list plugins:

```bash
python chathaiku_dev.py
```

```text
/plugin
```

Reload after adding or editing plugins:

```text
/plugin reload
```

---

## Plugin model metadata

`chathaiku_dev.py` caches health metadata in the shared runtime state after startup, `/ping`, and `/endpoint`.

Plugins can read:

```python
ctx.health
ctx.model_name
```

Plugins can refresh health metadata with:

```python
ctx.refresh_health()
```

This is used by bundled plugins so result files and generated DPO records include the active model name instead of `unknown` whenever `/api/health` reports a model.

If the health request fails transiently, the developer client keeps the last known model name instead of wiping it.

---

## Sampling controls

The public client sends a fixed sampling payload:

```json
{
  "temperature": 0.85,
  "top_p": 0.92,
  "max_new_tokens": 200
}
```

The developer client sends configurable sampling values.

Current developer defaults match the `balanced` preset:

```json
{
  "temperature": 0.35,
  "top_p": 0.37,
  "top_k": 0,
  "max_new_tokens": 80,
  "repetition_penalty": 1.15,
  "no_repeat_ngram": 4
}
```

Inspect current values and active preset:

```text
/params
```

Change values:

```text
/temp 0.7
/top-p 0.9
/top-k 40
/max-new 200
/rep-penalty 1.15
/no-repeat-ngram 4
```

---

## Sampling presets

The developer client includes built-in sampling presets for common workflows.

List presets:

```text
/preset
```

or:

```text
/presets
```

Apply a preset:

```text
/preset balanced
/preset precise
/preset creative
/preset long
/preset eval
```

| Preset | Use case | Temperature | Top-p | Top-k | Max new tokens | Repetition penalty | No-repeat ngram |
|---|---|---:|---:|---:|---:|---:|---:|
| `balanced` | Default chat behavior | `0.35` | `0.37` | `0` | `80` | `1.15` | `4` |
| `precise` | Lower-variance answers | `0.15` | `0.50` | `0` | `100` | `1.12` | `4` |
| `creative` | Higher-diversity replies | `0.75` | `0.90` | `0` | `180` | `1.10` | `3` |
| `long` | Longer helpful responses | `0.45` | `0.75` | `0` | `240` | `1.12` | `4` |
| `eval` | Deterministic scoring | `0.0` | `1.0` | `1` | `32` | `1.0` | `0` |

Manual sampling changes still work after applying a preset. If the active values no longer match a preset, `/params` reports the preset as `custom`.

## Collecting SFT and DPO data

The developer client can collect local JSONL preference data while you test the model.

Default output files:

```text
data/dpo_pairs.jsonl
data/sft_positive.jsonl
```

### Positive SFT examples

After a good model reply:

```text
/good
```

Example record:

```json
{
  "prompt": "User message that caused the reply",
  "chosen": "The model reply marked as good",
  "history": [],
  "server": "https://chathaiku.com/api/haiku.php",
  "model": "h2o8_preview_01",
  "ts": "2026-01-01T12:00:00"
}
```

### DPO pairs

After a bad reply:

```text
/bad
```

The client asks for the preferred answer. End multi-line input with:

```text
/end
```

Cancel with:

```text
/cancel
```

Example record:

```json
{
  "prompt": "User message that caused the reply",
  "chosen": "Your corrected reply",
  "rejected": "The model's original bad reply",
  "history": [],
  "source": "bad",
  "server": "https://chathaiku.com/api/haiku.php",
  "model": "h2o8_preview_01",
  "ts": "2026-01-01T12:00:00"
}
```

Use `/rewrite` when the model reply is acceptable but you want to provide a better preferred answer.

Check counts:

```text
/stats
```

---

## Expected server API

ChatHaikuCLI expects a Rootcomputer-compatible HTTP API.

### Chat route

A chat route accepts `POST` requests with JSON.

For a server base URL, the route should be:

```text
/api/chat
```

Example request:

```json
{
  "history": [
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.85,
  "top_p": 0.92,
  "max_new_tokens": 200
}
```

The developer client may also send:

```json
{
  "top_k": 0,
  "repetition_penalty": 1.15,
  "no_repeat_ngram": 4
}
```

Expected response:

```json
{
  "reply": "Hello. How can I help?"
}
```

### Health route

The optional health route is:

```text
/api/health
```

Expected response:

```json
{
  "model": "h2o8_preview_01",
  "params": 217515008,
  "device": "cuda:1",
  "status": "ok"
}
```

The `model` field is used for saved preference examples and plugin result provenance.

---

## Troubleshooting

### `python` is not recognized

Try:

```bash
python3 --version
```

If that works, run:

```bash
python3 chathaiku.py
```

Otherwise, reinstall Python and enable **Add Python to PATH** during installation.

### The client says the server is offline

Check that:

- the endpoint is typed correctly,
- your internet connection is working,
- the public endpoint is online,
- your local server is running,
- the server exposes `/api/chat` if using a base URL.

Use the developer client for diagnosis:

```bash
python chathaiku_dev.py --server YOUR_ENDPOINT
```

Then run:

```text
/ping
```

### The model name is `unknown`

Run:

```text
/ping
```

Then check:

```text
/info
```

The endpoint must expose a compatible `/api/health` response with a non-empty `model` field. Plugins use cached `ctx.model_name`, so once the developer client sees a model name, AutoDPO and Evaluator can reuse it.

### Update checks fail

Manual check:

```text
/update
```

If it fails, check:

- the manifest URL is reachable,
- the manifest is valid JSON,
- the manifest contains an app entry for `chathaiku` or `chathaiku_dev`,
- the client has internet access.

Disable update checks:

```bash
python chathaiku.py --no-update-check
python chathaiku_dev.py --no-update-check
```

### HTTP 412 from the public PHP endpoint

Some WAF/shared-host setups can block Python-origin POST requests. The clients include a backend fallback for this case. If both the public endpoint and fallback fail, the client prints the fallback error.

### JSON decode error

The endpoint returned non-JSON, usually because:

- the URL points to an HTML page,
- the server crashed and returned an error page,
- a proxy injected HTML,
- the endpoint is not Rootcomputer-compatible.

### Empty reply

The server responded, but the `reply` field was missing or empty. Check server logs and confirm the response shape is:

```json
{"reply": "text here"}
```

### Colors look broken

Run with:

```bash
python chathaiku.py --no-color
python chathaiku_dev.py --no-color
```

or set:

```bash
NO_COLOR=1
```

---

## Security notes

ChatHaikuCLI sends conversation text to the configured endpoint.

Do not send secrets, passwords, API keys, private keys, or sensitive personal data unless you control the endpoint and understand its logging behavior.

The developer client writes local feedback JSONL files. Review those files before publishing, sharing, or training on them.

Update checks are notify-only. The clients do not automatically download, replace, or execute updated code.

---

## License

MIT License
