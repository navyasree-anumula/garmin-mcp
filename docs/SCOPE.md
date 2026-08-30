# garmin-mcp — Scope

**Status:** pass 1 shipped and proven live on the target laptop. Pass 2 in progress.
**Version:** 1.3 · 2026-08-30
**Ratification:** this document is the artifact to approve before any code is written.
**v1.3** stress-tested across all 10 dimensions of `sop/stress-test-10-dimensions.md`;
audit trail in the final section.

---

## Decision log

| Decision | Value | Settled |
|---|---|---|
| Users | One — Mahi's own Garmin account | v1.0 |
| Data source | Unofficial `python-garminconnect` | v1.0 |
| Packaging | Container, portable | v1.0 |
| Surface | Reads **plus** workout/calendar writes | v1.0 |
| Login as an MCP tool | **Withdrawn** — CLI bootstrap instead | v1.1 |
| Host | **Mahi's laptop** | v1.2 |
| Transport | **stdio** (`docker run -i`) | v1.2 |
| MFA on the account | **Not enabled** | v1.2 |
| Watch | **Forerunner 570** | v1.2 |
| Credential entry | **Local web form**, CLI retained as fallback | v1.3 |
| Web UI lifecycle | **On demand**, loopback-bound, **no Docker socket** | v1.3 |
| Tool surface | **User-selected** from what the capability probe finds | v1.3 |
| Multi-tenancy | **Still no.** Re-confirmed at v1.3 | v1.3 |

---

## §1 Purpose and non-goals

An MCP server that lets an MCP client read Mahi's own Garmin Connect data and create,
update and schedule structured workouts on his own training calendar.

**Non-goals, explicitly:**

- Multi-tenancy. There is one account and the design assumes it everywhere.
- Handling any second person's credentials. Ever.
- A product, a service, or anything charged against WhatsScale product time.
- A public or network-reachable endpoint.
- Redistribution of Garmin data to anyone.

This is a personal tool. It has no relationship to the WhatsScale "AI business assistant
on automation data" vision and should never be mistaken for product work. The one mild
side benefit — it exercises MCP server patterns (credential boundaries, tool-surface
curation) that may transfer later — is a note, not a justification.

---

## §2 Data source and dependency policy

**Dependency:** `garminconnect[workout] >= 0.3.11, < 0.4`

Floor-and-ceiling, **never an exact pin**. An exact pin is precisely what stranded the
most popular existing MCP server on a 4-month-old release of a library whose entire job is
chasing Garmin's blocking. The floor gets us the current Cloudflare handling; the ceiling
stops a major bump landing unreviewed.

The `[workout]` extra is not optional for us — the workout builder models live behind it
(`pydantic>=2.4.0; extra == "workout"`). Installing bare `garminconnect` silently omits
the write surface.

**Why unofficial.** Garmin's official Connect Developer Program is closed. The application
form has been removed from the site and the team's position via the developer forum
(Apr–May 2026) is that new API access requests are paused with no projected re-opening
date. Even when it was open, personal-use applications were rejected by policy — it
required a legal entity, a company domain and a published privacy policy. Connect IQ is
open but irrelevant; it is for on-device watch apps, not the data API.

**Contingency.** If the unofficial path dies permanently, the rewrite cost is the whole
`source/` package — the tool layer and its contracts survive. Worth re-checking the
official program occasionally: applying under WhatsScale as a legal entity would at least
be *possible*, which it is not for an individual.

**Why we build rather than adopt.** Seven existing MCP servers were evaluated. All were
rejected: exact-pinned or stale dependencies, no license (all rights reserved), AGPL
network copyleft, dependency on the now-dead `garth`, or built on the false premise that
`python-garminconnect` is broken. The MCP layer over a healthy library is roughly 400–600
lines. Adopting a 110-tool repo with 44 open issues imports liability to save very little.

---

## §3 How authentication works

The library replays the Android app's network conversation. There is no stolen secret
involved: it uses the real public client identifiers (`GCM_ANDROID_DARK`,
`GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2`) and a real `User-Agent: GCM-Android-5.23`,
with Basic auth over an empty password — a public OAuth client, published by definition.

