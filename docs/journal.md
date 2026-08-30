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

---

## 2026-08-30 (phase 2) — Rate limiter and cross-process lock. Cache deferred.

Phase 1 was verified end to end on the laptop first: the old image reports `pytest in image:
True`, the new one `False`; `server_version` reads `0.2.0+12e6960…`; a live authenticated call
against the new image returns the account name. Both halves of the pytest check were run,
because a single `False` would have been equally consistent with a broken probe.

### What phase 2 built

**`source/ratelimit.py` — a token bucket whose state lives in a file, not in memory.**
This is the direct consequence of the two-container finding. An in-process bucket is enforced
once per process, and there are two processes sharing one token volume, so the real rate
against Garmin would be exactly double the configured one. A limiter that permits twice what
it claims is worse than no limiter, because it invites trust it has not earned. State sits on
the shared volume so both containers draw from one allowance.

Default is one request every two seconds with a burst of four, overridable by
`GARMIN_RATE_PER_SEC` / `GARMIN_RATE_BURST`. The number still has **no empirical basis** and
the module says so in its own docstring.

**`source/lock.py` — a cross-process `flock`.** garminconnect refreshes tokens inline at the
top of every data request and dumps the result to disk, guarded by a `threading.Lock` that
means nothing between containers.

Two details that were easy to get wrong, and would have produced a lock that looked fine:

- **The lock must not be on the token file.** `dump()` writes a temp file and `replace()`s it,
  so the token file gets a new inode on every refresh. `flock` binds to the open file
  description, not the path — a lock held there would, after the first refresh, be held on an
  unlinked inode and exclude nobody. Separate `.garmin.lock`, never replaced.
- **It must be re-entrant within a process.** `flock` is per open file description, so a
  second acquire in the same process blocks against the first. Depth-counted instead.

Both are reached through one doorway, `_garmin_access()` in `client.py`: lock first, then
budget. The other order would let a process spend a token and *then* queue on the lock, so
tokens would be spent by waiting rather than by requesting.

### The cache was deferred, deliberately

§6 called for a short-TTL cache in this phase. Its only possible consumer today is
`garmin_auth_status`, and that tool must never be cached — its entire job is proving the
tokens work *right now*, so a cached answer makes it vacuous. Building it now meant inventing
TTLs before a single data endpoint had been seen. It moves to phase 5, alongside its first
real consumer. §6's placement rule is untouched and still binding: the cache lives in
`source/`, and no tool implements its own.

### Testing the claim, not the code

Both modules assert cross-process behaviour, so both are tested with real subprocesses. An
in-process test here would prove nothing — `threading.Lock` would pass it, and
`threading.Lock` is precisely the thing garminconnect already has and which does not help.

The rate-limit test merges the grant timestamps from two child processes and measures the
spacing of the merged sequence, which is the global rate directly, independent of how the two
happened to interleave.

That test was then **shown to fail**: re-run with a separate state directory per process —
simulating the in-memory bucket — six requests completed in 0.21s against a required 0.35s,
and both assertions failed. A passing test is not evidence until it has been seen to
discriminate.

Also added: an autouse fixture pinning a very high rate for every test that is not about the
rate. The shipped default is one request per two seconds, and a suite that is slow for a
reason nobody remembers is a suite somebody weakens later.

Tests: 26 → 39.

### Open items

- Cache: phase 5.
- Capability probe: phase 3, unstarted. Still the precondition for fixing the tool surface.
- The two-writer risk remains **structural, not observed** — tokens have not needed a refresh
  yet, so the concurrent path has never actually run. The lock is insurance bought before the
  fire.
- `pytest>=8,<9` still blocks the 9.0.3 that clears the dev-side CVE.

---

## 2026-08-30 (phase 2b) — `selftest`, and a lock that blamed the wrong thing

### The bug writing `selftest` exposed

`lock.py` shipped in PR #5 with the retry loop catching bare `OSError`:

```python
except OSError:
    if time.monotonic() >= deadline:
        raise LockTimeout(...)
    time.sleep(_POLL_INTERVAL_S)
```

`flock` signals two entirely different conditions through that one base. It raises
`BlockingIOError` when the lock is genuinely held by someone else — and `OSError` with
`ENOLCK`/`EINVAL` when **the filesystem cannot do advisory locking at all**. Some network
and container volume drivers cannot.

