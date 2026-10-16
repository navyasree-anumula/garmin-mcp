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

Tests: 192 -> 192 (documentation-only; `2efa9f0` on `docs/scope-v1.5-coaching-boundary`).

## RESUME HERE

State: `main` = `07d1e4a`. **192 tests.** CI green on `main` and the image is published.
PRs **#15** (probe envelope timestamps + SCOPE v1.4) and **#16** (the lock bump) both merged on
2026-10-11.

**Two branches outstanding, both pushed, neither with a PR opened:**
`docs/journal-2026-10-11-deps-and-flake` (this file — `089cd04` plus the v1.5 entry above) and
`docs/scope-v1.5-coaching-boundary` (`2efa9f0`, the SCOPE v1.5 amendment, branched from
`origin/main` so it carries no journal changes). **v1.5 is drafted, not ratified.** Two
`[Mahi-verify]` items block locking it: that Claude Desktop retains the athlete context between
sessions, and whether this account logs cycle data at all. Note the trap above — local `main` is
stale at `df7323b`; branch from `origin/main`.

Laptop: tokens on the `garmin-tokens` volume, backed up at `C:\garmin-backup`. The cached
tokens had aged out after six weeks and were re-bootstrapped on 2026-10-11; **both mobile
login paths took a 429 and a fallback carried it**, so the login path is IP-rate-limited and a
`login` should not be run casually. The data path was unaffected across eleven requests.

**Next, in order:**

1. **`--explain max_metrics_range`, and the six new candidates** — `heart_rates`, `hydration`,
   `body_battery_events`, `morning_readiness`, `heart_rate_zones`, `stats_and_body`. Seven
   requests. `max_metrics_range` is the one that settles a question rather than opening one:
   if the range variant answers where the single date returns `list(0)`, that endpoint is a
   call-shape bug of the `race_predictions` family. v1.4's finding 10.a adds a second reason:
   the range endpoints are how a trend gets answered in one request instead of 28.

   **v1.5 widens this list.** `get_lactate_threshold` is not among the 33 candidates at all,
   and it is the number that sets every running target — note it costs **two** requests on
   `latest=True` (`/latestLactateThreshold` plus power-to-weight) and works around a Garmin
   field typo, `hearRate`. Also unprobed and now needed: **activity detail and splits**, which
   §5 already admits is "the most coach-relevant surface in this document and the least
   evidenced" (and which carries the §9 location exposure v1.6 owes), plus a read-only look at
   `get_workouts` and `get_scheduled_workouts` to see the shape the write path must match.
   Roughly 12 requests in total.

2. **Re-probe the whole account with the fixed counter**, writing the v1.4 vocabulary and a
   `schema_version`. `capabilities.json` still holds August verdicts, two of them wrong in
   opposite directions — `sleep` understated, `spo2` a false positive the old counter would
   have written as `has_data`. 33 candidates, empty ones retried across three dates; budget it
   in one sitting. An aborted run must report what it got to, never look like a clean run that
   found less.

3. **Give `capabilities.json` a consumer.** v1.4 specifies the four-value vocabulary, the
   `schema_version`, an unknown version falling back to the default read-only tool set, and a
   regressed verdict keeping the previous verdict's date. None of it is implemented, and
   nothing reads the file at all — one producer, zero consumers. The metric→source map and the
   tool-selection UI are the intended readers.

4. **Then `garmin_health(metric, date)`**, built on the sourcing map in the 5b entry, not on
   the obvious endpoint names. Four of nine metrics come from somewhere other than their own
   endpoint. Hard requirements collected so far:
   - descriptor-driven decoding for positional arrays (`floorValuesArray` is 96x4 with
     mixed-type columns; `bodyBatteryValuesArray` is width 4 in `stress` and width 2 in
     `body_battery`)
   - map keys selected by `primaryTrainingDevice`, never hardcoded — `training_status` keys
     its DTOs by device id
   - return `…FeedbackPhrase`, treat `trainingStatus`/`fitnessTrend`/`maxMetCategory` as
     opaque codes until a mapping is sourced
   - units decided per field: `time5K: int` and `timestamp: int` are unnamed, so 5a's
     "units are readable off the field names" does not generalise
   - `resting_hr` matched against the nested `calendarDate`, because its `DATE` argument
     returns a statistics window; "no data for this date" rather than the nearest value
   - one winning source per metric, fallback explicit, and the response names the source it
     used — resting HR is `float` from `get_rhr_day` and `int` from the sleep payload
   - the accumulation flag lives in the map beside the source, or `not_matured` can never be
     emitted
   - never return raw series: sleep alone carries ~1,700 elements across ten lists
   - **v1.5 amends the cache line:** the **seam** ships here, not the store — one function in
     `source/`, above `_garmin_access`, the only caller of it on the data path, coalescing
     repeated sources within a call. The store waits for the second consumer, keyed on payload
     plus map version, with TTLs from 5c and the health-data-at-rest question answered first.
   - **metric-plural signatures**: `metrics=[…]`, not one metric per call, or the opening sweep
     becomes eight tool calls instead of four. The enum is the agent's vocabulary, built from
     the map and filtered by `tools.json`, which is what finally gives `capabilities.json` a
     consumer.

5. **Then the write path, preview first.** Upstream supplies the builders, the uploads, the
   calendar and the exercise taxonomy (see the v1.5 entry), so what we build is the part it
   does not have: structure validation, idempotency by name against `get_workouts`, both ids
   returned so an undo needs no search, create-and-schedule as one action, and a `preview`
   action that composes and validates with **zero** Garmin requests. Preview lands first
   because it is the only write behaviour that is unit-testable, and §8's one supervised live
   create → verify → delete runs only once its tests are green. A plan is written **a week at
   a time** — twelve weeks is ~96 requests, about three minutes at the shipped budget, and no
   MCP client waits that long.

**Two decisions owed before they bite again:**

- **The ratelimit per-gap assertion** (this entry). It passes on a quiet runner and fails on a
  busy one, and the run it failed on was compliant. Fix it to assert the aggregate invariant,
  or accept the flake knowingly.
- **A scheduled audit run.** The gate found six weeks of advisories the moment someone opened
  a PR. A weekly cron on `main` would have found them on the day.

**Open items:** **SCOPE v1.6** now owes what v1.5 carried rather than closed — §7
(Connectors-not-Developer, per-user Docker path), §7a (the bind-address correction, knowingly
stale for a second version) and §9 (location exposure via activity detail —
`get_activity_details` returns polylines, and 5c is about to call it) — one session, with its
own ten dimensions plus passes A and B. §6's cache **key** is settled by v1.5; the store is
what moved, and it carries a new open question with it: a payload cache is health data at rest,
and nothing yet says what may be stored, under what mode, or excluded from which backup.

An **open v1.3 commitment** is still unmet and now has a second reason: §5 requires
`garmin_auth_status` to report the tool set actually in force, and `server.py:63-83` returns
token state and `server_version` only — with write tools in `tools.json`, "are writes enabled
right now" becomes a question the agent must be able to ask. Phase 4b tool selection and
`tools.json` still unstarted.

**Two token-file cases are unclaimed** (v1.5 entry): the unconditional bootstrap `dump()` that
would write three nulls on the `JWT_WEB` fallback, and a corrupt token file surfacing as
`SourceUnavailable` instead of "re-run the bootstrap" — neither observed, neither tested.

**Closed this session:** the "coach" wording drift between §13's prose and §1's purpose line —
v1.5 settles it by deciding the coach is the conversation. Workout structure validation is no
longer merely owed; it is specified in §5 with its test cases in §8, and unbuilt.
