# garmin-mcp — Scope

**Status:** pass 1 shipped and proven live on the target laptop. Pass 2 in progress.
**Version:** 1.6 · 2026-10-31 — **RATIFIED by Mahi, 2026-10-31**, on the diff summary,
with three decisions taken at ratification: §15.15.
**Ratification:** this document is the artifact to approve before any code is written.
**v1.3** stress-tested across all 10 dimensions of `sop/stress-test-10-dimensions.md`;
audit trail in §12. **v1.4** walked the same ten plus adversarial passes A and B; §13.
**v1.5** the same ten plus A and B; §14. **v1.6** the same ten plus A and B; §15.

**v1.5 settles who does the thinking.** It covers the division of labour between the server
and the conversation, where the athlete's own context lives, what ships of §6's cache, and the
shape of the write path — the four things that block phase 5 and everything after it. Sections
touched: the decision log, §1, §5, §6, §8, §9, §11.

**v1.6 settles how hard, and what an exercise is called.** Three things, two of them already
built and proven live before this document caught up with them — which is the wrong order and
is recorded as such in §15.1:

- **(a) A named strength exercise measured by time.** Built, written live on 2026-10-31, and
  **confirmed end to end**: Garmin accepted it and the watch shows each exercise name, its
  duration and the rounds as planned. No reps fallback is needed.
- **(b) Timed warm-up and cool-down steps in a strength session.** Built; Garmin's acceptance
  of a warm-up inside a `strength_training` workout is the one thing still unmeasured.
- **(c) Explicit pace-range targets for running.** Not built. This is the part this version
  exists to authorise, and it **deliberately reverses a v0 decision** — see §5.

Sections touched: the decision log, §5, §8, §9, §15.

**The items v1.4 said would ride in v1.5, and v1.5 said would ride in v1.6 are now committed
to v1.7 — firmly, by Mahi's ratification decision of 2026-10-31.** They are §7's client-wiring
corrections (Connectors not Developer, the per-user Docker path), §7a's bind-address
correction, and §9's GPS/location exposure via activity detail. Carrying them twice was a
scope decision; a third deferral would have been a debt, and §15.1 says so rather than
repeating v1.5's reasoning as if it were still fresh. What changes here is that they stop being
*carried* and become v1.7 **scope**, alongside unschedule-by-lookup (§15.13) — which gives v1.7
a code item and so stops it being the docs-only version that slips a fourth time. None of the
three bears on pace targets: two are documentation, one is a read path, and §15.15 records the
check. §7a's prose is knowingly wrong for a third version and for no more than that.

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
| Coaching judgement | **In the conversation, never in the server** | v1.5 |
| Athlete context — feel, time, injuries, cycle | **Claude Desktop chat only. No server-side store** | v1.5 |
| Plan continuity between sessions | **Garmin's own calendar is the store** | v1.5 |
| Cycle data | **Opt-in, read-only, never swept by the probe** | v1.5 |
| §6 cache | **The seam now, the store with the second consumer** | v1.5 |
| A write reaches Garmin | **Only after a preview the human approved** | v1.5 |
| Sports composed | **Running and strength.** Strength is a circuit of named exercises held for a time | v1.6 |
| An exercise's identity | **Garmin's catalogue pair**, never free text; ambiguity refused, never guessed | v1.6 |
| Targets | **Explicit pace ranges only.** Zone-based targets stay refused | v1.6 |
| Reps and weight | **Not built.** Strength is prescribed by time in v1.6 | v1.6 |
| Undoing a schedule | **Look the scheduled id up on Garmin's calendar at undo time.** No ledger, no second store | v1.6, ships v1.7 |

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

**Who does the thinking, stated at v1.5.** The server supplies **evidence** and executes
**validated writes**. The coaching judgement happens in the conversation. This is not a
modesty clause; it follows from where the inputs are. A decision about tomorrow's session
needs how the legs feel, how much time there is, what hurts, and where in a cycle the day
falls — and none of those four exist in any Garmin endpoint. They exist because they were
typed into a chat. A recommender inside the server would have to either ignore all four or
invent a channel to receive them, while the model already has them in front of it.

So the loop is: the agent reads measurements → the human adds the context only they have →
they argue it out → the agent composes a plan and shows it → the human approves → it is
written to Garmin. The server is authoritative about what was measured and about what a
workout structure may contain. It has no opinion about what anyone should run.

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
- **A recommendation engine (v1.5).** No training science in Python. No load-ratio threshold
  that decides a week is too hard, no percentage-of-threshold pace table, no periodisation
  template. Three reasons, each one of this document's own rules pointed at a new target: such
  a constant cannot be falsified by anything Garmin returns, so no test can do more than
  restate it; §5 already refuses to interpret `trainingStatus` because the `int` has no
  sourced meaning, and an invented load threshold is that same interpretation in a field we
  happen to have opinions about; and it would ship inside an image, so revising the advice
  would mean a commit, a CI run and a `docker pull`. Arithmetic Garmin's own fields define —
  dividing acute load by chronic load — is not science and stays. If a threshold ever must
  live in code, it lives in data beside the metric map, with a cited source and a version.
- **A store of the athlete's own context (v1.5).** Feel, available time, injuries and cycle
  phase are not persisted by this server. They live in the Claude Desktop conversation. The
  *plan* persists, on Garmin's calendar, which is also where next week reads it back from — so
  what is lost between sessions is the subjective framing, not the work. The alternative was a
  file of injury and cycle history sitting on the same volume as the credential, inside the
  one backup copy that has none of that file's protections (§9). That trade was declined.

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

### Evidence in, workouts out (v1.5)

§1 says the server supplies evidence and the conversation supplies judgement. This section is
what that means for the tool surface, end to end.

#### The read sweep — how the agent gets the numbers

A session opens with four tool calls, not forty. They cost about eight Garmin requests, which
is roughly twelve seconds at §6's budget with the burst absorbed:

| Call | Garmin requests | Why this shape |
|---|---|---|
| `garmin_training(metrics=[status, load_ratio, readiness, vo2_max, lactate_threshold], date=today)` | 2–3, coalesced | three metrics live in one `get_training_status` payload; the map dedupes the sources before any request is made |
| `garmin_health(metrics=[sleep, hrv, resting_hr, body_battery], range=last_7_days)` | ~4, range endpoints | a trend in one request per metric, never 28 day-calls |
| `garmin_activities(window=last_14_days)` | 1 | what was actually run, against what was prescribed |
| `garmin_workouts(action=scheduled, window=next_7_days)` | 1–2 | what is already on the calendar |

Three requirements follow, and none of them is cosmetic:

- **Tools take a list of metrics, not one.** A singular `metric` argument turns the sweep into
  eight tool calls; each round trip costs the agent context and the human latency. This is the
  concrete form of §5's "12–18 parameterized tools, not 110 narrow ones".
