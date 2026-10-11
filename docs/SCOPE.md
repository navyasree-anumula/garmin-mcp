# garmin-mcp — Scope

**Status:** pass 1 shipped and proven live on the target laptop. Pass 2 in progress.
**Version:** 1.4 · 2026-10-11
**Ratification:** this document is the artifact to approve before any code is written.
**v1.3** stress-tested across all 10 dimensions of `sop/stress-test-10-dimensions.md`;
audit trail in §12. **v1.4** walked the same ten plus adversarial passes A and B; §13.

**v1.4 is a partial amendment, deliberately.** It covers §1 and §5 — the sections that block
phase 5c. §7's client-wiring corrections (Connectors not Developer, the per-user Docker path)
and §7a's bind-address correction are still owed and ride in **v1.5**, which has its own
stress-test pass. Splitting was chosen over one large version so that the end-of-session
review stays inside the time a review can actually be given.

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
| What it is for | **Planning and adjusting running training** | v1.4 |
| Capability verdicts | **Dated, three-valued, reversible** — never a one-time gate | v1.4 |
| Unit of exposure | **The metric, not the endpoint** | v1.4 |

---

## §1 Purpose and non-goals

An MCP server that lets an MCP client read Mahi's own Garmin Connect data and create,
update and schedule structured workouts on his own training calendar.

**What it is for, stated at v1.4: planning and adjusting running training.** Not "exposing
Garmin data" in the abstract. The distinction is not cosmetic and it has already changed two
decisions:

- **It sets the priority order of the tool surface** (§5). Load, readiness and per-run detail
  outrank weight and body composition, and the table is ordered to say so.
- **It makes a missing measurement a real cost rather than a tidy answer.** A capability
  probe that cuts a metric is not being economical; it is removing something a coach needs.
  §5's verdict rules exist because of this.

The measurements that serve it are load (acute, chronic, and the ratio between them),
readiness and recovery, the aerobic/anaerobic balance against Garmin's own targets, VO2 max
and race predictions, threshold and zones, and what happened inside each run. Sleep, HRV,
resting HR and stress are in scope as *inputs to training decisions*, which is also the limit
of how far they are interpreted — see the non-goal below.

**Non-goals, explicitly:**

- Multi-tenancy. There is one account and the design assumes it everywhere.
- Handling any second person's credentials. Ever.
- A product, a service, or anything charged against WhatsScale product time.
- A public or network-reachable endpoint.
- Redistribution of Garmin data to anyone.
- **Medical or clinical interpretation (v1.4).** The coach framing invites "is this HRV a
  problem", "should I be worried about this resting heart rate", and the data is
  special-category health data that looks clinical enough to answer. The server reports
  measurements, the units they are in, and Garmin's own phrasing where Garmin provides it. It
  does not diagnose, and no tool description should imply it can. This is a boundary worth
  writing down before a tool is built that quietly crosses it.

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

**Ordered by what §1 is for (v1.4).** The first three are the coaching surface; the rest
support it. An earlier version listed these alphabetically-by-accident, which put body
composition above per-run detail.

| Tool | Shape |
|---|---|
| `garmin_training` | training status and its phrase, acute/chronic load and ratio, aerobic–anaerobic balance against Garmin's targets, VO2 max, race predictions, threshold, zones |
| `garmin_activities` | list, **and per-run detail: splits, pace, cadence, HR through the run** — date-windowed |
| `garmin_health(metric, range)` | sleep, HRV, resting HR, stress, body battery, respiration, skin temperature |
| `garmin_workouts` | list / get / create / update / schedule / unschedule |
| `garmin_body` | weight, composition |
| `garmin_auth_status` | token state and expiry, and the tool set actually in force |

Three notes on that table, each from measurement rather than preference:

- **`garmin_activities` per-run detail is unverified as of v1.4.** The probe has only ever
  called the activity *list*. Every per-run endpoint — detail, splits, typed splits, HR in
  zones — is unprobed, which means the single most coach-relevant surface in this document is
  also the least evidenced. Phase 5c probes it before the tool is designed.
