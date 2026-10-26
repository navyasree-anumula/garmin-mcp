# Plan — the write path, preview only

**Status:** executed 2026-10-26, approved in session before execution.
**Baseline:** `main` = `190380e`, green, image published (`latest`, `sha-8154e24` on the laptop).
**352 passed, 2 skipped** at the start; **419 passed, 2 skipped** at the end.
**Stress-tested across 10 dimensions + passes A and B** — audit trail in §9.

---

## 1. Why this session

The read half of the daily loop works end to end from Claude Desktop as of step 0b: the laptop
runs `sha-8154e24` and `garmin_health` answered with real data. The loop is **read what the body
says → write a session to the watch → check it got done**, and the write half did not exist.

SCOPE §5 (v1.5) settled the write path's shape and this plan does not re-litigate it. It builds
only the first behaviour §5 names: a **`preview`** that composes and validates a running session
and sends nothing to Garmin — zero requests, no budget, no 429, no tokens. §8 (v1.5) names it as
the first write-path behaviour testable without a network, and that is the reason to do it first:
the one supervised live cycle is then spent confirming Garmin's acceptance rather than finding our
own bugs.

**Nothing here can reach Garmin.** There is no code path from this session's code to a request,
and §9's pass B asserts that structurally rather than by counting calls.

## 2. Decisions taken before planning

Both were put to Mahi with the trade-offs and answered before the plan was written.

- **`estimatedDurationInSecs` = the sum of time-based steps only.** A distance step contributes 0
  and the response says so, including that Garmin may recompute on upload. The rejected
  alternative was an assumed pace: this account has no measured pace, and a fabricated number in
  a field the calendar may display is the failure shape the previous session was spent on.
- **No approval hash yet.** Preview returns the structure alone; the integrity marker arrives with
  `create`, the consumer that checks it. Accepted cost: `create` changes this tool's response
  shape next session. The rejected alternative shipped a field with no consumer, which is
  `capabilities.json`'s problem and this repo does not need a second one.

## 3. Scope

### In

| File | Change |
|---|---|
| `garmin_mcp/source/compose.py` | **new.** Compose a running workout; validate a composed structure; estimate duration. |
| `garmin_mcp/server.py` | **new tool** `garmin_workouts`, `action` enum of exactly one value. |
| `tests/test_compose.py` | **new.** 51 tests: composition, V1–V9 refusals with controls, the estimate, the no-request claim. |
| `tests/test_garmin_workouts.py` | **new.** 16 tests: the tool, its refusals, its schema and annotations. |
| `docs/plan-2026-10-26-write-path-preview.md` | this document. |
| `docs/journal.md` | session entry, and the resume block. |

### Out, explicitly

- `upload_workout`, `schedule_workout`, `update_workout`, `unschedule_workout`, `delete_workout`.
- **Idempotency resolution** (name match ⇒ update in place, no match ⇒ upload). It needs a live
  `get_workouts` read, so it cannot live inside a zero-request preview. It ships with `create`,
  where it gets a consumer on arrival.
- **Every target except `NO_TARGET`.** `TargetType` offers only zone-based targets,
  `get_heart_rate_zones` has never been run against this account, and absolute ranges through
  `ExecutableStep`'s `extra="allow"` are unverified against what Garmin accepts.
- Calendar reads and the month-boundary rule; cassettes; the supervised live cycle.

## 4. The composer

`compose(name, steps, description=None) -> dict` builds through the library's own
`create_warmup_step` / `create_interval_step` / `create_distance_interval_step` /
`create_recovery_step` / `create_cooldown_step` / `create_repeat_group`, wraps the result in
`RunningWorkout`, and returns `to_dict()`.

**The composer assigns `stepOrder`.** Contiguous from 1, flat across the segment — a repeat
group's children continue the parent's numbering rather than restarting. The caller cannot supply
it, which removes a whole bug class at the source instead of validating against it afterwards.

**Two facts read out of the library rather than assumed:**

- `create_*_step(..., target_type=None)` does not leave `targetType` absent. It fills in an
  explicit `{"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}`. So v0 passes nothing
  and the structure still states NO_TARGET — and a test asserts it, because if upstream ever
  changed that default to `None`, `to_dict()`'s `exclude_none` would silently drop the field.
