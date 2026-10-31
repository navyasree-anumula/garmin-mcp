# garmin-mcp

An MCP server over one person's Garmin Connect account — reads, plus structured workout
creation and calendar scheduling.

**Status: pass 2, phase 5.** Six tools — `garmin_auth_status`, `garmin_training`,
`garmin_health`, `garmin_exercises`, `garmin_workouts` and `garmin_workout_create` — plus a
local web UI for signing in and checking the machine's state. The
two metric tools read Garmin's own measurements through the metric→source map, which exists
because in four of nine known cases the endpoint named after a metric is not the one that
answers it. They return measurements and never advice: what they mean for a given week
needs things no endpoint holds, so that judgement belongs in the conversation
(`docs/SCOPE.md` §1).

`garmin_health` covers HRV (last night, weekly average, Garmin's status phrase), sleep
(score, Garmin's qualifier, duration), all-day stress (average and peak) and resting heart
rate — ten metrics over four requests, because rows sharing a payload are coalesced before
anything is sent. It answers about SpO2 by reporting it unsupported rather than omitting it:
this watch measures none, which is a better answer than silence.

Body battery is measured but deliberately unmapped. It arrives inside the stress payload as
a descriptor-indexed positional array, which needs a decoder that resolves a descriptor key
to a position, and a row written before that decoder exists would be a guess.

**The write path reaches Garmin.** `garmin_workouts` composes a running or strength session
and previews it without sending anything; `garmin_workout_create` uploads and schedules the
structure a human approved, never a recomposed one. A strength session is a circuit of named
exercises held for a time, and `garmin_exercises` searches Garmin's own catalogue of 1527
entries to find what a movement is called — a constant compiled into the library, so it costs
no request. A running step may carry **one** target: an explicit pace range in min/km, faster
bound first. Heart-rate, power, cadence and zone-based targets are refused, because every one
of them depends on a profile this account has never been read for. Garmin's side of the pace
contract — the key string, the unit, the display order, and which value holds the faster
bound — is community convention confined to one constant and is **unverified** until it is
read back from Connect.

Read [`docs/SCOPE.md`](docs/SCOPE.md) before adding to it.

## Use it

Pull the image. It is built for `linux/amd64` and `linux/arm64`, so it runs natively on
both Intel and Apple Silicon:

```bash
docker pull ghcr.io/navyasree-anumula/garmin-mcp:latest
```

If the GHCR package is private you will need to authenticate once first
(`docker login ghcr.io` with a personal access token carrying `read:packages`). If it is
public, the pull needs no authentication at all.

To build it yourself instead:

```bash
docker build -t ghcr.io/navyasree-anumula/garmin-mcp:latest .
```

Bootstrap once. This is the only place a password is ever entered — `-it` because
`getpass` needs a TTY:

```bash
docker run -it --rm -v garmin-tokens:/data ghcr.io/navyasree-anumula/garmin-mcp:latest login
```

Then back up the token volume immediately. If a captcha ever blocks a fresh login, this
file is the only way back:

```bash
docker run --rm -v garmin-tokens:/data -v "$PWD":/backup alpine \
  cp /data/garmin_tokens.json /backup/
```

### The web UI

Two pages — a status page and a sign-in form — served on demand and stopped when you are
done. It never runs on its own, and nothing starts it for you:

```bash
docker compose up -d web     # then open http://127.0.0.1:8765/
docker compose down
```

The **status page** answers "is this machine set up": whether tokens exist, when they were
written, whether their permissions are still `0600`, whether the volume supports `flock`,
and which build is running. It makes no call to Garmin, so it costs nothing to reload and
cannot itself get you rate limited. It never shows what the tokens contain.

The **sign-in form** is the same exchange as `login`, in a browser. It is a deliberate
security regression against `getpass` — a browser means password managers, autofill,
extensions and devtools can all see the field, where a terminal exposed none of them. The
CLI stays available for anyone who wants the tighter boundary, and it is the only route
that can handle an MFA code.

Add it to Claude Desktop's `claude_desktop_config.json`. **On a Microsoft Store install
that file is not where the docs usually say it is.** MSIX redirects the roaming profile, so
it lives under:

```text
%LOCALAPPDATA%\Packages\Claude_<packageid>\LocalCache\Roaming\Claude\claude_desktop_config.json
```

`%APPDATA%\Claude\claude_desktop_config.json` is the non-Store path. On a Store install it
does not exist at all — creating it there changes nothing, the server never appears, and
there is no error to explain why. Either folder exists only after the app has been launched
once. `Settings → Developer → Edit Config` opens the right one without guessing.

```json
{
  "mcpServers": {
    "garmin": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "garmin-tokens:/data",
                "ghcr.io/navyasree-anumula/garmin-mcp:sha-8154e24", "serve"]
    }
  }
}
```

**Pin a `sha-` tag, never `latest`.** `docker run` does not re-pull a moving tag — it uses
whatever was last pulled — so a config on `latest` runs a silently stale image with nothing
anywhere saying so. This is not hypothetical: the laptop ran eight weeks and 36 commits
behind, missing three dependency bumps and two whole tools, and `docker ps` was what finally
said so. `git log --oneline -1 origin/main` gives the short commit; the tag is `sha-` plus
that. Upgrading is deliberate — edit the tag, restart Desktop, then confirm with `docker ps`.

## Things that are easy to get wrong

- **`-it` for `login`, `-i` alone for `serve`.** A TTY applies line-discipline translation
  and corrupts the JSON-RPC stream. Same image, two invocations, and mixing them up gives
  a confusing protocol failure rather than a clean error.
- **The password goes through the CLI, never an MCP tool.** The serving container holds
  tokens only and cannot log in. That is deliberate, and it is what keeps a live credential
  out of conversation transcripts.
- **Never `print()` in this server.** stdout carries the protocol. All logging goes to
  stderr; `tests/test_stdio_purity.py` enforces it.
- **Do not trust `server_version` to tell you which build answered.** `garmin_auth_status`
  returns it, stamped by CI with the commit — but a client that summarises tool output can
  drop the field, and one did: the first time it mattered, `docker ps` had to answer instead.
  To check that an upgrade actually took, **call a tool that cannot exist on the old image**
  (`garmin_health` did not exist before `sha-8154e24`). A capability probe cannot be
  summarised away; a version string can.
- **A configured server appears under Settings → Connectors, not Settings → Developer.**
  The Developer page has a "Local MCP servers" list that stays on *"No servers added"* even
  while a config-file server is connected and answering. Look in Connectors, where it shows
  as `Desktop / Local dev / Connected`. An hour was lost to reading the Developer page and
  concluding the config had been ignored.
- **If it does not appear, redo the restart before anything else.** Quitting from the tray
  icon is the documented step and it is easy to believe it happened when it did not —
  closing the window is not enough. Ten seconds, and it beats every other diagnostic here.
- **The web UI's loopback guarantee is in the publish spec, not the bind address.**
  Inside the container it binds `0.0.0.0` — it has to, because a process bound to
  `127.0.0.1` in a container's own network namespace is unreachable through a published
  port. What keeps it local is `127.0.0.1:8765:8765` in `docker-compose.yml`, backed by a
  `Host` header allowlist that rejects anything else. Run outside a container, the `web`
  command defaults to `127.0.0.1`, so typing nothing gets you the safe case.
- **Loopback is not the same as safe.** The threat is the browser already running on your
  machine: any page you visit can reach `127.0.0.1`, and DNS rebinding defeats a naive
  origin check. Hence the `Host` allowlist, same-origin enforcement, a per-form CSRF token,
  and no CORS headers anywhere. `tests/test_web_security.py` covers each one, and each was
  observed to fail with its guard removed.
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

**684 passed, 2 skipped** as of 2026-10-31. Both skips are in `tests/test_metrics_map.py`,
which parameterises over the metric map: one row has no source endpoint and is covered by
its own test, and one carries no reading and so has no unit to check. A count below 684
means something was removed, which `docs/SCOPE.md` §8 treats as needing a reason rather
than a shrug.

## CI

`.github/workflows/docker.yml` runs the tests, then builds and publishes the multi-arch
image to GHCR. Pull requests build the image but cannot publish it — the registry login
step is skipped for them, so a PR proves the build without being able to ship one.

Tags: `latest` on `main`, a short-SHA tag on every push, and a semver tag when a `v*` tag
is pushed.
