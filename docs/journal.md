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

---

## 2026-08-30 (phase 4a, follow-up 3) — Every noun in the error was wrong

Second attempt at the form, on `sha-21829cc`. The same-origin fix held: the POST reached
the handler and a real login was attempted. Garmin rejected it, and the page said:

```
Garmin rejected the stored tokens at /data/garmin_tokens.json. They have expired
or been revoked. Re-run the bootstrap to get new ones: docker run ... login
```

On a machine with no token file, to somebody who was running the bootstrap, with lockout
advice attached and the submit button disabled. Every noun was wrong and the prescribed
action was the one already in progress.

### One exception, two meanings

`GarminConnectAuthenticationError` is raised on both of our call paths.

On a **token load** it means the stored tokens are no longer good -- `AuthExpired`, and
naming the file and the backup is exactly right. On a **credential login** the library
raises the same type for `INVALID_USERNAME_PASSWORD` ("401 Unauthorized (Invalid Username
or Password)") and stops the strategy chain immediately rather than trying the rest.

Nothing on the exception distinguishes them. The only thing that can is which of our own
calls was in flight, so `_translate` now takes `during_login`. Opt-in, because `_connect`
is the common path and a default of True would mislabel every genuine expiry -- the mirror
mistake, and just as bad: it would tell somebody to check a password when the fix is to
re-authenticate.

Three defects, one line of code:

1. **Wrong type.** A typo reported as an expiry.
2. **The library\'s own message was discarded.** `AuthExpired(path)` never carried
   `str(exc)`, so "401 Unauthorized (Invalid Username or Password)" -- the one line that
   said what actually happened -- was thrown away at the boundary. That is why the cause
   could not be stated with certainty after the fact.
3. **Wrong advice.** The web layer attached rate-limit advice and disabled submit for
   *every* `GarminSourceError`. A wrong password is the one authentication failure that is
   not a lockout; the recovery is to fix the field and press the button again. Disabling it
   makes an ordinary mistake look like an outage, and devalues the times it is disabled for
   real.

§3\'s table has said "INVALID_USERNAME_PASSWORD | Wrong credentials | Report plainly" since
v1.0. The code had never done it, and nothing noticed until a real password was typed.

`InvalidCredentials` now names `connect.garmin.com`: signing in there is free and settles
"is it the password or is it us" without spending an attempt against an IP this project has
already seen throttled. The CLI gets the same treatment -- reported plainly, no captcha
advice bolted on.

### What it cost

An auth rejection is much cheaper than a 429 or a captcha: the library stops the strategy
chain on the first one instead of burning all four. One rejected login, no lockout risk.

Tests: 136 -> 152, including the mirror cases -- an expired token must still say
`AuthExpired`, no token file must still say `NotBootstrapped`, and a 429 must still disable
the button. Three states, three messages; the bug was two of them collapsing into one.

### The release loop was being used as a debug loop

Three bugs, three PR-merge-CI-pull cycles. The bugs were real and each lived in a seam no
test could reach -- garminconnect\'s exception types, the browser\'s header derivation, and
now one exception with two meanings. First real use was always going to find them.

What was wrong was the cost of each discovery. `WORKDIR /app` puts `/app` first on
`sys.path`, so a bind mount at `/app/garmin_mcp` shadows the installed package and code
changes take effect on container restart with no rebuild:

```powershell
docker run --rm -p 127.0.0.1:8765:8765 -v garmin-tokens:/data `
  -v "${PWD}\\garmin_mcp:/app/garmin_mcp" ghcr.io/...:<tag> web --host 0.0.0.0 --port 8765