Treating them alike meant a volume without locking would poll for the full 30 seconds and
then report *"Another instance of this server is busy."* Precise, actionable, and wrong: it
sends the operator hunting a container that does not exist, while the truth is that
cross-process protection was never active. The one thing the lock exists to guarantee would
be absent, and the error message would argue it was present.

Now `BlockingIOError` means contention and everything else raises `LockUnsupported`, which
says plainly that protection is not active on this volume. Tested both directions, because a
fix that turned every failure into `LockUnsupported` would pass the new test while breaking
the old behaviour.

This matters right now rather than theoretically: the next thing to happen is a real
two-container test on a **Docker named volume**, which is precisely where flock support is
worth verifying instead of assuming.

### `garmin-mcp selftest`

The cross-process claims — one global budget, real mutual exclusion — cannot be tested from
inside one process. `threading.Lock` would pass any such test, and `threading.Lock` is
exactly the thing garminconnect already has and which does not help across containers.

`selftest` acquires the lock and budget N times and prints one `GRANT <label> <n> <epoch>`
line per grant to stdout, so two containers' output can be concatenated and sorted. It makes
**zero Garmin calls**, needs no token file, and can therefore be run repeatedly against a
live account with no risk and no rate-limit cost.

It also probes locking **by actually taking a lock**, not by checking that `fcntl` imports —
that only proves we are on a UNIX, and it is the volume that varies. If the probe fails it
exits non-zero and emits no `GRANT` lines, so it cannot report a budget it never exercised.

### pytest ceiling widened

`pytest>=8,<9` → `>=9.0.3,<10`, clearing PYSEC-2026-1845 (predictable `/tmp/pytest-of-{user}`
paths; local DoS or privilege escalation, through 9.0.2). Dev-only and never shipped, but
`pip-audit` gates CI and a standing advisory teaches people to skim the output.

Measured before changing it: the suite passes unchanged on 9.1.1.

Tests: 39 → 49.

---

## 2026-08-30 (phase 3) — Capability probe

Phase 2 was verified in the real containers first: `flock : SUPPORTED` on the Docker named
volume — the load-bearing assumption behind the whole locking design — and two containers
running four grants each produced eight grants, span 8.00s, gaps `0, 0, 0, 2.00, 2.00, 2.00,
2.00` at the shipped 0.5 req/s. Per-container buckets would have shown a span near zero. The
budget is shared. No Garmin calls were spent proving it.

### `garmin-mcp probe`

26 candidate endpoints across health, training, body, daily, activity and device. §5 requires
the tool surface be trimmed to what this account and this watch actually return, and a spec
sheet is not evidence about an API.

**It needs no delay of its own.** The original plan called for a manual 2s sleep between
endpoints; the phase 2 rate limiter already does exactly that, so the probe calls through
`_garmin_access` and inherits the spacing. About a minute for a full run — which is why it is
a CLI command and never a tool.

**It never prints or stores a value.** The question is a boolean. A diagnostic is an absurd
reason to put sleep scores and heart rates into a terminal, its scrollback and a JSON file
(§8). Verdicts and a count of populated fields are all that leave it.

**The hard part was telling "no data" from "not supported".** Garmin answers an unworn day
with a well-formed envelope: the date echoed back and every measurement null. `bool(payload)`
is not imprecise there, it is *inverted* — it reports data for a response containing none.
So `count_populated` walks the payload ignoring echoed request parameters and identifiers,
and counts meaningful leaves. Zero and `False` count as measurements; a zero-step day is a
reading, not an absence.

Anything that still looks empty is retried on further dates, and the offsets are **spread
(1, 3, 7) rather than consecutive** — three days running can all be days the watch was not
worn. Only the empties are retried, which matters against an account that has already been
rate limited once today. A 404 is reported distinctly, as the only reliable signal that an
endpoint does not exist for this account.

A `RateLimited` aborts the entire probe rather than being recorded per-endpoint. Carrying on
through a 429 across 26 endpoints is the retry storm §6 exists to forbid.

### Two bugs the dry run found that the tests did not

**`write_capabilities` never created its parent directory.** The unit test used `tmp_path`,
which always exists, and `/data` always exists inside the container — so both hid it. Running
the real command against a fresh directory surfaced it immediately. Fixed, with a test that
writes into a directory that does not exist.