`curl_cffi` is in the stack because Python's TLS fingerprint is what Cloudflare catches.
It forges the handshake of a real mobile client. It ships prebuilt wheels
(`manylinux_2_17_x86_64`, `musllinux_1_2_x86_64`, and aarch64 variants, `cp310-abi3`), so
there is no compile-from-source risk on Debian-slim or Alpine, on amd64 or arm64.

**Flow:** password → `sso.garmin.com/mobile/api/login` → (MFA, if the account has it) →
`serviceTicketId` → `diauth.garmin.com` → bearer + refresh tokens → cached to disk.

**Documented failure modes, read from the library source:**

| Signal | Meaning | Our handling |
|---|---|---|
| `429` | IP rate limited | Back off. Do not retry in a loop. |
| `403` / `CAPTCHA_REQUIRED` | Bot challenge | Falls through to the next login strategy. |
| `INVALID_USERNAME_PASSWORD` | Wrong credentials | Report plainly. |
| Captcha on all four strategies | Unrecoverable | **The CLI must print "wait, do not retry."** Retrying makes the lockout worse. There is no solver, by design. |

**MFA.** The account does not have MFA enabled today. The bootstrap CLI will still
implement the interactive code prompt — it is roughly ten lines, a CLI is the one safe
venue for typing a 6-digit code, and the alternative to building it is a bricked tool on
the day Garmin decides to require it.

---

## §4 Credential boundary

**The password never enters the MCP surface.** That is the invariant. *How* the password
is typed is an implementation detail underneath it; *where it can appear* is not.

Two entry points satisfy it:

1. **Local web form** (v1.3, pass 2 phase 4) — the primary route. Served on
   `127.0.0.1` only, on demand. See §7a for what makes this safe.
2. **CLI bootstrap** (v1.1, shipped) — retained as the hardened fallback:

```
docker run -it --rm -v garmin-tokens:/data \
    ghcr.io/navyasree-anumula/garmin-mcp:<tag> login
```

The CLI reads the password via `getpass`. The web form posts it once over loopback and
never renders it back.

**The web form is a deliberate, and real, security regression** against `getpass`: a browser
means password managers, autofill, extensions and devtools can all see the field, where a
terminal exposed none of them. It is taken knowingly, for a UX that stopped being
theoretical — wiring the CLI route by hand cost an hour of a real session. The CLI stays
for anyone who wants the tighter boundary.

The password is read once. It is **never** passed as argv, never as `-e` or any
environment variable (both are visible in `docker inspect`), never as a tool argument,
never logged. It is exchanged once for tokens and **never persisted**.

Only `garmin_tokens.json` lands on the volume, mode `0600`. The library already rejects
symlinked tokenstore paths — verified in source.

**The serving container holds tokens only.** It can refresh them; it cannot log in. This
is the whole point: an agent with the full tool surface still has no path to a password
prompt.

> **Withdrawn from v1.0:** exposing `garmin_login` / `garmin_submit_mfa` as MCP tools.
> The library's `return_on_mfa` / `resume_login` capability is real and it would have
> worked. It is withdrawn because it puts a live Garmin password into the conversation
> transcript and the session logs. The CLI-bootstrap design is not a workaround for a
> missing capability; it is the correct boundary.

**Missing token file on `serve`** must fail with an actionable message naming the bootstrap
route — not a stack trace. Once the web UI ships, that message names the compose command and
the URL. **Until then it must keep naming the CLI**: pointing an operator at a UI that does
not exist is worse than the imperfect instruction it replaces.

---

## §5 Tool surface

Roughly 12–18 parameterized tools, not 110 narrow ones. A large flat tool list is itself a
failure mode: it burns context and gives the agent more ways to pick the wrong call.

| Tool | Shape |
|---|---|
| `garmin_activities` | list / get detail, date-windowed |
| `garmin_health(metric, range)` | sleep, HRV, body battery, resting HR, stress, SpO2 |
| `garmin_training` | training status, readiness, acute load |
| `garmin_body` | weight, composition |
| `garmin_workouts` | list / get / create / update / schedule / unschedule |
| `garmin_auth_status` | token state and expiry |

**Permanently excluded, no override:** `delete_activity`, `delete_weigh_in`,
`delete_weigh_ins`, `delete_blood_pressure`. Destructive calls against years of personal
history have no business behind an agent.

### Device capability — determined empirically, not from a spec sheet

