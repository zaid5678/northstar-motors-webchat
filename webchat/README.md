# Northstar Motors webchat

The server and customer widget. The website loads the widget with one script tag.

## What you need

- Docker with Compose. This is all you need to run the product, AI included.
- Free ports: 4010, 4173, and 4020.
- About 3 GB of memory for Docker, and about 10 GB of disk for the bundled AI model and its server.
  The first start downloads 4 to 5 GB of that.
- To run from source or run the checks: Python 3.11 or later, and Node 20.19 or later, or 22.12 or
  later.

## Run without Docker

On Windows, macOS, or Linux, with Python 3.11 or later, and Node 22.12 or later, or 20.19 or later,
for the first run:

```bash
python run.py
```

The first run installs everything into `webchat/.local`. Then open http://localhost:4173. Press
Ctrl+C to stop. There is no bundled model this way: AI replies need Ollama with `qwen3.5:9b`, and
without it the chat runs in degraded mode. [WEBCHAT.md](../WEBCHAT.md#without-docker) has every
step, including where to get each install.

## Run with Docker

From the repository root:

```bash
docker compose up --build -d
```

The webchat runs on http://localhost:4020, alongside the platform on 4010 and the website on 4173.
Database migrations run on start, and data stays in the `northstar-webchat-data` volume.

The AI needs nothing installed (D-033). At start, the chat picks one of two models:

| Model | Used when | Reply time |
| --- | --- | --- |
| `qwen3.5:9b` in [Ollama](https://ollama.com) on the computer | Ollama is running and has the model | 3 to 15 seconds on an M1 Pro |
| `qwen2.5:3b`, bundled in Docker | Otherwise | 25 to 80 seconds on Docker's default 2 cores |

The bundled model is slow because Docker cannot use a Mac's graphics chip. Writing a reply is the
slow part, so replies written by code, such as opening hours and forms, are often quicker. The model downloads on first
start into the `northstar-ollama-models` volume, and its server opens no port on the computer. The startup log line names the model in use, as `model` and `llm_choice`.

For the faster model, install Ollama, then:

```bash
ollama pull qwen3.5:9b
docker compose restart webchat
```

The choice is made only at start, so restart the webchat after starting or stopping Ollama. On
Linux, Ollama only listens inside the machine by default, so containers cannot reach it. Start it
with `OLLAMA_HOST=0.0.0.0 ollama serve`, and make sure port 11434 is not open to your network. Any
OpenAI-compatible server can be the first choice, through `LLM_BASE_URL` and `LLM_MODEL`.

Without either model, for example offline, or with any model outage, chat runs in degraded mode:
buttons, lookups, forms, and confirmations still work. A model that misses its limit, 20 seconds for
the first choice and 60 for the bundled one, also means degraded mode for that reply.

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

Instead of `WEBCHAT_PEPPER`, `WEBCHAT_PEPPER_FILE` can name a file. The server makes a random pepper
there on first start and reuses it. Docker does this, in the `northstar-webchat-secrets` volume, so
no pepper is committed. Deleting that volume resets the lockout on repeated failed booking checks,
and a cancel or change in progress would need starting again.

## Logs

The server writes one JSON line per event to stderr: each turn, AI failures, dealership-system
failures, write outcomes, and server errors. With Docker, read them with
`docker compose logs webchat`. `WEBCHAT_LOG_LEVEL` sets the level, and defaults to `INFO`. Logs
never hold what a customer typed or their details, and sessions appear only as a short hash.

## Decisions and limits

- Decisions: [docs/DECISIONS.md](../docs/DECISIONS.md)
- Known limitations: [docs/LIMITATIONS.md](../docs/LIMITATIONS.md)
- Scaling: [docs/SCALING.md](../docs/SCALING.md)