**Progress reported a stale date on retries.** An empty retry does not replace the stored
result, and the progress callback was handed the stored one — so the second pass printed the
*first* date beside a probe of a later one. The verdict was right and the provenance was
wrong, which is the sort of thing that gets believed. Now reports what the current pass saw.

Tests: 49 → 67.

### Open

- The probe has **not been run against the real account yet.** The tool surface stays unfixed
  until it has.
- Cache still deferred to phase 5.
- Two-writer token race still structural, not observed.

---

## 2026-08-30 (phase 3 results) — The probe ran. 17 usable, 1 self-inflicted, 8 unresolved.

First real run against the account, image `sha-b5a3069`. Counts and verdicts only — no
readings are recorded here or in `capabilities.json`.

### Settled

**17 endpoints returned data**, and the field counts corroborate rather than just pass a
threshold: stress 1935, respiration 1501, activity_types 923, steps 576, primary_device 322,
devices 304, personal_records 181, activities 142, daily_stats 41, user_summary 41,
training_status 25, fitness_age 11, body_battery 10, intensity_minutes 8, weigh_ins 2,
body_composition 2, sleep 2.

**The FR570 does produce Body Battery** (10 fields). That was one of the four device-tier
unknowns §5 named, and it is now answered by measurement rather than a spec sheet.

### Self-inflicted

**`race_predictions` — the `ValueError` is ours.** The library ends the method with

```python
raise ValueError("you must either provide all parameters or no parameters")
```

It takes zero arguments or all three; the probe passed two, because the candidate is
classified `RANGE` and should be `NONE`. **That endpoint has never actually been tested.**

### Not trusted, deliberately not concluded

**`sleep` returned 2 fields.** Beside stress at 1935 and respiration at 1501 that is not a
sleep payload — it is a nearly-empty response that cleared a "greater than zero" threshold.
The verdict says `HAS DATA`; the count disagrees. Recording the count is what made this
visible, and it is an argument for the verdict being a judgement rather than a boolean.

**`resting_hr` is empty while `daily_stats` and `user_summary` return 41 fields each**, and
those almost certainly contain resting heart rate. The data plausibly exists for that date
and `get_rhr_day` is not the way to reach it — a wrong call shape rather than an absent
metric. Unverified either way.

**Empty across all three spread dates:** `hrv`, `resting_hr`, `spo2`, `training_readiness`,
`max_metrics`, `endurance_score`, `hill_score`, `floors`.

Some are plausible on their face — Pulse Ox ships disabled on Garmin watches to save
battery, and VO2 max needs a qualifying activity. **No conclusion is being drawn.** Declaring
"this watch does not do HRV" on this evidence would be exactly the confident-and-wrong claim
the counting heuristic was written to avoid, and it would permanently cut a tool.

### What would resolve it

A structure-only diagnostic: the response's **key paths and value types, never values**.
`restingHeartRate: <int>` establishes the shape; `restingHeartRate: 52` would be a reading in
a terminal scrollback. Key names are not health data, so this stays inside §8.

That separates the two cases currently indistinguishable: *the endpoint returned an empty
container* versus *it returned data our counter walked past*.

Planned as `garmin-mcp probe --explain <key>`, **not yet built**.

### State at pause

`main` = `b5a3069`, image `sha-b5a3069`, 67 tests. Phases 1–3 merged. `capabilities.json` is
written on the laptop volume and reflects the run above, including the two verdicts we do not
believe — it should not be treated as final until `--explain` has run.

---

## 2026-08-30 (phase 4a) — The credential UI, and a laptop with nothing to lose

### Why this and not `probe --explain`

The working laptop changed mid-session. The Docker Desktop problem from the first entry is
resolved after the reboot that never happened at the time, and that machine has **no token
file and no backup** — the state §7a's login form exists for. `probe --explain` still
matters and is still unbuilt; it just stopped being the thing the hardware was pointing at.

### The sequencing, which mattered more than the code

There is no token backup anywhere. So the first credential login on that laptop is a
one-shot event: it runs the exact path Garmin 429'd on a first attempt from the other
machine, a captcha lockout has no workaround by design, and the file that would be the way
back does not exist yet.

