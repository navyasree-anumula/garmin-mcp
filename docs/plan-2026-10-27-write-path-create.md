# Plan — the write path, create and schedule (offline)

**Status:** executed 2026-10-27, approved in session before execution.
**Baseline:** `main` = `5d9993b`, green. **461 passed, 2 skipped** at the start;
**527 passed, 2 skipped** at the end.
**Stress-tested across 10 dimensions + passes A and B** — audit trail below.
**Nothing was written to Garmin.** Every call this session adds was exercised against a fake
client; the first real write is the next session's supervised live cycle.

---

## Context

`garmin_workouts(action="preview")` composes a running session and sends nothing. It is live on
the laptop. **No workout has ever been written to Garmin by this project** — nothing has
reached the calendar and no `workoutId` has ever existed.

This session builds everything needed to write one, and still writes nothing. Every Garmin call
it adds is exercised against a fake client, the way `test_garmin_health.py` does. The first real
write is the *next* session's supervised live create → verify → delete cycle, and §8 makes that
conditional on this work being green first: the one live attempt should be spent on Garmin's
acceptance, not on our bugs.

Two decisions were taken before planning, both put to Mahi with trade-offs:

- **Writes are gated by `GARMIN_MCP_ENABLE_WRITES`, default off.** The create tool refuses by
  name, and `garmin_auth_status` reports `writes_enabled`. This answers SCOPE §14's 7.b — "are
  writes enabled right now" — in one field, without building `tools.json`, whose web UI half
  does not exist. The first live write then needs a deliberate edit to the Desktop config, which
  needs a re-pin anyway, so the gate costs nothing it was not already costing.
- **A name match checks the target date's month before scheduling.** One
  `get_scheduled_workouts(year, month)`; if this workout is already on that exact date, the
  schedule call is skipped and the response says `already`. No month-boundary problem, because
  it checks one date rather than a window.

## The spec assumes something MCP cannot express

SCOPE §5 says `garmin_workouts` carries both actions, **and** that "`preview` is read-only and
idempotent; create and schedule are neither". `ToolAnnotations` are per *tool*, not per action.
One tool holding both actions therefore has to be annotated `read_only_hint=False`, which makes
preview — the safest call in the flow — look like a write, and §5 itself warns that a client
gating non-read-only tools behind a confirmation would put a wall in front of it.

**So this plan splits them: `garmin_workouts` keeps `preview` and its honest read-only
annotation, and `garmin_workout_create` is a second tool annotated as the write it is.** That
deviates from §5's "one tool, two actions" phrasing in order to honour §5's annotation
requirement, which is the substantive half. Flagged for ratification rather than taken quietly;
if Mahi prefers one tool, the cost is preview's annotation and the plan changes in one place.

## Scope

**In:**

| File | Change |
|---|---|
| `garmin_mcp/source/write.py` | **new.** Approval marker, idempotency resolution, paged workout listing, the create-and-schedule orchestration. |
| `garmin_mcp/source/compose.py` | `approval_for(structure)` lives here (pure, beside the composer it describes). |
| `garmin_mcp/server.py` | `approval` added to the preview response; new `garmin_workout_create` tool; `writes_enabled` on `garmin_auth_status`. |
| `tests/test_fetch_seam.py` | `source/write.py` added to `ALLOWED`, with the reason. |
| `tests/test_write.py`, `tests/test_garmin_workout_create.py` | **new.** |
| `docs/plan-2026-10-27-write-path-create.md` | this plan plus the absorption notes. |
| `docs/journal.md` | entry, plus the two folded-in corrections owed from 2026-10-26 (the laptop runs `sha-71d0541`, and `sha-5d9993b` was skipped deliberately because nothing user-facing changed). |

**Out:** `push_workout_to_device` (a third copy on hardware a later edit cannot reach — §5
forbids it by default); `delete_workout`/`unschedule_workout` as tools (the live cycle's cleanup
is done by hand next session); cassettes (§8 wants them recorded from a real exchange, which is
next session); the calendar *read* tool and its month-boundary rule for windows; targets.

**Size:** ~300 lines source, ~450 tests. Tests 461 → ~510.

## Design

### The approval marker (`compose.approval_for`)

`sha256` over canonical JSON (sorted keys, no whitespace). `preview` returns it alongside
`structure`; `create` takes both, recomputes, and refuses on mismatch — then re-runs
`validate_structure` anyway, so a structure edited in flight is caught twice. **`create` takes no
`name`**: the name lives in `structure["workoutName"]`, and a second copy is a second source of
truth that can disagree with what was approved.

### Idempotency (`write.resolve`, pure)

Exact match on the stripped `workoutName` against the account's workouts. None → upload. One →
update in place, which keeps the id and therefore keeps existing calendar entries valid. **Two or
more → refuse**, because updating one of two identically-named workouts picks arbitrarily and the
human cannot see which.

`get_workouts(start, limit)` defaults to **100**. A silently truncated list means a name match is
missed and a duplicate is uploaded instead of an update — so `write.list_workouts` pages until a
short page, caps at 10 pages, and **raises at the cap rather than matching against a partial
list**. Each page is a request, so idempotency costs at least one before any write.