- **`garmin_training` is one tool over two endpoints.** Status, load balance and VO2 max come
  from `training_status`; readiness and recovery time from `training_readiness`. Splitting
  them per endpoint would decode the same payload twice and leak the source layout into the
  tool names.
- **SpO2 is deliberately absent from `garmin_health`'s metric list, and the tool still
  answers about it.** This watch measures none — `get_spo2_data` returns a full envelope of
  window timestamps with every reading null, confirmed 2026-10-09. "This watch does not
  measure SpO2" is a better answer to a user who asks than a tool that has no opinion, so it
  is a *reported-unsupported* metric, not a removed one. §5's verdict rules below make that a
  general mechanism rather than a special case.

**Permanently excluded, no override:** `delete_activity`, `delete_weigh_in`,
`delete_weigh_ins`, `delete_blood_pressure`. Destructive calls against years of personal
history have no business behind an agent.

### Device capability — determined empirically, not from a spec sheet

The watch is a **Forerunner 570**. Body Battery, HRV status and Training Readiness are
device-tier dependent, and the spec sheet is not evidence of what the *API* returns for
this account.

**A capability probe calls every candidate metric endpoint for a recent date and records
which return data.** The surface is informed by that result.

**Built in v1.3.2** as `garmin-mcp probe` over `source/probe.py` — 33 candidate endpoints as
of v1.4. A CLI command, never a tool: at the shipped budget it takes about a minute, far past
any MCP client's patience, and it is run deliberately rather than per conversation.

#### A verdict is a measurement with a date on it, not a fact about the device (v1.4)

v1.3 said the probe "runs once", that the surface is "trimmed to that result", and that
"tools for metrics this device never produces are dead weight and get cut". **All three are
withdrawn.** Following them in August would have cut four working tools:

| Metric | August verdict | October reality |
|---|---|---|
| HRV | empty across three spread dates | `get_hrv_data` returns a full summary |
| Resting HR | empty | returns the reading, nested in a statistics window |
| VO2 max | `get_max_metrics` returns `list(0)` | present in `training_status` |
| Race predictions | ERROR | returns all four distances — the error was our own call shape |

Two mechanisms failed at once, and the amendment addresses both.

**1. An empty answer has three meanings, not two.** v1.3 distinguished "no data" from
"unsupported". There is a third: **not yet matured.** HRV status needs roughly three weeks of
consistent overnight wear before Garmin computes a baseline; endurance and hill scores need
accumulated qualifying activity. Those read identically to "this device cannot" on any single
day. So:

- **`unsupported`** — a 404, and nothing else. It remains the only reliable signal.
- **`no_data`** — a well-formed envelope with no readings. **Says nothing about capability.**
- **`not_matured`** — `no_data` for a metric with a known accumulation requirement. Which
  metrics have one is recorded in the metric map, beside the source.
- **`error`** — **our call failed.** Four values, not three: `race_predictions` sat on this
  one for a month because the probe passed two arguments to a method that takes nought or
  three. Folding that into "Garmin has no data" would have blamed the account for our bug.
- A verdict carries **when it was measured** — `generated_utc`, `dates_probed` and a
  per-endpoint `observed_on` are already written; what was missing is a reader that uses them.
- **A regressed verdict keeps the previous verdict's date.** A re-probe overwrites in place,
  so a metric that answered in August and is empty today would lose the evidence that it ever
  worked — which is the exact signal `not_matured` depends on.
- **`capabilities.json` carries a `schema_version`.** A reader that does not recognise it
  falls back to the default read-only tool set, never to no tools — the same rule that already
  governs an absent `tools.json`. A file with no `schema_version` is pre-v1.4 and means
  "re-probe needed", not "corrupt".
- **An aborted probe says so.** A 429 ends the run; a partial result must never be written in
  a shape that reads like a clean run which found less.

