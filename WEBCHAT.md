# Northstar Motors AI webchat: start here

`README.md` starts the dealership services. The same command also starts the webchat.

## Start

```bash
docker compose up --build -d
```

Then open http://localhost:4173 and use the chat button in the bottom-right corner.

| Part                | Address                     |
| ------------------- | --------------------------- |
| Dealership website  | http://localhost:4173       |
| Webchat server      | http://localhost:4020       |
| Platform API docs   | http://localhost:4010/docs  |
| Systems Console     | http://localhost:4010/admin |

Ports 4010, 4173, and 4020 must be free. Database setup runs on start, and no settings or secrets
need to be entered.

## AI

There is nothing to install. The first start downloads 4 to 5 GB for an AI model and its server,
and takes several minutes. Later starts reuse it. Docker needs about 3 GB of memory and 10 GB of
disk.

That bundled model is slow, with AI replies in 25 to 80 seconds, because Docker cannot use a Mac's
graphics chip. For replies in 3 to 15 seconds from a stronger model, install
[Ollama](https://ollama.com) on the computer, then:

```bash
ollama pull qwen3.5:9b
docker compose restart webchat
```

The chat picks the faster one whenever it is running when the chat starts. Customer messages never
leave the computer either way. If neither model is available, for example offline, the chat still
works in a reduced mode: replies are written by code, and searches, bookings, lookups, and
confirmations all still work.

## Stop and reset

```bash
docker compose down     # stop, keeping conversations and bookings
./reset.sh              # while running: restore the dealership seed data
docker compose down -v  # stop and delete all saved data
```

## Without Docker

`run.py` starts the same three parts directly, on Windows, macOS, or Linux.

### 1. Install

| What | Version | Where to get it | Needed |
| --- | --- | --- | --- |
| Python | 3.11 or later | [python.org/downloads](https://www.python.org/downloads/) | Always |
| Node.js | 22.12 or later, or 20.19 or later | [nodejs.org](https://nodejs.org) (the LTS version) | First run only, to build the chat widget |
| Ollama | Any recent version | [ollama.com/download](https://ollama.com/download) | Optional, for AI replies |

- **Windows:** in the Python installer, tick "Add python.exe to PATH".
- **macOS:** the installers above work. With [Homebrew](https://brew.sh), `brew install python node`
  also works.
- **Linux:** use your package manager, for example `sudo apt install python3 python3-venv nodejs`.
  Check the versions it gives you. Older releases may ship versions that are too old.

Check what you have:

```bash
python --version   # macOS and Linux may need python3
node --version
```

### 2. Run

From this folder:

```bash
python run.py      # macOS and Linux may need python3 run.py
```

The first run downloads and installs what the chat needs into `webchat/.local`, which takes a few
minutes and needs internet. Later runs start in seconds. When it says so, open
http://localhost:4173. Press Ctrl+C to stop all three parts.

Ports 4010, 4173, and 4020 must be free. `python run.py --check` starts everything, confirms each
part answers, and stops.

### 3. AI

Without Docker there is no bundled model. The chat uses Ollama on the computer, so for AI replies,
install Ollama and download the model once. It is 6.6 GB, and needs about 8 GB of free memory:

```bash
ollama pull qwen3.5:9b
```

The chat uses it as soon as Ollama is running, with no restart. Without Ollama, the chat still
works in its reduced mode: replies are written by code, and searches, bookings, lookups, and
confirmations all still work.

### Reset and saved data

```bash
./reset.sh         # macOS and Linux, while running: restore the dealership seed data
.\reset.ps1        # Windows PowerShell, the same
```

Conversations, bookings, and the install are kept in `webchat/.local`. To start completely afresh,
stop `run.py` and delete that folder. The next run installs again.

## More

- [webchat/README.md](./webchat/README.md): settings, running from source, tests, and logs.
- [docs/DECISIONS.md](./docs/DECISIONS.md): product decisions and why they were made.
- [docs/LIMITATIONS.md](./docs/LIMITATIONS.md): known gaps.