```

The laptop clones the repo, pulls a branch, restarts the container. Merging goes back to
recording a result rather than being part of finding it.

---

## 2026-08-30 (phase 4a) — Signed in through the web UI. Whole chain proven.

The first credential login on this laptop went through the **web form**, not the CLI. The
CLI-first sequencing recommended earlier was declined; recorded as a decision, and it came
out fine.

### What the run settled

`garmin_auth_status`, called over stdio against the pinned image, returned
`authenticated: true` with a fresh token timestamp and a `server_version` matching the tag
that had been pulled. Tokens on the volume, container, stdio, MCP handshake, live
authenticated Garmin call -- the whole chain, on the intended host.

Two facts not previously recorded:

- **`full_name` is populated.** Only `display_name` is the GUID. The account does have a
  human name available, which matters for the eventual read surface.
- **The account is metric**, re-confirmed on a second machine. §5\'s units contract now
  rests on a fact observed twice rather than once.

Values are deliberately not recorded here; this repository is public.

### The first login attempt failed, and the container log said why

The earlier refusal was not a typo. Output from the successful run carried:

```
mobile+cffi returned 429: IP rate limited by Garmin
mobile+requests returned 429: IP rate limited by Garmin
```

Both mobile credential strategies were throttled from this IP; a later strategy got
through. The same pattern the other laptop showed, now confirmed on a second network --
§10\'s partial finding holds: **the 429 risk sits on the bootstrap login paths, not on
serving.**

It also vindicates narrowing the credential verdict an hour earlier. Had
`GarminConnectAuthenticationError` been mapped wholesale onto "check your password", this
failure -- which had nothing to do with the password -- would have been reported as a typo,
and the operator would have gone off to reset a credential that was already correct.

### `serverInfo.version` was empty

Visible in the raw handshake. `MCPServer("garmin")` was constructed without a version, so
the field a client displays before calling anything was blank: a server could show as
connected while saying nothing about *which* build had connected. That is precisely the gap
`_server_version` exists to close, left open at the one place it is cheapest to read. Now
passed to the SDK, with a test.

### Claude Desktop cannot use it on this machine

Config verified valid, in the file the app actually reads (the MSIX-redirected path, found
programmatically rather than guessed), with the absolute `docker.exe` path -- which turned
out to be a **per-user install under `AppData\\Local\\Programs`**, not `Program Files`. The
documented advice to use an absolute path was right; the commonly assumed value would have
been wrong, and would have failed silently.

The app never attempted to launch the server: the MCP log directory stayed empty, and
Settings offers no Developer toggle, no Connectors section and no Extensions section. So
this build does not expose local MCP servers at all. Nothing on our side is wrong and
nothing on our side can fix it. **Open, and not a blocker** -- the server is verified
working over stdio by hand.

### A verification detour worth writing down

Piping the three JSON-RPC messages into `docker run -i` from PowerShell does **not** work,
and fails in a way that looks exactly like a hang in our code. PowerShell buffers output
into a native command\'s stdin, so the messages arrive together with EOF immediately behind
them; the server answers `initialize` instantly, starts the tool call, and is shut down by
EOF before the live Garmin round trip can finish. Lengthening a `Start-Sleep` inside the
pipeline does not help -- the sleep is inside the buffered block, so the timing is
unchanged.

Running `docker run -i` interactively and pasting each line keeps stdin open as long as the
window is, and works first time. Worth knowing before somebody concludes the tool hangs.

Tests: 164 -> 165.

---

## 2026-08-30 (phase 4a, follow-up 4) — Connected to Claude Desktop. It was the restart.

`garmin_auth_status` now answers inside a real Claude Desktop conversation: authenticated,
metric, live call, tokens refreshed today. The full chain works through the intended client,
not just by hand over stdio.

### An hour lost to looking in the wrong place

The server showed as **`garmin · Desktop · Local dev · Connected` under Settings →
Connectors**, alongside the web connectors. We spent the whole search on **Settings →
Developer**, which has a "Local MCP servers" section that said *"No servers added"* the
entire time — and still did while the server was connected and working.

So on this build, the Developer page's server list is not where a config-file server
appears. Connectors is. Anyone debugging this by reading the Developer page will conclude
the config was ignored, which is what happened here.

### The evidence that misled, and what it was actually worth

The diagnosis ran down a chain of plausible causes, and most of the readings were weaker
than they looked:

- **Empty MCP log directory.** Read as "the app never tried to launch it". Wrong: this
  build writes nothing to that directory at all, not even a `main.log`, so it was evidence
  of nothing. Stated too confidently at the time.
- **No Developer toggle, no Connectors, no Extensions.** Reported as absent; Connectors did
  exist and was where the answer was. A UI question asked from a Linux box about a Windows
  app, answered by someone scanning quickly, is a lossy channel — the fix is to ask "tell me
  everything on this page" rather than "is X present", which is what eventually worked.
- **Org policy.** Ruled out properly, and cheaply: `extensions-blocklist.json` was empty
  and `dxt:allowlistEnabled:<org>` was `false`. Worth doing — a managed account was a real
  hypothesis given `orgWorkAcrossAppsDisabled` in the config.
- **Stale build.** Ruled out: `first_launch_at` decoded to the same day.

The actual cause was that the earlier "fully quit from the tray" did not take effect. The
timestamps could not distinguish "restarted after saving the config" from "still running
since before", and that ambiguity is what made every other reading look meaningful.

**The cheap check that resolved it was the one already in the docs**: quit from the tray and
reopen. It was performed once, believed, and not repeated. When a config-driven feature does
not appear, re-doing the restart costs ten seconds and should come before any forensics.

### Also confirmed

`claude_desktop_config.json` at the MSIX-redirected path **is** read by this build — the
config we wrote is the one that worked. The MSIX path finding and the absolute `docker.exe`
requirement in §7 both held. The Docker path being a **per-user install under
`AppData\Local\Programs`** rather than `Program Files` is new, and is exactly why §7 says to
resolve it rather than assume it.

### Note on the tool surface

The Desktop session correctly observed that only `garmin_auth_status` is exposed, and
speculated the server might "still be loading more tools". It is not: there is one tool by
design (pass 1), and the data tools are gated on the capability probe. Worth recording
because that speculation is the natural reading, and the next person to see it will assume
something is broken.

### Open

- §7 needs the Connectors-not-Developer finding and the per-user Docker path. That is a
  plan-doc amendment and owes the 10-dimension pass, so it rides with the §7a bind-address
  correction in a session of its own.
- Everything else unchanged: `probe --explain`, `race_predictions`, phase 4b, phase 5.

---

## 2026-08-30 (phase 5a) — `probe --explain`, and an endpoint we had never tested

Asked for real data tools. This is the step before them, and it is not a detour.

### Why the tool could not be written yet

§5 requires units on every numeric and a hard result cap, so `garmin_health` cannot hand
back Garmin's payload — **stress alone returned 1935 populated fields**, which would destroy
a conversation's context window on one call. Our layer has to define the shape: a named
subset, each field with an explicit unit.

That means knowing what the fields are called. The probe deliberately never recorded them —
it counts leaves and prints nothing (§8) — so the names had to be learned before the
extractor could be written. The alternative was guessing them from library source, which is
the same guessing that cost three release cycles earlier today.

### What it answers that a count cannot

A zero from `count_populated` is consistent with two opposite situations:

    the endpoint returned an empty container    -> the watch has no such data
    it returned data our counter walked past    -> we are calling it wrong

Eight endpoints are stuck on exactly that ambiguity. Declaring "this watch does not produce
HRV" on the strength of a zero would permanently cut a tool that works.

`describe_shape` emits key paths and value **types**. `restingHeartRate: int` establishes
the shape; `restingHeartRate: 52` would be a reading in a terminal, its scrollback, and
anywhere the output gets pasted. Key names are schema, not health data, so this stays inside
the §8 line the probe already holds.

Three decisions that were not obvious:

- **`null` leaves are reported, not skipped.** `restingHeartRate: null` against the key
  being absent entirely *is* the distinction being drawn. Dropping nulls — the natural
  instinct, since they are not data — would throw away the answer.
- **Repeated siblings collapse.** A list prints its length and the shape of its first
  element only. Without it, stress would emit ~1440 lines and the diagnostic would be as
  unreadable as the payload it explains.
- **Data-shaped dict keys collapse to `<key>`.** A payload keyed by date would otherwise
  print one line per day, so the output would grow with the size of the account rather than
  with the shape of the response — and would leak a set of dates for no benefit.

Verified by removing the redaction: emitting values instead of type names fails 9 of the 20
tests. A privacy guarantee that has never been observed to fail is an assumption.

### `race_predictions` had never actually been tested

Confirmed in library source: `get_race_predictions` accepts **zero arguments or all three**
and raises `ValueError` for anything between. The candidate was classified `RANGE`, so
`_call` passed two — and it failed before issuing a request. The ERROR recorded against it
in the first probe run was entirely our own.

Now `NONE`, with a test pinning the classification and a second test asserting the real
library still rejects two arguments, so a future release that accepts them fails loudly
rather than silently making the comment wrong.

### Cost, deliberately

Stops at the first date that yields a populated payload, so the common case is one request
per endpoint rather than three. Runs through `_garmin_access`, inheriting the shipped
spacing. An unknown key refuses the whole run rather than probing the valid ones first —
partial execution would spend requests and then fail, which is the worst of both.

It writes nothing. `capabilities.json` has one writer and a read-only diagnostic must not
quietly become a second.

Tests: 165 -> 189.

### Next

Run it against the five confirmed health endpoints, then `garmin_health` gets built from
real field names rather than assumed ones.

---

## 2026-08-30 (phase 5a results) — The probe was wrong about sleep, and HRV was never missing

First `--explain` run, against the real account, date 2026-08-29. Schema only — field names
and types, no readings, which is why the output could be pasted around freely.

### The counting heuristic was measuring the wrong thing

**`sleep` returned 4589 populated fields.** The first probe run recorded **2**, and that
verdict was explicitly recorded as one we did not believe — 2 fields beside stress at 1935
is not a sleep payload. It was right not to believe it: the count was measuring an unworn or
partial day, not the endpoint.

Had the number been trusted, sleep would have been cut or stubbed. Recording the count
alongside the verdict is what made the disagreement visible, and refusing to conclude from
it is what left the door open.

### Three metrics were never missing. We were asking the wrong endpoint.

This is the case `--explain` was built for, and it landed on the first run.

- **HRV.** `get_hrv_data` was empty across three spread dates. The sleep payload carries
  `hrvData: list(115)` with `.value: float`, plus `avgOvernightHrv: float` and
  `hrvStatus: str`.
- **Resting heart rate.** `get_rhr_day` empty; `restingHeartRate: int` sits in the sleep
  payload. This was suspected — `daily_stats` and `user_summary` returning 41 fields each
  made "the data exists and we are not reaching it" the likelier reading — and is now
  confirmed rather than suspected.
- **Respiration and body battery** are in there too, as `wellnessEpochRespirationDataDTOList`
  and `sleepBodyBattery`, independent of their own endpoints.

Declaring "this watch does not produce HRV" on the strength of a zero would have permanently
cut a working metric, and nothing downstream would ever have questioned it. That single
outcome pays for the whole diagnostic.

**Also found: skin temperature** — `avgSkinTempDeviationC`, `avgSkinTempDeviationF`,
`skinTempDataExists`. It is not in `CANDIDATES` at all, so no probe run could have found it.
The candidate list is a guess about what to ask for, and this is the first evidence of what
that guess missed.

### Two facts that constrain the extractor

**The big arrays are positional, and a sibling list names the columns.**

    stressValueDescriptorsDTOList[0].index / .key
    stressValuesArray[0]: list(2)

So each entry is `[<col0>, <col1>]` and the descriptor list says which is which. Hardcoding
"index 0 is the timestamp" would work today and break silently later — and the trap is
already visible: **`bodyBatteryValuesArray` is `list(4)` inside `stress` and `list(2)`
inside `body_battery`.** Same name, different width, same account, same day. The extractor
must read the descriptors.

**Every payload carries GMT and Local timestamps** — `startTimestampGMT` beside
`startTimestampLocal`, throughout. §5 requires every date-bearing response to state what its
date means, and that can now rest on a fact rather than a choice.

**Units are readable off the field names**: `…Seconds`, `…Mins`, `…InMilliseconds`,
`…DeviationC` / `…DeviationF`. The §5 units contract does not need an invented mapping.

### Scale, which is the real design constraint

Sleep's series hold roughly 1,700 elements across ten lists (`sleepMovement` 701,
`sleepHeartRate` 291, `wellnessEpochRespirationDataDTOList` 291, `sleepStress` 194,
`sleepBodyBattery` 194, `hrvData` 115, `sleepRestlessMoments` 43, `sleepLevels` 24, ...).
Stress carries 480 samples, respiration 720. Returning any of that raw is exactly the
context-window destruction §5's hard cap exists to prevent.

## 2026-10-11 (phase 5b results) — Five of eight had data, and one of the five was lying

Six weeks after the 5a run. `--explain` against the eight unresolved endpoints, one date
(`--days 2`, 2026-10-09), eight requests. Schema only.

The gap matters to the reading: the cached tokens had aged out and needed a fresh `login`,
which took a 429 on both mobile paths before a fallback succeeded. The login path is
IP-rate-limited; the data path was not, and the probe ran clean.

### `hrv` and `resting_hr` work on their own endpoints. The 5a conclusion was wrong.

`get_hrv_data` returned 320 populated fields — `hrvSummary.lastNightAvg`, `weeklyAvg`,
`lastNight5MinHigh`, `baseline.{lowUpper,balancedLow,balancedUpper,markerValue}`, `status`,
`feedbackPhrase`, plus `hrvReadings: list(102)` of `{hrvValue, readingTimeGMT,
readingTimeLocal}`. `get_rhr_day` returned the reading at
`allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE[0].value`.

**5a said to source both from the sleep payload** "because that is where this account's data
measurably is". That instruction is now withdrawn: a 24-field summary beats mining the same
number out of sleep's 4589, and `lastNightAvg` is already the scalar the tool wants.

### The third category: empty can mean *not yet established*

HRV status needs roughly three weeks of consistent overnight wear before Garmin computes a
baseline. August-empty and October-populated is almost certainly that, not a device limit.

So an empty response has three meanings, not two: the watch was not worn, the device cannot
produce it, **or the metric has not matured yet.** A single probe run cannot tell them apart.
`endurance_score` and `hill_score` are the same shape of answer — full envelopes,
`enduranceScoreDTO: null`, `hillScoreDTOList: list(0)`, both accumulation-based. `max_metrics`
returned `list(0)`; VO2 max needs a qualifying activity, and `training_readiness` reporting
`acuteLoad` says there is activity but nothing qualifying.

**None of the three should be cut.** The tool must distinguish "endpoint answers, nothing
earned yet" from "unsupported", and `capabilities.json` verdicts have a shelf life its
current design does not acknowledge.

### `spo2` read HAS DATA with nothing in it, and the count was our bug

Verdict `HAS DATA`, 8 populated. Every reading was null — `averageSpO2`, `lowestSpO2`,
`avgSleepSpO2`, `spO2SingleValues`, `continuousReadingDTOList`, all of them. Reproduced
locally against `count_populated` from the pasted shape alone:

    count today      : 8
    counted keys     : sleepStartTimestampGMT, sleepEndTimestampGMT,
                       sleepStartTimestampLocal, sleepEndTimestampLocal,
                       tomorrowSleepStartTimestampGMT, tomorrowSleepEndTimestampGMT,
                       tomorrowSleepStartTimestampLocal, tomorrowSleepEndTimestampLocal

All eight are window timestamps. `_IGNORED_KEYS` named `starttimestampgmt` and
`endtimestampgmt` as literal strings, so every prefixed sibling walked past it. A full probe
run today would have written `spo2: has_data` into `capabilities.json`, and a tool would have
been built for a metric this watch never measures.

This is the 5a sleep false negative pointing the other way, and it is worse: the sleep
disagreement was visible because the count was recorded beside the verdict, and nothing about
`spo2` looked wrong at all.

**Fixed by suffix, not by name** — any key ending `timestampgmt` or `timestamplocal` is
envelope metadata. A per-reading time like `readingTimeGMT` inside `hrvReadings` does not
match, and there is a test pinning that it still counts.

### Two more constraints on the extractor

**`resting_hr`'s date is not a date.** Alone among the `DATE` candidates it answers with a
statistics window — `statisticsStartDate`/`statisticsEndDate`, and the reading nested under
`allMetrics.metricsMap` carrying its own `calendarDate`. The value must be matched against
the requested date rather than read from `[0]`. It is also a `float` here and an `int` in the
sleep payload: two sources, two types, and §5 forces the tool to name which it used.

**`training_readiness` root is `list(4)`** — four snapshots for one day, selected by
`timestamp`, not by index. Same trap as the positional arrays. It carries `hrvFactorPercent`
and `hrvWeeklyAverage`, making it a third HRV source after the endpoint and the sleep payload.

**`floors` confirms the descriptor rule**: `floorsValueDescriptorDTOList: list(4)` beside
`floorValuesArray: list(96)` of width 4, and `floorValuesArray[0][0]` is `str` while floor
counts are numeric — mixed-type columns, 96 fifteen-minute buckets, 384 values raw.

### Skin temperature cannot be probed, so the slot went elsewhere

5a's next-step said to add skin temperature to `CANDIDATES`. It cannot be done:
`garminconnect 0.3.11` exposes no skin-temperature endpoint across all 149 public methods. It
exists only inside the sleep payload (`avgSkinTempDeviationC/F`, `skinTempDataExists`) and is
a sleep-derived field, not a candidate.

The same diff found **79 `get_*` methods `CANDIDATES` never asked for.** Six were added:
`heart_rates` (daily HR — no candidate existed at all, while `resting_hr` had two sources),
`hydration`, `body_battery_events`, `morning_readiness`, `heart_rate_zones`, `stats_and_body`.
Nutrition, golf, badges and gear are out of a health probe's scope. The menstrual and
pregnancy endpoints are left alone deliberately — §8 is about not putting health data where it
does not belong, and their schema is not worth printing to satisfy curiosity.

A new test asserts every candidate's method exists on `Garmin`, which would have caught the
skin-temperature dead end without a session spent on it.

### Then two more requests, and VO2 max turned out not to be missing either

`--explain training_status,race_predictions`. Both answer. This is the 5a pattern for the
third time in two sessions, and it is now the default suspicion rather than a surprise.

**VO2 max is present.** `get_max_metrics` answers a single date with `list(0)`, and earlier
today that was read as "not earned yet — needs a qualifying activity". Wrong.
`training_status` carries `mostRecentVO2Max.generic.vo2MaxValue: float` and
`vo2MaxPreciseValue: float`, plus `maxMetCategory: int`.

**`race_predictions` confirms it independently** — `time5K`, `time10K`, `timeHalfMarathon`,
`timeMarathon`, all `int`, all populated. Those are derived *from* VO2 max, so they could not
exist without it. **And this is the first time that endpoint has ever returned anything:** the
5a `RANGE` -> `NONE` reclassification is proven, a month after the `ValueError` was diagnosed
as ours.

`max_metrics_range` was added as a candidate on the strength of this. If the range variant
answers where the single date does not, `max_metrics` is a call-shape bug of the same family
as `race_predictions` rather than an absent metric. One request settles it.

### `training_status` is rich, and shaped unlike anything probed so far

61 populated fields, pre-fix count. Three nested DTOs — `mostRecentVO2Max`,
`mostRecentTrainingLoadBalance`, `mostRecentTrainingStatus` — and two new classes of trap.

**Dynamic dict keys, keyed by device id.** The status lives at

    mostRecentTrainingStatus.latestTrainingStatusData.3612870660.trainingStatus
    mostRecentTrainingLoadBalance.metricsTrainingLoadBalanceDTOMap.3612870660.monthlyLoadAerobicLow

`3612870660` is a device id, not a field name. The extractor must walk the map and select on
`primaryTrainingDevice: bool`, which both DTOs carry. Hardcoding the key would work on this
account and no other, and would break on this one the day the watch is replaced. The
positional-array rule has a sibling: **never hardcode a map key either.**

**`trainingStatus` is an `int`, not a label.** So are `fitnessTrend`, `maxMetCategory` and
`recordedDevices[].category`. The human-readable text is in a separate field —
`trainingStatusFeedbackPhrase: str`, `trainingBalanceFeedbackPhrase: str`,
`acwrStatusFeedback: str`. We have no mapping from those codes to meanings, and inventing one
would be guessing at Garmin's enum. **The tool should return the phrase and treat the code as
opaque** unless a mapping can be sourced.

**5a's units claim needs narrowing.** It recorded that units are readable off the field names
(`…Seconds`, `…Mins`, `…InMilliseconds`). `time5K: int` is a counterexample: a race prediction
in unnamed units, almost certainly seconds but not stated. Likewise `timestamp: int` beside
the `…TimestampGMT` strings elsewhere — one payload, two time encodings. §5 needs a per-field
decision for these, not a naming convention.

Also: `mostRecentVO2Max.cycling: null` — running only. `fitnessAge` is null inside this DTO
while the `fitness_age` endpoint returned 11 fields in August, so that scalar has a source and
it is not this one. `recordedDevices[].imageURL: str` is a CDN link; nothing should pass it
through to a response.

### The sourcing map, which is the real output of 5a and 5b

Where each metric actually lives, after eleven requests across two sessions:

| Metric | Source | Not from |
|---|---|---|
| HRV | `get_hrv_data` — `hrvSummary.lastNightAvg` | sleep payload (5a said this; withdrawn) |
| Resting HR | `get_rhr_day`, matched on nested `calendarDate` | sleep payload's `restingHeartRate` |
| VO2 max | `training_status` — `mostRecentVO2Max.generic.vo2MaxValue` | `get_max_metrics` (`list(0)`) |
| Race predictions | `get_race_predictions`, no arguments | — |
| Training status | `training_status`, device-keyed, phrase not code | — |
| Skin temperature | sleep payload — `avgSkinTempDeviationC/F` | no endpoint exists |
| Respiration, body battery | own endpoints, also inside sleep | — |
| SpO2 | nowhere — this watch measures none | `get_spo2_data` (envelope only) |
| Endurance, hill score | nowhere yet — not accumulated | — |

Four of nine rows say the obvious endpoint is the wrong one. That ratio is the finding.

Tests: 189 -> 192. Candidates: 26 -> 33.

## 2026-10-11 (SCOPE v1.4) — A verdict is a measurement with a date on it

### Why this is a partial amendment

v1.4 covers §1 and §5 only — the sections that block phase 5c. §7's client-wiring corrections
(Connectors not Developer, the per-user Docker path) and §7a's bind-address correction are
still owed and ride in v1.5, which gets its own stress-test pass. Splitting was chosen over
one large version so the end-of-session review stays inside the time a review can actually be
given. The cost of the split is recorded below, not hidden.

### Three findings changed the plan, not just its wording

The ten dimensions ran against real code and two live `--explain` runs. Three came back with
something the draft had wrong:

- **The verdict vocabulary is four values, not three.** The draft proposed
  `has_data`/`no_data`/`not_matured`. The code already has four (`source/probe.py:115-118`),
  and `ERROR` is not redundant: `race_predictions` sat on it for a month because of a
  call-shape bug of *ours*. A three-valued scheme would have filed our own bug under "Garmin
  has no data" — the exact mistake 5a already made once.
- **A re-probe would have erased the evidence the amendment depends on.** Verdicts are
  overwritten in place, so a metric that read `has_data` in August and `no_data` today loses
  any record of having worked — and that record is the whole signal behind `not_matured`. A
  regressed verdict now keeps the previous verdict's date. This came out of pass B, walking
  the probe lifecycle; the ten dimensions did not surface it.
- **Nothing reads `capabilities.json`.** Grepped `garmin_mcp/`: a writer and a comment
  (`cli.py:263`, `cli.py:318`), no reader. `write_capabilities`'s docstring claims the web UI
  reads it to build the tool-selection list, and the web UI does not. One producer, zero
  consumers — so changing the vocabulary is free today and expensive after 5c, which is the
  argument for doing v1.4 now. It is also why August's wrong verdicts never did any damage.

The dating requirement turned out to be **already satisfied** — `generated_utc`,
`dates_probed` and per-endpoint `observed_on` are all written. Only the reader is missing,
which narrows item 3 of the previous resume list from "add dating" to "add a consumer".

### What §1 now says the thing is for

**Planning and adjusting running training**, not "exposing Garmin data" in the abstract. That
sets the §5 priority order — load, readiness and per-run detail outrank weight and body
composition — and it makes a cut metric a cost rather than a tidy answer. With it comes a new
non-goal: **no medical or clinical interpretation.** The framing invites "is this HRV a
problem" about special-category health data that looks clinical enough to answer; the server
reports measurements, their units, and Garmin's own phrasing where Garmin gives one.

### Deferred on purpose, recorded so the split cannot lose it

- **Per-run detail carries GPS.** `get_activity_details` returns polylines; a run history is a
  precise record of where Mahi lives and when he is out. §9 treats exposure as credentials and
  health data and says nothing about location, and v1.4 is the first amendment that makes
  activity detail a priority. Owed to §9 in v1.5.
- **The §6 cache must key on the fetched payload plus the map version**, not on metric+date, or
  two metrics sourced from one payload fetch it twice and a re-pointed map keeps serving the
  old source's answer.

### §7a is knowingly wrong for one more version

§7a still says bind `127.0.0.1`, never `0.0.0.0`, while the shipped container does the
opposite for a reason understood since phase 4a. Accepted as the price of the split and
written here so it is not rediscovered as a bug.

### One inference, flagged rather than absorbed

"HRV status needs ~3 weeks of wear" is domain knowledge, not something measured. The
August→October change is consistent with it, but firmware, wear pattern or an account setting
fit the same evidence. Marked `[Mahi-verify]` in §13. The amendment does not depend on the
reason being right — only on verdicts changing over time, which is measured.

### Also

Fixed a cross-reference in §13.8 that pointed the state sub-analysis at §13.11 (Risks) instead
of §13.12 (the pass A matrix). Noted for the next session: `pytest` lives at
`.venv/bin/pytest` — neither `python` nor `python3` has it on this box.

Tests: 192 -> 192 (documentation-only commit).

## 2026-10-11 (CI) — The audit gate had been red for six weeks, and a timing test cried wolf

### A gate that only fires on PRs is silent exactly when nobody is working

Opening PR #15 ran CI for the first time since 2026-08-30, and `pip-audit -r requirements.lock
--strict` failed with **17 advisories across two packages**. Nothing in the repository had
changed to cause it: every ID is a `PYSEC-2026-41xx` disclosed in the six weeks since the last
run. `main` was failing its own gate, and a four-file documentation-and-probe PR inherited the
failure.

`PyJWT 2.13.0 -> 2.15.1` cleared fourteen (`PYSEC-2026-4140..4152`, `4183`) and
`urllib3 2.7.0 -> 2.8.0` cleared three (`4175..4177`). Both are transitive — neither appears
in `pyproject.toml`; PyJWT arrives under `mcp`, urllib3 under `requests`. The audit went to
`No known vulnerabilities found`, and `build-and-publish` — which had been skipping behind the
failed audit, not building — published an image for the first time since August.

**The open question this leaves:** the gate fires on pull requests and on pushes to `main`.
Six weeks of no commits meant six weeks of not knowing. A scheduled weekly run would have
said so on the day. Not decided.

### PYSEC-2026-4146 listed no fix version, so it was read rather than assumed

An advisory with no fix version normally means a bump cannot clear it and the gate needs an
ignore entry with a written justification. That is worth not guessing at, so it was audited by
hand first.

`PyJWT.decode()` mutates a **caller-supplied `options` dict in place** when `verify_signature`
is falsy, writing `verify_exp`/`verify_nbf`/`verify_aud`/… onto the caller's object. Code that
reuses one dict — a module-level constant, a wrapper's `self.options` — can have a later
verifying call silently inherit `verify_exp=False` and accept an expired or wrong-audience
token. Signature verification is unaffected; it is a claims-check bypass, and a regression of
a 2021 bug fixed in 2022 and reintroduced by a 2025 refactor.

**There is no `jwt.decode` call site in this dependency graph at all.** `garminconnect`
hand-decodes Garmin's tokens with base64 (`client.py:263`) — deliberately unverified, since
the client holds no signing key, with its own `alg: none` rejection. The only `import jwt`
anywhere in site-packages is `mcp/client/auth/extensions/client_credentials.py`, which calls
`jwt.encode` in a flow this server never runs. So exposure was nil independently of the
version, and 2.15.1 clears the advisory regardless — **no ignore entry was added**. Written
down because the next no-fix advisory needs the same walk: read the mechanism, then look for
the call site, and only then argue about versions.

### A timing test cried wolf, and the arithmetic says the limiter was fine

`test_two_processes_share_one_budget` failed on the deps PR:

    global rate not enforced: [0.210, 0.0317, 0.176, 0.0697, 0.0885]

Six stamps at `GARMIN_RATE_PER_SEC=10`, `GARMIN_RATE_BURST=1`. One is free, so five paid
requests need **0.5s** end to end, and the observed span was **0.576s** — compliant. The
companion assertion, `stamps[-1] - stamps[0] >= 0.35`, passed. What failed is
`all(g >= 0.07 for g in paid)`, which requires every *individual* gap to be wide.

A lock-serialised token bucket across two processes cannot promise instantaneous spacing. The
telltale is in the data: `0.210` followed by `0.0317` sums to `0.242`, about two full
intervals. Credit accrued while both children were blocked, then was spent back-to-back — the
long-run rate held, the momentary spacing did not. A re-run of the **same commit** passed, and
the suite passes on this box.

The test's own docstring says the merged-spacing check "does not depend on how the two
processes happened to interleave". The per-gap assertion is precisely the part that does.
**Not fixed, deliberately** — changing a load-bearing test's invariant is a decision, not a
cleanup. What it should assert is the aggregate invariant the limiter actually promises:
elapsed span >= (n - burst) / rate, with tolerance, keeping the existing span check.

### Merge order, and why #15 was merged into rather than rebased

#16 (deps) merged first so #15 could land on a green `main`. #15 was then brought up to date
with GitHub's update-branch endpoint, which performs a **merge** (`85cba73`) — a true rebase of
a pushed branch needs the force push the standing orders deny absolutely, and routing the same
history rewrite through the API would have been a bypass rather than a compliance. Worth
knowing: `gh pr update-branch` does not exist in this `gh` build;
`gh api --method PUT /repos/.../pulls/15/update-branch` is the working equivalent.

Also: `gh`'s active account on this box is `mahidhark`, which has no write access here — a
push 403s with the repo's own `user.name` set correctly, so the symptom points at the wrong
thing. `gh auth switch -u navyasree-anumula`, push, and switch back.

Tests: 192 -> 192. Lock: PyJWT 2.13.0 -> 2.15.1, urllib3 2.7.0 -> 2.8.0.

---

## 2026-10-16 (SCOPE v1.5) — The coach is the conversation, not the server

The session started as "explain what is in the token file" and ended as the amendment that
settles who does the thinking. That was the question actually blocking phase 5: every plan for
`garmin_training` has assumed an answer without writing one down.

### Evidence in, workouts out

The server supplies measurements and executes validated writes. The coaching judgement happens
in the conversation. The argument is not modesty, it is where the inputs are: a decision about
tomorrow's session needs how the legs feel, how much time there is, what hurts, and where in a
cycle the day falls — and **none of those four exists in any Garmin endpoint.** They exist
because somebody typed them into a chat. A recommender inside the server would have to ignore
all four or invent a channel to receive them.

So §1 gains two non-goals. **No recommendation engine**, with three reasons, each one of this
document's own rules pointed at a new target: a hardcoded load-ratio threshold cannot be
falsified by anything Garmin returns, so no test can do more than restate it; §5 already
refuses to interpret `trainingStatus` because the `int` has no sourced meaning, and an invented
threshold is that same interpretation in a field we happen to have opinions about; and it would
ship inside an image, so revising the advice would mean a commit, a CI run and a `docker pull`.
Arithmetic Garmin's own fields define — acute over chronic — is not science and stays.

### The athlete's context has no home in this system, deliberately

Feel, time, injuries and cycle phase stay in the Claude Desktop conversation. No
`/data/athlete_notes.json`. The alternative was injury and cycle history at rest on the volume
that holds the credential, inside the one backup copy with none of that file's protections.

What makes that affordable is a thing that was already true and had not been noticed: **Garmin's
calendar is already the store for the plan.** `get_scheduled_workouts` and `get_workouts` read
back what was prescribed and `get_activities` shows what was actually done, so next week's
session recovers the plan without us persisting anything. What is lost between sessions is the
subjective framing, not the work.

Cycle data is opt-in and read-only — never swept by the probe, which has always left the
menstrual and pregnancy endpoints alone. Recorded in §9 that **the opt-in governs collection,
not containment**: once asked for, it reaches the model provider exactly like every other
metric, and calling it a privacy boundary would be false.

### The cache argument I had been making was wrong

v1.4 justified the cache partly on amplification: three training metrics arrive in one
`get_training_status` payload, so a cache keyed on metric and date would fetch it three times.
That is true and it is **not a cache problem.** Resolving all requested metrics first and
fetching each source once per call is per-call coalescing — ordinary code, no TTL, nothing to
invalidate. And a single `garmin_training` call is two or three requests against a burst of
four, so the wait is zero and a cache saves nothing there.

So the cache ships half-built: **the seam with `garmin_training`, the store with the second
consumer.** The seam is one function in `source/`, the only caller of `_garmin_access` on the
data path. It sits **above** `_garmin_access` (`source/client.py:52`), because that context
manager takes the cross-process lock and then the budget — a cache hit placed inside it would
wait two seconds for a token it does not need while holding the lock.

Two reasons the store cannot land yet. TTLs are unmeasured: today's `training_status` changes
during the day and yesterday's does not, and which fields move intraday is what 5c measures.
And a question this document had never asked — **a payload cache is health data at rest.**
`capabilities.json` holds no readings by design; a cache of fetched payloads is readings on the
volume, beside the credential, inside the unprotected backup. What may be stored, under what
mode, excluded from which backup, and for how long is now an open §8/§9 item.

### Upstream already has almost the whole write path

Read `garminconnect` 0.3.11 rather than assuming: `RunningWorkout` and `StrengthWorkout`,
`WorkoutSegment`, `ExecutableStep`, `RepeatGroup`, the `create_warmup_step` /
`create_interval_step` / `create_repeat_group` / `create_strength_set` family,
`upload_running_workout`, `upload_strength_workout`, `schedule_workout`, `unschedule_workout`,
`push_workout_to_device`, and `garminconnect.exercises` with roughly 2,550 entries plus
`resolve()` and `find()`. **Full-body strength composition is a lookup, not a research
project**, and this project writes no exercise taxonomy of its own.

Which means the scope of the write path is not a format. It is validation, idempotency and
reversibility — and a `preview` action that composes and validates while making zero Garmin
requests, which is also the first write behaviour that can be unit-tested.

### Four traps, found by reading rather than by uploading

- **`get_scheduled_workouts(year, month)` is month-scoped**, and converts to Garmin's 0-indexed
  month internally (`__init__.py:3426`). "The next seven days" across a month boundary is two
  requests; one call returns a partial week and looks complete.
- **`unschedule_workout` takes the `scheduledWorkoutId`, not the `workoutId`**
  (`__init__.py:3471`). The id needed to undo is not the id the create returns, so a rollback
  without keeping it costs a month-scoped read first.
- **`update_workout` PUTs the whole structure and preserves the id** (`__init__.py:3168`), so
  calendar entries pointing at it stay valid. That is the idempotency mechanism: a
  re-prescription of the same named session updates in place instead of becoming a duplicate.
- **`upload_workout` validates only that the payload is a dict or list** (`__init__.py:3148`),
  which confirms v1.4's grep from the other direction. The structure check is genuinely ours.

### Pass A changed the design; Pass B found the orphan

**A.1** is the finding worth the whole pass: a previewed plan is **persisted nowhere**, so a
create action that re-composes from the same arguments would let the human approve one
structure while a second one lands — the right value recorded against the wrong event. Approval
therefore carries the previewed structure itself.

**A.4** is the opposite shape: the athlete context has no producer, consumer or store on any
surface. Here that is the intention, so it is written down as a **non-mechanism** — no tool may
require it, and no tool description may imply the server remembers it.

**B.2**: abandon the conversation between `create` and `schedule` and a template exists on
Garmin that appears on no calendar. Next week's session reads the *calendar*, so it would never
see it. Create and schedule are one action, or the response names what was left behind.

### Two token-file findings, from explaining the token file

Neither observed on this box; both read off 0.3.11 while answering what the three fields are.

- **The bootstrap `dump()` is unconditional** (`__init__.py:782`). It writes after a credential
  login without checking a DI token came back, so a login carried by the `JWT_WEB` cookie
  fallback — the path that is never serialized — would write three nulls. Relevant because the
  11 Oct bootstrap took a 429 on both mobile paths; that fallback evidently still produced DI
  tokens, since eleven requests then worked.
- **A corrupt token file reports as the wrong thing.** `client.load()` wraps every failure in
  `GarminConnectConnectionError`, which `_translate` maps to `SourceUnavailable` — so a file
  that exists but will not deserialize says "Garmin is unreachable" instead of "re-run the
  bootstrap". Missing-file and wrong-password both got deliberate handling; this is the third
  case, unclaimed in code and untested (grepped: the only "corrupt" test is the rate-limit
  bucket).

### Also: local `main` is 37 files behind, and a branch off `origin/main` tracks `main`

Local `main` sits at `df7323b` (PR #2) while `origin/main` is at `07d1e4a` — about 6,700 lines
and 37 files apart, because the merges happened on GitHub and local `main` was never pulled.
Branching "from main" would have rewound the repo to before phases 2–5. Caught by diffing
before switching; **branch from `origin/main`, and clear the stale local ref before it misleads
someone mid-session.**

And `git checkout -b <new> origin/main` sets the new branch's upstream to `origin/main`, so a
bare `git push` would target main — which the standing orders deny absolutely, but the deny
rule should not be the thing that catches it. `git branch --unset-upstream` immediately after,
and push with an explicit refspec.

> **Correction, 2026-10-16 (same evening).** The advice above is half the lesson. `origin/main`
> was *itself* five days stale — nothing had fetched since 11 October — so "branch from
> `origin/main`" still branched from a commit two merges behind. The rule is **fetch first,
> then branch from `origin/main`**. The cost of getting this wrong is in the next entry: PR
> **#17** had already merged `089cd04`, and this session reported that commit as unmerged
> because it was reading a ref that predated the merge.

Tests: 192 -> 192 (documentation-only; `2efa9f0` on `docs/scope-v1.5-coaching-boundary`).

---

## 2026-10-16 (phase 5, part 2) — The map got a consumer, and an advisory got a call site

Two unrelated things, and the second one ate the evening. The map, the seam and
`garmin_training` were built in a few hours with no Garmin requests at all. Then opening the PR
found a five-day-old advisory in the lockfile, which turned out to be five failure emails with
one cause.

### What shipped: five modules and one tool

`garmin_training(metrics=[...], date=None)` over `source/metrics.py` (the map),
`source/extract.py` (the decoders), `source/fetch.py` (the seam), `source/readings.py`
(assembly) and `source/verdicts.py` (the vocabulary). **Zero Garmin requests to build it** —
every field path came out of the 5a/5b entries, which is the first time those two sessions paid
for themselves.

The map is the point. Nine training metrics over three endpoints, and **three of them arrive in
one `get_training_status` payload**, so `sources_for` collapses them before a request is made.
Asking for all nine costs three requests. That number is asserted in a test, because it is the
figure §6's read-sweep arithmetic rests on.

### The seam, and why the store still is not here

v1.5 split §6's cache: the seam ships, the store waits. The amplification argument v1.4 made
for a cache turned out to be **per-call coalescing**, not caching — ordinary code with no TTL
and nothing to invalidate. A single `garmin_training` call is two or three requests against a
burst of four, so the wait is already zero.

The placement is the part worth remembering: the seam sits **above** `_garmin_access`
(`client.py:52`), never inside it. That context manager takes the cross-process lock first and
the budget second, so a cache hit placed inside would wait two seconds for a token it does not
need while holding the lock another container is queued on. A hit as slow as a miss is not a
cache.

### The vocabulary needed a fifth word

v1.4 specified four verdicts — `unsupported`, `no_data`, `not_matured`, `error` — and they were
written for `capabilities.json`, which records whether an endpoint *answers*. A tool response
also needs a word for "here is the reading", and the probe never needed one because `has_data`
was its whole purpose. Hence `PRESENT`. Four describe absence; the fifth is the one a reader
hits most.

`NOT_MATURED` still has no producer, honestly rather than unfinished: only a row flagged
`accumulates` can earn it, and no row has a *measured* accumulation requirement — the HRV
three-week figure is domain knowledge, already `[Mahi-verify]` in §13.

### Rows carry their own provenance, because half of them are inferred

`Metric.verified` says the dotted path was read off a payload this project actually received,
and a test asserts every unverified row says in its note what would confirm it. Verified:
`vo2_max` at `mostRecentVO2Max.generic.vo2MaxValue`, and the four race-prediction field names.
Inferred: where `trainingStatusFeedbackPhrase` and `trainingBalanceFeedbackPhrase` sit inside
their device DTOs, and the field names *and* units for `readiness` and `recovery_time`. Also
unrecorded and now flagged: **`get_race_predictions`' root shape** — a dict is assumed, and if
it is a list those four rows need a decoder. One 5c request settles all of it.

A map that looked authoritative while being half guesswork is the failure this flag exists to
prevent. A field path is a measurement with a date on it, exactly like a verdict.

`readings.py` carries the consequence: **a path that resolves to the wrong type is `error`, not
`no_data`.** Reported as `no_data` an inferred-path bug is indistinguishable from an unworn
watch, and the two need opposite responses — one a one-line map fix, the other nothing.

### Three findings while building it

- **The doorway test passed a file it should have failed.** Its first version walked only
  `Attribute` and `Name` AST nodes, so a planted file containing nothing but
  `from .client import _garmin_access` sailed through. Caught by its own
  `test_the_doorway_check_can_actually_fail` — the negative control convention earning its keep
  on the first new guard written since it was adopted. It walks `ast.alias` now.
- **`cli.py` is a legitimate fourth caller of the doorway.** The `selftest` exercises the lock
  and the budget on a real filesystem and makes **no Garmin request at all**, so "data reads
  must coalesce" does not apply to it. Allowlisted with the reason written out rather than
  refactored.
- **An unknown call shape was being recorded as an `error` verdict**, dressing a map bug as a
  Garmin outcome. Building a request now happens outside the `try`: constructing the call is
  our job and raises, making it is Garmin's and is recorded.

### `mcp` 2.1.1 had a call site, unlike the last advisory

**CVE-2026-59951 / GHSA-84m7-p3x7-pcfv.** Stateful Streamable HTTP servers — `MCPServer`,
`streamable_http_app()`, `StreamableHTTPSessionManager` — retain session entries with no idle
expiry and no aggregate cap; DELETEs, refused opens and completed sessions do not reliably
discard them, so a reachable endpoint grows memory until restart. CVSS v4 availability-only.

The advisory excludes stdio, and this server is stdio with `CMD ["serve"]`. **But `cli.py:93`
runs `mcp.run(transport="streamable-http", ...)` behind `serve --http`**, which is exactly the
affected transport, with no authentication. So exposure is *conditional* rather than nil:
somebody has to start `--http` and something has to reach the port, which §7's loopback-scoped
publish keeps narrow.

Worth setting beside PYSEC-2026-4146 from 11 October, which was dismissed because there was no
`jwt.decode` call site in the graph at all. **Same walk, opposite answer** — which is the
argument for walking it rather than reading the severity score.

Bumped to **2.2.0, not the latest 2.3.0**: it is the minimum fix version, and the pin's own
comment is the reason to prefer it — the entry class was renamed once already between 1.x and
2.x. All twelve floors 2.2.0 declares were checked against the lock and already satisfied, so
nothing transitive moved. Verified before pushing with the gate's own command,
`pip-audit -r requirements.lock --strict` → no known vulnerabilities, from a throwaway venv so
`.venv` stayed clean. The new code was then run against 2.2.0 as well: 288 passed.

### Five failure emails, one advisory

Every PR **and** every push to `main` audits the lock. So two docs merges produced four red
runs — a PR run and a `main` run each — plus #18's, for five. All five were
`mcp 2.1.1 / CVE-2026-59951`, verified by reading each job log rather than assuming.

The part that matters: **`main` was red and published no image** for either docs merge, because
`Build and publish` is gated on the audit. The gate did its job; nothing had told anyone the
advisory existed until a PR was opened. **Second time in two sessions.** The weekly cron on
`main` is no longer a nice idea — it is the thing that would have caught this on 11 October,
and the one that would have caught the seventeen advisories before it.

### The stale-ref episode

Local `main` sat at `df7323b` (PR #2). `origin/main` sat at `07d1e4a`, five days old. Both were
read as current, and this session told Mahi that `089cd04` was "pushed but not on main" when PR
**#17** had merged it on 11 October. The claim was confident, sourced from a ref, and wrong.

Nothing broke — the branches cut from the stale ref touched files the intervening merges did
not, so GitHub reported them mergeable and they were. But the correction is recorded above the
entry that caused it, and the rule is now: **fetch, then branch.** `git fetch origin main:main`
fast-forwards a non-checked-out local branch without a checkout and refuses anything that is
not a fast-forward, which is the cheapest way to keep the trap from re-arming.

### Branch hygiene, which is the same story

Twelve local and twenty remote branches were fully merged and still present, including
`docs/journal-2026-10-11-deps-and-flake` — merged by #17 on 11 October, still sitting there
five days later, which is precisely why this session picked it up and treated it as live work.
All thirty-two deleted (`git branch -d`, and a single `git push --delete` for the remote), tip
shas recorded in the session transcript. The repo is now two branches: `main` and the open PR's
head. **A merged branch left lying around is not clutter, it is a trap with a name that looks
current.**

Tests: 192 -> **288 passed, 2 skipped** on PR #18; `main` stays at 192 until it merges. Much of
the rise is parametrisation — the map's five per-row assertions run once per row.

## 2026-10-19 (Option B plan) — The flake diagnosis was wrong, and reading the limiter is what caught it

A planning session: no product code changed. It produced the Option B plan doc, an architecture
reference page, and one correction that matters more than either.

### The stale-ref trap re-armed within three days

Local `main` sat at `6a87f5d` while `origin/main` was at `76e62aa` — two merges behind, PRs **#18**
and **#22**. Branching from it would have dropped `garmin_training`, the map, the seam **and the
`mcp` 2.2.0 bump**, reintroducing the vulnerable lockfile on a branch that looked clean and would
have reported mergeable.

The 16 October rule caught it: fetch before branching. What that rule did *not* prevent is the
second half of the same trap — **both branches merged on 16 October were still present**, local and
remote, three days later. That is the exact condition the last entry deleted thirty-two branches
over. Worse, `feature/metric-map-and-training-tool` had a **local tip behind its own remote tip**
(`83e7963` vs `91000b2`, the `update-branch` merge), so the same branch name meant two different
commits on one box. Both tips verified ancestors of `origin/main` before any delete was proposed.

The previous `RESUME HERE` also claimed "#18 open, awaiting Mahi's merge" and "192 tests on `main`".
Both were true when written and stale within the hour, because #18 merged at 19:48Z and the entry
describing it was committed on a branch that merged at 20:16Z. **A resume block written before the
last merge of its own session is stale on arrival.**

### The ratelimit diagnosis was inference, and it was wrong

First telling: an idle stretch on a loaded runner refills the bucket, producing a *second* free gap
that the test's `sorted(gaps)[1:]` does not account for. Confident, plausible, and wrong.

`ratelimit.py:123` is why:

```python
tokens = min(budget.burst, tokens + elapsed * budget.rate_per_sec)
```

Tokens cap at `burst` and `acquire` spends exactly one (`:126`), so after any grant the bucket holds
zero and **every** consecutive grant must wait `1/rate`. With the test's `burst=1, rate=10` all five
gaps should be ≥ 0.1s. There is no second free gap available to find.

The flake is in the **measurement**. The child records its stamp *after* `acquire` returns
(`test_ratelimit.py:36-37`), so a stamp is grant time plus however long the process was preempted
before appending it. Sorting merges that delay into the gaps: two grants a real 0.1s apart read as
0.01s apart if the earlier stamp was delayed 0.09s more than the later one. On a two-core shared
runner that is ordinary.

**And the fix I had proposed would not have worked.** A sub-window invariant
`(j - i + 1) <= burst + rate × (s_j - s_i)` reduces, for adjacent stamps with `burst = 1`, to
`s_{i+1} - s_i >= 0.1` — the assertion that flakes, restated. It would have been no improvement
wearing the costume of one.

What the plan specifies instead: assert only the **widest** window, with the bound derived rather
than guessed — `(N - burst) / rate` = `(6 - 1) / 10` = 0.5s — and slack named for what it absorbs
(delay on the *first* stamp, which shrinks the measured window). Today's `>= 0.35` has no stated
derivation; the replacement is both tighter and explained, with a pure-function negative control so
the comparison itself can be shown to fail.

This is dimension #3 earning its place. The first two tellings were read off the test; only reading
the limiter settled it.

### What Pass A found, and it is the house failure mode again

The scheduled audit cron is a **new producer of CI state whose only consumer is a notification** —
it gates nothing, by design, so a red cron run cannot stop an image publishing. But nobody has
verified who receives a *scheduled* run's failure mail. If the answer is nobody, the cron is one
producer and zero consumers: **precisely `capabilities.json`'s problem**, shipped a second time as
the fix for an unrelated one. Flagged as the step that decides whether the cron counts as a fix.

Pass A also caught that removing `serve --http` **flips the SCOPE disagreement rather than closing
it**: today the code offers a transport §7 says it does not have; afterwards §7a's prose will cite a
flag that no longer exists. Carrying that silently would make §7a more stale, not less — so it is
recorded as a v1.6 input in as many words. And `--port` on the `serve` subparser is referenced only
by the branch being deleted (`cli.py:93`), so it is an orphan the moment the flag goes.

Pass B's finding is this session's own output: the architecture page now exists in two places and
they can disagree in either direction.

### The architecture page

`docs/architecture.html` — the §11 import boundary, the single doorway with the lock and budget
nested in order, the nine-metrics-to-three-requests collapse, the above-vs-inside placement
argument, the five verdicts, all ten map rows with per-row provenance, the four guard tests, and
what is deliberately unbuilt. Three hand-authored SVG figures, each with one claim.

It is **the source file**, published to the artifact URL rather than the other way round, and its
header says so — plus the commit it describes, because nothing tests a diagram against the code.
A dated snapshot, on the same principle as a capability verdict or a map field path.

Tests: 288 -> 288, 2 skipped (documentation only; no product code touched).

## 2026-10-24 (Option B) — The gate caught something on the way to being built

Three PRs where the plan expected one, because the premise of the thing being built proved itself
mid-execution.

### A documentation PR failed its audit, and that was the whole argument

#23 carried the Option B plan, the architecture page and a journal entry. Three files under `docs/`,
no product code. Its audit went red on two advisories published in the eight days since `main` last
ran one: `certifi` PYSEC-2026-4320 and `opentelemetry-api` CVE-2026-114603.

Nothing in the PR caused it. **`main` had been carrying a vulnerable lockfile for eight days and
nothing was looking at it** — which is §3's case for a scheduled audit, demonstrated before the
workflow existed. It is also v1.0's **B.2** arriving early: the red run is permanent history now, so
"is `main` healthy?" cannot be answered by "is the newest audit run green?" without asking which
commit it ran against. B.2 predicted that for the cron. It happened on a PR first.

So #24 went in ahead of the work. Both bumps are transitive, both were verified by reading rather
than by trusting the advisory text, and one of them changed how §5 should be read.

**certifi: three roots, found by diffing the bundles.** The advisory prose disagreed with itself on
the vendor, so the question was settled by comparing the shipped CA stores — 121 roots at 2026.7.22,
118 at 2026.10.23, and the three that go are `emSign ECC Root CA - C3`, `emSign ECC Root CA - G3`
and `emSign Root CA - C1`. The call site is unavoidable: certifi *is* the trust store for every
outbound Garmin request. Exploitation still needs a network position, so it is hygiene, not a hole.

**opentelemetry-api: a ReDoS, and the call site is on stdio.** 1.44.0 split the `tracestate` header
with `re.split(r"[ \t]*,[ \t]*", header)` — a delimiter with `[ \t]*` on both sides of the comma,
which backtracks catastrophically on runs of spaces or tabs. 1.45.1 replaces it with
`header.split(",")` plus a literal `strip(" \t")`, and caps the parser's input at 8192 characters
before `from_header` is reached. Read from both wheels, diffed; everything else in that change is
reformatting.

**That finding is the one worth keeping.** `mcp` 2.2.0 installs `OpenTelemetryMiddleware` by default
and calls `extract_trace_context(ctx.meta)` on every inbound message. `_meta` is a **JSON-RPC body
field**, so it arrives identically over stdio. The transport is irrelevant. This is the opposite
shape to CVE-2026-59951, which `--http` alone made reachable — and it means **removing `--http`
closes one advisory's shape and does nothing for another's**. §5's argument for removal stands on
its own merits. It is not a general shield, and the plan now says so in as many words.

Pass B had declared the request lifecycle "untouched, 0 findings". It had missed a value read on
every inbound message, before any handler. That omission is what made this look like a routine
transitive bump for most of an hour.

### The laptop is two months behind, and `docker ps` is what said so

`#2.a` asked whether the Desktop config passes `--http`. The config file **does not exist** at the
documented path, while the connector — named `garmin local dev`, not `garmin` — shows connected. So
the question got answered from the running containers instead:

    intelligent_shannon | ghcr.io/.../garmin-mcp:sha-21829cc | "python -m garmin_mcp serve"
    friendly_leakey     | ghcr.io/.../garmin-mcp:sha-21829cc | "python -m garmin_mcp serve"