**2. Trimming is reversible, and a verdict never deletes a tool.** A metric that is not
answering is *reported as not answering*, with which of the three reasons applies. The tool
continues to exist and continues to be asked. A probe run may change a verdict in either
direction and must never be able to permanently remove a capability — the August run would
have done exactly that, and nothing downstream would have questioned it again.

**3. A count greater than zero is not evidence of data.** `spo2` scored 8 populated fields on
2026-10-09 with every reading null: all eight were window timestamps that the emptiness
heuristic was counting. Fixed in code by matching envelope keys on suffix, but the general
rule belongs here — **the count is a heuristic, the verdict is a judgement, and both get
recorded so they can disagree.** That disagreement is what exposed the sleep false negative
in phase 5a and the spo2 false positive in 5b; a design that recorded only one of them would
have shipped both errors.

Three properties it needs, none of them obvious:

- **It never records or prints a value.** The question is "does this endpoint answer for
  me", which is a boolean. Emitting readings into a terminal, its scrollback and a JSON file
  for the sake of a diagnostic is precisely what §8 forbids. Verdicts and a count of
  populated fields are all that leave it.
- **"No data" is not "not supported".** Garmin answers a day the watch was not worn with a
  well-formed envelope — the date echoed back, every measurement null — so `bool(payload)`
  is not merely imprecise, it is inverted. Anything that looks empty is retried on further,
  **non-consecutive** dates before the verdict sticks; three days running can all be days the
  watch sat on a dresser. Only the empties are retried, which keeps the request count down.
- **A 404 is the only reliable "unsupported" signal**, and it is reported distinctly.

#### The metric is the unit of exposure; the endpoint is an implementation detail (v1.4)

**`capabilities.json` is keyed per endpoint. `tools.json` and the UI offer metrics. In four
of nine known cases those two disagree**, so a UI derived from the probe's output as-is would
offer SpO2 (which this watch never measures) and hide HRV (which works). The mapping between
them is therefore a **first-class artifact in `source/`, with a test**, not an inference
anyone makes at the call site:

| Metric | Source | Not from |
|---|---|---|
| HRV | `get_hrv_data` — `hrvSummary.lastNightAvg` | the sleep payload |
| Resting HR | `get_rhr_day`, matched on the nested `calendarDate` | the sleep payload |
| VO2 max | `training_status` — `mostRecentVO2Max.generic.vo2MaxValue` | `get_max_metrics` |
| Race predictions | `get_race_predictions`, **no arguments** | — |
| Training status | `training_status`, device-keyed, phrase not code | — |
| Skin temperature | the sleep payload — `avgSkinTempDeviationC/F` | no endpoint exists |
| SpO2 | nowhere — reported unsupported | `get_spo2_data` |

Three rules follow:

- **A metric names its source; a source never names the metric.** The map is the only place
  that knows `vo2_max` comes out of `training_status`.
- **One metric may have several sources, and the map says which wins.** HRV is available from
  its own endpoint, from the sleep payload, and from `training_readiness`. A response states
  which it used (§5 response contracts), because two sources can disagree and silence about
  which was read makes the number unauditable. **Fallback is explicit or absent — never
  silent:** resting HR is a `float` from `get_rhr_day` and an `int` in the sleep payload, so a
  quiet substitution changes both the value's provenance and its type.
- **A metric with no source is still a metric.** Skin temperature has no endpoint in
  `garminconnect 0.3.11` at all — it exists only inside the sleep payload. The map records
  that rather than the candidate list pretending to ask for it.

**Availability and exposure are two different things (v1.3).** The probe determines what the
account and watch *can* return, and writes `capabilities.json`. The web UI then lets Mahi
choose which of those are actually *exposed* to the agent, writing `tools.json`. The MCP
server reads that at startup. **The UI offers metrics, which means it reads the map, not the
probe output directly (v1.4).**

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
**Four contracts added at v1.4, each from a payload we have now actually seen:**