- **The `metrics` enum is the agent's vocabulary.** It is how the model learns what exists, so
  each value carries a one-line meaning and its unit in the tool schema. The enum is built from
  the metric map, filtered by `tools.json` — so a metric this watch cannot answer is not
  offerable, and `capabilities.json` finally has a consumer (§13's finding 7.a).
- **Every value self-describes.** Value, unit, source endpoint, date measured, and — when
  there is nothing — which of `no_data` / `not_matured` / `unsupported` applies. Without that
  last field the model cannot tell "you did not wear it" from "your watch cannot" from "three
  more weeks of wear needed", and it will state one of them confidently.

**Cycle data is opt-in and read-only.** `get_menstrual_calendar_data(startdate, enddate)`
exists, and the probe deliberately never calls it or its siblings (`source/probe.py`). That
stands: it is never swept, never arrives unasked, and is offered only as a metric the operator
has enabled. The opt-in governs *collection*, not containment — once requested it travels to
the model provider exactly like every other metric, per §9.

#### The write path — preview, approve, then write

**A write is never the first act.** `garmin_workouts` has a `preview` action that composes and
validates a session and returns the structure, and composes nothing against Garmin: zero
requests, no budget, no 429. It is therefore also the first write-path behaviour that is
testable without a network (§8).

- **Approval carries the previewed structure.** The create action takes the exact structure the
  preview returned, not a re-composition from the same arguments. Otherwise the human approves
  one thing and a second composition is what lands — the same value recorded against a
  different event.
- **Create and schedule are one action, or an orphan is reported.** `upload_*` returns a
  `workoutId`; `schedule_workout(workout_id, date)` is a second request. Abandon the
  conversation between them and a template exists on Garmin that appears on no calendar — and
  next week's session reads the *calendar*, so it will never see it. Either both happen or the
  response says what was left behind.
- **Re-prescribing updates in place.** `update_workout` replaces the whole workout via PUT,
  keeps the id, and therefore keeps every calendar schedule pointing at it valid
  (`garminconnect/__init__.py:3168`). So "week 3 Thursday threshold" prescribed twice resolves
  to an update, matched by a naming convention checked against `get_workouts` — not a second
  upload. Upload is for a name that does not exist yet; update is for one that does; the plan
  says which, rather than leaving two producers of the same id able to disagree.
- **The response returns both ids.** Undo needs the `scheduledWorkoutId`, which is not the
  `workoutId` — `unschedule_workout` takes the former (`__init__.py:3471`). Without keeping it,
  rolling back costs a month-scoped re-read first.
- **Reading the calendar is month-scoped, not window-scoped.** `get_scheduled_workouts(year,
  month)` takes a calendar month and converts to Garmin's 0-indexed month internally
  (`__init__.py:3426`). "The next seven days" across a month boundary is **two** requests, and
  a single call silently returns a partial week.
- **A partial write reports what it got to.** A plan write that 429s halfway must never read
  like a smaller plan that succeeded — the rule v1.4 already gave the probe, applied to writes.
- **Nothing is pushed to a device by default.** `push_workout_to_device` creates a third copy
  of a session, on hardware, which a later edit cannot reach. Scheduling is enough; the watch
  syncs the calendar.
- **Tool annotations are honest.** `ToolAnnotations` carries `read_only_hint`,
  `destructive_hint`, `idempotent_hint` and `open_world_hint` (verified by import). Reads keep
  the current `read_only_hint=True`; `preview` is read-only and idempotent; create and schedule
  are neither read-only nor idempotent, and are **not** destructive, because
  `delete_workout`/`unschedule_workout` stay available and §5's four permanent deletion
  exclusions are untouched.
- **Composition lives in `source/`.** The workout builders are `garminconnect.workout`, so a
  `from garminconnect.workout import RunningWorkout` in `server.py` would break §11. The
  composer sits beside the metric map, imports nothing from `client.py`, and makes no requests.

---

### Strength, and how hard (v1.6)

#### A strength session is a circuit of named exercises held for a time

Garmin stores an exercise as a `category`/`exerciseName` pair from a fixed catalogue of 1527
entries. It is never free text, so **a name the catalogue does not hold cannot be written at
all.** Resolution is exact display name before substring, case-insensitive; an ambiguous term
is **refused with its candidates listed, never resolved to the nearest match.** The failure
directions are not symmetric: refusing costs one conversational turn, guessing puts a different
exercise on the watch under a name that reads correct in the preview.

Refusing is only half a design, so `garmin_exercises` searches the catalogue. It reaches no
endpoint — the catalogue is a constant compiled into the library — so it spends no request
budget and cannot hit a rate limit. Every name it returns resolves unambiguously, which is the
property that makes it worth having and is asserted as such.

**A named exercise measured in *time* is a shape the library does not build.**
`create_strength_exercise_step` ends on REPS and takes no duration; `create_strength_rest_step`
is timed and names nothing. The combination is this project's, assembled from the library's own
enum members through `ExecutableStep`'s `extra="allow"`. **CONFIRMED live 2026-10-31** — workout
1746708075: Garmin accepted it and the watch shows each exercise name, its duration and the
rounds as planned.

A circuit's rounds are one repeat group, and **a repeat group cannot vary by iteration**, so the
rest after the final round cannot be dropped while keeping the rests between rounds. The preview
states that rather than letting it appear on the watch. Writing the rounds out flat is equally
valid and is what the first live session did.

Timed `warmup` and `cooldown` steps are allowed in a strength session. They carry no exercise
name by design and are exempt from the category check. **Garmin's acceptance of a warm-up step
inside a `strength_training` workout is unverified** and settles on the next live strength
create.

**Reps and weight are deliberately not built.** Both are expressible upstream; neither was
asked for, and every kind offered is a kind the live cycle has to confirm.

#### Pace targets — and why this reverses v0

v0 refused every target, and the reason given was that *"every TargetType Garmin offers is
zone-based and this account's zones have never been fetched."* The second half is still true.
The first half was **too broad**: an explicit pace *range* is a pair of absolute speeds and
needs no zone fetched, no heart-rate data and no profile read. So the refusal narrows rather
than disappears.

**What v1.6 allows:** an explicit pace range on a running step.
**What stays refused:** heart-rate targets, pace *zones*, power, cadence and resistance — every
target whose meaning depends on a profile this account has never been read for.

The rules:

- **Input is min/km, as a range, faster bound first** — `["4:20", "4:40"]`. A human approves
  min/km and must never be shown m/s.
- **Converted to m/s** as `1000 / seconds-per-km`, **rounded to 3 decimals before the approval
  marker is computed.** Rounding after the marker would make preview and create disagree. The
  `600.0`-vs-`600` class of mismatch does **not** apply to a non-whole float — measured, not
  assumed — but one converter feeding both the structure and the summary is what guarantees it.
- **Garmin's side is `workoutTargetTypeKey` `"pace.zone"` (id 6) with `targetValueOne` and
  `targetValueTwo`.** Every part of that sentence is community convention: the library writes
  only `"no.target"` and holds no pace key at all. **[Mahi-verify]** by building one pace step
  in Connect and reading it back — the key string, the unit, the `displayOrder`, and **which
  value Garmin expects first.** Until that read-back, the ordering lives in one named constant.
- **Refused:** an empty range; bounds equal; the slower bound first; `mm:ss` with seconds ≥ 60;
  fractional seconds; any pace outside roughly 2:30–12:00 per km.
- **A pace target on a strength step is refused.** Every upstream builder accepts a target, so
  this is expressible and must be stopped by sport rather than by hope.
- **The preview shows min/km and the structure carries m/s**, and both come from one conversion.
  The words and the bytes being two renderings of one value is the whole guarantee; re-parsing
  the input string for the summary would let the human approve the right words over the wrong
  workout.
- **`estimated_duration_basis` must stop claiming there is no pace** for a step that now carries
  one. The sentence was true when written and becomes false the moment this ships. **Settled
  2026-10-31, option (a):** the sentence changes and **the arithmetic does not**. A paced distance
  step still contributes 0, and the basis says why — a target is what the athlete was *told* to
  run, not a measurement of what they do run, so estimating from it would turn an instruction into
  a computed number in the same displayed field this rule exists to keep invented numbers out of.
  Option (b), estimating from the range's midpoint, was considered and rejected on that ground.
- **An out-of-band edit in Connect is overwritten.** The update path reads a workout's name and
  id and never its stored target, so a pace changed by hand in Connect is replaced by the next
  re-prescription. Consistent with "the calendar wins", and stated here because the losing side
  is now a value a human may have deliberately set.

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

**v1.5: the seam ships with `garmin_training`; the store does not.** v1.4 said the cache
arrives "alongside its first real consumer", and `garmin_training` is that consumer. It
arrives half-built on purpose, and the arithmetic is why:

- **A single call does not need it.** `garmin_training` answering one question costs two or
  three requests, and the burst is four — so the wait is zero. A cache saves nothing there.
- **The amplification v1.4 worried about is not a cache problem.** Three training metrics
  arriving in one `get_training_status` payload is solved by resolving all requested metrics
  *first* and fetching each source once per call. That is per-call coalescing: ordinary code,
  no TTL, nothing to invalidate. The cache's real job is repeat calls across turns and across
  the two containers, which is a later and smaller problem.
- **Honest TTLs are still unmeasured.** Today's `training_status` changes during the day;
  yesterday's does not. Which fields move intraday is exactly what phase 5c measures, and
  setting TTLs before it would be the invention v1.3 refused.

**What the seam is:** one function in `source/`, the only caller of `_garmin_access` on the
data path, which coalesces repeated sources within a call. Two constraints on it:

- **It sits above `_garmin_access` (`source/client.py:52`), never inside it.** A future cache
  hit must skip both the lock and the budget; a hit that still waits two seconds for a token
  it does not need saves nothing and holds the cross-process lock while doing it.
- **The `garmin_auth_status` exemption is enforced at the seam, not remembered.** That tool's
  whole job is proving the tokens work *now*; it must be structurally incapable of a cached
  answer.

**When the store lands**, it keys on the fetched payload plus the map version, never on metric
and date (v1.4's A.5 / B.1 — the key is now settled, the store is what moves). Two things are
owed before it ships, and neither is owed today: the TTLs from 5c, and a decision this
document has not made — **a payload cache is health data at rest.** `capabilities.json` holds
no readings by design; a cache of fetched payloads is readings on the volume, beside the
credential, inside the one backup copy with none of the file's protections. What may be
stored, under what mode, excluded from which backup, and for how long is a §8/§9 question to
answer when the store is built, not while it is being used.

**Writing a plan is budget-shaped, and that changes its tool.** Twelve weeks at four sessions
is roughly 48 uploads plus 48 schedules: about 96 requests, or three minutes of wall clock at
one request per two seconds. The limiter will not complain — each individual `acquire` waits
about two seconds against a 30-second timeout (`ratelimit.py:45-47`) — but no MCP client waits
three minutes for a tool result. So a plan is written **a week at a time**, or by a deliberate
CLI command in the shape of `probe`. Deciding this here rather than discovering it at the first
twelve-week plan.

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
  Config** rather than guessing the path. **Confirmed 2026-10-26 (step 0b)** on the live
  install, `Claude_pzs8sxrjxfjjc` — and confirmed by *causation*, not by reading a filename:
  editing that file moved the running containers onto a new image tag. It had been found by
  filename in the previous session and recorded as an inference until then. The README said
  only `claude_desktop_config.json` with no Store caveat, which is now fixed there too.
- A packaged app does **not** reliably inherit the shell `PATH`. `"command": "docker"` can
  fail to resolve with no visible error — the server simply never appears. Use the absolute
  path to `docker.exe`, with backslashes escaped for JSON.

**Pin the image tag in the client config, not `latest`.** `docker run` does not re-pull a
moving tag: it uses whatever was last pulled. A config on `latest` therefore runs a silently
stale image with nothing anywhere saying so. `garmin_auth_status` reports `server_version`,
stamped by CI with the commit, so the running build is identifiable **from the payload**.

> **Correction, 2026-10-26 (step 0b).** This section used to say the running build was
> "always identifiable" from `server_version`. It is not. The field reached the client and
> the client summarised it away, so the first real "which build is answering?" was answered
> by `docker ps`, from outside the conversation — the exact thing the stamp was added to
> avoid. **A field a summarising client can drop is not a verification mechanism unless
> something asks for it by name.** The reliable check is a capability probe: call a tool that
> cannot exist on the older image and let its existence prove the build. Pinning, and the
> reason for pinning, are unaffected — the laptop had run 36 commits behind for eight weeks,
> which is the failure this paragraph exists to prevent.

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

**Added at v1.5, for the write path and the seam:**

- **Preview is the testable half of the write path.** Composition and structure validation are
  pure functions over the metric map and the `create_*_step` builders — no network, no tokens,
  no budget. So the first write behaviour to ship is also the first one with real unit tests,
  and the cases to cover are the ones upstream accepts and should not: a repeat group with no
  steps, an inverted target range, zero reps, a duplicated `stepOrder`, a target referencing a
  zone this account does not have.
- **Idempotency has a test.** The same prescribed session composed twice must resolve to an
  update in place, not a second upload — asserted against a fake `get_workouts`, no live call.
- **The seam has two tests.** One: two metrics sharing a source produce exactly one fetch, and a
  repeated metric produces one. Two: nothing on the data path calls `_garmin_access` directly —
  an AST check in the shape of `test_layering.py`, because a seam is a call-site discipline and
  erodes the first time someone bypasses it.
- **The month boundary has a test.** A seven-day calendar window spanning two months issues two
  `get_scheduled_workouts` reads, not one.
- **The supervised live cycle is unchanged and now has a precondition.** One hand-run create →
  verify → delete on a throwaway workout, documented — and it runs only once the preview tests
  are green, so the live request is spent confirming Garmin's acceptance rather than finding our
  own bugs.
- **One thing is explicitly untestable here.** Whether Claude Desktop retained the athlete
  context between sessions is a property of a client this repo does not own. It is recorded as
  an assumption (§14.3), not as a coverage gap, and the design's answer to it being false is
  that the plan still lives on Garmin's calendar.
**Added at v1.6:**

- **The approval marker must be proven to survive a float round trip.** A preview→create test
  serialises the structure with `json.dumps`, parses it back, and asserts the marker is
  unchanged. This is the regression test for the `600.0`-vs-`600` bug generalised: measured
  stable for non-whole floats, and now asserted so it stays that way.
- **The m/s conversion is pinned to literals**, both directions, so a refactor cannot quietly
  change what a pace means: 4:40/km → 3.571 m/s, 4:20/km → 3.846 m/s.
- **The summary and the structure are asserted to agree.** One conversion, two readers — the
  test is what stops them becoming two conversions.
- **A pace target inside a repeat group** has a test, because a repeat group's children go
  through the same builder and that is a claim rather than an observation.
- **A pace target on a strength step is refused**, with a test, and the control that the same
  session composes without it.
- **`estimated_duration_basis` changes** when a step carries a pace, asserted on the sentence.
- **Every name `garmin_exercises` returns resolves unambiguously**, parameterised over several
  movement words. The search is only useful if its output can be handed straight back, and that
  is two functions agreeing rather than either being right alone.
- **Baseline for v1.6 work: 638 tests, 2 skipped.** **After pace targets: 684 passed, 2
  skipped** (2026-10-31), +46. Nothing removed; two tests in `test_compose.py` changed premise
  rather than being loosened, because a heart-rate zone is still refused but the refusal's wording
  is no longer "no targets".

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
| Agent writes a *structurally valid but wrong* workout (v1.5) | Medium | A week of misdirected training — reversible, but it may be run before anyone notices | **Accepted. It is the price of §1's division of labour**, named rather than mitigated away: validation cannot catch training sense, and by decision nothing in the server tries to. Reduced by preview-then-approve, by writing a week at a time instead of a block, and by `unschedule_workout` / `delete_workout` staying available |
| Cycle data reaching the model provider (v1.5) | Certain *if asked for* | Special-category data becomes conversation content | **Accepted, and the opt-in is not containment.** Opt-in means never swept by the probe and never arriving unasked. Once requested it travels exactly like every other metric — see the row above on health data reaching the provider |
| Athlete context lost between sessions (v1.5) | Medium | Injuries and constraints re-stated by hand each session | **Accepted deliberately.** The alternative was injury and cycle history at rest on the volume that holds the credential, inside the one backup copy with none of that file's protections. The *plan* survives on Garmin's calendar; only the subjective framing is re-typed |
| Health-data **network** exposure | Near-zero | High if it were a public HTTP endpoint | Mitigated by §7 / §7a: loopback only, on demand |
| Health data **reaching the model provider** | **Certain, by design** | The data you ask for becomes conversation content | **Accepted and stated plainly.** A model cannot reason over data it cannot see. Tool results travel to the provider exactly like pasted text. Nothing is fetched unless asked for — there is no sync, no background job. "Tokens stay on the laptop" is true; "nothing leaves the laptop" is false, and conflating them is the actual risk |
| Password enters a browser | Certain once §7a ships | Password managers, autofill, extensions and devtools see the field | **Accepted for UX.** A real regression against `getpass`; CLI bootstrap retained as the tighter path (§4) |
| Two containers, one token file | **Observed** 2026-08-30 | Concurrent refresh could lose an update | Cross-process file lock around refresh. Structural risk; not yet observed to bite |
| Single-maintainer bus factor | Low now | Fork treadmill | Accepted. MIT means forking is at least possible |
| A pace range written with its bounds swapped (v1.6) | Medium until `[Mahi-verify]` closes | The watch alerts continuously and the easy end becomes the hard end | **Accepted for one session.** Which of `targetValueOne`/`Two` Garmin expects first is unmeasured, so the ordering lives in **one named constant**, is read back from Connect before the first live pace write, and is wrong in only one place if wrong at all |
| An important id exists only in a tool response (v1.6) | **Observed five times** | `unschedule_workout` takes the scheduled id and nothing else, so a calendar entry becomes removable only by hand in Connect | **Decided 2026-10-31: no ledger.** The id is looked up from Garmin's calendar at undo time (§15.13 option 2), so a dropped id is *recoverable* rather than stored twice. A returned field is not a delivered one — summarising clients dropped `server_version`, the provenance note, the refusal distinction, the write's ids and `id_keys` — which is why the answer is a lookup and not a louder response. Unschedule-by-lookup is **v1.7 scope**; until it ships, an undo is still by hand in Connect, and the residual risk is accepted for that window |
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
the pressure this rule exists to survive.

**The workout builders are part of the library (v1.5).** `garminconnect.workout` — the typed
models, the `create_*_step` helpers — and `garminconnect.exercises`, which carries the ~2,550
exercise taxonomy with `resolve()` and `find()`, are both inside the boundary. So workout
composition belongs in `source/`, not in the tool layer: a
`from garminconnect.workout import RunningWorkout` in `server.py` would be the first breach of
this rule, and it would look entirely innocent. The composer sits beside the metric map, makes
no requests, and returns a structure the tool layer passes through. There are no sister modules to be consistent with in a new
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

---

## §14 v1.5 10-dimension stress-test absorption notes

Walked against `sop/stress-test-10-dimensions.md`: all ten dimensions, then passes A and B.
A and B are **not** skippable here — the amendment introduces new state (a composed plan, a
workout template, a schedule) and a new consumer of existing state (the metric map, read by
the write path).

### §14.1 Scope of this amendment, and what is carried again

No dimension was missed in v1.4, so there is no coverage gap to explain. What this subsection
records instead is the **deferral**: v1.4's header promised §7, §7a and §9's location finding
in v1.5, and v1.5 does not deliver them. They are deployment and privacy corrections with no
bearing on who does the coaching or on the write path, and folding them in would push the
review past the twenty minutes a review can honestly be given. They move to **v1.6**. §7a's
bind-address prose is therefore knowingly wrong for a second consecutive version — recorded
here so it is not rediscovered as a bug, exactly as v1.4's 10.c asked.

### §14.2 #1 Edge cases — 5 findings, 4 actionable

- **1.a:** `get_scheduled_workouts(year, month)` takes a **calendar month**, not a date window,
  and converts to Garmin's 0-indexed month internally (`garminconnect/__init__.py:3426`). "The
  next seven days" across a month boundary needs two requests; one call returns a partial week
  and looks complete. — **ACTIONABLE §5.**
- **1.b:** `unschedule_workout` takes the **`scheduledWorkoutId`**, not the `workoutId`
  (`__init__.py:3471`). The id required to undo is not the id the create returns, so rollback
  without keeping it costs a month-scoped read first. — **ACTIONABLE §5.**
- **1.c:** a plan write interrupted by a 429 leaves some sessions scheduled and some not. A
  partial write that reads like a smaller successful plan is the probe's finding 1.d in a new
  place. — **ACTIONABLE §5.**
- **1.d:** `schedule_workout` validates the date *format* only. Two workouts on one date, or a
  date in the past, are accepted. The agent must not be the only guard. — **ACTIONABLE §5**:
  the preview states every date, and the create refuses a date already occupied unless the
  action is explicitly a replacement.
- **1.e:** with no athlete-context store, the context is empty on the first turn of every
  session. If any tool *requires* feel or time, the first call fails. — **✓ by design**: every
  subjective input is an optional argument to `preview`, never a precondition of a read.

### §14.3 #2 Unverified assumptions — 4 findings, 2 `[Mahi-verify]`

- **2.a:** "Claude Desktop remembers the athlete context between sessions" is **assumed, not
  verified.** Desktop memory and project instructions are client features this repo does not
  own; they may be off, chat-scoped, or pruned. — **`[Mahi-verify]`.** The design's answer if
  false is already in place: the plan persists on Garmin's calendar, so the loss is the
  subjective framing, not the work. Nothing in this document should promise more than that.
- **2.b:** "cycle data is in Garmin at all" is unverified. The endpoint exists; whether this
  account logs anything is unmeasured, and deliberately so — the probe has never called it. —
  **`[Mahi-verify]`**, and the metric stays opt-in regardless of the answer.
- **2.c:** "`update_workout` keeps existing schedules valid" — **✓ VERIFIED**: it PUTs the whole
  structure, forces `workoutId` in the body to match, and the id is preserved
  (`__init__.py:3168`), so calendar entries pointing at it survive.
- **2.d:** "upstream validates nothing on upload" — **✓ VERIFIED**: `upload_workout` checks only
  that the payload is a dict or list (`__init__.py:3148`). Consistent with v1.4's §5 contract,
  which grepped `workout.py` for `raise|validate|assert` and found none.

### §14.4 #3 Actual code checks — 4 findings, 3 actionable

- **3.a:** `ToolAnnotations` really does expose `read_only_hint`, `destructive_hint`,
  `idempotent_hint` and `open_world_hint` (checked by importing `mcp.types`), and
  `garmin_auth_status` currently sets the first two (`server.py:58`). So honest annotations on
  write tools are available rather than aspirational. — **ACTIONABLE §5.**
- **3.b:** `_garmin_access` (`source/client.py:52`) takes the cross-process lock and *then* the
  request budget. A cache seam placed inside it would make a future hit wait for a token it
  does not need, while holding the lock. The seam must sit **above** it. — **ACTIONABLE §6.**
- **3.c:** limiter constants read, not assumed: `DEFAULT_RATE_PER_SEC = 0.5`,
  `DEFAULT_BURST = 4.0`, `DEFAULT_TIMEOUT_S = 30.0` (`ratelimit.py:45-47`). Each `acquire` waits
  about two seconds, well inside its own timeout — so the limiter will not fail a plan write;
  the MCP client's patience is what fails it. — **ACTIONABLE §6.**
- **3.d:** `garminconnect.exercises` ships `EXERCISES` (~2,550 entries), `BY_NAME`,
  `CATEGORIES`, `resolve()` and `find()`. Full-body strength composition is a lookup, and this
  project writes no exercise taxonomy of its own. — **✓ VERIFIED.**

### §14.5 #4 Security — 4 findings, 1 actionable

- **4.a:** declining the athlete-context store removes a class of exposure rather than
  mitigating it: no special-category data at rest beyond what Garmin already holds, nothing new
  beside the credential on `/data`, nothing new in the unprotected `C:\garmin-backup` copy. —
  **✓ ALIGNED**, and it is the principal security argument for the decision.
- **4.b:** the cycle opt-in governs collection, not containment. Once requested, the data
  reaches the model provider like every other metric. — **ACTIONABLE §9**, so the opt-in is
  never mistaken for a privacy boundary it does not provide.
- **4.c:** the write path is a new authenticated mutation surface but introduces no new
  credential, secret or scope — same bearer token, same boundary. Its residual risk is a wrong
  workout, which §9 now carries explicitly with reversibility named. — **✓ ALIGNED.**
- **4.d:** no new dependency: `garminconnect[workout]` is already required and already pinned in
  `requirements.lock`, so `pip-audit`'s surface is unchanged. — **✓ VERIFIED.**

### §14.6 #5 Vision alignment — 2 findings, 0 actionable

- **5.a:** the decision closes v1.4's knowingly-open wording drift — §13's "coach" prose against
  §1's purpose line. The coach is the conversation; the server is evidence. An open item is
  retired rather than another added. — **✓ ALIGNED.**
- **5.b:** it strengthens §1's medical non-goal. A server that returns measurements, units and
  Garmin's own phrasing cannot diagnose; a server that returned "back off this week" would be
  one short step from doing so. — **✓ ALIGNED.**

### §14.7 #6 Architecture consistency — 4 findings, 2 actionable

- **6.a:** the seam lives in `source/`, below the tool layer, and no tool grows TTL logic —
  §6's placement rule honoured in its weakened form. — **✓ ALIGNED.**
- **6.b:** `garminconnect.workout` and `.exercises` are inside the §11 boundary, so composition
  cannot live in the tool layer. A `from garminconnect.workout import …` in `server.py` would be
  this rule's first breach and would look harmless. — **ACTIONABLE §11.**
- **6.c:** metric-plural tool signatures are the concrete form of §5's "parameterized, not
  narrow" rule, not a new convention. — **✓ ALIGNED.**
- **6.d:** `preview` must reach Garmin not at all — otherwise a dry run costs budget and can
  429, and the safest action in the flow becomes a rate-limit risk. — **ACTIONABLE §5.**

### §14.8 #7 Impact on other features — 3 findings, 2 actionable. State sub-analysis → Pass A

- **7.a:** `garmin_auth_status` must never be cached (§6). Nothing changes today because no
  store ships, but the exemption must be structural at the seam rather than remembered when the
  store lands. — **ACTIONABLE §6.**
- **7.b:** the open v1.3 commitment — `garmin_auth_status` reporting the tool set actually in
  force — becomes more load-bearing, not less: with write tools in `tools.json`, "are writes
  enabled right now" is a question the agent must be able to ask. — **ACTIONABLE §5**, still
  open, now with a second reason.
- **7.c:** a `tools.json` change still needs an MCP client restart, since clients cache the tool
  list at session start. Adding write tools does not change that. — **(no action)** — covered by
  §5's existing rule.
- The #7 state sub-analysis is **triggered** (a workout's existence and its scheduled-ness are
  new state) and is walked in Pass A below, per the SOP's own routing.

### §14.9 #8 Test coverage — 5 findings, 4 actionable

- **8.a:** `preview` is the first write-path behaviour that is unit-testable — pure composition
  and validation, no network. The cases are the ones upstream accepts and should not: empty
  repeat group, inverted target range, zero reps, duplicated `stepOrder`, a target naming a zone
  this account lacks. — **ACTIONABLE §8.**
- **8.b:** idempotency needs its own test: the same session composed twice resolves to an update
  in place, not a second upload, against a fake `get_workouts`. — **ACTIONABLE §8.**
- **8.c:** the seam needs two tests — one fetch for two metrics sharing a source, and an AST
  check that nothing on the data path calls `_garmin_access` directly, in the shape of
  `test_layering.py`. A call-site discipline with no test is a convention, and conventions
  erode. — **ACTIONABLE §8.**
- **8.d:** the month boundary (1.a) needs a test: a seven-day window across two months issues
  two reads. — **ACTIONABLE §8.**
- **8.e:** whether Desktop retained the athlete context is untestable from this repo. Recorded as
  an assumption (2.a), not as a gap, so it is not mistaken for missing coverage. — **✓
  documented.**

### §14.10 #9 Deployment & rollback — 3 findings, 1 actionable

- **9.a:** no new file on the volume, no migration, no new env var. Rollback remains an image tag
  change. — **✓ VERIFIED.**
- **9.b:** the write path's rollback is **data, not code** — a plan already on the calendar
  survives an image rollback, so undoing it means `unschedule_workout` then `delete_workout`,
  which needs both ids from 1.b. — **ACTIONABLE §5.**
- **9.c:** an older image meeting state written by a newer one: the only new persisted state is
  in Garmin's own calendar, in Garmin's own format, so there is no version skew to survive. The
  opposite of `capabilities.json`'s 9.a problem, and for the same reason — we are not the
  schema's owner here. — **✓ VERIFIED.**

### §14.11 #10 Risks — 4 findings, 3 actionable

- **10.a:** a plan lands on a real calendar and, via sync, a real watch. Blast radius is a week
  of misdirected training, reversible but possibly run before noticed. — **ACTIONABLE §9**, new
  row, mitigated by preview-then-approve and week-at-a-time writes.
- **10.b:** the agent becomes the only guard on training sense. A structurally valid session can
  still be a stupid one — 10×1 km at VO2max pace passes every check this server will ever have.
  By decision nothing in the server tries to catch it. — **ACTIONABLE §9**, stated as the price
  of §1 rather than as an oversight.
- **10.c:** plan-write wall clock (~190 s for twelve weeks) exceeds any MCP client's patience,
  so the tool shape must be a week at a time or a CLI command. — **ACTIONABLE §6.**
- **10.d:** carrying §7, §7a and §9's location finding a second time leaves shipped behaviour
  and documented behaviour diverging for another version. — **Accepted**, recorded in §14.1 and
  the journal.

### §14.12 Pass A — states, producers and consumers (matrix + 6 findings)

Surfaces, as the checklist adapts to this repo: the **MCP tool layer** (`server.py`), the
**`source/` layer**, **Garmin** as the remote system of record, **disk** (`/data`), and **the
conversation** — which is a consumer, and in this amendment the most important one.

| State | Producer(s) | MCP tool layer | `source/` | Garmin | Disk | Conversation |
|---|---|---|---|---|---|---|
| Previewed plan (composed, unwritten) | `preview` | returns it | composes it | — | **not persisted** | reads and approves it |
| `workoutId` (template) | `upload_*` **and** `update_workout` | returns it | calls both | stores it | — | sees the name, not the id |
| Scheduled-ness (`scheduledWorkoutId` + date) | `schedule_workout` | returns it | calls it | stores it, syncs to watch | — | sees the dates |
| Athlete context (feel, time, injury, cycle) | the human, in chat | **no surface** | **no surface** | — | **no surface** | the only holder |
| Metric values in flight | the seam | returns them | fetches, coalesces | serves them | **no store (v1.5)** | reasons over them |
| Exposure set (`tools.json`) | the web UI | reads at startup | — | — | `/data` | claimed via `garmin_auth_status`, **absent** |

- **A.1:** the previewed plan is **persisted nowhere**, and the create action could recompose
  from the same arguments instead of taking what was shown. Then the human approves one
  structure and a second one lands — the right value against the wrong event. — **ACTIONABLE
  §5**: approval carries the previewed structure itself.
- **A.2:** `workoutId` has **two producers that can disagree** — `upload_*` mints a new id,
  `update_workout` reuses one — and nothing said which wins for a re-prescription. —
  **ACTIONABLE §5**: name match ⇒ update in place; no match ⇒ upload.
- **A.3:** `scheduledWorkoutId` is **consumed by the only undo path and not kept by the
  producer**. A mechanism whose input never leaves the call that produced it is not a mechanism.
  — **ACTIONABLE §5** (with 1.b, 9.b).
- **A.4:** the athlete context has **no producer, consumer or store anywhere in this system** —
  its row is empty on every surface but one. That is the shape pass A exists to flag, and here
  it is the intended design, so it must be written down as a **non-mechanism**: no tool may
  require it, and no tool or description may imply the server remembers it. — **ACTIONABLE
  §1/§5.**
- **A.5:** the seam is one producer with zero consumers — structurally the same shape as
  `capabilities.json`'s problem. Accepted knowingly, because a seam is a call-site discipline
  rather than stored state, but it therefore depends on a test to exist at all. — **ACTIONABLE
  §8** (8.c).
- **A.6:** `tools.json` will now carry write tools, and its claimed consumer
  (`garmin_auth_status` reporting the live set) still does not exist. — duplicate of 7.b, same
  disposition.

### §14.13 Pass B — the lifecycles (5 findings)

- **B.1 (request lifecycle):** preview → approve → create → schedule is four steps with three
  handoffs, and the composition can be redone at step three. Same defect as A.1, found from the
  other direction, which is why it is the one to fix first.
- **B.2 (chat lifecycle):** abandon the conversation between `create` and `schedule` and an
  orphan template exists on Garmin that appears on no calendar — and next week's session reads
  the *calendar*, so it will never see it. — **ACTIONABLE §5**: one action, or the response
  names what was left behind.
- **B.3 (chat lifecycle, resumption):** a template hand-edited in Garmin Connect between
  sessions leaves the calendar and the previously-agreed plan disagreeing. **The calendar wins**,
  by the same decision that made it the store. — **ACTIONABLE §5**, stated rather than assumed.
- **B.4 (artifact lifecycle):** `push_workout_to_device` creates a third copy on hardware that a
  later edit cannot reach, so a revised plan would leave a stale session on the watch. —
  **ACTIONABLE §5**: nothing is pushed by default; scheduling is enough.
- **B.5 (deployment lifecycle):** an old reader meeting new rows — not applicable, since the new
  state lives in Garmin's format, not ours. — **✓ VERIFIED** (9.c).

### §14.14 Net v1.5 changes

| Finding | Section | Change |
|---|---|---|
| — (the decision itself) | log, §1 | Coaching lives in the conversation; non-goal added: no recommendation engine, no training science in Python |
| — (the decision itself) | log, §1, §9 | Athlete context lives in Desktop chat only; non-goal added; the risk of re-typing it is accepted |
| — (the decision itself) | log, §1 | Garmin's calendar is the store for plan continuity |
| 2.b, 4.b | §5, §9 | Cycle data opt-in, never swept; the opt-in is collection, not containment |
| 1.e, A.4 | §1, §5 | Subjective inputs are optional arguments and a declared non-mechanism |
| A.1, B.1 | §5 | Approval carries the previewed structure, never a re-composition |
| A.2 | §5 | Name match ⇒ `update_workout` in place; no match ⇒ upload |
| 1.b, A.3, 9.b | §5 | Both ids returned, so undo needs no search |
| B.2 | §5 | Create and schedule are one action, or the orphan is reported |
| B.3 | §5 | The calendar wins over a remembered plan |
| B.4 | §5 | No push to device by default |
| 1.a, 8.d | §5, §8 | Calendar reads are month-scoped; a cross-month window is two requests, with a test |
| 1.c | §5 | A partial plan write reports what it reached |
| 1.d | §5 | Preview states every date; create refuses an occupied date unless replacing |
| 3.a | §5 | Honest `ToolAnnotations` on every write tool |
| 6.d | §5 | `preview` makes no Garmin request at all |
| 6.b | §11 | The workout builders are inside the boundary; composition lives in `source/` |
| 3.b, 7.a | §6 | The seam sits above `_garmin_access`; `garmin_auth_status` exempt structurally |
| 3.c, 10.c | §6 | A plan is written a week at a time, or by a CLI command |
| A.5, 8.a–8.c | §8 | Tests: preview validation, idempotency, seam coalescing, no direct `_garmin_access` |
| — | §6 | Cache key settled (payload + map version); **store deferred**, and "a payload cache is health data at rest" is an open §8/§9 question |
| 10.a, 10.b | §9 | Two new risk rows: a valid-but-wrong workout, and the agent as sole guard |
| 2.a | §14.3 | `[Mahi-verify]`: Desktop context retention is assumed, not verified |
| 10.d | §14.1 | §7, §7a and §9's location finding carried to v1.6; §7a knowingly stale |

---

## §15 v1.6 10-dimension stress-test absorption notes

### §15.1 Coverage gap rationale, and an order that was wrong

v1.5 walked all ten plus A and B. No dimension was skipped, so there is no coverage gap to
explain. There is a **process** gap, and it belongs at the top of this section rather than
buried in it: **two of v1.6's three items were built and merged before this document authorised
them.** Named strength exercises measured by time (#39, #42) and strength warm-ups (#43) shipped
across 2026-10-31 while §5 still described a running-only composer. Every forward reference in
the code said "amendment drafted, NOT yet ratified", so the gap was visible rather than hidden —
but visible is not the same as correct. The ten dimensions below are therefore walked against
**(c) pace targets as a plan**, and against **(a) and (b) as shipped code**, which is the honest
framing and is why #3's findings are unusually concrete.

### §15.2 #1 Edge cases — 6 findings, 5 actionable

- 1.a: `mm:ss` with seconds ≥ 60 — `"4:75"` parses arithmetically to 5:15 and is not what a
  human meant — **ACTIONABLE §5**, refuse.
- 1.b: bounds equal — `["4:30","4:30"]` is a zero-width range and Garmin's handling is unknown —
  **ACTIONABLE §5**, refuse and ask for a range.
- 1.c: bounds inverted — `["4:40","4:20"]` — **ACTIONABLE §5**, refuse naming the expected order
  rather than silently sorting, because sorting would hide a caller that misunderstands the field.
- 1.d: a pace target is expressible on a **strength** step, since every upstream builder accepts
  a `target_type` — **ACTIONABLE §5**, refuse by sport. **Sharpened 2026-10-31, during
  implementation:** it is load-bearing rather than belt-and-braces, because strength's `warmup`
  and `cooldown` are built by **running's** `create_warmup_step` / `create_cooldown_step`, which
  do take a target. The refusal must also come **before** the `exercise` builder's early return,
  or a pace on an exercise step is dropped instead of refused.
- 1.e: paces outside ~2:30–12:00/km — **ACTIONABLE §5**, refuse (Mahi specified the band).
- 1.f: fractional seconds — `"4:20.5"` — no separate action; 1.a's parser is strict and rejects it.

### §15.3 #2 Unverified assumptions — 5 findings, 4 `[Mahi-verify]`

- 2.a: **`"pace.zone"` as the key string.** Checked the library: it writes `"no.target"` and
  writes no pace target anywhere. Entirely community convention — `[Mahi-verify]` via a read-back
  from Connect. **CORRECTED 2026-10-31, during implementation:** this said the library "holds no
  pace key at all", which is too strong. `garminconnect/workout.py` defines `class TargetType`
  with `PACE_ZONE = 6`, so **the id is upstream's** and is read from it rather than written as a
  literal. What stays unverified is narrower and is what `PACE_CONTRACT` holds: the key *string*,
  the `displayOrder`, the unit, and which value carries the faster bound.
- 2.b: pace stored as **m/s** — plausible, unmeasured — `[Mahi-verify]`.
- 2.c: **which of `targetValueOne`/`targetValueTwo` is the faster bound** — unmeasured, and Mahi
  flagged it himself — `[Mahi-verify]`. Confined to one named constant (§15.11, A.1).
- 2.d: the target block's `displayOrder`. The `no.target` block uses `displayOrder: 1`, equal to
  its id, so `6` for pace is an inference from one sample — `[Mahi-verify]`.
- 2.e: Garmin accepting a **warm-up step inside `strength_training`** — shipped in #43, unmeasured.
  Settles free on the next live strength create. No action beyond saying so.

### §15.4 #3 Actual code checks — 5 findings, 4 actionable

Source read, not inferred.

- 3.a: `validate_structure` refuses **any** target that is not `no.target`
  (`compose.py`, the `carries target` branch). The relaxation must be **per sport and per target
  type**, not a global loosening — **ACTIONABLE §5**. A global relax would silently re-permit
  targets on strength steps, which is 1.d arriving by the back door.
- 3.b: `_build_step` calls `builder(duration, order)` with two arguments, and **every** upstream
  builder — warmup, interval, recovery, cooldown, distance-interval — already accepts a third
  `target_type`. So the plumbing exists upstream and only our call site needs changing. No
  `extra="allow"` assembly is needed for the step itself, only for the two target values.
  **CORRECTED 2026-10-31, by a failing test:** "every upstream builder" reads the five *running*
  builders and over-generalises. `create_strength_rest_step(duration_seconds, step_order)` takes
  **no `target_type` at all**, so passing one — even `None` — is a `TypeError`. The third
  argument is therefore passed only when a target exists, which also keeps an untargeted session
  composing byte-for-byte what it composed before pace shipped.
- 3.c: `WorkoutStep` sets `extra="forbid"` (#41), so an undeclared `pace_per_km` would be refused
  with "Extra inputs are not permitted" and the feature would be unreachable — **ACTIONABLE §5**,
  the schema field ships in the same change as the composer.
- 3.d: `approval_for` / `_numeric_canonical` — **measured** stable across `json.dumps`/`loads`
  for non-whole floats, so the `600.0`-vs-`600` class does not apply to pace values. Mahi's
  concern is retired by measurement rather than by argument — **ACTIONABLE §8** as a regression
  test, no code change.
- 3.e: `estimate_duration` returns 0 for a distance step and its basis says *"this account has no
  measured pace"*. With a pace target on that step the sentence is **false** — **ACTIONABLE §5/§8**.

### §15.5 #4 Security — 3 findings, 1 actionable (deferred)

- 4.a: pace targets add no secret, no endpoint, no persistence and no new dependency. A pace is
  not health data. No action.
- 4.b: the **durable-id proposal** (§15.13) would add persistence, and a file of workout ids on
  the tokens volume is account data at rest, inside the one backup copy that has none of the
  token file's protections — the exact argument that kept athlete context out of the volume at
  v1.5. **ACTIONABLE §9** as an open decision; deliberately not folded into v1.6.
- 4.c: no logging change. `_configure_logging`'s `force=True` and its regression test already
  cover the rule that tool arguments and responses are never logged at debug.

### §15.6 #5 Vision alignment — 2 findings, 0 actionable

- 5.a: §1 forbids the server designing the session. A pace **range supplied by the conversation**
  is an input the server converts and validates; it never chooses one. Consistent — and the
  distinction is worth keeping sharp, because "pace targets" sounds like prescription.
- 5.b: pace targets advance v1.4's "planning and adjusting running training" more than any other
  open item, since a running workout could previously say how far and how long and **never how
  hard**.

### §15.7 #6 Architecture consistency — 2 findings, 2 actionable

- 6.a: pace parsing and conversion belong in `source/compose.py` beside `resolve_exercise`; §11
  says `source/` does not know MCP exists. The min/km **rendering** is a server-layer concern —
  **ACTIONABLE §5**, and the numbers it renders must come from the same conversion, not from
  re-parsing the input.
- 6.b: one function builds the target block, so the structure and the summary cannot disagree —
  **ACTIONABLE §5**. Two call sites computing the same conversion is the DRY violation that
  matters here, because the two outputs are read by different audiences.

### §15.8 #7 Impact on other features — 4 findings, 2 actionable. **State sub-analysis triggered → Pass A**

- 7.a: the strength path depends on `no.target` being enforced; see 3.a — **ACTIONABLE §5**.
- 7.b: a repeat group's children go through `_build_step`, so a pace target inside a repeat works
  with no extra code. That is a claim, not an observation — **ACTIONABLE §8**, test it.
- 7.c: "a step's target" is a new **state** a step moves through — sub-analysis triggered, §15.11.
- 7.d: an **older build** handed a pace structure refuses it at `validate_structure` rather than
  writing something wrong. A graceful refusal, and worth knowing given the laptop has drifted
  twice. No action.

### §15.9 #8 Test coverage — 7 findings, 7 actionable

All seven are recorded in §8 under "Added at v1.6" rather than repeated here. Baseline captured:
**638 passed, 2 skipped** before any pace work.

### §15.10 #9 Deployment & rollback — 3 findings, 1 actionable

- 9.a: reaches the laptop by a re-pin; rollback is re-pinning the previous tag. The re-pin script
  now matches whatever reference is in the config rather than a hardcoded expected tag, so a
  rollback cannot be blocked by this repo's own notes being stale. No action.
- 9.b: **re-pinning does not roll back a workout already on Garmin's calendar.** The only undo is
  `unschedule_workout` with the scheduled id — which is precisely the id that keeps being dropped
  from summaries — **ACTIONABLE §9**, and the reason §15.13 exists.
- 9.c: no migration, no schema, no process manager. Nothing to roll back but an image tag.

### §15.11 Pass A — states, producers and consumers (matrix + 6 findings, 5 actionable)

The new state is **a step's target**: absent (`no.target`) or an explicit pace range. The
surfaces that exist in this repo — there is no HTTP API, no web UX for workouts, no eval surface
and no tracing:

| State | Producers | Consumers |
|---|---|---|
| step target block (`no.target` \| `pace.zone`) | `compose._pace_target` (new); upstream builders' default | `validate_structure`; `approval_for` (whole structure); Garmin; the **watch** |
| pace range as min/km | the MCP tool argument | the preview's `step_summary` (**human-facing**) |
| pace as m/s, 3dp | one converter (new) | the `structure` field; the payload Garmin stores |
| which bound is `targetValueOne` | one named constant (new) | the payload; **the watch's alert direction** |
| `estimated_duration_basis` | `estimate_duration` | the preview response; the human |
| the written ids | `write.py` `_first_id_with_key` | the tool response **only** — nothing persists them |

- A.1: the pace range would have **two producers** if the summary re-parsed the input string
  while the structure used the converted value. They can disagree, and the human would approve
  the right words over the wrong workout — **ACTIONABLE §5**, one converter, both read it.
- A.2: `estimate_duration` is a consumer of distance steps that **cannot see targets at all**, so
  its basis sentence lies the moment a pace exists — **ACTIONABLE §5/§8** (same as 3.e).
- A.3: `approval_for` consumes the whole structure, so the **rounding must happen upstream of it**
  or preview and create disagree — **ACTIONABLE §5**.
- A.4: the **watch** is a consumer with no test surface. The only evidence is a live run, which is
  why the live check is "run outside the range and see whether it alerts" rather than "the
  workout exists". No action beyond scheduling that check.
- A.5: **nothing persists.** The target exists in the response and on Garmin, which is fine for a
  pace — but it is the same shape as the id gap: *a mechanism whose output never leaves the
  response is not durable* — **ACTIONABLE §9**, §15.13.
- A.6: the **MCP schema is a consumer**, and `extra="forbid"` means an undeclared field is
  refused. The schema and the composer must ship together or the feature is unreachable —
  **ACTIONABLE §5** (same as 3.c, reached from the other direction, which is what A is for).

### §15.12 Pass B — the lifecycles (5 findings, 2 actionable)

- B.1: **request lifecycle.** preview → approve → create. The marker must be computed from the
  **rounded** values; rounding at serialisation time only would mismatch — **ACTIONABLE §5**
  (A.3 from the lifecycle side).
- B.2: **the update path recomposes nothing** — it carries the approved structure through, so a
  re-prescribed pace workout keeps its target. No finding.
- B.3: **chat/session lifecycle, and Pass B's characteristic shape.** The human approves
  `4:20–4:40` in words while the bytes carry `3.846`/`3.571`. Not a wrong value but a **wrong
  rendering of the right one** if the two are computed separately. Covered by A.1; recorded here
  because this is the lifecycle where it would be noticed too late.
- B.4: **deployment lifecycle.** A reader on the old build meeting a pace structure refuses
  gracefully (7.d). The reverse — a new build reading an old workout — reads only name and id.
  No finding.
- B.5: **artifact lifecycle.** A workout's pace edited by hand in Connect is **silently
  overwritten** by the next re-prescription, because the update path never reads the stored
  target. Consistent with "the calendar wins", but the losing side is now a value a human may
  have set on purpose — **ACTIONABLE §5**, state it.

### §15.13 Making an important id durable — **RATIFIED: option 2**, a lookup and no ledger

Mahi's ask. Drafted as a proposal and **ratified on 2026-10-31** with v1.6; the implementation
is v1.7. The problem is measured: a returned field is not a delivered one, and five have been
dropped by summarising clients. Asking by name retrieves them; nothing makes them durable, and
`unschedule_workout` takes an id that nothing stores.

Three options, with the objection to each:

1. **A JSON ledger on the tokens volume** (`/data/workout_ids.json`): workout id, scheduled id,
   name, date, build. Cheap, survives restarts, and both containers can write it under the
   existing cross-process lock. **Objection:** §4's volume holds the credential and has exactly
   one backup copy; adding account data to it is the argument v1.5 used to keep athlete context
   out. It is also a second store of a fact Garmin already holds.
2. **Garmin's calendar is already the ledger** — the scheduled id is readable from
   `get_scheduled_workouts` for a month, so undo needs a **read**, not a store. **Objection:**
   one extra request per undo, and it only works while the entry is still on the calendar.
   **This is the option most consistent with the v1.5 decision that Garmin's calendar is the
   store for plan continuity.**
3. **Return it harder** — a dedicated `garmin_last_write` tool reporting the most recent write's
   ids from process memory. **Objection:** per-process, and Desktop runs two containers, so it
   answers from whichever one is asked. Fails exactly when it is needed, after a restart.

**Ratified: option 2.** Option 1 stays as a fallback, to be reached for only if a read-based
undo proves unreliable in use; option 3 is rejected outright, because it fails after exactly the
restart that makes it necessary. Option 2 adds no store, contradicts no earlier decision, and
the request cost is paid only on an undo, which is rare. What it needs is an `unschedule` path
that *finds* the id rather than being handed it — **committed to v1.7**, not built in v1.6.

Two things the v1.7 plan inherits and must walk in its own ten dimensions rather than assume
here: `get_scheduled_workouts` takes a **calendar month**, not a date window (§14.2's 1.a), so a
lookup near a month boundary reads two months or misses; and the lookup's failure mode when the
entry is already gone must be a **bounded refusal naming the date**, not a silent no-op, because
a silent success reads as evidence the undo happened.

### §15.14 Net v1.6 changes

| Finding | Section updated | Change |
|---|---|---|
| — (the decision itself) | log, §5 | Running and strength are both composed; strength is a timed circuit of named exercises |
| — (the decision itself) | log, §5 | An exercise is Garmin's catalogue pair, never free text; ambiguity refused |
| — (the decision itself) | log, §5 | Explicit pace ranges allowed; zone-based targets stay refused |
| — (the decision itself) | log, §5 | Reps and weight not built in v1.6 |
| 1.a–1.e | §5 | Five pace refusals: seconds ≥ 60, equal bounds, inverted bounds, strength steps, out-of-band paces |
| 2.a–2.d | §5 | The whole Garmin-side pace contract marked `[Mahi-verify]`; ordering confined to one constant |
| 3.a, 7.a, 1.d | §5 | The target relaxation is per sport and per target type, never global |
| 3.c, A.6 | §5 | The schema field ships with the composer, or `extra="forbid"` makes the feature unreachable |
| 3.e, A.2 | §5, §8 | `estimated_duration_basis` stops claiming there is no pace |
| 6.a, 6.b, A.1, B.1, B.3, A.3 | §5 | One converter; the summary renders min/km from it; rounding precedes the marker |
| B.5 | §5 | An out-of-band pace edit in Connect is overwritten, and that is now stated |
| 3.d, 7.b, and §8's list | §8 | Seven test requirements, incl. the float round-trip marker test Mahi asked for |
| 2.c, 10.a-equivalent | §9 | New risk row: a swapped pace bound, accepted for one session, confined to one constant |
| 4.b, 9.b, A.5 | §9, §15.13 | New risk row: an important id exists only in a response. **Option 2 ratified** — lookup, no ledger; implementation is v1.7 |
| 10.d-equivalent | header, §15.1, §15.15 | §7, §7a and §9's location debts **committed to v1.7**, not carried a third time |
| — | §15.1 | Recorded that (a) and (b) shipped before this document authorised them |

### §15.15 Decisions taken at ratification (2026-10-31)

Three decisions were taken at the moment of approval rather than during the walk, so they are
recorded here rather than folded into §15.2–§15.14 as if the walk had produced them.

1. **v1.6 is ratified as drafted, pace targets included.** The ten dimensions plus passes A and
   B in §15.2–§15.12 are the walk of record. (a) and (b) were already shipped and are ratified
   after the fact, which §15.1 names as the wrong order; (c) is authorised before any code, which
   is the right one.
2. **Durable ids: option 2** — look the scheduled id up on Garmin's calendar at undo time, no
   ledger. §15.13 holds the reasoning and what v1.7 inherits. This keeps the tokens volume free
   of account data, which was 4.b's objection.
3. **The three carried debts are committed to v1.7**, firmly: §7's client wiring, §7a's bind
   address, §9's location exposure via activity detail.

**Whether any of the three blocks pace targets — checked, and no.** Asked before the commitment
was made, because "commit it to the next version" is only a scope decision if the current
version does not depend on it:

- **§7 client wiring** is a documentation correction about the Desktop config surface. It touches
  no module. Pace targets reach the laptop by a re-pin, which 9.a already covers.
- **§7a bind address** is prose about a port on the web UI. There is no pace surface there and no
  composer path through it. The prose is wrong about a port, not about a target.
- **§9 location exposure** is a read path, `get_activity_details` and its polylines. Pace targets
  add no request, no endpoint and no persistence — 4.a — and §15.11's state matrix has no row
  that reaches activity detail.

The one adjacency anywhere is A.5 and 4.b, and those point at the **id** question, which is
decision 2 above and already v1.7 scope. So the deferral costs v1.6 nothing.

**No new ten-dimension walk is owed for these three decisions.** They add no state to v1.6 and
no consumer of state: decisions 1 and 3 are scope statements, and decision 2's mechanism is
explicitly v1.7, where it gets its own ten plus A and B before any code — with §15.13's two
inherited findings as its starting point. Said explicitly, per the SOP, rather than skipped
quietly.