### Orchestration (`write.create_and_schedule`)

1. `list_workouts` → `resolve`.
2. If updating: `get_scheduled_workouts` for the target date's month. **If this read fails,
   refuse now** — nothing has been sent yet, so refusing is free, and proceeding would mean
   double-booking blind. A fresh upload skips this step entirely; it cannot already be scheduled.
3. `upload_workout(structure)` or `update_workout(id, structure)`.
4. `schedule_workout(id, date)`, unless step 2 found it already on that date.

**Once the workout exists, no later failure may raise.** Every step after step 3 returns an
outcome describing what happened, including a 429 — §5's "a partial write reports what it got
to", and the reason is concrete: an exception here loses the `workoutId` and leaves a template on
Garmin that appears on no calendar, which next week's session cannot see because it reads the
calendar.

### The ids, which are the session's real unknown

`upload_workout` and `schedule_workout` have **never been called by this project**, so their
response shapes are unverified. Known from the library's own code, not from a payload:
`get_workouts` items carry `workoutName` and `workoutId` (`garminconnect/__init__.py:3403`).

So id extraction tries candidate keys (`workoutId`, then `workout.workoutId`; for the schedule,
`workoutScheduleId`, `scheduledWorkoutId`, `id`) and **when none matches, does not raise** — the
write has already happened. It returns the outcome with the id absent, the raw response included,
and a note saying the undo path is unavailable. SCOPE A.3's point is that `scheduledWorkoutId` is
consumed by the only undo path and not kept by its producer; losing it to an exception is the
same bug with better manners. The live cycle settles the key names, and a follow-up narrows the
candidates to what was observed.

### The write gate

`GARMIN_MCP_ENABLE_WRITES`, read per call rather than at import, so a test can flip it and so the
answer cannot be stale. Absent or anything but `1`/`true` → the tool refuses by naming the
variable, **before any request**. `garmin_auth_status` gains `writes_enabled`, which is how the
agent asks the 7.b question.

### The doorway

A write must not go through `fetch.py`: that seam exists to *coalesce* reads, and silently
de-duplicating a write would be a bug with no upside. `source/write.py` therefore takes
`_garmin_access` directly — which `test_fetch_seam.py`'s `ALLOWED` set will refuse until it is
added with a reason. That guard firing is the design working.

## 10-dimension stress test (`sop/stress-test-10-dimensions.md`)

1. **Edge cases — 6 actionable.** `>100` workouts truncating the match (paged, capped, raises at
   the cap). Two workouts sharing a name (refuse). A 429 between upload and schedule (reported,
   never raised). A date in the past (allowed — backfilling a session is legitimate, and
   inventing a floor would refuse a real use). `_validate_positive_integer` raising on a
   non-numeric id from a malformed response (caught, reported with the raw response). The
   pre-check read failing on an update path (refuse before writing).
2. **Unverified assumptions — 3 recorded.** `upload_workout`'s response shape;
   `schedule_workout`'s response shape; whether Garmin accepts our structure at all
   (`estimatedDurationInSecs` understated, `NO_TARGET` steps). All three settle in the live cycle,
   and none may be guessed in a way that loses an id.
3. **Actual code checks — 3 actionable.** `update_workout` forces `workoutId` in the body
   (`__init__.py:3194`), so we must not also set it and disagree. `schedule_workout` validates
   the date format itself — our error translation must surface its `ValueError` as a tool message,
   not a traceback. `get_workouts` items carry `workoutName`/`workoutId` per the library's own
   usage, which is the fixture's provenance (`# recorded: library source`, not a session —
   `test_fixture_provenance.py` will want that marker honest).
4. **Security — 2 actionable.** Writes are gated and the gate defaults closed. The structure, the
   name and the description are never logged (the 2026-10-26 rule, now also covering a payload
   that reaches Garmin).
5. **Vision alignment — 1 actionable.** §1 again: create takes an approved structure and a date
   and decides nothing. It does not pick the date, does not retry into a different day, and does
   not "fix" a session it thinks is wrong.
6. **Architecture consistency — 2 actionable.** `write.py` sits in `source/`; the doorway
   allowlist is amended deliberately, not bypassed.
7. **Impact on other features — 2 actionable.** `garmin_auth_status` gains a field, so its test
   gains an assertion. The preview response gains `approval`, which is the deferred half of last
   session's decision finally getting its consumer — and the tool split means preview's
   annotations stay honest instead of being widened to cover a write.
8. **Test coverage — see Verification.** Every refusal paired with its passing control, and the
   orphan path asserted by making `schedule_workout` fail after a successful upload.
9. **Deployment & rollback — 2 actionable.** Re-pin plus a config edit for the env var, so this
   one is not optional the way `sha-5d9993b` was. Rollback is the previous tag **and** removing
   the variable; a workout already written is rolled back by hand, which is what makes the gate
   worth having.
10. **Risks — 1 accepted, 1 mitigated.** Accepted: Garmin rejects the structure, found only
    live. Mitigated: a half-written state, by never raising after the workout exists.