- **An enum code is returned as its phrase, never as a number, and never as a guess.**
  `trainingStatus`, `fitnessTrend`, `maxMetCategory` and `recordedDevices[].category` are
  `int`. Garmin ships the human-readable text in a sibling field —
  `trainingStatusFeedbackPhrase`, `trainingBalanceFeedbackPhrase`, `acwrStatusFeedback`. We
  have no mapping from code to meaning and inventing one would be guessing at Garmin's
  internal enum, so the phrase is what we return and the code stays opaque unless a mapping
  can be sourced.
- **Dynamic map keys are selected, never hardcoded.** `training_status` keys its DTOs by
  device id — `mostRecentTrainingStatus.latestTrainingStatusData.<deviceId>` — so the
  extractor walks the map and selects on `primaryTrainingDevice: bool`, which both DTOs
  carry. A hardcoded key works on this account only, and breaks on this one the day the watch
  is replaced.
- **Positional arrays are decoded through their descriptor list.** `floorValuesArray` is 96
  rows of width 4 with mixed-type columns, and `floorsValueDescriptorDTOList` names them.
  `bodyBatteryValuesArray` is width 4 inside `stress` and width 2 inside `body_battery` —
  same name, same account, same day. Reading index 0 as "the timestamp" would work today and
  break silently later.
- **Units are decided per field, not read off the name.** v1.3's rule — `…Seconds`, `…Mins`,
  `…InMilliseconds` carry their unit — holds for most fields and fails for the ones a coach
  needs most: `time5K`, `time10K`, `timeHalfMarathon`, `timeMarathon` are bare `int`, and
  `timestamp: int` sits in the same payload as `…TimestampGMT` strings. Any field whose name
  does not carry a unit gets an explicit decision recorded where the map lives.

- **A trend comes from a range endpoint, not from N day-calls (v1.4).** §1's purpose makes
  trends the common question — a four-week HRV slope, not one night — and answering that by
  looping 28 single-date requests against §6's two-second budget is both slow and the
  behaviour most likely to draw a 429. `get_hrv_data_range`, `get_rhr_daily`,
  `get_sleep_daily` and the `get_weekly_*` family exist for this; phase 5c probes them because
  of this contract, not for completeness.

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

**Status (v1.3.1): the rate limiter is built; the cache is deferred to phase 5.**

The limiter is `source/ratelimit.py`, and it keeps its state **in a file on the shared token
volume, not in memory**. That is not over-engineering. Two containers run per client session
(§7), so an in-process bucket would be enforced twice and the real rate against Garmin would
be double the configured one — a limiter permitting twice what it claims is worse than none,
because it invites trust it has not earned. Default: one request every two seconds, burst of
four, overridable by `GARMIN_RATE_PER_SEC` / `GARMIN_RATE_BURST`.

**The cache is deferred, deliberately.** Its only possible consumer today is
`garmin_auth_status`, which must *never* be cached — the tool's entire job is proving the
tokens work *right now*, and a cached answer makes it vacuous. Building it now would mean
inventing TTLs before a single data endpoint has been seen. It ships in phase 5 alongside its
first real consumer, with TTLs derived from what the endpoints actually return. §6's
placement rule is unaffected and still binding: the cache lives in `source/`, and no tool
implements its own.

**Concurrency: `source/lock.py`.** garminconnect rewrites the token file whenever it refreshes
(`_run_request` refreshes inline at the start of any request), guarded only by a
`threading.Lock` — which means nothing between two containers. A cross-process `flock`
serialises our call sites, which are the only seam available: the refresh happens inside the
library and we have no hook into it.