No `--http`. `#2.a` cleared, from the live invocation rather than from a file that turned out not to
be there. **Two containers is the documented shape**, not a leak — it is the exact reason the budget
lives in a file with a cross-process lock, as `test_ratelimit.py`'s own docstring says.

Two things fell out of that. **B.1 is fully neutralised rather than mitigated:** the pin is a `sha-`
tag, not `latest`, and `docker run` never re-pulls a moving tag, so the removal cannot reach the
laptop until a deliberate re-pin. And `sha-21829cc` is **2026-08-30, 36 commits behind** — missing
`garmin_training` entirely, all three dependency bumps, the wrong-password/expired-token fix, and,
with some irony, `1934bfc`, the serverInfo build stamp that would have made this checkable from the
server rather than from `docker ps`. On that image `serve --http` would still start `mcp` 2.1.1's
actually-vulnerable streamable endpoint; the combination is worse than either half.

The laptop upgrade is its own task, and it is gated on finding where `garmin local dev` is
configured. The README tells people to edit a file this working install does not have.

### What execution corrected in the plan

**A.1 was wrong.** It claimed removal would leave §7a citing a flag that no longer exists, flipping
the SCOPE disagreement rather than closing it. `docs/SCOPE.md` contains no occurrence of `--http` at
all: §7 says "Transport: stdio", which removal makes unambiguously true; §7's v1.3 supersession note
attributes the listening port to the credential UI; §7a's bind rule cites `127.0.0.1:8765:8765`, the
web UI on 8765, never 3001. Withdrawn, not carried. The §7a bind-address staleness is a separate
pre-existing matter and v1.6 still owes it — just not because of anything here.