Spending that attempt on never-live-tested web code would have been the wrong risk, so the
order is: **CLI bootstrap first** (shipped, proven end to end), back the volume up
immediately, and only then re-authenticate through the new form with insurance in hand.
The build itself cost nothing to verify — the entire UI is tested against injected seams,
so 126 tests run without a token file, without a network, and without a single request
against an account that has already been throttled once today.

### §7a says to bind 127.0.0.1, and that is wrong in a container

"Bind `127.0.0.1` only. Never `0.0.0.0`" is right on a bare host and unimplementable in a
container: a process bound to loopback inside the container's own network namespace is
unreachable through a published port, so following §7a literally produces a UI that does
not work. The guarantee has to live in the publish spec — `127.0.0.1:8765:8765` — exactly
as the existing `serve --http` path already documents for the MCP transport.

The consequence is not cosmetic. It means the **Host allowlist is the real in-container
backstop**, not merely a DNS-rebinding defence, so it is not optional and cannot be traded
away later for convenience. The CLI still defaults to `127.0.0.1`, because the safe case
should be what you get by typing nothing, and it prints a warning when told otherwise.

§7a needs amending to say this. That is a plan-doc change and owes the full 10-dimension
stress test, so it gets its own session rather than a quiet edit here.

### A CSS rule made three security assertions vacuous

`test_a_missing_field_leaves_the_button_usable` — the *negative* test, asserting the button
is **not** disabled after an ordinary typo — failed on first run. The reason was the
stylesheet: `button[disabled] { opacity: .5 }` contains the substring `disabled`, so
`assert "disabled" in response.text` was true of every page ever rendered, including pages
with a perfectly live button.

Three assertions about the single most important UX property on the form — that a 429 or a
captcha disables submit, because pressing it again is what extends the block — were
passing without testing anything. They now match `<button type=submit disabled>`. The only
reason this surfaced is that a negative case was written alongside the positive ones; three
green positives would have looked exactly the same.

### Every guard was watched failing

Each of the four load-bearing checks was disabled in turn and the suite re-run:

| Guard removed | Result |
|---|---|
| Host allowlist | 8 failed |
| Same-origin check | 3 failed |
| CSRF validation | 3 failed |
| `disable_submit` | 3 failed |

Restored, 126 pass. A security test that has never been seen to discriminate is decoration.

### What the UI does and does not do

**Status page** — tokens present, when written, permissions (`0600` flagged in red when it
is not), `flock` support probed by *taking a real lock*, the budget in force, and the
running build. It makes **no Garmin call**, which is what makes it free to reload while you
are fixing something; a diagnostic page that could rate limit the account it is diagnosing
would be self-defeating. It never renders token contents.

**Login form** — the same exchange as the CLI, with the password never echoed into a
re-rendered form (the email is, so a typo does not mean retyping both) and never logged.

**MFA is not handled**, deliberately. The account has no MFA today; if Garmin asks for a
code the page says so and names the CLI, which does implement the prompt. A page that
pointed at itself would be pointing at a route that does not exist (§4).

### Also

- `web/` is the second adapter over `source/`, which is the first real pressure on §11.
  `tests/test_layering.py` now enforces it by walking the AST rather than grepping —
  `cli.py` and `web/app.py` both import lazily inside functions, so a grep for top-level
  import lines would give a clean bill of health to a file that reaches past the boundary
  in every handler.
- No new shipped dependency. `starlette`, `uvicorn` and `python-multipart` were already in
  `requirements.lock` via `mcp`. They are now declared explicitly in `pyproject.toml`: a
  direct dependency satisfied only transitively breaks silently the day the intermediary
  drops it. Lock unchanged, `pip-audit` still clean.
- The compose volume is `external: true`, so `docker compose down -v` cannot delete the
  tokens. Without a backup that would mean a fresh credential login, which is the one
  operation that can end unrecoverably.
- No Docker socket, anywhere. Auto-start was never on the table.

Tests: 67 → 126.

### Open

- **Not yet run on the laptop.** Everything above was verified on the staging box: the real
  server bound to `127.0.0.1` (confirmed with `ss`), a forged `Host` refused with 403, no
  `Access-Control-*` header on any response, and the status page correctly reporting a
  machine with no tokens. The compose path and the live login are next.
- `errors.py::BOOTSTRAP_COMMAND` still names the CLI. §4 requires it name a route that
  currently exists; it switches to the compose command once the UI has been used for real.
- Tool selection and `tools.json` — phase 4b. It reads `capabilities.json`, which exists
  only on the other laptop's volume.
