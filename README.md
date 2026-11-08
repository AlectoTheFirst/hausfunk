# Hausfunk

AI-moderated private home radio with multiple channels. See [docs/PLAN.md](docs/PLAN.md).

## Development

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/) and Podman with `podman compose`.

```bash
uv sync
cp .env.example .env                                 # fill in URLs, keys, passwords
cp config/hausfunk.example.yaml config/hausfunk.yaml # channels, profiles, personas, feeds
uv run hausfunk check-config                         # validate and print a masked summary
uv run pytest
```

### Configuration

Structure lives in `config/hausfunk.yaml` (path via `HAUSFUNK_CONFIG`). Every setting can be
overridden by env vars with the prefix `HAUSFUNK_` and `__` for nesting, e.g.
`HAUSFUNK_JELLYFIN__URL`. Precedence: env > `.env` > `HAUSFUNK_*_FILE` > YAML > defaults.

Secrets (API keys, passwords, tokens) are refused in the YAML file. Set them as env vars, or as
`HAUSFUNK_<NAME>_FILE=/path/to/file` for mounted Podman/Kubernetes secrets.

### Local stream

```bash
cp some-track.mp3 media/test/        # played while a channel's queue is empty
podman compose up
```

Channels listed in `LIQUIDSOAP_CHANNELS` stream at `http://localhost:8000/<slug>`, e.g.
`http://localhost:8000/hausfunk`. Push a track by hand via the control interface:

```bash
echo "hausfunk.push /media/test/other.mp3" | nc -q1 localhost 1234
```