**§7 step 6 was not executable where it stood.** Scheduled runs use the default branch only, and
`workflow_dispatch` requires the file on the default branch before it can be triggered at all. So
the dispatch and the notification-recipient check are post-merge by necessity: **the cron cannot be
proven a fix before it is merged.** Split into 6b and 6c.

**An eighth call site.** §5.2's table listed seven. `docs/architecture.html:732` listed the flag as
"pending a decision" — written in the same session as the plan, so it postdated its own table.

**The 60-day auto-disable is confirmed**, from GitHub's docs: public-repo scheduled workflows stop
after 60 days of repository inactivity, a cron-expression change reactivates them, and no
notification is documented. On a repo that has already gone six weeks between sessions, the gate's
failure mode is silence — the same failure mode it exists to fix. Not solved here, deliberately: a
loud-failure consumer is a new mechanism the plan does not specify, and adding it mid-execution
would be scope expansion. Carried as its own item.

### Weekly, and the flake fix verified under load

Mahi ratified **weekly** (`17 6 * * 1`), reversing #4.b's argument for daily, and **removal** of
`--http`. Both had been carried for three sessions.

The ratelimit change asserts only the widest window, with the bound derived — `(6-1)/10 = 0.5s`,
effective 0.40s against the 0.35s it replaces, which had no stated derivation. §7 asked for ten runs
because a flake fix verified by one green run is not verified. Ten passed idle, and ten more passed
under eight busy loops on four cores, since an idle box cannot exercise the scheduling delay the
flake needed. Run times rose ~1.2s to ~1.8s, so the contention was real. It is recorded in the
commit that this does not prove the flake to zero: a first stamp delayed more than 0.1s beyond the
last would still fail. One rare exposure instead of five likely ones, and derived, so a future
failure means something.

