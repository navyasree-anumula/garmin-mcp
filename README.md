# garmin-mcp

An MCP server over one person's Garmin Connect account — reads, plus structured workout
creation and calendar scheduling.

**Status: pass 1 — walking skeleton.** One tool (`garmin_auth_status`), proving the whole
chain end-to-end. The data and workout tools come next.

Read [`docs/SCOPE.md`](docs/SCOPE.md) before adding to it.

## Use it

Build the image (on the machine that will run it):

```bash
docker build -t garmin-mcp:0.1.0 .
```

Bootstrap once. This is the only place a password is ever entered — `-it` because
`getpass` needs a TTY:

```bash
docker run -it --rm -v garmin-tokens:/data garmin-mcp:0.1.0 login
```

Then back up the token volume immediately. If a captcha ever blocks a fresh login, this
file is the only way back:

```bash
docker run --rm -v garmin-tokens:/data -v "$PWD":/backup alpine \
  cp /data/garmin_tokens.json /backup/
```

Add it to Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "garmin-tokens:/data", "garmin-mcp:0.1.0", "serve"]
    }
  }
}
```

## Things that are easy to get wrong

- **`-it` for `login`, `-i` alone for `serve`.** A TTY applies line-discipline translation
  and corrupts the JSON-RPC stream. Same image, two invocations, and mixing them up gives
  a confusing protocol failure rather than a clean error.
- **The password goes through the CLI, never an MCP tool.** The serving container holds
  tokens only and cannot log in. That is deliberate, and it is what keeps a live credential
  out of conversation transcripts.
- **Never `print()` in this server.** stdout carries the protocol. All logging goes to
  stderr; `tests/test_stdio_purity.py` enforces it.
- **The HTTP transport must be published to loopback only.** `serve --http` binds
  `0.0.0.0` inside the container on purpose — exposure is controlled by Docker:
  `-p 127.0.0.1:3001:3001` is correct, `-p 3001:3001` serves your health data to every
  network the machine joins, with no authentication.
- **Deletes are permanently excluded** from the tool surface.
- **The dependency is pinned floor-and-ceiling** (`>=0.3.11,<0.4`), never exactly — an
  exact pin is what stranded the most popular existing server on a stale release.

## Claude chat / mobile

They cannot reach this. Claude connects to remote MCP servers from Anthropic's cloud, not
from your device, so a server on your laptop is unreachable no matter how it is configured.
Reaching it from chat needs a public HTTPS tunnel **and** authentication in front of it —
Claude permits authless remote servers, so the lazy version of that is a public,
unauthenticated endpoint serving your health data. That work gets its own plan.

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
```
