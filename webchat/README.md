# Northstar Motors webchat

The server and customer widget. The website loads the widget with one script tag.

## What you need

- Docker with Compose. This is all you need to run the product.
- Free ports: 4010, 4173, and 4020.
- For AI chat, optional: [Ollama](https://ollama.com) on the same computer, and about 8 GB of spare
  memory. Without it, the chat still runs in degraded mode.
- To run from source or run the checks: Python 3.11 or later, and Node 20.19 or later, or 22.12 or
  later.

## Run without Docker

On Windows, macOS, or Linux, with Python 3.11 or later and Node 20.19 or later:

```bash
python run.py
```

The first run installs everything. Then open http://localhost:4173. Press Ctrl+C to stop.

## Run with Docker

From the repository root:

```bash
docker compose up --build -d
```

The webchat runs on http://localhost:4020, alongside the platform on 4010 and the website on 4173.
Database migrations run on start, and data stays in the `northstar-webchat-data` volume.

The LLM is optional. Compose points at Ollama on the host, so for full chat run:

```bash
ollama pull qwen3.5:9b
```

Without it, or with any model outage, chat runs in degraded mode: buttons, lookups, forms, and
confirmations still work. Slower computers may hit the 20-second model limit, which also means
degraded mode. Any OpenAI-compatible server can be used instead through `LLM_BASE_URL`.

On Linux, Ollama only listens inside the machine by default, so containers cannot reach it. Start it
with `OLLAMA_HOST=0.0.0.0 ollama serve`, and make sure port 11434 is not open to your network.

## Run from source

For development. These commands are for macOS and Linux. On Windows, use `python run.py`.

```bash
cd webchat
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
(cd widget && npm ci && npm run build)
cp .env.example .env   # Then fill in the two secrets.
set -a; source .env; set +a
.venv/bin/uvicorn --factory northstar_webchat.main:create_from_env --port 4020
```

## Checks

```bash
.venv/bin/python -m pytest
.venv/bin/mypy
(cd widget && npm run check && npm test)
```

The tests need no key and no network. Some start the unmodified platform on a private port. Build
the widget first. Browser tests run in Chromium (`.venv/bin/python -m playwright install chromium`),
need ports 4010, 4173, and 4020 free, and skip themselves if those are in use.

## Developer inspector

Set `WEBCHAT_DEV_INSPECTOR=1` on the server and add `?inspector=1` to a page URL. It shows session
state, pending operations, tool calls, and validator verdicts. Never enable it in production.

## Environment

`.env.example` lists every name. `NORTHSTAR_API_KEY` and `WEBCHAT_PEPPER` are required, and the rest
have local defaults. Any OpenAI-compatible model can be used through the `LLM_` settings.

## Decisions and limits

- Decisions: [docs/DECISIONS.md](../docs/DECISIONS.md)
- Known limitations: [docs/LIMITATIONS.md](../docs/LIMITATIONS.md)
- Scaling: [docs/SCALING.md](../docs/SCALING.md)