Tests: 288 -> 292, 2 skipped. `--http` removal +2 (the transport guard and its negative control),
ratelimit +2 (the derivation and its negative control).

## 2026-10-26 (v0 arc, session 1) — `garmin_health`, and a config that was never missing

The direction changed before any code was written. Five phases of measurement had produced one
usable tool, so the goal is now a loop used daily — **read what the body says, write a session
to the watch, check it got done** — and everything else waits. The probe mechanism work planned
the day before was dropped mid-plan, unstarted.

### The order had to be reversed before it could be built

The acceptance criterion was "something I can call from Claude Desktop". The laptop runs
`sha-21829cc`, which does not contain `garmin_training` at all, so **nothing built in this arc
is reachable from Desktop until the pin moves** — which put the laptop work at step 0 rather
than at the end, where the proposed order had it.

Two other things were blocking and were found by reading rather than by trying:

**Sleep and stress had no measured field paths.** 5b recorded that sleep returned 4589
populated fields and never recorded a path out of it; stress had only a shape note. HRV and
resting HR did have paths. So the health tool split: three HRV rows and resting HR could be
written immediately, and sleep and stress could not be written at all without one probe call.

**Pace and HR targets need zone data that has never been fetched.** `TargetType` offers only
zone-based targets and `get_heart_rate_zones` has never been run. Absolute ranges are
expressible — `ExecutableStep` is `extra="allow"`, so `targetValueOne`/`targetValueTwo` pass
through — but unverified against Garmin's acceptance. v0 run workouts are therefore time and
distance with `NO_TARGET`, so the one supervised live cycle is spent confirming our structure
rather than debugging two unknowns at once.

### The laptop config was redirected, not absent

`claude_desktop_config.json` does not exist at the documented path, and the reason is that this
is the **MSIX/Store** build of Claude Desktop, whose `%APPDATA%` writes are virtualised:

    C:\Users\<user>\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json