The watch is a **Forerunner 570**. Body Battery, HRV status and Training Readiness are
device-tier dependent, and the spec sheet is not evidence of what the *API* returns for
this account.

**Before the tool surface is finalised, a capability probe runs once:** call every
candidate metric endpoint for a recent date and record which return data. The surface is
trimmed to that result. Tools for metrics this device never produces are dead weight and
get cut.

**Availability and exposure are two different things (v1.3).** The probe determines what the
account and watch *can* return, and writes `capabilities.json`. The web UI then lets Mahi
choose which of those are actually *exposed* to the agent, writing `tools.json`. The MCP
server reads that at startup.

Three rules on that config, each earned:

- **An absent `tools.json` means the default read-only set, never "no tools".** A missing
  config file must not silently disarm the server.
- **One writer.** The web UI writes it; the MCP server only reads. Written atomically.
- **A change does not take effect until the MCP client restarts.** MCP clients read the tool
  list once at session start and cache it. Ticking a box and then asking the agent would
  otherwise be a silent no-op — the worst failure class. The UI must say so, and
  `garmin_auth_status` reports the tool set actually in force.

### Response contracts

These are requirements on our layer, not things the library gives us:

- **Units on every numeric.** Garmin returns metric or imperial depending on an account
  setting. Never emit a bare number.
- **Explicit timezone basis.** Garmin dates are device-local. Mahi is in Amsterdam and
  travels; an activity near midnight abroad lands on an ambiguous day. Every date-bearing
  response states what the date means.
- **"No data" is not "call failed."** A day the watch was not worn returns nothing. If the
  two are indistinguishable, the agent will confidently report a broken watch as zero
  sleep.
- **Hard result cap and a default date window.** "All activities" on a multi-year account
  returns thousands and destroys the context window. Paging must be a deliberate act.
- **Distinguishable `AUTH_EXPIRED`.** When tokens expire mid-conversation every tool fails
  at once; the failure must name its cause and point at `garmin_auth_status`.
- **We validate workout structure before upload.** The library's `workout.py` is Pydantic
  models and `create_*_step` helpers with **zero semantic validation** — grepped for
  `raise|validate|assert`, no hits. A repeat group with no steps, or an inverted target
  range, uploads cleanly and produces a broken workout on the watch. That check is ours.

---

## §6 Account safety

**Client-side rate limit, deliberately conservative.**

Garmin publishes no limits for this path. The number we pick has **no empirical basis** and
this document is not going to pretend otherwise. Start very low, raise it only against
observed behaviour, and treat any `429` as a signal to back off rather than tune.

**Short-TTL disk cache.** Yesterday's sleep score does not change. Caching cuts request
volume, which is the only lever we have on the risk in §9.

**Placement:** both the cache and the rate limiter live in `source/`, below the tool layer.
Decided here rather than discovered later, because TTL logic that leaks into individual
tools never comes back out.

**Status (v1.3): neither exists.** Pass 1 shipped one tool making one call and needed
neither. Pass 2 adds the first multi-call surface, so both are built **before** the tools,
not after — which is the whole point of having decided placement up front.

**A rate limit does not always announce itself.** `GarminConnectTooManyRequestsError` is
raised only by the *login* strategies. On a data request `client._run_request` maps every
status >= 400 except 404 onto `GarminConnectConnectionError`, with no 429 branch at all — so
a throttled read arrives looking like a connectivity blip, and an agent reading "could not
reach Garmin" will retry into the block. Our `source/` layer must detect and re-type it.

---

## §7 Deployment and transport

**Host: Mahi's laptop. Transport: stdio, via `docker run -i`.**

> **Superseded in part, v1.3.** "No listening port" no longer holds unconditionally: the
> credential UI listens on one, loopback-bound and on demand. The rest of this section
> stands, and §7a covers what the port costs and what pays for it.

This decision resolves four separate findings at once:

- No listening port during normal operation, therefore no unauthenticated endpoint serving
  special-category health data and accepting workout writes. The web UI is off unless
  explicitly started.
- Login originates from a Dutch consumer ISP — the neighbourhood Garmin expects Connect
  Mobile to come from. A datacenter IP presenting itself as `GCM-Android-5.23` is the
  same wrong-neighbourhood pattern that forced the residential-proxy work on the WhatsApp
  side, and it would have been the highest-ranked risk here.
- No image built on the staging box, which has a known disk problem and no image retention
  policy.