- `probe --explain` still unbuilt; the eight unresolved empties stay unresolved.
- `race_predictions` is classified `RANGE` and should be `NONE` — a real one-line bug the
  probe run exposed, kept out of a web-UI branch on purpose.
- Cache still phase 5. Two-writer token race still structural, not observed.

---

## 2026-08-30 (phase 4a, follow-up) — The MFA branch was dead code

Mahi chose to skip the CLI bootstrap and do the first login on the new laptop through the
web form instead. That put weight on a path no test had ever run: every web test injects a
fake `bootstrap`, which is right for testing the form and wrong for testing the wiring
underneath it. Reading it before the live attempt found the branch broken.

**`raise` from `prompt_mfa` does not come back as itself.** garminconnect calls the
callback inside `resolve_mfa`, deep in the strategy loop of `client.login`. Nothing there
re-raises our type; it crosses the loop, crosses `Garmin.login`, and lands in
`bootstrap_login`'s broad `except Exception`, which hands everything to `_translate`.
`_translate` knows only the library's vocabulary, so an unrecognised exception becomes
`SourceUnavailable(str(exc))` -- and `str(MfaRequired())` is the empty string.

So `except MfaRequired` in the web layer was unreachable, and the operator would have been
shown:

```
Could not reach Garmin Connect:
```

An empty reason, in the one situation where the message has real work to do: the web flow
genuinely cannot collect a code, and the CLI genuinely can. Printed from the actual code
path rather than reasoned about, then re-checked after the fix.

The answer is now read from a flag the callback sets before raising, not from the
exception type, which is robust to how `bootstrap_login` re-types things. Reverting to the
type-based catch fails two of the new tests.

`tests/test_web_bootstrap_wiring.py` exercises `_default_bootstrap` for real -- through
`bootstrap_login`, `_garmin_access` and `_translate` -- replacing only
`garminconnect.Garmin`, the one thing that would reach the network. It also pins the
things that had to keep working across the extra layer: a login 429 still arrives as
`RateLimited` (the type the form keys on to disable submit), tokens are still written
`0600`, and a failed login leaves no token file behind, since a partial one would make the
status page report "tokens present" for something that cannot authenticate.

Tests: 126 -> 133.

---

## 2026-08-30 (phase 4a, follow-up 2) — A security header that disabled a security check

First real use of the login form, on the laptop, against `sha-a04b05b`. Pressing Sign in
returned:

```
Refused: this request did not come from this page.
```

The form refused its own submission. No Garmin call was made -- the middleware rejects
before routing -- so the one-shot login attempt was not spent, which is the only reason
this was a nuisance rather than a real cost.

### The conflict

`_SECURITY_HEADERS` set `Referrer-Policy: no-referrer`. Per Fetch, appending the `Origin`
header to a non-CORS request whose method is not GET or HEAD switches on the referrer
policy, and for `no-referrer` it sets the serialized origin to **`null`**. No `Referer` is
sent either, by definition. So the browser posted `Origin: null` and nothing else, the
same-origin check had nothing it could match, and the request was refused.

Two defences that are individually correct and jointly unsatisfiable. The header was
chosen for one reason and the check for another, and nothing in between them knew both.

### Why no test caught it

Every test in `test_web_security.py` sets `Origin` by hand, because that is the only way to
exercise a rejection. `TestClient` does not derive the header from a referrer policy the
way a browser does, so the two settings never met in a test -- and there is no test at that
level that could have made them meet. A real browser found it on first use.

The tempting fix was to accept `Origin: null`. That would have removed the check rather
than repaired it: `null` is also what a sandboxed iframe and a cross-origin redirect send.
It is now refused explicitly.

`Referrer-Policy: same-origin` sends a real Origin and a full Referer for our own requests
and neither for anybody else's -- the exact distinction the check is trying to draw, and
the loosest policy that works. Nothing leaks: CSP is `default-src 'none'`, so there are no
third-party requests to leak to.

### Verified against a running server, not only in tests

Browser-shaped headers under `same-origin` reach the success page (200). `Origin: null` is
refused with the identical message the laptop saw, so the failure was reproduced before it
was fixed. A cross-origin POST is still refused. The fix does not widen what is accepted;
it makes the accepted set reachable.

Tests: 133 -> 136.