So the README is not wrong about the logical path; it is wrong for a Store install, which is
the install that exists. **Found by filename, not by content** — the content grep covered
`%APPDATA%\Claude`, `%LOCALAPPDATA%\Claude` and `.claude`, and `Packages\` was in none of
them. That it holds the `garmin local dev` connector is still an inference.

`latest` pulled clean and reported `0.2.0+5db7c477e3d70d6a0d7b067e129e72d9b65510be`, so the
image side is current and the 36-commit gap is confirmed as a pin rather than a pull failure.

### Two requests settled nine rows, and found two things nobody asked for

`--explain sleep,stress` on 2026-10-25. Both HAS DATA: sleep 3584 populated, stress 2896.

**Sleep answers for HRV and resting heart rate at its own root.** `avgOvernightHrv`,
`hrvStatus` and `restingHeartRate` all sit there, which makes sleep a genuine second source
for two metrics this map answers from dedicated endpoints. 5a said to read them off sleep; 5b
withdrew that. **The withdrawal stands** — `get_hrv_data` carries the weekly average and the
baseline that sleep does not — but it is now a decision with two live options rather than one,
so it is asserted in a test instead of left as a comment. `avgSkinTempDeviationC` is there
too, confirming 5a's note that skin temperature is readable only off this payload.

**The descriptor-driven positional arrays v1.4 predicted are real.**
`stressValueDescriptorsDTOList` of `{index, key}` against `stressValuesArray: list(480)` of
`list(2)`, and the same shape with four descriptors for body battery. Reading those needs a
decoder that resolves a descriptor key to a position — so **body battery is measured and
deliberately unmapped**, guarded by a test in the shape of the one that keeps
`lactate_threshold` out. What made stress shippable anyway is that `avgStressLevel` and
`maxStressLevel` are plain ints at the root and need no decoder at all.

One near-miss worth recording: four of the eight `sleepScores` sub-objects carry **no `value`
at all**, only a qualifier and an optimal range. Mapping `totalDuration.value` would have
looked entirely reasonable and returned `no_data` forever.

### What execution corrected

**A present reading from an inferred path said nothing about being inferred.** Writing the
`hrv_status` test exposed that `readings.read` attached a row's note only on the paths where
there is no reading. So `training_status`, `load_balance`, `readiness` and `recovery_time` have
been returning confident values from guessed field paths in silence — while
`MetricReading.note`'s own schema already promised "what is unconfirmed". Unverified rows now
carry their note on present readings; verified rows do not, because provenance commentary on
every reading is how a field stops being read. Four shipped metrics change behaviour.

**`resting_hr` is matched on `calendarDate`, not `[0]`.** 5b recorded the path as
`...WELLNESS_RESTING_HEART_RATE[0].value`, but the `DATE` argument returns a statistics
window: a response exists for a date with no reading in it. The test puts the asked-for date
second in the window, so an index read would return a real resting heart rate wearing the
wrong date. `match_calendar_date` already existed for exactly this and had no caller.

The group guard and the date basis moved out of `garmin_training` and are now shared, because
a second copy of a guard is how one tool ends up accepting a key the other refuses.

Tests: 292 -> 352, 2 skipped. 36 of those are the nine new map rows passing through
`test_metrics_map.py`'s parametrised assertions; 24 are `test_garmin_health.py`.

### Not done here, on purpose

The re-pin. It cannot happen until this branch is merged and CI publishes an image containing
`garmin_health`, so step 0 split in two and 0b is post-merge. **The resume block below is
therefore not rewritten in this entry** — this session ends with an unmerged branch, and the
one thing three stale resume blocks have taught this repo is that the block can only be true
after the last merge.

## 2026-10-26 (v0 arc, session 2) — step 0b: the pin moved, and the stamp went missing

Step 0b was the post-merge half of step 0: #28 merged as `8154e24`, the Docker push run
(`40382393960`) succeeded, so for the first time an image existed that contained
`garmin_health`. The laptop was re-pinned from `sha-21829cc` (2026-08-30) to `sha-8154e24`,
closing a 36-commit, eight-week gap in one edit.

Run as one script with the checks in front of the edit, because the thing being edited was a
file whose role was still an inference. Order: print the config, back it up to
`C:\garmin-backup`, pull and assert the digest, replace the tag, assert the result still
parses as JSON, stop Desktop, restart. The digest gate and the JSON gate both exit non-zero,
so reaching the last line is itself the evidence both passed.

### The config path stopped being an inference

5a found `claude_desktop_config.json` under
`...\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\` **by filename**, and recorded
that it held the `garmin local dev` connector as an inference. Editing it moved the running
containers, which is the only proof that was ever available: causation, not file contents.

So the README defect is now definite rather than suspected. It says to add the server to
`claude_desktop_config.json` and shows the classic `%APPDATA%\Claude\` location, which on an
MSIX/Store install does not exist. Fixed in its own commit on
`docs/client-wiring-msix-and-stamp`, together with the README example's other defect — it
pinned `:latest`, which SCOPE §7 has forbidden since v1.2 and which is how a laptop runs 36
commits behind.

**And SCOPE §7 did not owe the path.** It has carried the MSIX path since v1.3, correctly,
including that the folder does not exist until first launch. The first draft of this entry
said §7 owed the same correction; it did not, and the claim is withdrawn. What §7 *did* have
wrong is below, and it is this session's finding rather than an inherited one.

### Both containers came back on the new tag

    angry_wright         | ghcr.io/.../garmin-mcp:sha-8154e24 | "python -m garmin_mc…"
    stupefied_mendeleev  | ghcr.io/.../garmin-mcp:sha-8154e24 | "python -m garmin_mc…"

New names, ~30s old, so Desktop respawned them from the new pin rather than reattaching
anything. `sha-8154e24` and `latest` were the same digest
(`sha256:3ef253704955e03bef28149e161ab986f2e075f55f03e6b61a41b46efd9923d9`) at pull time;
the pin is still a `sha-`, so B.1's neutralisation holds and the next upgrade is again
deliberate.

### The build stamp was dropped by the conversation it was built for

`garmin_auth_status` authenticated, metric units, tokens last written 19:41 local. It did
**not** report `server_version`. The client summarised the field away, and
`0.2.0+8154e24ec067a8d8e8bdbf062d0d02697b10131d` was never seen.

`1934bfc` added that stamp precisely so "which image is answering?" would be answerable from
inside a conversation instead of from `docker ps`. On its first real use the answer existed
on the wire and did not survive to the reader. **A field a summarising client can drop is not
a verification mechanism** — it is one only when something asks for it by name. `docker ps`
answered the question again, from outside.

SCOPE §7 said the running build was "**always** identifiable" from `server_version`. That is
the sentence this disproves, and it is corrected in place on
`docs/client-wiring-msix-and-stamp` — identifiable from the payload, with the capability
probe named as the check that cannot be summarised away. Pinning is unaffected: the reason
for pinning is the 36-commit drift, which stands.

What made the verification hold anyway was asking for something that cannot exist on the old
image. `garmin_health` is not in `sha-21829cc` at all, so a successful call proves the build
by its own existence, with nothing to summarise away. **Prefer a capability probe to a
version string** when a human-readable layer sits in between.

### First real data through `garmin_health`

Six metrics asked, six returned, no `no_data`: `hrv_last_night` 90 ms, `hrv_weekly` 80 ms,
`hrv_status` Unbalanced, sleep 8h13m with score 84 (Good), `resting_hr` 46 bpm, stress avg 27
and max 93. One request's worth of payloads answering across three endpoints.

The sleep score returning a number matters more than the number. Four of the eight
`sleepScores` sub-objects carry no `value` at all, and 5a recorded that mapping one of those
would have looked reasonable and returned `no_data` forever. The mapped path is not one of
them.

**`hrv_status` narrowed, not settled.** The row is `verified=False` because *which* of
`status` and `feedbackPhrase` is the human-readable verdict was inferred. "Unbalanced" is
consistent with `hrvSummary.status`'s enum and not with a feedback phrase, so the inference
looks right — but one field seen through a summariser is not a measurement, and the row stays
`verified=False`.

**Corrected 2026-10-26, later the same day: `--explain hrv` cannot settle this.** This entry,
the resume block and the map row all said it could. `--explain` prints key paths and value
*types* only (`probe.py:343`), so it would report `status: str` and `feedbackPhrase: str` —
exactly what 5b already recorded. The question is about values, and the diagnostic suppresses
values deliberately, because a reading in a terminal's scrollback is health data (§8). The
claim was asserted three times in one session and was never true; worse, it sat in
`metrics.py`'s own note, which is the instruction the next session would have followed into a
wasted live request. What settles it costs no request at all: compare the row's output against
the status Garmin Connect displays for the same night.

### `hrv_status` is verified, and the app answered a question nobody asked

**Settled 2026-10-26, against the Garmin Connect app rather than against any endpoint.** The
row reads `hrvSummary.status` and returned "Unbalanced" for the night of 25-26 Oct; the app
displayed the same status for the same night, alongside the last-night (90 ms) and 7-day
(80 ms) averages this map also returns, both matching. So `status` is the field carrying the
verdict, the row is `verified=True`, and it is the **first of five guessed rows to be
confirmed.** Four remain, and they are the "should I train hard today" rows:
`training_status`, `load_balance`, `readiness`, `recovery_time`.

Verifying it deleted the only test that guarded a behaviour shipped the day before — that an
unverified row attaches its note to a *present* reading. The guard moved to
`training_status` rather than being rewritten away, and it now asserts that the row it uses is
still unverified, so the next person to verify that row is told to move the guard rather than
delete it.

**And then the app appeared to show something this map does not return.** Asked whether a
feedback sentence sat next to the status, the answer was yes: Garmin was saying yesterday's HRV
was **good**, while `status` said **Unbalanced**.

**What was built on that, and what the live read did to it, is the lesson of the day — see the
withdrawal below.** At the time it was read as two verdicts about different windows: `status`
classifying HRV against the personal baseline band (90 ms outside a band built around an 80 ms
average), `feedbackPhrase` commenting on last night, which was above average. On that reading
the map returned one of two opinions and looked complete while doing it — a half-read payload
rather than a wrong path. `feedbackPhrase`
is recorded by 5b, sits in the payload `hrv_last_night` already fetches, and therefore costs no
request.

**The row was added the same day, once Mahi asked for it.** `hrv_feedback`,
`hrvSummary.feedbackPhrase`, verified — the path was recorded by 5b and the app confirmed the
field is surfaced. It returns **Garmin's own key**, not the prose the app renders from it: that
mapping has never been measured, and a hand-written key-to-sentence table would be exactly the
invention the `verified` column exists to refuse.

### Withdrawn the same evening, by the first live read of the row

The re-pin went in and the very first call returned **`hrv_status: UNBALANCED` and
`hrv_feedback: HRV_UNBALANCED_11`**. They agree. Desktop noticed before anyone else did, and
said so: the tool's own description claimed the two had disagreed on this date, and the live
data did not.

**The "two verdicts about different windows" mechanism was never measured.** It was inferred
from one paraphrase of a sentence on a phone screen — plausibly about a different night than the
one queried, or rendered from a field we never read — and then written into the map row's note,
`garmin_health`'s docstring, a test docstring, the journal and a PR body as though it were a
finding. Five places, one unverified inference, asserted in the voice the repo reserves for
things it has measured. It is withdrawn: whether the two fields can disagree is **unknown**.

What the live read *did* settle is the part worth keeping. `feedbackPhrase` is a **lookup key
with a variant number**, not a verdict and not a sentence; the app renders prose from it, and
what message 11 reads is not known here. So the row's justification changes from "it carries a
second opinion that can contradict the first" to "it carries specificity `status` does not, and
costs no request". The row stays. Its reason for existing is smaller and true.

**The shape of this mistake is worth more than the correction.** The repo's whole epistemic
apparatus — `verified`, dated notes, "a verdict is a measurement with a date on it" — exists to
stop exactly this, and it did not fire, because the inference was mine rather than a field
path's. The map has a column for an unconfirmed path and no column for an unconfirmed
*explanation of why a row exists*. The guard that would have caught it is cheaper than a column:
**a mechanism gets written in the indicative only after something has been read twice.** One
paraphrase is a lead, not a measurement.

What changed beyond the row is the tool's own guidance, because a map row the agent does not know
to ask for is a row that does not exist. `garmin_health`'s docstring and the `metrics` enum help
now say the two are verdicts about different windows, that either alone is half the answer, and
that all four HRV metrics still cost **one** request. `test_the_whole_health_group_is_four_requests`
is the assertion that the second verdict was free: eleven metrics, still four endpoints.

### The provenance note did not reach the reader either

`hrv_status` is an unverified row, and yesterday's change was precisely that unverified rows
carry their note on *present* readings, not only on absent ones. The summary that came back
carried no provenance at all. Same failure as the build stamp, one day after the mechanism
was built: **the note reaches the wire model and dies at the client.** An inferred reading
was presented as confidently as a measured one.

This does not make the change wrong — a client that renders the payload shows it, and the
schema is the right place for it. It does mean provenance-by-note cannot be the *only*
defence against an unverified path being read as fact. Carried as an open question, not
solved here: the candidates are refusing to ship an unverified row at all, or putting the
qualifier in the value rather than beside it.

### Where step 0 stands

**Closed.** The arc's acceptance criterion — "something I can call from Claude Desktop" — is
met for reads. Every tool built in this arc is now reachable from the laptop, and the
dependency bumps, the wrong-password/expired-token fix and the serverInfo stamp arrived with
it. No code changed in this session; tests stay at 352 passed, 2 skipped.

## 2026-10-26 (v0 arc, session 3) — the preview, which reaches nothing

The write path's first behaviour: `garmin_workouts(action="preview", ...)` composes a running
session, validates it, and sends nothing. Zero Garmin requests, no tokens, no budget, no lock.
SCOPE §8 (v1.5) named this as the first write-path behaviour testable without a network, and
that is the whole reason it went first — the one supervised live cycle is now spent confirming
Garmin's acceptance rather than finding our own bugs.

Tests: 292 -> 352 last session, **352 -> 419** this one, 2 skipped. 67 added, which is roughly
double the plan's estimate: the estimate counted test functions and the suite counts cases,
and six of the refusal tests are parametrised over the values that break them.

### Two decisions taken before the plan was written

**`estimatedDurationInSecs` is the sum of time-based steps only.** A distance step's duration
is unknowable without a pace, and this account has no measured pace — `get_heart_rate_zones`
and the pace endpoints have never been run. So a distance step contributes 0, and the response
carries a sentence saying so, including that Garmin may recompute on upload. The rejected
option was assuming a pace: a fabricated number in a field the calendar may display is exactly
the shape of the failure the previous session was spent on.

That means an all-distance session estimates **0 seconds**, which reads as broken unless the
basis is read with it. The two are returned together for that reason and a test asserts the
wording explains the zero rather than merely describing the method.

**No approval hash yet.** §5 requires that `create` take the structure the preview returned
rather than re-composing from the same arguments — otherwise the human approves one thing and
a second one lands. The integrity marker that enforces it arrives *with* `create`, the
consumer that checks it. Shipping it now would have put a second zero-consumer artifact next
to `capabilities.json`. Accepted cost: `create` changes this tool's response shape.

### Reading the library killed one unknown and found another

The plan had a decision in it about whether to construct an explicit `NO_TARGET`.
There was nothing to decide: `create_warmup_step(..., target_type=None)` **already** fills in
`{"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}`. v0 passes nothing and the
structure still states the target explicitly, so that is one fewer thing for the live cycle to
confirm.

It is asserted in a test anyway, and the reason is the interesting part: if upstream ever
changed that default to `None`, `to_dict()`'s `exclude_none` would **silently drop the field**
and we would upload steps with no target at all. The assertion is not about today's behaviour;
it is about a change that would otherwise be invisible.

The same read found the opposite shape. `create_repeat_group` writes the iteration count into
**both** `numberOfIterations` and `endConditionValue`. Two fields holding one fact are two
fields that can disagree, so V9 refuses a structure where they do.

### The composer owns `stepOrder`, which deletes a whole §8 case

`stepOrder` is flat across a segment, not per-group: a repeat's children continue the parent's
numbering rather than restarting at 1. Garmin orders a segment by that field, so getting it
wrong produces a workout whose steps run in an undefined order.

So the caller cannot supply it. §8 asks for a duplicated-`stepOrder` refusal, and a value the
caller cannot supply is a value the caller cannot duplicate — the bug class is removed at the
source rather than validated against. `validate_structure` still checks it, because that is
the function `create` will run on a structure that came back from a conversation rather than
from the composer, and four of §8's nine cases are only reachable that way. Their tests compose
a valid session and then break it, which is the same path.

### Nine refusals, each with the valid variant beside it

V1 no steps, V2 a repeat group with no steps, V3 fewer than two iterations, V4 duplicated or
non-contiguous `stepOrder`, V5 a non-positive or **non-finite** measure, V6 any target but
NO_TARGET including an absent one, V7 an empty name, V8 a repeat nested in a repeat, V9 the two
iteration fields disagreeing.

Two of those are worth naming. **V5 exists because `float` accepts NaN and infinity and typing
catches neither** — `endConditionValue` is a float all the way to the wire, so a NaN duration
would serialise and travel. **V7 refuses only the empty name**: Garmin's length limit has never
been measured, and an invented cap would refuse sessions Garmin accepts. The repo's habit of
refusing to invent a number held here.

Every refusal is paired with the valid variant passing, because a validator only ever seen to
refuse might be refusing everything.

### "Makes no requests" is asserted structurally, not counted

Counting calls proves nothing was called that time. So the claim is asserted two ways: an AST
check that `compose.py` never names `_garmin_access` and imports nothing named `client`, in the
shape of `test_fetch_seam.py`'s doorway check; and a runtime check that replaces the doorway
with something that raises, then composes a full session anyway. The tool's entire test file
runs with the doorway trapped, so any future test there that needs a request fails loudly.

### What the stress test caught that the code had already got wrong

**The tool converted its arguments twice** — once to build the structure, once to compute the
estimate. Two readings of one argument is a miniature of the re-composition §5 forbids between
preview and create, and it was in the first draft of the tool. Converted once now, with the
comment saying why.

**#4's security finding was about arguments, not responses.** `name` and `description` are free
text an agent may fill with health context — "easy run, HRV unbalanced". §8's "no health data
in logs" had only ever had to hold for responses. Nothing logs the structure, the name or the
description.

**#7 found the trigger condition rather than a blocker.** SCOPE §14's 7.b/A.6 — that
`garmin_auth_status` cannot report the tool set actually in force, and `tools.json` is unbuilt
— does not bite yet, because a preview is read-only. It bites the moment `create` lands, when
"are writes enabled right now" becomes a question the agent must be able to ask. Recorded as a
condition on the next session instead of being quietly carried a fourth time.

### Two guards, because the lesson was a convention and conventions erode

The withdrawal above produced a rule — *a mechanism gets written in the indicative only after
something has been read twice* — and a rule in prose is what this repo keeps catching itself
trusting. So the rule got two mechanisms, and neither is the obvious one: a keyword lint on note
text would have passed my bad note, which said the app "was **found** to show both".

**1. `provenance` on the metric row, orthogonal to `verified`.** `verified` answers "was this
dotted path read off a real payload". `provenance` answers "what evidence put this row in the
map at all", from a closed set: `measured`, `reported`, `inferred`. The combination that matters
is **a verified path with a reported reason** — precisely `hrv_feedback` on the day it was
added, whose path 5b genuinely recorded while the reason was a paraphrase of a phone screen. The
map could not express that state, so nothing caught it.

Two fields holding one fact is what the composer's own V9 refuses, so the distinction had to be
real rather than a second spelling of `verified`. It is: the four unverified training rows are
`measured` with `verified=False` — the payload *was* read, and it is *which* field answers that
was inferred. Same evidence for the row, different confidence in the path.

The teeth are a literal. `test_the_number_of_rows_not_backed_by_a_payload` asserts the list is
**empty**, so adding a row on somebody's word costs a deliberate edit to a number — the same
device as `test_the_whole_health_group_is_four_requests`. The field defaults to `inferred`, the
pessimistic value, so forgetting it fails that test rather than quietly claiming a measurement.
`readings.read` now attaches the note for any row that is not *both* verified and measured, with
the condition asserted as an AND in both directions against a synthetic row, since no real row
is reported today.

**2. Fixture provenance, AST-checked — and this is the one that would actually have stopped
me.** Both metric-tool test files open by declaring it: "payload shapes here are the ones 5b
actually recorded, not invented ones." Stated, never enforced. Every module-level payload
fixture now carries `# recorded: <session>, <date>`, and `tests/test_fixture_provenance.py`
fails on one that does not.

Why it bites where the first guard would not: my claim was that two fields *can disagree*. A
claim about payload behaviour belongs in a test, and a test needs a fixture — so writing it
would have meant inventing a payload with disagreeing values and citing no session for it. The
check fires at the moment the fabrication has to be written down.

Three details were worth the extra thought. The file list is **enumerated, not globbed**:
`test_compose.py`'s step lists are our own inputs, invented on purpose, and demanding a session
for them would train everybody to write a meaningless marker — which is worse than none,
because it looks like evidence. The lookback is **bounded at 8 lines**, with a test proving a
distant marker is not borrowed by the next fixture, since an unbounded one would let a single
citation at the top of a file bless every shape below it. And `tree.body` is walked rather than
`ast.walk`, so a dict inside a test function is left alone: a one-off shape is argued for by the
test around it, while a module-level constant quietly becomes the project's record of what
Garmin returns.

The markers carry exactly the precision the files already claimed — `5b, 2026-10-11` where the
docstring states a date, `5a/5b probe sessions` where only the collective claim exists. A marker
more precise than its evidence would be the same mistake one layer down.

**What neither guard does** is judge whether a cited session really said that. They make the
citation mandatory and visible in review, which is the difference between a claim somebody has
to defend and a sentence nobody re-reads. Tests: 427 -> 461.

### Not done here, on purpose

Create, schedule, update, unschedule, delete. Idempotency resolution, because name-matching
needs a live `get_workouts` and cannot live in a zero-request preview; it ships with `create`,
where it gets a consumer on arrival. Every target but NO_TARGET. Cassettes. The supervised live
cycle.

**And the session is not finished at merge.** A new tool is invisible in Desktop until CI
publishes, the laptop is re-pinned, and Desktop is restarted — step 0b again, now a one-minute
scripted operation, but a step rather than an afterthought. The previous session learned that
by discovering it.

## 2026-10-27 — everything needed to write a workout, and still nothing written

`garmin_workout_create` exists: it takes a previewed structure, its approval marker and a date,
resolves the name against the account, writes, and schedules. **No workout has reached Garmin.**
Every call is exercised against a fake client, because §8 makes the one supervised live cycle
conditional on this being green first — the live attempt should be spent on Garmin's acceptance,
not on our bugs.

Tests: **461 -> 527**, 2 skipped.

### Two folded-in corrections owed from yesterday

**The laptop runs `sha-71d0541`, not `sha-1964dec`.** The resume block was never updated after
the second re-pin.

**`sha-5d9993b` was skipped deliberately.** Nothing user-facing changed in it — `provenance` is
internal and every row is `measured`, so the two read tools answer identically on either image.
The distinction worth keeping is that this drift is **chosen**, where the eight-week drift was
**unnoticed**, and from `docker ps` the two look exactly the same. A skipped re-pin needs saying
out loud or the next session reads it as a failure.

### The spec asked for something MCP cannot express

§5 says `garmin_workouts` carries both actions **and** that preview is read-only while create is
not. `ToolAnnotations` are per *tool*. One tool holding both would have to be annotated as a
write, and §5 itself warns that a client gating non-read-only tools behind a confirmation would
then put a wall in front of the safest call in the flow.

So the write went into a second tool, `garmin_workout_create`, and `garmin_workouts` kept
`preview` and its honest `read_only_hint=True`. That deviates from §5's phrasing to honour §5's
substance; flagged in the plan for ratification rather than taken quietly, and asserted in a test
that says why.

### The write gate, which is the cheap answer to a question carried for three versions

SCOPE §14's 7.b — "are writes enabled right now" must be a question the agent can ask — has been
open since v1.5 waiting on `tools.json`, whose web UI half does not exist. The first write tool
was the trigger, and the answer turned out to be one environment variable:
`GARMIN_MCP_ENABLE_WRITES`, **default off**, with `garmin_auth_status` reporting `writes_enabled`.

Three details earned their tests. The value is read **per call**, never cached at import, because
two consumers — the refusal and the status field — must agree; a cached one could tell the agent
writes are on and then refuse it. A near-miss does **not** open the gate: `yes`, `on`, `enabled`
are all off, because a gate that accepts a near-miss is a gate whose state nobody can predict,
and the thing behind it writes to a real training calendar. And the gate is checked **before**
validation, so a disabled server gives the same answer for a good structure and a bad one.

It also means the first live write needs a deliberate edit to the Desktop config — which a new
build needs anyway, so the gate costs nothing it was not already costing.

### The rule the write path is built around

**Once the workout exists, nothing raises.** `upload_workout` mints a template and
`schedule_workout` is a second request; an exception between them loses the `workoutId`, leaving
a template on the account that is on no calendar. Next week's session reads the *calendar*, so it
would never find it. §5 calls this "a partial write reports what it got to"; concretely it is the
difference between a mess that can be cleaned up and one that cannot be located.

So every failure after the write comes back as an outcome — including a 429, which the read path
re-raises and this path must not. The two rules genuinely conflict there and the write side wins,
because the id is the only thing that can find the orphan.

### What is actually unknown here, and it is most of the response handling

`upload_workout` and `schedule_workout` have **never been called by this project**, so nobody
knows which keys their responses carry. Known from the library's own code rather than from a
payload: `get_workouts` items carry `workoutName` and `workoutId` (`__init__.py:3403`).

`_first_id` therefore tries candidate keys, looks one level into an envelope, and **returns None
rather than raising** on a miss — by which point the write has happened, so an exception would
destroy the id's only copy. A missing workout id reports the raw response and does not attempt a
schedule it cannot make; a missing *schedule* id reports success plus the gap, because undo takes
the schedule id and not the workout id, so losing it costs the only rollback.

### Three cases the plan did not anticipate

All three were found while writing the tests, which is the argument for writing them.

**An exact multiple of the page size needs one more request.** `get_workouts` defaults to 100 and
200 workouts returns two full pages — and a full page is indistinguishable from "there may be
more". Without the third call a name at position 201 looks absent, and the re-prescription
uploads a duplicate instead of updating in place.

**`True` is an `int`.** A response carrying `{"id": true}` would have scheduled workout number 1.
`_first_id` skips booleans before anything else.

**On the update path a silent write response does not lose the id**, because the match already
supplied it. That narrows the orphan case to the upload path alone.

### Both of yesterday's guards fired, and both were right

**The doorway allowlist.** Adding `source/write.py` broke `test_fetch_seam.py` until the module
was named with a reason. The reason is worth having written down: the seam exists to *coalesce*
reads, and a write sequence is ordered — resolve, then write, then schedule — which is the
opposite of a set of interchangeable fetches. Coalescing one would mean silently dropping it.

**The fixture-provenance check, which caught me honestly.** It demands a session citation for
every module-level payload shape, and this session's shapes are genuinely invented. Forcing a
citation would have produced a fake one — and a fake citation is worse than none, because it
reads as evidence.

So the guard learned a second label: **`# invented:`**, which states that no session has seen the
shape. It is not a loophole, it is the honest answer, and it is a standing invitation to replace
it with `# recorded:` after the live cycle. The check then caught two fixtures with no label at
all, one of which was not a Garmin response but our own composed structure — which is why the
rule stayed deliberately simple: in a file carrying payload shapes, *every* module-level shape
says where it came from. A rule with a category exemption is a rule people argue with.

### Not done here, on purpose

The write itself. `push_workout_to_device` stays out — §5 forbids it by default, because it
creates a third copy on hardware that a later edit cannot reach. `delete_workout` and
`unschedule_workout` are not tools; next session's cleanup is done by hand. Cassettes wait for
the live exchange, which is the only thing one can be recorded from.

**The next session is the first that can change the account**, and it needs both a re-pin and a
config edit before it can start. That is the gate doing its job: it cannot begin by accident.

## 2026-10-27 (later) — the approval marker refused its own preview

Writes were enabled on the laptop and the first create attempt failed. Not on Garmin's side:
**the server refused its own preview's structure.** Nothing was sent.

Claude Desktop diagnosed it correctly and unprompted, which is worth recording because it
diagnosed it from behaviour rather than from the code. It re-previewed, got a byte-identical
structure and the same marker (`04af53…`), ruled out its own first guess that the marker was
tied to a server session, and landed on the real mechanism: the step lengths come out as `600.0`
and `300.0`, and something between the two calls was dropping the `.0`.

That is exactly it. **JavaScript has one number type**, so `JSON.stringify(600.0)` emits `600`.
Our steps carry `endConditionValue` as a float because that is the library model's type, so the
preview emits `600.0`, the client re-serialises it as `600`, and what comes back to `create` is
numerically identical and textually different. The hash is over text. Every workout with a
whole-number duration — which is nearly all of them — was unwritable.

### Why no test caught it, which is the part worth keeping

**Python's own `json` round trip preserves `600.0`.** So a `json.dumps`/`loads` pair in a test
reproduces nothing, and every test in this repo passed the structure in-process as a Python
object. The marker's entire purpose is to survive a transport, and not one test exercised a
transport.

The new test does not use `json` at all. It uses a deliberate `through_a_javascript_client`
helper that collapses whole floats to ints, written as a helper precisely because the obvious
Python idiom does not reproduce the bug. A guard whose honest form is "simulate the other
runtime" should say so in its name.

### The fix, and what it deliberately does not do

`_numeric_canonical` collapses whole-valued floats to ints at any depth before hashing — and
**for the hash only.** The structure sent to Garmin is still the one the client handed us.
Rewriting a payload on its way out would be a second composition, which is the thing §5 forbids
in the first place.

Booleans are skipped explicitly, because `True` is an `int` subclass and a careless normaliser
turns it into `1` — after which `{"displayable": true}` and `{"displayable": 1}` would hash alike
while meaning different things to whoever reads the payload. That is the second time this week
`bool` being an `int` has had to be handled on purpose; the first was `_first_id`.

Asserted in both directions: a 600.5 and a 600 must NOT collide, or a half-minute edit would
pass as approved.

### The failure direction was right, and that is why this cost nothing

A marker that is too strict refuses a write; one that is too loose lets an edited session
through. The first costs a round trip, the second costs a wrong workout on a watch. This bug was
the strict kind, so the worst it did was block a throwaway test — and **nothing reached the
account**, which is also what the gate and the supervised cycle were for.

Tests: 527 -> 537, 2 skipped.

## 2026-10-27 (later still) — the first workout this project ever wrote

`throwaway test` is on the Garmin calendar for 27 October: a 10 minute warmup and a 5 minute
cooldown, no targets. Uploaded and scheduled in three requests, no warnings, both ids readable.
Confirmed in the Connect app, where the deletion also has to happen — the server has no delete
tool on purpose.

    {"action": "uploaded", "workout_id": 1742892340, "scheduled": "new",
     "scheduled_workout_id": 1821895273, "date": "2026-10-27",
     "requests_made": 3, "warnings": [], "raw": {}}

**Six phases of measurement, five sessions of building, and the loop now closes**: read what the
body says, write a session to the watch, check it got done. The structure Garmin accepted was the
one `compose` builds with `estimatedDurationInSecs` summed from time steps only and every step
carrying `no.target` — so both of the unverified assumptions the preview session recorded are
answered, and answered in the affirmative.

The session that proved it is the same one that could not be written an hour earlier. It was the
regression check and the milestone in one call.

### What a success could not tell us, which is a flaw in what I built

The call returned both ids and **taught us nothing about Garmin's response shape.** `_first_id`
tries three candidate keys and returns the value; the raw response is attached only on a *miss*.
So a success proves the candidate list is sufficient and leaves which candidate matched unknown —
which is precisely what `test_write.py`'s labels were waiting on.

**A response that only explains itself on failure cannot teach you anything when it works.**

Fixed by recording the key name on every call: `id_keys` now carries e.g.
`{"workout_id": "workoutId", "scheduled_workout_id": "workoutScheduleId"}`, nested keys written
as `workout.workoutId` so the record is specific enough to narrow the list afterwards. Key names
are schema rather than health data — the same argument `probe --explain` makes for printing paths
and never values — so surfacing them costs nothing. An id that came from the name match rather
than the response says so, because that is not evidence about Garmin's shape.

### A third provenance label, because the real state was neither of the two

`# recorded:` would overclaim and `# invented:` would throw away what the live call settled. So
`# recorded in part:` exists now: the value shapes are known (plain ints), the keys are not. The
guard learned it the same way it learned `# invented:` yesterday — by refusing a label that was
honest and unlisted.

Both of `test_write.py`'s id fixtures carry it, and the line that matters most says why:
`unschedule_workout` takes `scheduled_workout_id` and nothing else, so a readable schedule id is
the whole undo path. It is readable. Which key carried it settles on the next create, for free.

### The pattern that held all session

Four times a summarising client dropped a structured field we deliberately returned:
`server_version`, the provenance note, the local-vs-Garmin refusal distinction, and the ids from
this very write — which came back only when asked for by name. It is a constraint rather than bad
luck. **Anything that has to be read back must be asked for by name; anything that has to be
verified must be a capability probe or an observable side effect.** The Connect app is what
confirmed this workout, not the response.

Tests: 537 -> 541, 2 skipped.

## 2026-10-31 — a strength circuit, composed but not yet written

The real use case arrived and it was not the one v0 was built for: a circuit of
named exercises, each held for a work time, with timed rest between, repeated
for N rounds. Two findings, and the second is the one that mattered.

**Our composer was running-only, structurally.** `compose` hardcoded
`RunningWorkout` and the running `sportType` block in two places, and `Step` had
nowhere to put an exercise name. A 40s/20s circuit would have composed — the
timed repeat group is a shape we already had — and landed as a *running* workout
of unnamed intervals. Times would have shown on the watch; names would not have
existed.

**The library builds named exercises by reps, and timed steps without names.**
`create_strength_exercise_step` hardcodes `endCondition` REPS (id 10) with the
rep count as the value; `create_strength_rest_step` is timed and names nothing.
So "40 seconds of Goblet Squat" is a combination upstream does not offer, and
`_build_exercise` assembles it from the library's own enum members through
`ExecutableStep`'s `extra="allow"`. **Garmin's acceptance of that combination is
unverified.** Every field is one upstream writes somewhere; the pairing is not.
That is what the next live create settles, and it is why this entry does not say
the feature works.

**Resolution is exact-before-substring, and the order is load-bearing.** Garmin
stores an exercise as a `category`/`exerciseName` pair from a fixed catalogue of
1527 entries. "Squat" is the display name of one of them *and* a substring of 125
others, so substring-first would refuse the name a human is most likely to type.
An ambiguous term is refused with its candidates listed, never resolved to the
nearest match: refusing costs one turn in the conversation, guessing puts a
different exercise on the watch under a name that reads correct in the preview.

**The preview shows the resolved pair, not the term typed.** `goblet squat`
renders as `exercise: Goblet Squat 40s [SQUAT/GOBLET_SQUAT]`. A preview that
echoed the question would let a human approve a word rather than an exercise.

**A repeat group cannot vary by iteration**, so `[work, rest] x N` keeps the rest
after the final round. That cannot be engineered away, so the preview states it
in `note` rather than letting it turn up on the watch — with a control test
asserting the sentence is *absent* when a circuit ends on work, because a note
appended unconditionally would read as true and be false.

**Two sports, one flat enum.** `StepKind` is the union of both sports' kinds,
because a single `Literal` cannot be conditional on another argument. The
per-sport rule therefore lives in the refusal, which names the sport and lists
its kinds. `sport` defaults to `"running"`, and a test asserts the default
composes byte-identically to asking for running explicitly — so nothing written
before this change composes differently after it.

**The cassette question answered itself while being asked.** Tracing the
transport for §8: `_run_request` ends at `sess = self._api_session`, a plain
`requests.Session()`. Only the *login* strategies use `curl_cffi`, which vcrpy
cannot patch at all. So the data and write calls are recordable, and **the
password POST never touches the recordable stack** — which removes the larger
half of §8's scrubbing worry. `before_record_response` is still required:
`_run_request` calls `_refresh_session()` when the token is near expiry, and a
refresh response carries tokens. Nothing was built; the finding is the
deliverable.

**Verified on the artifact, not the build log.** A local `docker build`, then a
capability probe inside the image — `compose.SPORTS`, the strength kinds, the
`exercise` field on the published schema, and a live resolution of "goblet
squat". `server_version` was not consulted, for the documented reason.

**SCOPE is not amended.** §5 still describes a running composer. The v1.6
amendment is drafted for ratification and every forward reference in the code
says so in those words, because a docstring citing a section that does not exist
is the same overclaim in a smaller font.

Tests: 541 -> 585, 2 skipped.

### Later the same day — the re-pin, and two bugs in the script that did it

The re-pin was skipped at first and then asked for, so #39 merged and the laptop
moved to `sha-7ac390f` in the same session. It took three attempts, and each
failure was worth more than the step itself.

**A Go template in a PowerShell script is a quoting hazard.** PowerShell 5.1
mangles embedded double quotes on their way to a native command, so
`--format '{{join .RepoDigests ","}}'` reached docker as a broken template and
`docker image inspect` wrote nothing to stdout. Array splatting does not help;
the same bug destroyed a `python -c` one-liner on the next line. The fixes:
a template with **no string literal** (`'{{.RepoDigests}}'`), and for the probe,
**send the code over stdin** (`$code | docker run --rm -i --entrypoint python
<img> -`) so it never becomes a command-line token at all.

**The digest check then printed `digest OK` having verified nothing.** With its
input empty, the guard passed. That is the failure this repo keeps naming in
other people's code — a check that cannot see its input and reports success —
and it shipped here in the one script whose entire job is to refuse an
unverified image. The guard now forces its input to a scalar string, **throws if
that string is empty**, and uses `.Contains()` rather than `-like`, because
`-like` against an array filters instead of returning a boolean. No harm done:
the `docker pull` output happened to show the right digest. The pull told us,
not the check.

**Hardcoding the expected old tag was a design mistake that paid for itself.**
The script refused because the config pinned `sha-eeb26dd`, not the
`sha-7a5b852` this file had been asserting for five days. Rewritten to match
whatever `ghcr.io/.../garmin-mcp[:@]...` reference is present, print it, and
replace it — so the next re-pin cannot be blocked by this file being wrong, and
prints the truth on its way past. The withdrawal is at the bottom of this file.

**What the script does right, and kept doing right across all three runs:** pull,
assert the digest, and probe the image's *capability* — all before the config is
touched. Every failure above left the config exactly as it was, and the one
backup taken before a refusal was simply unused.

## RESUME HERE

**The rule, holding: this block quotes no commit hash, no pending list, nothing unknowable at
write time.** Five earlier blocks went stale by doing exactly that. It states what the work is,
which does not change when a merge lands.

State: **585 passed, 2 skipped.** If that count disagrees with the suite, this block is older
than the code and the suite wins.

---

## OPEN CHECKS — do these first, in this order

**~~1. Re-pin the laptop.~~ DONE** — the laptop carries everything on `main` as
of this block, digest asserted before the config was touched, capability-probed
inside the image, and confirmed by `docker ps` after relaunch. The tag and the
measurement date live with the laptop's state at the bottom of this file, not
here, because this block does not quote values that a later re-pin supersedes.
The claim it replaces was wrong for five days; the withdrawal is down there too.
Nothing below is gated any more.

**1. The strength circuit, live — the highest-value call this project can make.**
It is the real use case *and* it settles the one unverified thing in the new
code: whether Garmin accepts a named exercise measured in TIME. Preview the
session, approve it, create it, schedule it, then read the watch for the exercise
names and the work/rest times. Three outcomes worth distinguishing: accepted and
named correctly; accepted but shown unnamed (the `category`/`exerciseName` pair
is not read for a timed step); rejected outright. The fallback for the last two
is reps-based sets, which upstream supports and which loses the work time.
**This call also settles `id_keys` for free** — see check 2.

**2. `id_keys` settles Garmin's response keys, on whatever create runs first.**
Expect something like `{"workout_id": "workoutId", "scheduled_workout_id":
"workoutScheduleId"}`. Whatever comes back, **write the real key names into
`test_write.py`** and change its two id fixtures from `# recorded in part:` to
`# recorded:`. If a key differs from our first candidate, narrow
`WORKOUT_ID_KEYS` / `SCHEDULE_ID_KEYS` to what Garmin actually sends and say so.

**3. The update path has never run live**, and the second create of the same
circuit is how it gets exercised — which is also the normal weekly
re-prescription rather than an artificial test. The live cycle so far has only
taken the **upload** branch: no match ⇒ `upload_workout` ⇒ `schedule_workout`.
The other branch — match ⇒ read the target month ⇒ `update_workout` in place ⇒
skip or schedule — is four code paths and one Garmin behaviour never near a real
account. Create the same-named circuit on a different date: expect
`action: "updated"`, the **same** `workout_id`, `requests_made: 4`, and
`scheduled: "new"`. Then a third create on the date it already holds, expecting
`scheduled: "already"` and **no second calendar entry**.

**4. Delete the throwaways from Connect** — `throwaway test` (workout 1742892340
and its calendar entry) and whatever check 2 leaves behind. There is no delete
tool by design, so this does not happen on its own.

**5. Cassettes (§8).** The transport is now known: writes go through
`requests` via `_api_session` and are recordable; login is `curl_cffi` and is
not, which means the password POST cannot reach a cassette. Still required by
§8: `filter_headers` for `Authorization`/`Cookie`, `before_record_response` (a
near-expiry `_refresh_session()` carries tokens), and a test that **asserts** no
cassette on disk holds a token-shaped string — with a positive half driving a
synthetic token through the real scrubber, because that assertion passes
vacuously while the cassette directory is empty. `vcrpy` is not yet a dependency,
though `tests/test_runtime_lock.py` already lists it as dev-only.

**6. Decisions waiting on Mahi, none blocking:**

- **Ratify or reject the SCOPE v1.6 §5 amendment** — strength as a second sport,
  with the timed-exercise shape labelled unverified. The code is written and
  every reference to the amendment says it is unratified. Per the standing
  orders a SCOPE amendment wants the 10-dimension walk plus passes A and B
  before it is committed; that was not done this session and is the reason the
  amendment is a draft rather than a commit.
- **Ratify or reject the §5 tool-split deviation** — `garmin_workouts` (preview,
  read-only) and `garmin_workout_create` (write) as two tools, because
  `ToolAnnotations` are per tool and §5 asks for per-action honesty. If rejected,
  one tool and preview loses its read-only hint.
- **`id_keys` surfacing Garmin's response key names.** Argued as schema rather
  than account data, by analogy with `probe --explain`. Remove it if you read key
  names as account information.
- **`paths-ignore: ['docs/**']` on `docker.yml`.** Confirmed live on 2026-10-31:
  the docs-only merge of #38 ran a full multi-arch build and moved `latest` to
  `sha-0c20fd8`, whose application code is identical to `sha-43ba988`. The fix
  would also mean `latest` points at the last *code* commit rather than `main`'s
  tip.
- **`ignore-error=true` on `cache-to`.** Turns the `failed to reserve cache`
  flake into a warning instead of a red check; it hit two PRs in one evening.
- **Reps and weight for strength.** Left out of v1.6 on purpose — upstream
  supports both, neither was asked for, and every kind offered is a kind the live
  cycle has to confirm.

**7. Longer-lived, in rough order of value:** the four unverified metric rows
(`training_status`, `load_balance`, `readiness`, `recovery_time`) settled the
free way `hrv_status` was — against the Connect app, **not** `probe --explain`,
which prints value types and never values; then the provenance question (refuse
unverified rows, or move the qualifier from `note` into `verdict` as a fifth
value `present_unconfirmed`); then `tools.json` and `garmin_auth_status`
reporting the live tool set, which the env-var gate only answers for now; then
SCOPE v1.6's standing debts (§7's Connectors-not-Developer and per-user Docker
path, §7a's bind address, §9's location exposure).