- Nothing registered under the WhatsScale PM2 ecosystem, where a foreign process gets
  caught by deploy scripts that know nothing about it.

**Accepted cost:** the tool works only when the laptop is on. HTTP would buy reach from
Claude web or a phone, and would cost a real authentication design. Not worth it now.

**Development happens on the staging box; execution happens on the laptop.** The repo is
authored here, pushed to GitHub, cloned on the laptop. The staging box never builds or
runs the image.

**Verified 2026-08-30**, on the laptop, not inferred from staging: Docker Desktop 4.44.2,
engine 28.3.2, `linux/amd64`, WSL2 backend. The full chain ran end to end against the real
account.

**Client wiring — Windows, Claude Desktop (v1.3).** Two findings that cost an hour:

- Claude Desktop from the Microsoft Store is an **MSIX package**, and MSIX redirects the
  roaming profile. The config is **not** at `%APPDATA%\Claude`; it is under
  `%LOCALAPPDATA%\Packages\Claude_<packageid>\LocalCache\Roaming\Claude\`. The folder
  does not exist until the app has been launched once. Use **Settings → Developer → Edit
  Config** rather than guessing the path.
- A packaged app does **not** reliably inherit the shell `PATH`. `"command": "docker"` can
  fail to resolve with no visible error — the server simply never appears. Use the absolute
  path to `docker.exe`, with backslashes escaped for JSON.

**Pin the image tag in the client config, not `latest`.** `docker run` does not re-pull a
moving tag: it uses whatever was last pulled. A config on `latest` therefore runs a silently
stale image with nothing anywhere saying so. `garmin_auth_status` reports `server_version`,
stamped by CI with the commit, so the running build is always identifiable.

**Image hygiene from day one:** tag, and prune old tags. Do not repeat the no-retention
mistake on a second machine.

**Rollback:** previous image tag. The token volume is independent of the image, so a
rollback never costs a re-authentication.

**Back up the token volume before any re-auth attempt.** If a captcha window makes a fresh
login impossible, a saved working token file is the only way back in.

---

## §7a Web UI security (v1.3)

One port, `127.0.0.1:8765`, started on demand by `docker compose up -d web` and stopped when
done. It serves three pages: **login**, **tool selection**, **status**.

**Loopback is not the same as safe.** The threat is not someone on the network — it is the
browser already running on the same machine. Any page you visit can issue requests to
`127.0.0.1`, and DNS rebinding defeats a naive origin check by making a hostname the browser
trusts resolve to loopback. So:

- **Bind `127.0.0.1` only.** Never `0.0.0.0`. The port publish is
  `127.0.0.1:8765:8765` — a bare `8765:8765` would serve a credential-minting page to every
  network the laptop joins.
- **Validate the `Host` header against an allowlist** (`127.0.0.1:8765`, `localhost:8765`).
  This is the DNS-rebinding defence; an origin check alone is not.
- **Require same-origin** on every state-changing request, and send **no CORS headers at
  all**. No `Access-Control-Allow-Origin`, ever.
- **CSRF token per form**, bound to the session cookie.
- **No auto-start.** Nothing brings this up on its own.

**No Docker socket, anywhere, for any reason.** Auto-start was considered and rejected: for
a container to start another container it needs `/var/run/docker.sock`, and that socket is
the daemon's full root-privileged control interface — a container holding it can start a
privileged peer that mounts the host filesystem. Mounting it is equivalent to granting root
on the host. Doing that to the *agent-driven* container would invert the entire design of §4,
which exists precisely to make the serving process the least privileged component. The cost
of not having it is one command, typed rarely.

**The UI never displays a token or a password.** Status shows whether tokens exist and when
they were written — never their contents.

---

## §8 Testing

`pytest`. **CI never touches live Garmin.**

> **v1.3 correction.** This section claimed `vcrpy` cassettes. `vcrpy` is not a dependency
> and no cassette has ever existed. Rather than leave the document asserting coverage that
> is not there, the claim is downgraded to a **plan**: cassettes arrive with the first write
> tool, which is the point at which they earn their cost. The scrubbing requirements below
> are requirements *on that future work*, not descriptions of the present.

- **Cassettes will capture credentials unless we stop them.** A recording of the auth flow
  contains the password POST body and the bearer tokens. Requires
  `filter_post_data_parameters`, `before_record_response` scrubbing, and a test that
  **asserts** no cassette on disk contains a token-shaped string.
- **Write tools cannot be safely end-to-end tested.** There is one Garmin account and
  testing `schedule_workout` for real pollutes an actual training calendar. Writes are
  covered by cassettes, plus **one** supervised live create → verify → delete cycle on a
  throwaway workout, performed by hand once and documented.
- **The smoke test must assert it actually read a known value**, and must be demonstrated
  to fail with the token file removed. A smoke test that passes when the system is broken
  is worse than no smoke test.
- **No health data in logs.** Our server must not log tool arguments or responses at debug
  level. The library already scrubs URL query values from exception text; we must not undo
  that.
- **Supply chain.** `curl_cffi` is a binary wheel whose stated purpose is TLS
  impersonation. Pin exact versions in the lockfile and run `pip-audit` in CI.
  **Wired up in v1.3** as a job gating the image publish, auditing `requirements.lock`
  because the lock is exactly what ships.
- **The lockfile is the shipped artifact.** `Dockerfile` runs
  `pip install -r requirements.lock`, so every pin lands in the production image. Until
  2026-08-30 the lock carried `pytest`, `pluggy`, `iniconfig`, `Pygments` and `packaging` —
  the test runner and its tree, inside the runtime container. Found by adding `pip-audit`,
  which flagged a pytest CVE in an image that should never have contained pytest. A test now
  fails if dev packages return to the lock.
- **No health data in logs — and the log level is what enforces it.** garminconnect logs the
  full response body at DEBUG on any API error; on a health endpoint that body *is* the
  health data. Nothing else stands in the way, so `_configure_logging` uses `force=True`
  (`basicConfig` is a silent no-op when root already has handlers) and the level has a
  regression test.
- **Baseline: 0 tests.** This repo starts empty. That is the reference point for every
  later count. **Pass 1: 10. v1.3 phase 1: 26.**

---

## §9 Accepted risks

| Risk | Probability | Impact | Position |
|---|---|---|---|
| Garmin breaks auth again | **High** — it happened in March 2026, and there have been six releases in the ten weeks since | Dead until upstream patches | **Accepted, not eliminable.** Floor-and-ceiling pin; track releases |
| Captcha lockout | Medium | Cannot log in | No workaround by design. Token backup, gentle rates, never retry into it |
| Terms-of-service violation | Certain | Own account, own data, single user | **Accepted.** This is the mild end of the spectrum, but it is a real violation and is recorded as one, not rationalised away |
| Agent writes a junk workout | Low | Bad workout on the watch | Structure validation (§5) + delete exclusions |
| Health-data **network** exposure | Near-zero | High if it were a public HTTP endpoint | Mitigated by §7 / §7a: loopback only, on demand |
| Health data **reaching the model provider** | **Certain, by design** | The data you ask for becomes conversation content | **Accepted and stated plainly.** A model cannot reason over data it cannot see. Tool results travel to the provider exactly like pasted text. Nothing is fetched unless asked for — there is no sync, no background job. "Tokens stay on the laptop" is true; "nothing leaves the laptop" is false, and conflating them is the actual risk |
| Password enters a browser | Certain once §7a ships | Password managers, autofill, extensions and devtools see the field | **Accepted for UX.** A real regression against `getpass`; CLI bootstrap retained as the tighter path (§4) |
| Two containers, one token file | **Observed** 2026-08-30 | Concurrent refresh could lose an update | Cross-process file lock around refresh. Structural risk; not yet observed to bite |
| Single-maintainer bus factor | Low now | Fork treadmill | Accepted. MIT means forking is at least possible |
| Datacenter-IP flag | **Resolved** | — | Eliminated by the laptop decision (§7) |

---

## §10 Carried forward to build time

All four items below were carried from v1.2. Three are now closed by measurement.

- ~~**`mcp` SDK decorator API shape.**~~ **CLOSED.** `FastMCP` was *removed* in mcp 2.x; the
  entry class is `MCPServer` from `mcp.server`. The negotiated protocol revision is
  **`2025-11-25`**, not the `2026-07-28` this document previously claimed.
- **Device capability probe** (§5) — **still open.** Must run before the tool surface is
  fixed. Built in pass 2 phase 3.
- ~~**Laptop toolchain**~~ **CLOSED.** Verified on the laptop 2026-08-30 — see §7.
- **Rate limit number** (§6) — **partially closed.** First real observation: Garmin 429s this
  IP on the mobile *credential login* paths, on a first attempt, while the *token load and
  refresh* path is clean. So the risk sits on bootstrap, not on serving. The steady-state
  read number is still a guess.

Also settled by the same live run:

- **Account unit system is `metric`** — the §5 units contract now rests on a fact.
- **`display_name` is a GUID, and it is load-bearing.** `_require_display_name()` builds the
  URL path for sleep, resting HR, daily summary, heart rates and personal records. It is not
  a cosmetic label; the read surface depends on it. (Value deliberately not recorded here —
  this repository is public.)

---

## §11 Internal architecture rule

All `garminconnect` imports are confined to `garmin_mcp/source/`. **Every adapter above it
is a peer**: `server.py` (MCP tools) and `web/` (the credential and configuration UI, v1.3)
both talk to `source/` and neither imports the library directly. A second adapter is exactly
the pressure this rule exists to survive. There are no sister modules to be consistent with in a new
repo, so "consistency" here means one piece of internal discipline: a future source swap —
official API, a fork, a different library — touches exactly one package.

---

## §12 v1.3 10-dimension stress-test absorption notes

Walked all 10 dimensions of `sop/stress-test-10-dimensions.md`. Findings verified against
real library source and against a live run on the target laptop — not reasoned about
abstractly. The primary artifact is §12.1, the lifecycle walkthrough; the dimensional log
in §12.2 is the disposition trail.

### §12.1 Lifecycle adverse-case walkthrough

| Stage | Adverse case | Desired behaviour |
|---|---|---|
| Pull image | (U) pulls `latest`, gets a stale local image, believes it current | `server_version` reports the build; config pins a tag (§7) |
| Start web UI | (S) port 8765 already bound | Fail naming the port, not a stack trace |
| Login page | (S) another origin POSTs to loopback | Host allowlist + same-origin + CSRF → reject (§7a) |
| Login page | (U) blank or wrong password | Plain "invalid credentials"; never echo the value back |
| Login | (S) 429 on every strategy | "Wait, do not retry", and **disable the submit button** rather than invite a loop |
| Login | (S) captcha on every strategy | Same, plus point at the token backup as the only way back |
| Login | (U) closes the tab mid-flow | No partial token file — `dump()` is already atomic |
| Token at rest | (U) syncs the backup into OneDrive/Dropbox | Docs warn explicitly; it is a live credential |
| Client wiring | (U) hand-edits a large config and breaks the JSON | Docs give backup + validate commands before the edit (§7) |
| Session start | (S) two containers spawn, two logins | Expected and documented; cross-process lock makes refresh safe |
| First call | (S) no tokens yet | Message names the bootstrap route that **currently exists** (§4) |
| Read query | (U) "show me every activity I've ever done" | Hard cap + default window; the response says it was capped and how to page |
| Read query | (S) Garmin 429s mid-conversation | Re-typed as a rate limit; agent told not to retry (§6) |
| Read query | (S) a day the watch was not worn | "No data for this date", distinct from a failed call |
| Tool config | (U) ticks a tool, asks the agent, nothing happens | UI states the restart requirement; status reports the live set (§5) |
| Token expiry | (U) tools fail days later with no context | `AUTH_EXPIRED` names the cause and the fix |
| Upgrade | (U) pulls a new image, config still pins the old tag | `server_version` makes the mismatch visible |
| Decommission | (U) wants access revoked | `docker volume rm garmin-tokens`, delete the backup, change the Garmin password |

### §12.2 Dimensional findings

**#1 Edge cases** — 5 findings, 5 actionable.
- 1.a: a data-path 429 is indistinguishable from a connectivity error — **ACTIONABLE, fixed** (§6).
- 1.b: `get_activities_by_date` paginates up to `MAX_PAGINATED_REQUESTS = 2000` with no delay, before our cap applies — **ACTIONABLE §5**: build on `get_activities(start, limit)`.
- 1.c: token refresh fires on every data request and rewrites the file; two containers, no cross-process lock — **ACTIONABLE**, phase 2.
- 1.d: absent `tools.json` must not mean "no tools" — **ACTIONABLE §5**.
- 1.e: 15s default timeout × ~15 probe endpoints exceeds MCP client patience — **(no action)**: the probe is a CLI command, never a tool.

**#2 Unverified assumptions** — 3 findings.
- 2.a: §6's rate limiter and cache asserted as placed, never built — **ACTIONABLE §6**.
- 2.b: §10's protocol revision, laptop toolchain, units, `display_name` shape — **CLOSED by measurement** (§10).
- 2.c: §8's `vcrpy` cassette coverage does not exist — **ACTIONABLE §8**, downgraded to a plan.

**#3 Actual code checks** — 0 *new*; this pass was the code check. Findings 1.a–1.c came from reading `garminconnect/client.py` and `__init__.py` directly.

**#4 Security** — 4 findings.
- 4.a: library logs full response bodies at DEBUG; only our log level prevents health data on disk — **ACTIONABLE, fixed** with `force=True` + regression test.
- 4.b: `requirements.lock` shipped `pytest` and its tree into the production image — **ACTIONABLE, fixed**; lock is runtime-only, guarded by a test.
- 4.c: web UI is reachable by any page in the local browser; DNS rebinding defeats naive origin checks — **ACTIONABLE §7a**.
- 4.d: Docker socket for auto-start would grant host root to the agent-driven container — **REJECTED outright** (§7a).

**#5 Vision alignment** — 0 findings. §1 states this is a personal tool with no relationship to the WhatsScale vision; the web UI does not change that. Single-user was re-confirmed at v1.3.

**#6 Architecture** — 2 findings.
- 6.a: `web/` must be a peer adapter over `source/`, not a second importer of the library — **ACTIONABLE §11**.
- 6.b: rate limiter and cache belong below the tool layer, as §6 already said — **(no action)**, honoured by phase ordering.

**#7 Impact on other features** — 1 finding, state-machine sub-analysis **triggered** by the new tool-enabled state.
- 7.a: the enabled-tool set is written by the UI and read by the MCP server at session start, which clients cache. A toggle therefore produces **no observable change** until restart — a silent no-op, the exact failure class the sub-analysis exists to catch. **ACTIONABLE §5**: UI states it, status tool reports the live set.

**#8 Test coverage** — 2 findings.
- 8.a: no cassettes despite §8 claiming them — **ACTIONABLE §8**, downgraded.
- 8.b: no regression test on the log level or the lockfile contents, both load-bearing — **ACTIONABLE, both added**.

**#9 Deployment & rollback** — 3 findings.
- 9.a: `docker run` never re-pulls a moving tag, so `latest` runs silently stale — **ACTIONABLE §7**: pin tags, report `server_version`.
- 9.b: MSIX config path and absolute `docker.exe` requirement undocumented — **ACTIONABLE §7**.
- 9.c: rollback unchanged and still sound — the token volume is independent of the image, so a rollback never costs a re-authentication. **✓ VERIFIED**.

**#10 Risks** — 2 findings.
- 10.a: §9 conflated network exposure with provider exposure — **ACTIONABLE §9**, restated.
- 10.b: the browser as a new credential surface — **ACTIONABLE §9**, recorded as accepted.

### §12.3 Net v1.3 changes

| Finding | Section | Change |
|---|---|---|
| 1.a | §6 | Rate limits must be re-typed in `source/`; fixed in code |
| 1.b | §5 | Never call `get_activities_by_date` |
| 1.c, 9.c | §9 | Two-writer risk recorded; lock in phase 2 |
| 1.d, 7.a | §5 | Tool-config rules: default set, one writer, restart required |
| 2.a | §6 | Rate limiter and cache marked not-built, required before tools |
| 2.b | §10 | Three of four carried items closed by measurement |
| 2.c, 8.a | §8 | `vcrpy` claim downgraded to a plan |
| 4.a, 8.b | §8 | Log level is the enforcement point; tested |
| 4.b | §8 | Lockfile is the shipped artifact; runtime-only, tested |
| 4.c, 4.d | §7a | New section: loopback, Host allowlist, CSRF, no socket |
| 6.a | §11 | `server.py` and `web/` are peer adapters |
| 9.a, 9.b | §7 | Pin tags, report `server_version`, document MSIX wiring |
| 10.a, 10.b | §9 | Health data reaching the provider stated plainly; browser risk accepted |