- `create_repeat_group` writes the iteration count into **both** `numberOfIterations` and
  `endConditionValue`. Two fields holding one fact are two fields that can disagree, so V9 asserts
  they do not.

`validate_structure(structure)` runs on the composer's own output inside `compose`. That looks
redundant and is not: the builders are upstream code, so a change in them should fail our tests
rather than Garmin's acceptance. It is also exactly what `create` will run on a structure that
came back from a conversation.

| | Refused | Why |
|---|---|---|
| V1 | no steps | an empty workout is not a session |
| V2 | repeat group with no steps | §8's case |
| V3 | iterations < 2, or missing | zero reps is §8's; one iteration is not a repeat — inline it |
| V4 | duplicate or non-contiguous `stepOrder` | §8's case; unreachable via `compose`, reachable via a supplied structure |
| V5 | duration or distance ≤ 0, or non-finite | `float` accepts NaN and inf and typing catches neither |
| V6 | any target but `NO_TARGET`, including an absent one | zones have never been fetched |
| V7 | empty name | **no length cap invented** — Garmin's limit is unmeasured |
| V8 | a repeat nested in a repeat | the model allows it; Garmin's acceptance is unverified |
| V9 | `numberOfIterations` ≠ `endConditionValue` | two fields, one fact |

Plus: a step measured both ways, a step measured no way, a distance step that is not an interval,
and an unknown step kind — each naming what would work.

`estimate_duration(steps)` returns the number **and the sentence explaining it**, never one
without the other, because an all-distance session estimates 0 and a bare 0 reads as broken. The
basis names how many distance steps contributed nothing, counted per iteration inside a repeat.

## 5. The tool

`garmin_workouts(action="preview", name, steps, description=None)`, annotated
`read_only_hint=True, destructive_hint=False, idempotent_hint=True`. Honest: it composes, returns,
and touches nothing. Deliberately **not** `destructive_hint=True` — a client that puts a
confirmation in front of read-only tools would wall off the safest call in the flow.

`action` is a one-value `Literal`, so the schema cannot offer what does not exist, and it is
guarded in the body as well: the enum stops a compliant client, the guard stops everything else.
A refusal names the valid actions, the same reasoning as `_read_group`'s guard.

The response carries `step_summary` — one readable line per step, repeat children indented —
because `structure` is forty lines of Garmin's shape per step and a human approving a session
should not have to read `conditionTypeId` to learn it is a ten minute warmup.

**The docstring says the tool does not design the session.** Which distance, how many reps, how
long the recovery, whether today should be hard at all — those need how the legs feel and how much
time there is, which no endpoint holds, so they stay in the conversation (§1). A composer is one
short step from a coach, and that step is the one §1 forbids.

## 6. Execution order

1. Branch `feature/workout-preview` off `190380e`.
2. `source/compose.py`, then its tests, then the tool, then its tests.
3. Full suite, then this document and the journal in the same commit as the code.
4. Push, PR, merge. **Then step 0b again** — see §8.

## 7. Test counts

Before: **352 passed, 2 skipped.** After: **419 passed, 2 skipped.** Added 67, removed 0.

Higher than the ~35 estimated at plan time, because six of the refusal tests are parametrised
over the values that break them (three iteration counts, five non-finite or non-positive
durations, three distances, two empty names). The estimate counted test functions; the suite
counts cases.

## 8. Deployment and rollback

A new tool is invisible in Desktop until **merge → CI publishes → the laptop is re-pinned → Claude
Desktop is restarted.** That is step 0b again, now a known one-minute scripted operation, and
`tools.json` would be its exposure surface if `tools.json` existed. **The session is not finished
at merge**, which is worth stating because the previous session learned it the hard way.

Rollback is free: previous `sha-` tag, no state, no migration, no file written, and nothing was
ever sent to Garmin.

---

## 9. v1.0 10-dimension stress-test absorption notes

Walked against `sop/stress-test-10-dimensions.md` before execution. Dispositions: **ACTIONABLE**
(changed the plan), **RECORDED** (carried as a known unknown), **NO ACTION**.