---

**What exists:**

- **Reads, live on the laptop.** `garmin_auth_status`, `garmin_training` (5 metrics),
  `garmin_health` (11 rows, four of them HRV including the second verdict `hrv_feedback`).
- **Preview of a running session, live on the laptop.** `garmin_workouts(action="preview")`
  composes and sends nothing — zero requests, nine refusal rules. Time and distance with
  NO_TARGET only.
- **Preview of a strength circuit, on `main` and NOT on the laptop.**
  `garmin_workouts(action="preview", sport="strength")` composes named exercises held for a time
  with timed rests, resolved against Garmin's 1527-entry catalogue, exact-name before substring,
  ambiguity refused with its candidates. **The timed-exercise step shape is ours and Garmin has
  never seen it** — the library names exercises by reps and times steps without names. Reps and
  weight are deliberately not built.
- **The write, reachable and never yet used.** `garmin_workout_create` takes a previewed structure,
  its approval marker and a date; resolves the name (match ⇒ update in place keeping the id, no
  match ⇒ upload, two matches ⇒ refuse); reads the target month on the update path only; writes;
  schedules. **Gated by `GARMIN_MCP_ENABLE_WRITES`, default off.**
- **The loop closes.** On 2026-10-27 `throwaway test` was uploaded and scheduled for 27 Oct in
  three requests, no warnings, both ids readable (`workout_id` 1742892340,
  `scheduled_workout_id` 1821895273), confirmed in the Connect app. **Garmin accepted the
  structure `compose` builds** — `estimatedDurationInSecs` summed from time steps only, every
  step carrying `no.target` — so both of the preview session's recorded assumptions are answered
  yes. Delete it in Connect; there is no delete tool by design.