Two details that are easy to get wrong and were: **the lock must not be taken on the token
file**, because `dump()` writes a temp file and `replace()`s it, so the inode changes on every
refresh and a lock on that path would end up held on an unlinked inode — healthy-looking and
excluding nobody. And it must be **re-entrant within a process**, because `flock` binds to the
open file description, so a second acquire in the same process blocks against the first.

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
- **Device capability probe** (§5) — **built and run; no longer a gate.** `garmin-mcp probe`
  shipped in v1.3.2 and ran against the real account on 2026-08-30, with `--explain` passes on
  2026-08-30 and 2026-10-11. v1.4 withdraws the idea that the surface waits on a single run:
  verdicts are dated and reversible, and the surface is informed by them rather than fixed by
  them. **What is still owed is a re-probe with the corrected counter**, because the stored
  `capabilities.json` holds August verdicts now known to be wrong in both directions.
- ~~**Laptop toolchain**~~ **CLOSED.** Verified on the laptop 2026-08-30 — see §7.
- **Rate limit number** (§6) — **partially closed.** First real observation: Garmin 429s this
  IP on the mobile *credential login* paths, on a first attempt, while the *token load and
  refresh* path is clean. So the risk sits on bootstrap, not on serving. The shipped default
  is one request per two seconds with a burst of four; that number is **still a guess**, now
  with a value attached and an env override. Raise it against observed behaviour, never to
  make something feel faster.

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

---

## §13 v1.4 10-dimension stress-test absorption notes

Walked all 10 dimensions of `sop/stress-test-10-dimensions.md`, **plus adversarial passes A
and B**. Findings were checked against real code and against two live `--explain` runs, not
reasoned about abstractly — and two of them changed what this amendment says.

### §13.1 Coverage gap rationale

**v1.3 ran the ten dimensions but not passes A and B**, which did not exist until 2026-10-06.
v1.4 runs both. They were not skippable here: this amendment changes the meaning of an
existing state (`capabilities.json` verdicts) and adds two consumers of it (the metric→source
map, and a UI that offers metrics rather than endpoints).

### §13.2 #1 Edge cases — 5 findings, 4 actionable

- **1.a:** `not_matured` requires knowing which metrics have an accumulation requirement, and
  the amendment named no home for that knowledge. Without one the verdict can never be
  emitted. — **ACTIONABLE §5**: the accumulation flag lives in the metric map, beside the
  source.
- **1.b:** HRV has three sources. The map says which wins, but the plan was silent on whether
  a `no_data` winner **falls back** to another. Silent fallback would answer the same question
  from different sources on different days — and for resting HR the types differ (`float` from
  `get_rhr_day`, `int` from the sleep payload). — **ACTIONABLE §5**: fallback is explicit, and
  the response names the source it used.
- **1.c:** `get_rhr_day` answers with a statistics *window*, so the requested date may not be
  in it. — **ACTIONABLE §5**: match the nested `calendarDate`; "no data for this date" rather
  than the nearest available value, which would be a silently wrong answer.
- **1.d:** a re-probe that hits a 429 mid-run aborts and writes nothing (`RateLimited` ends
  the run; `write_capabilities` is called once at the end, `cli.py:318`). The stale file
  survives with nothing recording that an attempt failed. — **ACTIONABLE §5**: an aborted
  probe reports what it got to; it must not look like a successful run that found less.
- **1.e:** SpO2 could become available on a future watch. — **(no action)**: dated, reversible
  verdicts cover exactly this; it is the mechanism the amendment adds.

### §13.3 #2 Unverified assumptions — 4 findings, 1 actionable

- **2.a:** the entire top of the v1.4 tool table — per-run detail, splits, threshold, zones —
  is **unprobed**. The most coach-relevant surface is the least evidenced. — **ACTIONABLE
  phase 5c**, and stated in §5 rather than left implicit.
- **2.b:** "HRV status needs ~3 weeks of wear" is domain knowledge, not something we measured.
  The August→October change is *consistent* with it; firmware, wear pattern or an account
  setting would fit the same evidence. — **[Mahi-verify]**, recorded as an inference. The
  amendment does not depend on the reason being right: it depends only on verdicts changing
  over time, which is measured.
