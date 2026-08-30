# Journal

Chronological log of real work on garmin-mcp. Newest entry last.

---

## 2026-08-30 — Pass 1 built, published, and merged. Blocked on Docker Desktop.

### What shipped

**Pass 1 walking skeleton** (PR #1, `43b7f17`) — the whole chain end to end with the
smallest possible surface: bootstrap CLI → token volume → container → stdio → MCP
handshake → one tool → a real authenticated Garmin call.

- One tool, `garmin_auth_status`: read-only, no arguments, so this pass carries none of
  the §5 response-contract decisions (units, timezone basis, result caps).
- 10 tests from a baseline of 0.
- `garmin_mcp/source/` is the only package importing `garminconnect` (SCOPE §11).

**CI + GHCR publishing** (PR #2, `683bea0`) — tests gate the image publish; multi-arch
`linux/amd64` + `linux/arm64`; pull requests build but cannot push, because the registry
login step is skipped for `pull_request` events.

`main` is at `df7323b`. Image is public at `ghcr.io/navyasree-anumula/garmin-mcp:latest` —
verified pullable anonymously (anonymous ghcr.io token + manifest fetch returned 200, both
architectures present).

### Two things the library disagreed with the plan about

**A missing token file does not raise `FileNotFoundError`.** `garminconnect.login()`
swallows the load failure in a broad `except Exception`, sets `tokens_loaded = False`, and
then raises `GarminConnectAuthenticationError("Username and password are required")` — the
same exception a genuinely rejected token raises. Without an explicit file-existence check
before `login()`, "never bootstrapped" and "token expired" are indistinguishable and the
operator gets told to re-authenticate when they never authenticated. Fixed, with a test
pinning the distinction.

**`FastMCP` was removed in mcp 2.x.** The entry class is `MCPServer` from `mcp.server`;
the old import path is a shim that raises `ModuleNotFoundError`. This also means every
public Garmin MCP server on GitHub is v1-era and broken against the current SDK, which
independently confirms the decision to build rather than adopt.

Also verified rather than assumed: `get_full_name()` and `get_unit_system()` are cached
attribute reads that make no HTTP call. A smoke test asserting on them alone would be
vacuous — the proof that tokens work is that `login()` completed, since it ends in a real
authenticated profile fetch on every path including cached-token reuse.

### Where we stopped

**Blocked getting Docker Desktop running on the Windows laptop.** Sequence so far:

1. `docker version` printed a `Client:` section with no `Server:` — engine not running.
2. Docker Desktop refused to start: "Virtualization support not detected".
3. Task Manager reported CPU virtualization *Enabled* — that flag is not what Docker means.
   It needs the Windows features.
4. `wsl --status` → WSL not installed.
5. `wsl --install --no-distribution` succeeded, installing WSL 2.7.12 and enabling
   `VirtualMachinePlatform`, twice printing "changes will not be effective until the system
   is rebooted".
6. **Reboot pending.** That is the resume point.

### Resume here after the reboot

> **Superseded by the next entry.** The reboot never happened — bootstrap ran on a
> different laptop that already had a working Docker Desktop. Kept for the record.

```powershell
docker version                      # expect a Server: section, not just Client:
docker pull ghcr.io/navyasree-anumula/garmin-mcp:latest
docker run -it --rm -v garmin-tokens:/data ghcr.io/navyasree-anumula/garmin-mcp:latest login
docker run --rm -v garmin-tokens:/data -v "${PWD}:/backup" alpine cp /data/garmin_tokens.json /backup/
```

Choose **WSL2** when Docker Desktop asks for a backend. Leave the `*.docker.internal`
/etc/hosts option unticked — this server never touches the host network.

Back up the token volume immediately after a successful bootstrap. If a captcha ever blocks
a fresh login there is no workaround by design, and that file is the only way back.

Then add to `%APPDATA%\Claude\claude_desktop_config.json` and fully quit/reopen Claude
Desktop (tray icon, not just the window):

```json
{ "mcpServers": { "garmin": { "command": "docker",
  "args": ["run","-i","--rm","-v","garmin-tokens:/data",
           "ghcr.io/navyasree-anumula/garmin-mcp:latest","serve"] } } }
```

Success looks like `garmin_auth_status` returning a real display name and unit system.

### If Docker still fails after the reboot

Then the hypervisor is blocked above the firmware — Credential Guard or corporate policy —
and it is an IT ticket, not a configuration problem. **The fallback is to run natively with
Python, no Docker at all.** Verified against PyPI: `curl_cffi` ships `cp310-abi3-win_amd64`
wheels, `garminconnect` and `mcp` are pure Python, `pydantic-core` has `win_amd64` wheels.
Nothing compiles and nothing needs Linux.

That route needs one contained change first, currently **not done**:

- `DEFAULT_TOKENSTORE` is hardcoded to `/data/garmin_tokens.json`, a container path that is
  meaningless on Windows. It needs a platform-aware fallback (e.g.
  `%LOCALAPPDATA%\garmin-mcp\garmin_tokens.json`) while keeping `/data` in the container.
- `BOOTSTRAP_COMMAND` in `source/errors.py` tells the operator to run a `docker run`
  command. On a machine that cannot run Docker that is actively wrong advice.
- Tests for both shapes, so the container path cannot silently break.

### Open items

- `docs/SCOPE.md` cites protocol revision 2026-07-28, but `mcp` 2.1.1 negotiates
  `2025-11-25` in the handshake. Harmless, uncorrected.
- A `garmin-mcp:0.1.0` image built on the staging box to verify the Dockerfile is still
  there (315MB), now redundant since CI builds the image.
- Pass 2 is unstarted: the FR570 capability probe, then the first real data tools. That is
  where the units, timezone and result-cap contracts get designed rather than guessed.

---

## 2026-08-30 (later) — Bootstrap done on a real account. Stress-test found a live bug.

### The blocker dissolved

The pending Windows reboot never happened. Bootstrap ran on a **different laptop** that
already had a working Docker Desktop (4.44.2, engine 28.3.2, `linux/amd64`, WSL2). The
previous entry's resume steps are superseded.

Pass 1 is now proven end to end against the real account: bootstrap → token volume →
container → stdio → Claude Desktop → `garmin_auth_status` returning a live authenticated
profile. The full chain, on the intended host.

### What the live run settled

Four things §10 carried as unverified now have measured answers:

- **Laptop toolchain** — verified, above.
- **Protocol revision** is `2025-11-25`, not the `2026-07-28` §10 claims.
- **Account unit system is metric.** This settles the §5 units contract with a fact.
- **`display_name` is a GUID**, not a human name — and it is load-bearing, not cosmetic:
  `_require_display_name()` builds the URL path for sleep, resting HR, daily summary, heart
  rates and personal records. The read surface depends on it. (Value not recorded here; the
  repo is public.)

And one thing nobody had asked:

- **Garmin 429s this IP on the mobile *credential* login paths**, both `mobile+cffi` and
  `mobile+requests`, on the very first attempt. A later strategy succeeded. The **token
  load and refresh path is not throttled** — proven by the serve-time call returning
  cleanly. So §6's rate-limit concern lands on bootstrap, not on serving.

### Two containers, not one

`docker ps` during a live session shows **two** containers for one configured stdio server,
stable across queries (not accumulating). `source/client.py` claimed "under stdio is exactly
one client session." That comment was wrong and is now corrected. It matters because
garminconnect re-dumps tokens whenever it refreshes them, so the token file has two
independent writers with only an in-process lock between them.

### The stress test (10 dimensions, per sop/stress-test-10-dimensions.md)

Run against real library source before building pass 2. Full findings live in
`docs/SCOPE.md` v1.3. The ones that changed the code:

**A data-path 429 does not raise `GarminConnectTooManyRequestsError`.** That type is raised
only by the login strategies. `client._run_request` maps every status >= 400 except 404 onto
`GarminConnectConnectionError` — there is no 429 branch. So a throttled *read* reached our
`_translate` dressed as a connectivity error, and the agent would read it as transient and
retry: exactly what §6 forbids, against an IP already being throttled. **Fixed**, with tests
covering both directions so the check is shown to be able to say no.

**`get_activities_by_date` auto-paginates up to `MAX_PAGINATED_REQUESTS = 2000`** with no
delay between pages. §5's hard result cap lives in our layer, which is too late — the library
has already fired the requests. Recorded so pass 2 builds on `get_activities(start, limit)`
instead.

**§6's rate limiter and short-TTL cache do not exist.** Zero hits across `garmin_mcp/`. Pass 1
did not need them; pass 2 is the first multi-call surface, so they come first.

### pip-audit found something better than a CVE

§8 has always mandated `pip-audit` in CI and it was never wired up. Adding it flagged
`pytest 8.4.2`. Chasing that surfaced the real problem: **`requirements.lock` carried
`pytest`, `pluggy`, `iniconfig`, `Pygments` and `packaging`**, and the Dockerfile does
`pip install -r requirements.lock` — so the production image shipped the test runner and its
whole dependency tree.

The lock is now runtime-only: 40 pins down to 35. Verified by installing the new lock into a
clean venv and importing every module, so nothing load-bearing was cut. `pip-audit` on the
result reports no known vulnerabilities, and a test now fails if dev packages return.

The pytest CVE itself remains in the dev environment. `pyproject.toml` pins `pytest>=8,<9`
and the fix is 9.0.3, so clearing it means widening that ceiling — left as a deliberate
decision rather than a silent bump.

### Also this pass

- `garmin_auth_status` now reports `server_version`, stamped by CI with the commit.
  `docker run` never re-pulls a moving tag, so a config pinned to `latest` runs whatever was
  pulled last with nothing saying so. This makes "am I on the current build?" answerable from
  inside a conversation.
- `_configure_logging` now passes `force=True`. Without it `basicConfig` is a **no-op** when
  the root logger already has handlers, so an earlier import configuring logging at DEBUG
  would have stood — and garminconnect logs full response bodies at DEBUG, which on a health
  endpoint is health data (§8). The level was the only thing preventing that; it now has a
  regression test.

Tests: 10 → 26.

### Client wiring, which cost an hour

Claude Desktop on Windows is the **MSIX/Store build**. Its config is not at `%APPDATA%\Claude`
— it is under `%LOCALAPPDATA%\Packages\Claude_<pkgid>\LocalCache\Roaming\Claude\`, and the
folder only exists after first launch. A packaged app also does not reliably inherit `PATH`,
so `"command": "docker"` is unsafe; use the absolute path to `docker.exe`. Both are now in
§7.

### Open items

- Two containers per session — understood, not yet mitigated. Cross-process token-refresh
  lock is Phase 2.
- Pass 2 phases 2–5 (rate limiter, cache, capability probe, web UI, first read tools) are
  planned and unstarted.
- The `vcrpy` cassette coverage §8 claims still does not exist.
