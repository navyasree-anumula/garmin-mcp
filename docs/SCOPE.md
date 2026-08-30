# garmin-mcp — Scope

**Status:** scoping. No implementation exists.
**Version:** 1.2 · 2026-08-30
**Ratification:** this document is the artifact to approve before any code is written.

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

**The password never enters the MCP surface.** Bootstrap is a CLI that Mahi runs by hand:

```
docker run -it --rm -v garmin-tokens:/data garmin-mcp login
```

The password is read via `getpass`. It is **never** passed as argv, never as `-e` or any
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
command — not a stack trace.

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

---

## §7 Deployment and transport

**Host: Mahi's laptop. Transport: stdio, via `docker run -i`.**

This decision resolves four separate findings at once:

- No listening port, therefore no unauthenticated endpoint serving special-category health
  data and accepting workout writes.
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

**Unverified:** Docker and a Python 3.12 toolchain on the laptop. The `3.12.3` + Docker
check in the plan was run against *staging* and does not transfer. Confirm on the laptop
before the first build. If the laptop is Apple Silicon, the aarch64 wheel situation is
already checked and fine.

**Image hygiene from day one:** tag, and prune old tags. Do not repeat the no-retention
mistake on a second machine.

**Rollback:** previous image tag. The token volume is independent of the image, so a
rollback never costs a re-authentication.

**Back up the token volume before any re-auth attempt.** If a captcha window makes a fresh
login impossible, a saved working token file is the only way back in.

---

## §8 Testing

`pytest` + `vcrpy` cassettes. **CI never touches live Garmin.**

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
- **Baseline: 0 tests.** This repo starts empty. That is the reference point for every
  later count.

---

## §9 Accepted risks

| Risk | Probability | Impact | Position |
|---|---|---|---|
| Garmin breaks auth again | **High** — it happened in March 2026, and there have been six releases in the ten weeks since | Dead until upstream patches | **Accepted, not eliminable.** Floor-and-ceiling pin; track releases |
| Captcha lockout | Medium | Cannot log in | No workaround by design. Token backup, gentle rates, never retry into it |
| Terms-of-service violation | Certain | Own account, own data, single user | **Accepted.** This is the mild end of the spectrum, but it is a real violation and is recorded as one, not rationalised away |
| Agent writes a junk workout | Low | Bad workout on the watch | Structure validation (§5) + delete exclusions |
| Health-data exposure | Near-zero under stdio | High if it were HTTP | Mitigated by §7 |
| Single-maintainer bus factor | Low now | Fork treadmill | Accepted. MIT means forking is at least possible |
| Datacenter-IP flag | **Resolved** | — | Eliminated by the laptop decision (§7) |

---

## §10 Carried forward to build time

- **`mcp` SDK decorator API shape.** Protocol revision 2026-07-28; `mcp` 2.1.1;
  `fastmcp` 3.4.7 stable. The SDK went through a major version bump and its source has not
  been read. Verify before writing tool definitions.
- **Device capability probe** (§5) — must run before the tool surface is fixed.
- **Laptop toolchain** (§7) — Docker and Python 3.12 unverified on the target machine.
- **Rate limit number** (§6) — a guess until there is observed behaviour behind it.

---

## §11 Internal architecture rule

All `garminconnect` imports are confined to `garmin_mcp/source/`. The tool layer never
imports the library directly. There are no sister modules to be consistent with in a new
repo, so "consistency" here means one piece of internal discipline: a future source swap —
official API, a fork, a different library — touches exactly one package.