### Pass A — states, producers, consumers

| State | Producer | MCP surface | persistence | consumer |
|---|---|---|---|---|
| Approval marker | `compose.approval_for` | returned by preview, required by create | not persisted | `create` verifies it |
| `workoutId` | `upload_workout` **or** `update_workout` | returned by create | Garmin | the human; `update` next time via name match |
| Scheduled-ness + `scheduledWorkoutId` | `schedule_workout` | returned by create | Garmin, syncs to watch | undo, by hand next session |
| Already-scheduled-ness | `get_scheduled_workouts` | `scheduled: already` | Garmin | the skip decision in step 4 |
| `writes_enabled` | the environment | `garmin_auth_status` + the refusal | the Desktop config | the agent, before trying |

- **A.1:** `workoutId` has two producers that can disagree — resolved by name match ⇒ update, no
  match ⇒ upload, which is SCOPE's A.2 and is now code rather than a sentence.
- **A.2:** `scheduledWorkoutId` has **no consumer in this session** — undo is by hand next
  session. Accepted knowingly, and it is why losing the id to an exception is forbidden: the
  consumer arrives, and the id cannot be re-derived without a month-scoped re-read.
- **A.3:** `writes_enabled` is produced by the environment and consumed in two places (the
  refusal and the status), so the two must read the same function, not two `os.environ` lookups.

### Pass B — the lifecycles

- **B.1 (request lifecycle):** 3 requests for a fresh upload, 4 for an update. Each takes the
  lock and the budget through `_garmin_access`; burst is 4, so the sequence does not stall. A 429
  mid-sequence is an outcome, not an exception.
- **B.2 (session lifecycle):** nothing cached. The workout list is read per call, deliberately —
  a cached list is how a workout created in one conversation is invisible to the next.
- **B.3 (artifact lifecycle):** the first session in this arc that creates a durable artifact.
  Create → (hand) verify → (hand) delete is the full cycle, and only the first third ships here.
- **B.4 (deployment lifecycle):** re-pin **and** a config edit, both required before the live
  cycle. The gate is the reason the next session cannot start by accident.

## Verification

1. `.venv/bin/python -m pytest -q` — expect ~510 passed, 2 skipped, from 461.
2. **The gate:** create refuses with the variable named and **zero requests made**, asserted by
   trapping the doorway; `garmin_auth_status` reports `writes_enabled` both ways.
3. **Approval:** a structure altered after preview is refused; the unaltered one passes; the
   marker is stable across runs and changes when any field does.
4. **Idempotency:** no match ⇒ `upload_workout`; one match ⇒ `update_workout` with that id and no
   upload; two matches ⇒ refused; a 150-workout fake proves paging; the page cap raises.
5. **Orchestration:** fresh upload schedules; an update already on the date skips the schedule
   and reports `already`; an update not on the date schedules.
6. **The orphan:** `schedule_workout` raising after a successful upload returns an outcome
   carrying the `workoutId` and saying it is on no calendar — and **does not raise**.
7. **Missing ids:** an upload response with no recognised id key returns the outcome with the raw
   response and the undo-unavailable note, rather than raising.
8. **Doorway:** `test_fetch_seam.py` passes with `write.py` allowed, and still fails for a file
   that is not.

**Not verified here, on purpose:** that Garmin accepts any of it. That is next session's one
supervised live cycle, and it runs only once all of the above is green.

---

## What execution corrected

**The doorway guard fired, as designed.** Adding `source/write.py` broke
`test_fetch_seam.py` until the module was named in `ALLOWED` with a reason. That is the guard
working rather than an obstacle: the allowlist now carries the argument for why a write must
not go through a seam built to *coalesce* reads — a write sequence is ordered (resolve, then
write, then schedule), which is the opposite of a set of interchangeable fetches.

**The fixture-provenance guard fired twice, and both times it was right.** Yesterday's check
demanded a session citation for every module-level payload shape — and this session's shapes
are genuinely invented, because `upload_workout` and `schedule_workout` have never been called.
Forcing a citation would have produced a fake one. So the guard learned a second label:
`# invented:`, which says no session has seen the shape and is a standing invitation to replace
it with `# recorded:` after the live cycle. It then caught two fixtures that had no label at
all, one of which was not a Garmin response but our own composed structure.

**Three cases were added that the plan did not anticipate**, all found while writing the tests:

- **An exact multiple of the page size needs one more request.** 200 workouts returns two full
  pages, and a full page is indistinguishable from "there may be more". Without the third call,
  a name at position 201 looks absent and the re-prescription uploads a duplicate.
- **`True` is an `int` in Python**, so a response carrying `{"id": true}` would have scheduled
  workout number 1. `_first_id` skips booleans explicitly.
- **On the update path, a silent write response does not lose the id**, because the match
  already supplied it. Only an upload can genuinely lose an id, which narrows the orphan case
  to one path rather than two.

**The gate is checked before validation, deliberately.** A disabled server gives the same
answer for a good structure and a bad one; validating first would leak whether a structure
would have been accepted by a server that is not going to send it anywhere.
