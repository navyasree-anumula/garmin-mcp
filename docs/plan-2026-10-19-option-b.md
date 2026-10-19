# Plan — Option B: CI cadence, the ratelimit flake, and the HTTP surface

**Status:** drafted 2026-10-19, **not ratified**. Two `[Mahi-verify]` decisions block execution (§10.1).
**Baseline:** `main` = `76e62aa`, green, image published (`latest`, `sha-76e62aa`). **288 passed, 2 skipped**
(measured this session, after `git pull --ff-only origin main`).
**Stress-tested across 10 dimensions + passes A and B** — audit trail in §11.

---

## 1. Why this session, and why not phase 5c

Phase 5c (the live probe sweep) is the next substantive step and it is deliberately **not** this
session. Three decisions have been carried across two sessions without being made, and two of them
have now bitten twice:

- The dependency gate only fires on a PR or a push. On 11 October it found six weeks of advisories
  the moment someone opened a PR; on 16 October it found a five-day-old one the same way, and `main`
  sat red through two docs merges **publishing no image**, because `Build and publish` needs
  `[test, audit]`.
- `tests/test_ratelimit.py:112` fails on a loaded runner on a compliant run, so a red CI run there
  costs a judgement call every time.
- `serve --http` is wired, unused, unauthenticated, and was the sole reason CVE-2026-59951 applied
  to this project.

None of this needs the laptop, a Garmin request or a token. Phase 5c needs all three.

**One hard dependency, discharged.** Local `main` was two merges behind `origin/main` (`6a87f5d` vs
`76e62aa`). Branching from the local ref would have silently dropped `garmin_training`, the map, the
seam **and the `mcp` bump** — reintroducing the vulnerable lockfile on a branch that looked clean.
Pulled before any branch was cut; that is step zero, not a fourth errand.

---

## 2. Scope

### In

| Item | Files |
|---|---|
| Scheduled dependency audit | new `.github/workflows/audit.yml` |
| Ratelimit flake | `tests/test_ratelimit.py` |
| Remove `serve --http` *(pending §10.1)* | `garmin_mcp/cli.py`, `garmin_mcp/web/app.py` (one comment), `README.md`, `Dockerfile` (comments), new `tests/test_transport_surface.py` |
| Ref and branch hygiene | no files — git operations |
| Session record | `docs/journal.md` |

### Out, explicitly

- `docs/SCOPE.md`. §7 and §7a are owed to **v1.6** with their own ten dimensions plus passes A and
  B. See finding **A.1**: skipping SCOPE here is correct but it leaves a recorded debt, not nothing.