### 9.1 #1 Edge cases (5 findings, 4 actionable)

- **1.a:** a `float` duration accepts NaN and infinity, and both serialise. → **ACTIONABLE**, V5
  checks `math.isfinite` as well as positivity.
- **1.b:** an all-distance session estimates 0 seconds. → **ACTIONABLE**, allowed, but the basis
  must explain the 0 rather than merely describe the method, and a test asserts the wording.
- **1.c:** a repeat's distance steps were counted once rather than per iteration, under-reporting
  what the estimate omits. → **ACTIONABLE**, `_repeat_totals` multiplies both.
- **1.d:** a session of only a warmup is legal. → **ACTIONABLE** in the negative: allowed, and not
  moralised about. The server does not have opinions about training.
- **1.e:** a step measured in both time and distance has two end conditions and Garmin takes one.
  → **ACTIONABLE**, refused with "never both".

### 9.2 #2 Unverified assumptions (4 findings, 1 killed, 3 recorded)

- **2.a:** that Garmin accepts an under-stated `estimatedDurationInSecs`. → **RECORDED.** Settled
  by the supervised live cycle, and the basis string says so to whoever reads a preview first.
- **2.b:** that `to_dict()` is the shape `upload_workout` wants. → **RECORDED**, not exercised
  until `create`.
- **2.c:** Garmin's workout-name length limit. → **RECORDED.** Unmeasured, so V7 refuses only the
  empty name; an invented cap would refuse sessions Garmin accepts.
- **2.d:** that NO_TARGET had to be constructed by us. → **KILLED by reading the library.** The
  builders already default to it. One fewer thing for the live cycle to confirm.

### 9.3 #3 Actual code checks (3 findings, 3 actionable)

- **3.a:** `server.py:97` already derives an enum with `Literal[tuple(...)]` from the metric map.
  → **ACTIONABLE**, `StepKind` is derived from `compose.STEP_KINDS` the same way rather than
  restated.
- **3.b:** `_read_group` guards keys in the body as well as in the schema. → **ACTIONABLE**, the
  `action` guard copies the shape, including naming what would work.
- **3.c:** the tool converted its arguments to composer steps **twice** — once for the structure,
  once for the estimate. Two readings of one argument is a miniature of the re-composition §5
  forbids between preview and create. → **ACTIONABLE**, converted once, with a comment saying why.

### 9.4 #4 Security (2 findings, 1 actionable)

- **4.a:** zero requests means no credential, token or budget surface is touched at all.
  → **NO ACTION**, and it is the strongest security property this session has.
