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

With Python 3.11 or later and Node 20.19 or later:

```bash
python run.py
```

Press Ctrl+C to stop.

## More

- [webchat/README.md](./webchat/README.md): settings, running from source, tests, and logs.
- [docs/DECISIONS.md](./docs/DECISIONS.md): product decisions and why they were made.
- [docs/LIMITATIONS.md](./docs/LIMITATIONS.md): known gaps.