- **2.c:** "four of nine metrics come from elsewhere" rests on 11 requests, one account, two
  days. Sound as evidence that the mechanism is needed; weak as a statistic. — **(no action)**,
  cited as the former only.
- **2.d:** the rule "the UI reads the map, not the probe output" constrains code that does not
  exist — the tool-selection page is unbuilt (`web/` holds only `app.py`, `pages.py`). —
  **(no action)**: constraining it before it is written is the cheap moment.

### §13.4 #3 Actual code checks — 3 findings, 2 actionable

This dimension changed the amendment twice.

- **3.a:** the plan proposed a three-valued vocabulary. The code has **four** verdicts —
  `HAS_DATA`, `EMPTY`, `NOT_FOUND`, `ERROR` (`source/probe.py:115-118`) — and `ERROR` is not
  redundant: `race_predictions` sat on it for a month because of a call-shape bug of *ours*. A
  three-valued scheme would have folded our own bug into "Garmin has no data". — **ACTIONABLE
  §5**: the vocabulary is four, and `error` means our call failed, never that the metric is
  absent.
- **3.b:** the dating requirement is **already satisfied**: `write_capabilities` writes
  `generated_utc`, `dates_probed` and a per-endpoint `observed_on`
  (`source/probe.py`, `write_capabilities`). What is missing is a *consumer* that reads them.
  — **✓ VERIFIED**; narrows the work to the reader.
- **3.c:** `capabilities.json` carries **no schema version**, which §13.10 needs. —
  **ACTIONABLE §5**.

### §13.5 #4 Security — 4 findings, 1 actionable (deferred)

- **4.a:** the §5 tables name field paths, never values, and this repository is public. —
  **✓ VERIFIED**, consistent with §8.
- **4.b:** skin temperature is read from a payload already fetched for sleep; no new request,
  no new exposure. — **(no action)**.
- **4.c:** §1's new clinical non-goal reduces a real risk the coach framing creates — an agent
  asked "is this HRV a problem" about special-category data. — **✓ ALIGNED**.
- **4.d:** **per-run detail carries GPS.** `get_activity_details` returns polylines; a run
  history is a precise record of where Mahi lives and when he is out. §9 treats exposure as
  credentials and health data and says nothing about location, and this is the first amendment
  that makes activity detail a priority. — **ACTIONABLE §9, deferred to v1.5** with the split.
  Recorded here so the split cannot lose it.

### §13.6 #5 Vision alignment — 1 finding, 0 actionable

- **5.a:** "running coach" sharpens §1's purpose without touching its non-goals: still one
  account, still not product work, still no WhatsScale relationship. The framing could drift
  toward "a product"; the non-goals already forbid it explicitly. — **✓ ALIGNED**.

### §13.7 #6 Architecture consistency — 2 findings, 0 actionable

- **6.a:** the map names `garminconnect` methods, so §11 requires it live in `source/`. —
  **(no action)**, §11 already binding and the amendment says `source/`.
- **6.b:** the map must stay metric→source and **not** grow into tool→metric knowledge, or the
  tool layer leaks downward and §11's one-package-swap property is lost. — **(no action)**,
  recorded as a boundary.

### §13.8 #7 Impact on other features — 2 findings, 1 actionable. **State sub-analysis triggered.**

The verdict state changes meaning, so the sub-analysis applies; its tabulation is §13.12.

- **7.a:** **nothing reads `capabilities.json`.** Grepped `garmin_mcp/` — the only references
  are the writer and a comment (`cli.py:263`, `cli.py:318`). `write_capabilities`'s own
  docstring says "the web UI reads this to build the tool-selection list", and the web UI does
  not. Changing the vocabulary is therefore **free today and expensive later**, and it is also
  why August's wrong verdicts never did any damage. — **ACTIONABLE §5**, and the reason v1.4
  is worth doing before 5c rather than after.