**The laptop runs `sha-7ac390f` with writes ENABLED** (`-e GARMIN_MCP_ENABLE_WRITES=1` in the
Desktop config's `args`, before the image name — **not** an `env` block: `docker run` does not
forward the parent process's environment into the container, so an `env` block looks configured
and leaves writes off). Measured 2026-10-31 15:34 local: digest
`sha256:81994ff44e1a591b0341faae5a77f7c08d210d9c387cc6ba8c71e47408252481` asserted before the
config was touched, a capability probe inside the image answered `STRENGTH_OK GOBLET_SQUAT`, and
`docker ps` after relaunch showed both containers on `sha-7ac390f` and none on the old tag.
`sha-5d9993b` was skipped deliberately back at #35, because nothing user-facing changed in it —
**a chosen drift and an unnoticed one look identical from `docker ps`**, which is why the choice
is written here.

**WITHDRAWN 2026-10-31: "the laptop is one build behind, the approval-marker fix is not on it,
so a create from Desktop still refuses any whole-number duration."** That was false, and it sat
in this file as an instruction to the next session for five days. The laptop was pinned to
`sha-eeb26dd`, which **is** the merge of #36 — the approval-marker fix — and which is *newer*
than the `sha-7a5b852` this file claimed:

| build | merged | carries |
|-------|--------|---------|
| `sha-7a5b852` | 2026-10-26 22:30 +01:00 | #35, `garmin_workout_create` |
| `sha-eeb26dd` | 2026-10-26 22:56 +01:00 | #36, the approval-marker fix |
| `sha-43ba988` | 2026-10-26 23:13 +01:00 | #37, `id_keys` |

What the laptop genuinely lacked was #37 onward, so the `id_keys` check was right while the
reason given for it was wrong. **A build claim is a measurement with a date on it, and the only
instrument is the config plus `docker ps`** — the rule this repo already wrote for metric rows,
never once applied to the laptop's own pin. Two sessions repeated the claim without measuring it;
what finally caught it was a re-pin script that hardcoded the expected old tag and refused when
it did not match. The bug found the stale claim, which is not a method anyone should rely on.

**DRAFT, awaiting Mahi's ratification — the forensic reading.** The containers stopped during
this re-pin were 41 hours old, started around 2026-10-29 22:30, which is *after* the write
sessions. So an unrecorded re-pin to `sha-eeb26dd` happened between the first successful write
and now, and the resume block was never updated for it. The likely sequence is that #36 merged,
the laptop was re-pinned inside that same session, and the successful write therefore ran on
`sha-eeb26dd` — but the containers that did it are gone and nothing was recorded, so **which
build wrote `throwaway test` cannot be established from here.** It is a reconstruction, not
evidence, and it is written as a draft for exactly that reason.

**The live cycle is done.** What it still owes: **cassettes**, which §8 wants recorded from a
real exchange and which did not happen during this one — so they are the next session's work,
with §8's scrubbing requirements and a test asserting no cassette on disk contains a token-shaped
string. Nothing else in the write path is unexercised.

**What the live cycle settled, and what it did not.** Garmin accepts the structure, and both ids
are readable — so the candidate lists in `_first_id` are sufficient. **Which key carried each id
is still unknown**, because the raw response is attached only on a miss: a success taught nothing.
That is now fixed — `id_keys` records the key name on every call — so **the next create settles
it for free**, and `test_write.py`'s two id fixtures are labelled `# recorded in part:` until it
does.

**Anything read back must be asked for by name.** Four times in one session a summarising
client dropped a structured field we deliberately returned: `server_version`, the provenance note,
the local-vs-Garmin refusal distinction, and the write's own ids. A field is not a channel.
Verification belongs in a capability probe or an observable side effect — the Connect app is what
confirmed the first workout, not the response.

**Rules this repo has paid for, in the order they were learned:**

- **A mechanism goes in the indicative only after two readings.** One paraphrase is a lead. Two
  guards now enforce it rather than only this sentence: `provenance` on the metric row (the count
  of non-`measured` rows is asserted as a literal), and `# recorded:`/`# invented:` on every
  module-level payload fixture.
- **Verify a build with a capability probe, never `server_version`.** The stamp reached a client
  and was summarised away; `docker ps` answered instead.
- **Prefer a bounded refusal to a silent partial.** A page cap that raises beats a name matched
  against a truncated list; an unreadable calendar refuses before writing rather than scheduling
  blind.
- **Once an external artifact exists, report instead of raising.** The id is the only thing that
  can find an orphan.
- **A resume block must not name a pending item it is inside of**, and must not quote a value
  unknowable at write time.
- **A response that only explains itself on failure teaches nothing when it works.** The first
  real write returned both ids and left Garmin's response keys unknown, because the raw payload
  was attached only on a miss.
- **A build claim is a measurement with a date on it.** The instrument is the config plus
  `docker ps`, never this file and never `server_version`. The laptop's pin was asserted wrongly
  here for five days while the rule was already written for metric rows.
- **A guard must fail when it cannot see its input.** An empty input that reports success is
  worse than no guard, because it reads as evidence. Force the input to a scalar, refuse an empty
  one, and prefer an explicit `.Contains()` to an operator whose meaning changes with the
  operand's type.
- **In a PowerShell script, a quote inside a native command's argument will not survive.** Use a
  template with no string literal, or pass the payload over stdin.

**Still open:**

- **Four unverified metric rows** — `training_status`, `load_balance`, `readiness`,
  `recovery_time`. All settleable the free way `hrv_status` was: compare the row's output against
  what the Garmin Connect app displays for the same day. **`probe --explain` cannot do it** — it
  prints value types, never values.
- **`tools.json` is still unbuilt.** The env-var gate answers 7.b for now; the exposure set and
  `garmin_auth_status` reporting the live tool set remain §5's design.
- **The provenance note does not reach a summarising reader.** Open question: refuse unverified
  rows, or move the qualifier out of `note` and into `verdict` as a fifth value.
- **The cron's failure mode is silence.** Public-repo scheduled workflows auto-disable after 60
  days of inactivity with no documented notification.
- **`docker.yml` has no path filter**, so a docs-only PR runs a full multi-arch build and a
  docs-only merge publishes a fresh tag for identical code. A `paths-ignore` would fix it and
  would also mean `latest` points at the last *code* commit rather than at `main`'s tip — a
  decision, not a cleanup.
- **The GHA cache export flakes** (`failed to reserve cache`) when two runs are in flight;
  `ignore-error=true` on `cache-to` would make it a warning instead of a red check.
- **SCOPE v1.6 owes** §7's Connectors-not-Developer and per-user Docker path, §7a's bind address
  for the web UI, §9's location exposure via activity detail — and now the §5 tool-split
  deviation, if Mahi ratifies it.

**A `login` should not be run casually.** Both mobile login paths took a 429 on 2026-10-11 and a
fallback carried it, so the login path is IP-rate-limited. The data path has been fine across
eleven requests then and six metrics on 2026-10-26.

Laptop: tokens on the `garmin-tokens` volume, backed up at `C:\garmin-backup`, config at
`C:\Users\<user>\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
(MSIX path, confirmed by causation). Two containers against one volume is the documented shape
and the reason the budget is file-backed with a cross-process lock. **The re-pin script shape that
works:** pull with retries and assert the digest **first**, then print the config, back it up,
replace the tag, assert the JSON still parses, stop Desktop, verify with `docker ps`. The step
most likely to fail runs before anything that leaves a trace.

**git/gh:** this repo's account is `navyasree-anumula`; the box defaults to `mahidhark`, which
403s here and lacks the `workflow` scope, and it reverted mid-session once — put the switch and
the push in one invocation. `navyasree-anumula` lacks `read:packages`, so registry tags are
checked with an anonymous ghcr token against `/v2/.../manifests/<tag>`. `delete_branch_on_merge`
is on, so a push that crosses a merge recreates the remote branch; local branches and cached
`origin/` refs still need `git fetch origin --prune` plus `git branch -d`.