- Anything needing a Garmin request, the laptop, or the token volume.
- `capabilities.json`'s consumer, `tools.json`, `garmin_health`, the write path.
- The payload store (§6's cache) — unchanged by this plan.

### Branches

This plan doc, `docs/architecture.html` and the journal entry are committed on
**`docs/option-b-plan-and-architecture`** — documentation only, no code.

Execution gets its **own** branch, cut from `origin/main` after this one merges:
`fix/ci-cadence-and-transport-surface`.

---

## 3. The scheduled audit

A new workflow rather than a `schedule:` trigger on `docker.yml`, for one deciding reason:
`build-and-push` needs `[test, audit]`, so a cron on `docker.yml` would **rebuild and republish
`latest` weekly from an unchanged SHA**. Gating that off with `if: github.event_name != 'schedule'`
works, but it adds a conditional to the publish path to serve a job that does not need it.

```yaml
name: Dependency audit
on:
  schedule:
    - cron: "17 6 * * *"      # daily; see §11.5 (#4.b) for why not weekly
  workflow_dispatch:
permissions:
  contents: read              # no packages: write — this job never publishes
jobs:
  audit:
    # Mirror of docker.yml's `audit` job. Kept duplicated on purpose (#6.b):
    # a scheduled run must not be able to reach `Build and publish`.
    ...
```

`workflow_dispatch` is not decoration: a cron's first firing is otherwise the only proof the
workflow runs at all, and that proof would arrive a day late.

**Accepted cost:** `pip-audit -r requirements.lock --strict` now lives in two files. A cross-
reference comment goes in each. A composite action is more machinery than the duplication costs.

---

## 4. The ratelimit flake

### 4.1 The diagnosis changed when the limiter was actually read

The first diagnosis — an idle stretch refilling the bucket and producing a second free gap — **is
wrong**, and `ratelimit.py:123` is why:

```python
tokens = min(budget.burst, tokens + elapsed * budget.rate_per_sec)
```

Tokens are capped at `burst`, and `acquire` spends exactly one (`:126`). So after any grant the
bucket holds zero and **every** consecutive grant must wait `1/rate`. With `burst=1, rate=10` as the
test sets (`:90`), all five gaps should be ≥ 0.1s. There is no second free gap to find.

The flake is in the **measurement**, not the limiter. The child records the stamp *after* `acquire`
returns (`tests/test_ratelimit.py:36-37`):

```python
acquire(tokens, timeout=60)
stamps.append(time.time())
```

Between the return and the append the process can be preempted. A stamp is therefore
*grant time + an unbounded scheduling delay*, and `sorted()` merges that delay into the gaps: two
grants a real 0.1s apart appear 0.01s apart if the earlier one's stamp was delayed 0.09s more than
the later one's. On a two-core shared runner with other jobs resident, that is ordinary.

**This also kills the fix I proposed first.** A sub-window invariant
`(j - i + 1) <= burst + rate × (s_j - s_i)` degenerates, for `j = i + 1` and `burst = 1`, to
`s_{i+1} - s_i >= 0.1` — the exact assertion that flakes. It would have been no improvement, dressed
as rigour.

### 4.2 What to do instead

Assert only what the delayed stamps can actually support: the **widest window**, with the bound
derived rather than guessed.

- True lower bound for N grants: `(N - burst) / rate` = `(6 - 1) / 10` = **0.5s**.
- Today's line 113 asserts `>= 0.35` with no stated derivation.
- Proposed: a pure helper `min_window_seconds(n, rate, burst)` returning 0.5 for `(6, 10, 1)`, and
  the test asserting `window >= min_window_seconds(...) - SLACK` with `SLACK = 0.1` and a comment
  naming what the slack absorbs (delay on the *first* stamp, which shrinks the measured window).
- **Delete** lines 110-112 (`gaps`, `paid`, the per-gap assertion).

Why the widest window survives what the per-gap check does not: a per-stamp delay δ shifts one
boundary of one gap and the whole gap with it, whereas across 6 stamps only delays on the first and
last move the window at all — and the slack is sized for exactly that.

**Negative control**, per this repo's convention: `test_the_rate_check_can_actually_fail` feeds
`min_window_seconds` a stamp list that violates the bound and asserts the comparison fails. Pure
function, no subprocess, and it makes the assertion itself falsifiable.

**Coverage is not reduced.** The deleted assertion was not measuring the limiter; the kept one is,
and it is now tighter (0.40 effective vs 0.35) and derived.

---

## 5. Removing `serve --http`

### 5.1 The argument

Not primarily surface reduction. **The only thing HTTP would buy is already documented as
impossible for this deployment.** `docs/SCOPE.md:656`:

> HTTP would buy reach from Claude web or a phone, and would cost a real authentication design.
> Not worth it now.

And `README.md`'s "Claude chat / mobile" section explains why that reach is unobtainable regardless:
Claude connects to remote MCP servers from Anthropic's cloud, not from the device, so a server on
the laptop is unreachable no matter how it is configured.

So the flag buys nothing and costs an unauthenticated endpoint serving special-category health data,
reachable by typing one word — while `SCOPE.md:34` records the transport as stdio since v1.2 and
`:644` claims "No listening port during normal operation". **The code contradicts the written
decision.**

### 5.2 Call sites, all read rather than assumed

| File:line | What | Action |
|---|---|---|
| `cli.py:88-93` | the `streamable-http` branch, incl. `# noqa: S104` | delete |
| `cli.py:347-351` | `--http`, and `--port` | delete **both** — see #3.a |
| `README.md:114` | the loopback bullet | delete |
| `Dockerfile:40-41` | `-p 127.0.0.1:3001:3001` comment | delete |
| `web/app.py:16` | cites `serve --http` as precedent for binding `0.0.0.0` | **reword, do not delete** — the reasoning stands, the citation goes |
| `docs/journal.md:521` | historical record | leave untouched |
| `docs/SCOPE.md` §7/§7a | the written decision | **v1.6** — see A.1 |

`_cmd_serve` collapses to `mcp.run()`. No test exercises `--http`
(`test_stdio_purity.py:47` runs bare `serve`), and `docker-compose.yml` deliberately defines no MCP
service at all.

### 5.3 New guard

`tests/test_transport_surface.py` — an AST walk asserting no module calls `mcp.run` with a
`transport` keyword, plus `test_the_transport_check_can_actually_fail`. A separate file because each
guard file in this repo owns one claim (`test_layering.py` owns imports, `test_fetch_seam.py` owns
the doorway).

Reverting is `git revert`. Nothing persists, nothing migrates.

---

## 6. Ref and branch hygiene

Three stale refs, all verified this session:

| Ref | State | Action |
|---|---|---|
| local `main` | was `6a87f5d`, two merges behind | **done** — fast-forwarded to `76e62aa` |
| `docs/journal-2026-10-16-map-and-advisory` | tip `6e60622`, merged by #22, ancestor of `origin/main` | delete local + remote |
| `feature/metric-map-and-training-tool` | remote tip `91000b2`, **local tip `83e7963`** — local is behind its own remote; both ancestors of `origin/main`, merged by #18 | delete local + remote |

Both branch tips confirmed ancestors of `origin/main` with `git merge-base --is-ancestor` before any
delete is proposed. The `RESUME HERE` block also still claims "#18 open, awaiting Mahi's merge" and
"192 tests on `main`" — both stale, both corrected in this session's journal entry.

---

## 7. Execution order

1. `git fetch origin main:main` *(already current)*; cut `fix/ci-cadence-and-transport-surface` from
   `origin/main`; `git branch --unset-upstream`.
2. `.github/workflows/audit.yml`. Commit: `ci: audit the lockfile on a schedule, not only on a PR`.
3. `tests/test_ratelimit.py` + the helper and its negative control. Run
   `.venv/bin/python -m pytest tests/test_ratelimit.py -q` **ten times** — a flake fix verified by
   one green run is not verified. Commit: `test(ratelimit): assert the window bound, not per-gap spacing`.
4. *(Pending §10.1)* remove `--http` across the seven call sites; add
   `tests/test_transport_surface.py`. Commit: `fix(cli): remove the unused Streamable HTTP transport`.
5. Full suite + journal entry. Commit: `docs: journal — …`.
6. Push with an explicit refspec. Open PR. `workflow_dispatch` the new audit workflow and record the
   result **and who was notified** (#2.c).
7. Delete the two merged branches (§6).

---

## 8. Test counts

Before: **288 passed, 2 skipped**.

| Outcome | Added | After |
|---|---|---|
| `--http` removed | +2 transport guard, +2 ratelimit helper/control | **292** |
| `--http` kept | +2 ratelimit helper/control | **290** |

Never lower. The per-gap deletion removes assertions inside an existing test, not a test.

---

## 9. Deployment and rollback

Reaches `main` by PR, as always; the image publishes on merge. Pre-state for restoration:
`main` = `76e62aa`, 288 passed / 2 skipped, image `latest` + `sha-76e62aa` published.

| Change | Rollback | Time |
|---|---|---|
| `audit.yml` | delete the file, or disable from the Actions UI | < 1 min |
| ratelimit test | `git revert` | < 1 min |
| `--http` removal | `git revert` | < 1 min |
| branch deletions | re-push the recorded tips (`6e60622`, `91000b2`) | < 1 min |

No migration, no data, no `.env`, no prod. Nothing touches the laptop or the token volume.

---

## 10. Open decisions and risks

### 10.1 Blocking — plan cannot be locked without these

- **`[Mahi-verify]` Remove `serve --http`, or keep it deliberately?** §5 is the case for removing.
  Keeping is defensible; what is not is leaving it undecided for a third session. If kept, step 4
  drops and the journal records the retention with its reason and a revisit trigger.
- **`[Mahi-verify]` Daily or weekly audit cron?** The journal said weekly. §11.5 (#4.b) argues
  daily: a GitHub-hosted runner minute is free at this scale, and both episodes had multi-day
  windows. Recorded as a deviation from the journal's wording rather than slipped in.

### 10.2 Risks

- **10.a** The flake fix masks a real limiter regression. *Mitigated:* the kept bound is derived and
  tighter than today's, and the negative control proves the comparison can fail.
- **10.b** `--http` is wanted back. *Blast radius:* a revert of ~10 lines.
- **10.c** The architecture page drifts from the code with nothing catching it. *Accepted,* with the
  framing this repo already uses for verdicts and field paths: it is a **dated snapshot**, labelled
  with the commit it describes, not a contract. See **B.4**.
- **10.d** This session delays phase 5c by one sitting. *Accepted:* two of the three decisions have
  now cost an evening each, and the gate is the only thing that has ever caught an advisory here.

---

## 11. v1.0 10-dimension stress-test absorption notes

### 11.1 Coverage gap rationale

First version; no prior pass to account for. Passes A and B are run rather than skipped: the plan
removes a delivery surface (`serve --http`) and adds a producer of CI state, which is a consumer
question even though no product state machine is touched.

### 11.2 #1 Edge cases (5 findings, 2 actionable)

- **1.a** Adjacent-pair sub-window assertions are not vacuous — they are *identical* to the per-gap
  check that flakes. **ACTIONABLE §4.2** (this is why only the widest window is asserted).
- **1.b** `assert len(stamps) == 6` keeps a short-read from making the bound vacuous — retained. *(no action)*
- **1.c** Stamps are wall-clock `time.time()` across two processes (necessarily — a monotonic clock
  is not comparable across processes). An NTP step mid-test would distort the window. Pre-existing,
  probability negligible over 0.5s. *(no action, recorded)*
- **1.d** GitHub disables scheduled workflows after a period of repository inactivity, so the gate
  could silently stop on a quiet repo. **ACTIONABLE §11.3 (#2.b)** — verify the exact behaviour
  before relying on the cron as the only producer.
- **1.e** After removal, `serve --http` exits 2 with argparse's usage text on stderr. Under stdio
  that is the correct channel and the message names the valid flags. *(no action)*

### 11.3 #2 Unverified assumptions (5 findings, 3 flagged)

- **2.a** "Nothing uses `serve --http`" — verified across the repo by grep (`cli.py`, `README.md`,
  `Dockerfile`, `web/app.py`, `docs/journal.md` only). **NOT verifiable here** for the laptop's
  Claude Desktop config, which this repo does not contain. **`[Mahi-verify]`** — confirm its args
  end in `"serve"` with no `--http`.
- **2.b** Scheduled-workflow deactivation after inactivity: assumed, not verified. **`[Mahi-verify]`**
  or verify from GitHub's docs at execution time.
- **2.c** Who receives a *scheduled* run's failure notification: assumed the repo owner, **not
  verified**. A cron whose failures reach nobody is a producer with zero consumers (see **A.3**).
  **ACTIONABLE §7 step 6** — record the actual recipient after the first dispatch.
- **2.d** The original flake diagnosis was an inference and was **wrong**; corrected in §4.1 by
  reading `ratelimit.py:123-126`. The failing run itself was not re-examined, so the *measurement*
  explanation is also inference — but the fix no longer depends on which story is right, because it
  asserts only what delayed stamps can support. ✓ VERIFIED (code), recorded (run).
- **2.e** A standalone audit workflow needs no `pip install -e .` — verified against
  `docker.yml:45-48`, which installs only `pip-audit`. ✓ VERIFIED

### 11.4 #3 Actual code checks (3 findings, 2 actionable)

Read, not grepped from headers: `cli.py:88-93, 173-192, 336-355`; `tests/test_ratelimit.py:28-40,
66-113`; `garmin_mcp/source/ratelimit.py` in full; `.github/workflows/docker.yml` in full;
`docker-compose.yml` in full; `README.md:100-120`; `Dockerfile:40-41`; `web/app.py:16`;
`docs/SCOPE.md:34, 634-691`.

- **3.a** `args.port` on the `serve` subparser is referenced **only** at `cli.py:93`. Removing
  `--http` orphans `--port`. `_cmd_web`'s uses of `args.port` belong to the separate `web`
  subparser. **ACTIONABLE §5.2** — remove both.
- **3.b** `pyproject.toml` has **no `[tool.ruff]` section** and CI runs no linter (`docker.yml` runs
  pytest and pip-audit only), so the `# noqa: S104` on the deleted line is inert and needs no
  follow-up. ✓ VERIFIED
- **3.c** The limiter caps tokens at `burst` and spends exactly one, which is what overturned the
  diagnosis. **ACTIONABLE §4.1.**

### 11.5 #4 Security (4 findings, 2 actionable)

- **4.a** Removing `--http` removes an unauthenticated listener for special-category health data
  permanently, rather than once per advisory. ✓ ALIGNED with §7.
- **4.b** Weekly leaves a window of up to 7 days; daily costs one runner minute. Both October
  episodes had multi-day windows. **ACTIONABLE §3 / `[Mahi-verify]` §10.1** — plan specifies daily,
  flagged as a deviation from the journal's "weekly".
- **4.c** `workflow_dispatch` is restricted to users with write access, and the audit job consumes
  no secrets, so manual triggering adds no exfiltration surface. ✓ VERIFIED
- **4.d** The new workflow must declare `permissions: contents: read` and must **not** carry
  `packages: write`. **ACTIONABLE §3.**

### 11.6 #5 Vision alignment (3 findings, 0 actionable)

- **5.a** Removing `--http` makes the code match §7's v1.2 transport decision. ✓ ALIGNED
- **5.b** The cron serves §8's posture that the lockfile is audited rather than trusted. ✓ ALIGNED
- **5.c** Nothing here advances §5 or the write path. This is maintenance, justified in §1 and
  accepted as a one-session delay to 5c (**10.d**). *(no action)*

### 11.7 #6 Architecture consistency (4 findings, 2 actionable)

- **6.a** `.github/workflows/audit.yml` sits beside `docker.yml`. ✓ ALIGNED
- **6.b** The audit command duplicated across two workflows is a DRY violation, **accepted
  deliberately** so a scheduled run cannot reach `Build and publish`; cross-reference comments in
  both. **ACTIONABLE §3.**
- **6.c** Every new guard needs a `test_the_..._can_actually_fail` companion — the convention that
  caught the doorway check's own AST bug. **ACTIONABLE §4.2 and §5.3** (both new guards get one).
- **6.d** The transport guard belongs in its own file, not in `test_layering.py` (imports) or
  `test_stdio_purity.py` (subprocess output), because each guard file owns one claim. ✓ ALIGNED

### 11.8 #7 Impact on other features (5 findings, 1 actionable)

- **7.a** `test_stdio_purity.py:47` invokes bare `serve` — unaffected by the removal. ✓ VERIFIED
- **7.b** `p_web`'s `--port` is a separate subparser argument and is untouched. ✓ VERIFIED
- **7.c** The limiter itself, `selftest`, and every product code path are unchanged by §4 — the
  change is test-only. ✓ VERIFIED
- **7.d** A new workflow does not become a required status check automatically; if `main` carries
  branch protection, confirm the required-check set is unchanged. **ACTIONABLE §7 step 6.**
- **7.e** *State-machine sub-analysis.* The only state touched is the CI run conclusion and the
  publish gate. The cron deliberately does **not** reproduce the `audit → build-and-push` coupling,
  so a red cron run leaves `latest` exactly as it was. Stated because the existing coupling is what
  made `main` publish nothing on 16 October. ✓ ALIGNED

### 11.9 #8 Test coverage (4 findings, 1 actionable)

- **8.a** Baseline captured by measurement, not inheritance: 288 passed, 2 skipped at `76e62aa`. ✓ VERIFIED
- **8.b** Expected after: 292 (or 290 if `--http` is kept). §8. *(no action)*
- **8.c** A flake fix proven by one green run is not proven. **ACTIONABLE §7 step 3** — ten
  consecutive runs of the file.
- **8.d** The cron cannot be unit-tested; `workflow_dispatch` is its verification and is a manual
  step in §7. *(no action)*

### 11.10 #9 Deployment and rollback (2 findings, 0 actionable)

- **9.a** Every rollback is a revert or a file deletion; no migration, no data, no `.env`, no prod
  surface. §9. ✓ ALIGNED
- **9.b** Branch-deletion rollback requires the tips to be recorded *before* deleting — they are, in
  §6. ✓ ALIGNED

### 11.11 #10 Risks (4 findings)

Recorded in §10.2 as 10.a–10.d with mitigations and acceptances.

### 11.12 Pass A — states, producers and consumers (3 findings, 2 actionable)

| State / surface | Producers | Consumers | Finding |
|---|---|---|---|
| `serve --http` flag | `cli.py:347` argparse | `cli.py:88-93`; `README.md:114`; `Dockerfile:40-41`; `web/app.py:16` (prose); `SCOPE.md` §7/§7a; `journal.md:521` (historical) | **A.1** |
| `--port` on `p_serve` | `cli.py:351` argparse | `cli.py:93` **only** | **A.2** |
| CI audit conclusion on `main` | PR runs; `main` push runs; **new:** scheduled run | `build-and-push` `needs:` (docker.yml only); GitHub notification; checks UI | **A.3** |
| ratelimit stamps | two child processes, `time.time()` | the parent's assertion | 0 findings |

- **A.1** `SCOPE.md` is a consumer this plan deliberately does not update. Today the code offers a
  transport SCOPE says it does not have; **after execution §7a's bind-address prose will cite a flag
  that no longer exists** — the same disagreement pointing the other way. Carrying it silently would
  make §7a *more* stale, not less. **ACTIONABLE §2 "Out"** — recorded as a v1.6 input in the journal,
  explicitly, not as a vague "owed".
- **A.2** `--port` becomes a state with a producer and **no consumer** the moment the http branch
  goes. **ACTIONABLE §5.2** — remove both in the same commit.
- **A.3** The finding worth the pass: the scheduled run is a new producer whose **only** consumer is
  a notification, and it gates nothing. If scheduled-run failures reach nobody (#2.c, unverified),
  the cron is one producer and zero consumers — precisely the `capabilities.json` failure mode this
  repo already carries. **ACTIONABLE §7 step 6** — verify the recipient, and if nobody is notified,
  the cron needs a consumer that cannot be ignored before it counts as a fix.

### 11.13 Pass B — lifecycles (4 findings, 1 actionable)

- **B.1** *Deployment lifecycle.* A client config pinned to an older `sha-` tag keeps working after
  the removal; one pinned to `latest` that re-pulls **and** passes `--http` breaks. Covered by
  **2.a**'s `[Mahi-verify]`.
- **B.2** *Audit lifecycle.* If the cron fires before a fix lands, that run stays red in history
  permanently — exactly like `4abc620` and `9ca9b9e`. So "is `main` healthy?" cannot be answered by
  "is the newest audit run green?" without checking which commit it ran against. Recorded; no
  mechanism change.
- **B.3** *Request lifecycle.* Untouched: no product code path changes except the deletion of a
  transport that nothing starts. 0 findings.
- **B.4** *Artifact lifecycle.* `docs/architecture.html` now exists in **two** places — the repo file
  and the published artifact — and they can disagree in both directions (edited here and not
  republished; republished from another conversation and not committed). Characteristically a Pass B
  finding: not a wrong value, a wrong order. **ACTIONABLE** — the repo file is the source, the
  artifact URL is recorded in the journal so a later session *updates* that artifact instead of
  forking a new one, and the file header names the commit it describes (**10.c**).

### 11.14 Net v1.0 changes

| Finding | Section updated | Change |
|---|---|---|
| 1.a, 3.c | §4.1, §4.2 | Diagnosis rewritten from source; the sub-window fix dropped as no better than the status quo |
| 1.d, 2.b | §11.3 | Cron deactivation flagged for verification before the cron is relied on |
| 2.a | §10.1 | `[Mahi-verify]` on the Desktop config |
| 2.c, A.3 | §7 step 6 | Record the notification recipient; a cron nobody hears is not a fix |
| 3.a, A.2 | §5.2 | `--port` removed together with `--http` |
| 4.b | §3, §10.1 | Daily rather than weekly, flagged as a deviation |
| 4.d | §3 | `permissions: contents: read`, no `packages: write` |
| 6.b | §3 | Duplication accepted with cross-reference comments |
| 6.c | §4.2, §5.3 | Both new guards get negative controls |
| 7.d | §7 step 6 | Confirm the required-check set is unchanged |
| 8.c | §7 step 3 | Ten runs, not one |
| A.1 | §2 "Out" | The SCOPE disagreement recorded as a v1.6 input rather than left implicit |
| B.4 | §11.13, §10.2 | Artifact URL in the journal; repo file is the source; header names the commit |