- **7.b:** v1.4's tool table says `garmin_auth_status` reports "the tool set actually in
  force". §5 has required that since v1.3 (finding 7.a) and **it is not implemented** —
  `server.py:63-83` returns token state and `server_version` only. — **(no action)** for v1.4,
  which is a plan doc; flagged as a v1.3 commitment still outstanding.

### §13.9 #8 Test coverage — 2 findings, 2 actionable

- **8.a:** the map needs a test that every metric resolves to a real library method **or** an
  explicit "no source" record. Skin temperature is the case that proves the second half: a
  test asserting every metric has a source would have to call it a failure. — **ACTIONABLE
  §8**.
- **8.b:** the four-valued vocabulary and the schema version need tests at the writer, since
  the reader does not exist yet to fail against them. — **ACTIONABLE §8**.

### §13.10 #9 Deployment & rollback — 2 findings, 1 actionable

- **9.a:** **a new `capabilities.json` meeting an old image.** A re-probe writes the new
  vocabulary; a rollback to a previous image tag (§7's rollback path) leaves an older reader
  against a newer file. Nothing reads it today, so the window is open rather than currently
  broken. — **ACTIONABLE §5**: `schema_version` in the file, and an unrecognised one falls
  back to the default read-only tool set — consistent with the existing rule that an absent
  `tools.json` must never mean "no tools".
- **9.b:** rollback itself is unchanged: the file lives on the token volume, independent of the
  image, so a rollback costs no re-authentication and no re-probe. — **✓ VERIFIED**.

### §13.11 #10 Risks — 3 findings, 1 actionable

- **10.a:** the coach framing pushes every question toward *trends* — a four-week HRV slope,
  not one night. Answered naively that is 28 day-calls against §6's two-second budget and
  §5's hard cap. — **ACTIONABLE §5/§6**: trends come from the range endpoints
  (`get_hrv_data_range`, `get_rhr_daily`, `get_sleep_daily`, `get_weekly_*`) in one request,
  and phase 5c probes them *because* of this finding rather than for completeness.
- **10.b:** location exposure via activity detail — duplicate of 4.d, same disposition.
- **10.c:** splitting v1.4/v1.5 leaves §7a saying "bind `127.0.0.1`, never `0.0.0.0`" while
  the shipped container does the opposite for a reason already understood. The doc is
  knowingly wrong for one more version. — **Accepted**, recorded here and in the journal so it
  is not rediscovered as a bug.

### §13.12 Pass A — states, producers and consumers

**Surfaces that exist here**, as the checklist adapted to this repo: the MCP tool list, the
CLI, the web UI, persistence, and logging. **There is no HTTP API, no chat/turn path and no
eval or lab surface** — stated rather than answered with invented findings.

| State | Producer | MCP tools | CLI | Web UI | Persistence | Logging |
|---|---|---|---|---|---|---|
| verdict (4 values) | `probe.py:115-118`, `run_probe` | **none** | `probe` prints it | **none** (claimed, absent) | `capabilities.json` | at INFO |
| `populated_fields` | `count_populated` | none | printed beside verdict | none | `capabilities.json` | — |
| `generated_utc` / `observed_on` | `write_capabilities` | none | none | none | `capabilities.json` | — |
| `schema_version` | **does not exist yet** | — | — | — | — | — |
| metric→source map | **does not exist yet** | will be the main consumer | — | will be the list source | none (code, not data) | — |
| exposure set (`tools.json`) | web UI (**unbuilt**) | read at startup (**unbuilt**) | — | writes it (unbuilt) | `tools.json` | — |
| tool set in force | — | **required since v1.3, absent** (`server.py:63-83`) | — | — | — | — |

Findings:

- **A.1:** the verdict state has **one producer and zero consumers**. A state that never
  leaves the function that computes it is not yet a mechanism — Pass A's characteristic
  finding, and it lands on the probe's entire output. — **ACTIONABLE §5** (same as 7.a).
- **A.2:** `write_capabilities`'s docstring names a consumer that does not exist. A comment
  asserting a mechanism is how the next reader concludes the wiring is done. — **ACTIONABLE**:
  code comment corrected when the reader ships.
- **A.3:** two states are **produced in two places that can disagree**: resting HR
  (`get_rhr_day` as `float`, sleep payload as `int`) and HRV (three sources). The map declares
  a winner; finding 1.b makes fallback explicit. — **ACTIONABLE §5**, absorbed.
- **A.4:** `tool set in force` is **consumed by a requirement and produced by nothing** —
  §5 has promised it since v1.3 and `garmin_auth_status` does not return it. — **ACTIONABLE**,
  carried as an open v1.3 commitment.
- **A.5:** the §6 cache is a future consumer of none of this, but it *will* key on
  metric+date. If it keys on endpoint+date instead, two metrics from one payload fetch twice.
  — **ACTIONABLE §6, v1.5**: the cache keys on the fetched payload, not the metric asked for.

### §13.13 Pass B — the lifecycles

- **B.1 (tool-call lifecycle):** metric asked → map resolves source → cache → `source/` →
  response. The response must name *the source it used* (A.3) and *the date basis* (§5). Order
  is sound; the failure mode is a cached payload answering a metric the map has since
  re-pointed. — **ACTIONABLE §6, v1.5**: a map change invalidates the cache, or the cache key
  includes the map version.
- **B.2 (probe lifecycle):** a re-probe **overwrites** verdicts in place. A metric that was
  `has_data` in August and `no_data` today loses its evidence of ever having worked, which is
  precisely the signal the maturity category depends on. — **ACTIONABLE §5**: a verdict that
  regresses records the previous verdict's date rather than silently replacing it. **This is
  the finding of the pass** — the amendment's own mechanism would have erased its own
  evidence.
- **B.3 (session lifecycle):** `tools.json` changes mid-session are a no-op until the client
  restarts (§5, v1.3). Unchanged by v1.4; a metric-keyed set does not alter the caching
  behaviour of MCP clients. — **✓ VERIFIED**.
- **B.4 (deployment lifecycle):** old reader, new file — §13.10's 9.a. New reader, old file:
  an August `capabilities.json` has no `schema_version`, which the reader must treat as
  "pre-v1.4, re-probe needed" rather than as corrupt. — **ACTIONABLE §5**, absorbed with 9.a.

### §13.14 Net v1.4 changes

| Finding | Section | Change |
|---|---|---|
| 1.a | §5 | Accumulation flag lives in the metric map |
| 1.b, A.3 | §5 | One winning source per metric; fallback explicit; response names the source |
| 1.c | §5 | `resting_hr` matched on nested `calendarDate`; no nearest-value answers |
| 1.d | §5 | An aborted probe reports partial results, never looks like a clean run |
| 2.a | §5 | Per-run detail recorded as unverified; 5c probes before the tool is designed |
| 2.b | §13 | Maturity reason recorded as inference, `[Mahi-verify]` |
| 3.a | §5 | Four verdicts, not three; `error` means our call failed |
| 3.c, 9.a, B.4 | §5 | `schema_version` on `capabilities.json`; unknown → default read-only set |
| 4.d, 10.b | §9 | **Deferred to v1.5:** GPS/location exposure via activity detail |
| 7.a, A.1, A.2 | §5 | The probe's output has no consumer; the map and the UI become it |
| 7.b, A.4 | — | Open v1.3 commitment: `garmin_auth_status` must report the live tool set |
| 8.a, 8.b | §8 | Tests: map resolution incl. "no source"; vocabulary and schema at the writer |
| 10.a | §5/§6 | Trends come from range endpoints, one request; 5c probes them |
| 10.c | §7a | Knowingly stale for one version; recorded, not forgotten |
| A.5, B.1 | §6 | **Deferred to v1.5:** cache keys on payload + map version, not metric |
| B.2 | §5 | A regressed verdict keeps the previous verdict's date |