- **4.b:** `name` and `description` are free text an agent may fill with health context ("easy run,
  HRV unbalanced"). → **ACTIONABLE**: nothing logs the structure, the name or the description. §8's
  "no health data in logs" previously only had to hold for responses; it now holds for arguments.

### 9.5 #5 Vision alignment (1 finding, 1 actionable)

- **5.a:** a composer is one short step from a coach, and §1 puts judgement in the conversation.
  → **ACTIONABLE**: the tool invents nothing — no default session, no "easy run" shorthand, no
  suggested pace — and the docstring says so, because a rule stated only in a plan is a rule the
  next session does not read.

### 9.6 #6 Architecture consistency (2 findings, 0 actionable)

- **6.a:** `compose.py` sits in `source/` beside `metrics.py`, so importing `garminconnect.workout`
  is the §11 rule rather than an exception to it. → **NO ACTION.**
- **6.b:** `test_layering.py` already asserts `server.py` cannot import the library, and it passes
  unchanged. → **NO ACTION**, and it is the second adapter case that rule was written for.

### 9.7 #7 Impact on other features (3 findings, 1 actionable)

- **7.a:** `_metrics_help` is group-parameterised and the metric enums are built per group, so the
  two read tools are untouched. → **NO ACTION**, asserted anyway by a test that the three existing
  tools are still registered.
- **7.b:** **SCOPE §14's 7.b / A.6 is the live item** — `garmin_auth_status` reporting the tool set
  actually in force does not exist, and `tools.json` is unbuilt. Preview is read-only, so it does
  **not** make that load-bearing yet. → **RECORDED as a trigger condition**: it becomes
  load-bearing the moment `create` lands, and "are writes enabled right now" becomes a question
  the agent must be able to ask.
- **7.c:** a client caches the tool list at session start, so the new tool needs a Desktop restart
  even after a re-pin. → **ACTIONABLE** in §8, as a deployment step rather than a code change.

### 9.8 #8 Test coverage (2 findings, 2 actionable)

- **8.a:** four of §8's named cases are unreachable through `compose`, which assigns `stepOrder`
  and passes no target. → **ACTIONABLE**: reached by composing a valid session and breaking it,
  which is also the path `create` will take with a structure from a conversation.
- **8.b:** every refusal needs the valid variant asserted beside it. → **ACTIONABLE**: a validator
  only ever seen to refuse might be refusing everything, and V1–V9 each carry a control.

### 9.9 #9 Deployment & rollback (2 findings, 1 actionable)

- **9.a:** the tool is unreachable from Desktop until merge → CI → re-pin → restart. →
  **ACTIONABLE**, §8 states it, because the previous session discovered it rather than planning it.
- **9.b:** rollback is the previous `sha-` tag and costs nothing — no state, no migration, no
  Garmin object. → **NO ACTION**, recorded because it is the property that makes this session safe
  to ship before the live cycle.

### 9.10 #10 Risks (2 findings, 1 accepted)

- **10.a:** the real risk is a composer whose output Garmin rejects, discovered only at the live
  cycle. → **ACCEPTED.** Mitigated by keeping v0 minimal and by V1–V9 catching our own errors
  first; not eliminable without a live request, which is the next session.
- **10.b:** a preview could read as an approval — "previewed, therefore done". → **ACTIONABLE**,
  the response's `note` says in as many words that nothing was sent and that no create action
  exists.

### 9.11 Pass A — states, producers and consumers

**Preview adds no state.** Nothing persisted, nothing cached, no file, no Garmin object. SCOPE
§14.12 already tabulated the write path's rows; this session touches one of them.

| State | Producer | MCP surface | web UX | persistence | consumer |
|---|---|---|---|---|---|
| Previewed plan (composed, unwritten) | `garmin_workouts(preview)` | returns it in full | — | **not persisted** | the human reading it; `create` later |
| Composed structure's validity | `validate_structure` | refusal text | — | — | `compose` now, `create` later |
| Duration estimate + its basis | `estimate_duration` | two fields, always together | — | — | the human reading the preview |
| Exposure set (`tools.json`) | the web UI | — | **unbuilt** | `/data` | **absent** (7.b) |

- **A.1:** preview is a producer whose `create` consumer does not exist yet. **Accepted and
  bounded** — the human reading the structure *is* a consumer, which is what separates this from
  `capabilities.json`'s zero-consumer shape. Recorded so the next session does not have to
  rediscover why it was acceptable here and not there.
- **A.2:** the new tool reaches exactly one surface — MCP/Desktop. No web page, no eval, no trace,
  no persistence. Its exposure surface would be `tools.json`, which is unbuilt (7.b).
- **A.3:** the estimate and its basis are **one state in two fields**, and the failure mode is
  surfacing the number alone. → the response model documents them as inseparable and a test
  asserts both are populated.

### 9.12 Pass B — the lifecycles

- **B.1 (request lifecycle):** **not entered.** No `_garmin_access`, so no lock, no budget, no 429
  path, no token read. Asserted twice — an AST check that `compose.py` never names the doorway, and
  a runtime check that booby-traps it and composes anyway — because "makes no requests" is a claim
  that rots the first time somebody adds a convenient lookup. The tool's whole test file runs with
  the doorway trapped.
- **B.2 (session lifecycle):** nothing cached, so nothing to invalidate. Two containers against one
  volume are unaffected: neither takes the lock for a preview.
- **B.3 (artifact lifecycle):** none. No workout id, no scheduled id, no calendar entry, no file.
  This is the last session in the arc for which that is true.
- **B.4 (deployment lifecycle):** the re-pin and restart in §8. A merged tool that nobody can call
  is the gap, and it is a deployment step rather than a defect.
