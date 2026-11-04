# v1.8 session A — past activities: the literal plan

**Status: walked 2026-11-04. Mahi approved A as the next session ("go ahead A"); the lock/CI
decision of the same conversation is deferred and is not a dependency of this work.** The ten
dimensions are in §4, Pass A in §5, Pass B in §6 — before the code.

Scope from `docs/plan-2026-11-04-v1.8-candidates.md` §3: list activities for a date range, one
activity's per-lap detail, and settle §9's location exposure. **Mahi's highest priority**, and the
half of the weekly loop that is missing entirely — planned versus done is unknowable from this
server today.

---

## §1 The split, and why A2 cannot be built yet

**A1 — the list, now.** Every field it returns was measured by step 0, and the one thing still
unmeasured about it (whether a GPS run's row carries coordinates) **cannot hurt it**, because an
allowlist drops what it does not name. That is the whole argument for building before measuring
here: the unmeasured risk is *unknown extra fields*, and an allowlist is exactly the defence
against unknown extra fields. The measurement then **confirms** rather than gates.

**A2 — the per-lap detail, after step 0b.** Genuinely blocked. Three candidate calls could carry
per-lap HR — `get_activity`, `get_activity_splits`, `get_activity_hr_in_timezones` — and step 0
ruled out the only source it could see: `splitSummaries` in the list aggregates per split type and
**carries no HR at all**. Building against a guess is what §5 forbids: *"the probe has only ever
called the activity list… phase 5c probes it before the tool is designed."*

**And A1 produces the input step 0b needs.** The per-run endpoints all take an **activity id**, and
nothing in the probe can supply one. A1's list is where a real run's id comes from. So the order is
A1 → Mahi runs step 0b with that id → A2, and each step hands the next its input.

**Two tools, not one with an `action` enum.** §5's table describes `garmin_activities` as one tool
doing both. It ships as `garmin_activities` now and `garmin_activity_splits` in A2, because an
`action` enum with a single value is exactly what left `server.py:725` claiming "Only 'preview'
exists" for three versions after that stopped being true. A one-value enum is a stale description
waiting to happen.

---

## §2 The contract

```
garmin_activities(start_date, end_date, sport=None) -> ActivityList
```

Per activity, and **nothing else leaves**: `activity_id`, `name`, `start_local`, `date_basis`,
`sport`, `distance_m`, `duration_s`, `moving_duration_s`, `avg_pace_min_km`, `avg_hr_bpm`,
`max_hr_bpm`, `aerobic_training_effect`, `anaerobic_training_effect`, `training_effect_label`,
`training_load`, `hr_time_in_zone_s` (a map of zone number to seconds), `workout_id`,
`lap_count`, `has_splits`, and `exercise_sets` for a strength session. Plus `requests_made`,
`requests_basis` and `note`.

**Step 0b adds to the probe** (CLI only, prints no value, makes no tool reachable): candidates for
`get_activity`, `get_activity_splits`, `get_activity_hr_in_timezones`, `get_hrv_data_range`,
`get_rhr_daily`, `get_sleep_daily` and `get_weekly_stress`, a `--activity-id` flag for the three
that need one, and two new call shapes. The range candidates are session C's input, gathered now
because the same run collects them for free.

---

## §3 Files in and out

**In:** `garmin_mcp/source/activities.py` (new), `garmin_mcp/source/compose.py` (one rename:
`_format_pace` becomes public), `garmin_mcp/source/probe.py` (candidates, two call shapes),
`garmin_mcp/cli.py` (`--activity-id`), `garmin_mcp/server.py` (one tool), `tests/test_activities.py`
(new), `tests/test_probe.py`, `tests/test_fetch_seam.py`, `tests/test_compose.py` if it names the
old symbol.

**Out:** A2's splits tool, session C entirely, the metric map, the write path, `garmin_zones`, the
Dockerfile, the dependency pins, any `.env`.

---

## §4 v1.8-A 10-dimension stress-test absorption notes

### §4.1 #1 Edge cases — 8 findings, 8 actionable

- **1.a** A window with no activities. — **ACTIONABLE**: an empty list with a note, never an error.
  The library breaks on the first empty page, so it costs one request.
- **1.b** `start_date` after `end_date`, or a window in the future. The library validates date
  *format* only (`_validate_date_format`), not order or sense. — **ACTIONABLE**: refuse before any
  request, naming both dates.
- **1.c** A window wide enough to be a scrape. — **ACTIONABLE**: cap the span at **31 days** and
  name the cap in the refusal. See 3.b for why the window is the *only* control available.
- **1.d** A strength or indoor activity has no distance and no usable speed. — **ACTIONABLE**:
  `avg_pace_min_km` is `None`, never `0:00`, and never a `ZeroDivisionError`. Pace is driven off
  the data (`distance > 0` and `speed > 0`), not off a sport allowlist.
- **1.e** `workoutId` **absent, or present and zero**, for an unplanned activity. A zero read as an
  id links a session to a workout that does not exist — **the `WORKOUT_ID_KEYS` defect of §16.2a
  in a new place.** — **ACTIONABLE**: treat `0` and `None` alike as "not from a scheduled
  workout", and say so rather than emitting a falsy id.
- **1.f** `hrTimeInZone_*` partially present. — **ACTIONABLE**: report the zones that are there and
  **never pad with zeros** — a zero is the claim "no time in that zone", which is different from
  "Garmin did not say".
- **1.g** Multi-sport parents (`isParent`, `activityType.parentTypeId`): a parent and its children
  would double-count in any weekly total. — **ACTIONABLE (by omission)**: this tool returns rows
  and computes **no** totals, so nothing can double-count. Recorded because the first consumer who
  sums distance will meet it.
- **1.h** An activity near midnight in another timezone lands on an ambiguous day (§5's standing
  problem; Mahi travels). — **ACTIONABLE**: return `startTimeLocal` as given with an explicit
  `date_basis`, and let Garmin's own endpoint do the windowing. No timezone arithmetic here.

### §4.2 #2 Unverified assumptions — 5 findings, 5 carried as labels

- **2.a** Whether a GPS run's list row carries coordinate fields. **Not a gate** — §1's argument.
  Settled by step 0b or by A's own live check.
- **2.b** Whether an unplanned activity omits `workoutId` or carries `0` (1.e).
- **2.c** **`distance` is metres and `duration` is seconds.** Step 0 printed `float`, not a value,
  so the *unit* is an assumption — and §5 forbids a bare number. — carried: the field is named
  `distance_m` and the note says the unit is inferred from Garmin's metric account setting (§10)
  until one activity is read against Connect.
- **2.d** `averageSpeed` is metres per second, which is what the derived pace rests on (2.c's
  class).
- **2.e** Whether `lapCount` and `hasSplits` mean anything for a strength session.

### §4.3 #3 Actual code checks — 6 findings, 5 actionable

- **3.a** `get_activities_by_date` pages 20 at a time internally, breaks on the first empty page,
  and raises `GarminConnectConnectionError` after `MAX_PAGINATED_REQUESTS` (2000)
  (`__init__.py:2664-2679`). ✓ VERIFIED by reading.
- **3.b** **The request budget takes ONE token for a call that makes N HTTP requests.**
  `client._garmin_access` wraps the single library call, and the pagination loop is *inside* it, so
  §6's limiter sees one acquire for `ceil(n/20)+1` requests. There is no hook to wrap the inner
  loop. — **ACTIONABLE**: the 31-day cap (1.c) is the mitigation, and it is the only one available
  short of doing our own pagination with `get_activities(start, limit)` — which cannot filter by
  date and would walk backwards through history to find an old window. **That is a decision, not a
  cleanup**, and it is recorded for Mahi rather than taken here.
- **3.c** `requests_made` therefore **cannot be observed**, only derived from the row count. —
  **ACTIONABLE**: report it with a `requests_basis` saying it is derived, because a field that
  silently means something different from the same field on another tool is worse than no field
  (Pass A.2).
- **3.d** The library validates date format only (1.b). ✓ VERIFIED.
- **3.e** `_format_pace` is private with ten call sites inside `compose.py` and no external user. —
  **ACTIONABLE**: promote to `format_pace_min_km`. One converter, two readings — the rule pace
  targets were built on.
- **3.f** **The compose pace band (150–720 s/km) must not apply to a read.** It exists to refuse a
  typo in a *prescription*; a real walk or a slow recovery jog can sit outside it, and refusing to
  display a pace that happened would be absurd. — **ACTIONABLE**: the read path formats any
  positive speed and applies no band.

### §4.4 #4 Security and privacy — 6 findings, 5 actionable. **The dimension A exists for.**

- **4.a** **An allowlist, never a denylist.** The projection names what may leave; everything else
  is dropped. Step 0 showed the row has **over a hundred keys**, so a denylist would be a list
  nobody can finish. — **ACTIONABLE §2**.
- **4.b** **Identity, measured present:** `ownerId`, `ownerDisplayName`, `ownerFullName`, three
  `ownerProfileImageUrl*`, `userRoles`, `privacy`, `deviceId`, `timeZoneId`. **`ownerDisplayName`
  is the GUID §10 calls load-bearing and deliberately does not record, because this repository is
  public** — and it would otherwise be in every row of every list. — **ACTIONABLE**: named in the
  test, not only in prose.
- **4.c** **The test needs a positive half.** An assertion that no coordinate leaves passes
  vacuously against step 0's fixture, which came from an indoor session and has none. —
  **ACTIONABLE**: one fixture carries synthetic coordinates, a synthetic location name **and a
  synthetic owner GUID** through the real projection, and the assertion is that none come out.
- **4.d** An activity **name** is user-written text that can contain a place — "morning run in
  <park>". — **ACTIONABLE**: never logged. The module logs counts only.
- **4.e** `source/activities.py` reaching `_garmin_access` needs the seam allowlist line, with its
  reason. — **ACTIONABLE §3**.
- **4.f** §9 owes **two** rows after this: location *and* identity. — **ACTIONABLE (doc, at
  ratification)**: the location row has been carried four times; the identity one did not exist
  until step 0.

### §4.5 #5 Vision alignment — 3 findings, 0 actionable

- **5.a** Planned versus done is §1's purpose stated almost literally: *"planning and adjusting
  running training"*, and `workoutId` is what links the two. ✓ ALIGNED.
- **5.b** **No totals, no weekly summary, no verdict on the week.** Rows out; the judgement happens
  in the conversation. ✓ ALIGNED with §1's division of labour, and it is also what makes 1.g
  harmless.
- **5.c** `hr_time_in_zone_s` answers "was the easy run actually easy" **without the server having
  an opinion about what easy is**. ✓ ALIGNED — and it is the purpose behind item 3 arriving two
  sessions early.

### §4.6 #6 Architecture consistency — 5 findings, 2 actionable

- **6.a** §11: the library stays in `source/`. ✓ `test_layering.py` enforces.
- **6.b** Not through `fetch.py`: a date *window* is not a `SourceCall` keyed by one date, and
  there is no second metric to coalesce with. Same reason `write.py` and `zones.py` reached past
  it. — **ACTIONABLE §3** (the allowlist line, with the reason in the docstring).
- **6.c** **`garmin_activities` IS in §5's tool table**, unlike `garmin_zones`. So A carries no
  tool-surface deviation — only the two-tools-instead-of-one shape, which §1 argues from
  `server.py:725` rather than from preference. ✓
- **6.d** Pace reuses the one converter (3.e). — **ACTIONABLE**.
- **6.e** Plain frozen dataclasses out of `source/`; pydantic only in `server.py`. ✓

### §4.7 #7 Impact on other features — 5 findings, 2 actionable

- **7.a** No change to the metric map, the write path or `garmin_zones`. The only shared file is
  `compose.py`, and only a rename. ✓
- **7.b** A new tool changes the client's tool list ⇒ **the schema-cache lead applies again**. —
  **ACTIONABLE §7**: a fresh Desktop conversation after the re-pin.
- **7.c** New probe candidates mean a stored `capabilities.json` lacks their keys. That already
  means "re-probe needed" by §5's rule, not "corrupt". ✓ No schema-version change.
- **7.d** The full `probe run` sweep must not break on candidates that need an activity id. —
  **ACTIONABLE**: they are **skipped with a stated reason** when no id is given, never recorded as
  an error. §5's own rule: our failure must not be dressed as Garmin's.
- **7.e** A2 will need the same exclusion list as A1. — **ACTIONABLE (Pass A.1)**: one module owns
  it and A2 imports it rather than copying.

### §4.8 #8 Test coverage — 7 findings, 7 actionable

Named in §7. The two that carry the most: **the positive-half privacy test** (4.c), and
**`workout_id` of zero** (1.e), because that one is a defect this repo has already shipped once in
another file.

### §4.9 #9 Deployment and rollback — 3 findings, 0 actionable

- No migration, no persisted state, no new dependency, no `.env`. The probe additions are CLI-only
  and cannot be reached by a tool. Rollback is a re-pin to the previous tag. ✓
- The merge-triggered run is what publishes; a PR builds and publishes nothing. ✓
- Nothing crosses builds but the token file and the rate-limit state, neither touched. ✓

### §4.10 #10 Risks — 4 findings, 3 actionable

- **10.a** **The unit assumption (2.c) is the most likely silent wrongness.** A metre/kilometre mix
  is obvious at a glance; a mile/metre mix would not be. Mitigated by §10's measured fact that the
  account unit system is `metric`, and by the field being named `distance_m`. — **ACTIONABLE §7**:
  one activity read against Connect settles it.
- **10.b** The budget under-count (3.b) — a wide window spends more of Garmin's patience than the
  limiter believes. Mitigated by the 31-day cap, and named for Mahi as a decision.
- **10.c** Building the list before a GPS run's row has been seen. Mitigated **by construction**
  (§1), and the live check confirms rather than gates.
- **10.d** An activity id in a tool response is an id a summarising client can drop — the standing
  five-times-observed problem. Here it is **recoverable**: the same list call returns it again, by
  name and date, so nothing is lost. ✓ No action.

---

## §5 Pass A — states, producers and consumers

| State | Producer | Consumers | Risk |
|---|---|---|---|
| The allowlist / exclusion set | `source/activities.py` only | the projection; **A2's splits** | A.1 |
| An `Activity` row | the projection | `server.garmin_activities` | — |
| `avg_pace_min_km` | `compose.format_pace_min_km` | the row | One converter (3.e); no band on a read (3.f) |
| `requests_made` | derived from the row count | the tool response | A.2 — a different meaning from the same field on `garmin_zones` |
| `workout_id` | the projection | the human, and any planned-versus-done comparison | 1.e: `0` must not become an id |
| New probe candidates + `--activity-id` | `probe.CANDIDATES`, `cli` | `probe run`, `probe --explain`, `capabilities.json` | 7.c, 7.d |

- **A.1** The exclusion set has **one** producer and will have **two** consumers the moment A2
  lands. — **ACTIONABLE**: A2 imports the constant; it does not restate it. Two allowlists that can
  drift is how a field leaks from the second reader a version later.
- **A.2** **`requests_made` now means two different things across two tools**: measured (`1`) on
  `garmin_zones`, derived on `garmin_activities` because the library's pagination is invisible to
  us (3.b, 3.c). — **ACTIONABLE**: a `requests_basis` field states which, on this tool. Session C
  inherits the question when it adds the field to the metric tools.
- **A.3** §6's limiter is a **consumer that under-counts** (3.b). The state it keeps on disk is
  correct about tokens spent and wrong about requests made.
- **A.4** **Nothing is persisted.** No cache, no store, and no activity name in any log (4.d).
- **A.5** `capabilities.json` is a persisted consumer of the candidate list. New keys absent from
  an old file already means "re-probe needed" (§5), so no reader breaks. ✓

---

## §6 Pass B — the lifecycles, in order

- **B.1 Request lifecycle.** Window validated → **refused before any request** (1.b, 1.c) → one
  `_garmin_access` → the library pages internally → project → derive pace and the request count
  from what came back. Nothing is read before it is written.
- **B.2 The ordering catch.** `requests_made` is derived **after** the call, from the row count, so
  on a mid-call failure there is no count to report. Acceptable: this is a read, nothing was
  written, and the error says what failed. Recorded because the write path's equivalent needed
  `Outcome` for exactly this reason.
- **B.3 The pagination lifecycle, which is the one that bites.** The limiter's token is taken
  **once, before** a loop whose length is unknown at that moment. So the order is: spend one
  token, then make N requests. That is the wrong order and it cannot be fixed from outside the
  library (3.b). The cap bounds N; it does not correct the order.
- **B.4 Session lifecycle.** A new tool is additive: a cached schema simply will not offer it. No
  error, only an absence. ✓
- **B.5 Deployment lifecycle.** Nothing crosses builds. An old `capabilities.json` means re-probe,
  which is already its documented meaning. ✓

---

## §7 Tests, by name

**`tests/test_activities.py`** (new)
- `test_the_window_is_refused_when_the_dates_are_backwards` — 1.b.
- `test_a_window_wider_than_the_cap_is_refused_and_names_it` — 1.c.
- `test_an_empty_window_is_a_note_not_an_error` — 1.a.
- `test_no_coordinate_or_owner_field_survives_the_projection` — **4.b and 4.c, with the positive
  half**: synthetic lat/long, location name and owner GUID go in; the assertion is that none of
  them come out.
- `test_an_unknown_field_is_dropped_rather_than_passed_through` — 4.a, allowlist by construction.
- `test_a_zero_workout_id_is_not_an_id` — 1.e.
- `test_a_strength_activity_has_no_pace_rather_than_a_zero` — 1.d.
- `test_pace_comes_from_the_one_converter` — 3.e, and asserts no band is applied (3.f).
- `test_partial_hr_zones_are_not_padded_with_zeros` — 1.f.
- `test_the_request_count_says_it_is_derived` — 3.c, A.2.
- `test_the_name_is_never_logged` — 4.d.

**`tests/test_probe.py`** — the new candidates are reachable by key; an activity-id candidate is
**skipped with a reason** rather than erroring when no id is given (7.d).

**`tests/test_fetch_seam.py`** — `source/activities.py` added to `ALLOWED`, with its reason.

---

## §8 The live check

1. **Fresh Desktop conversation** after the re-pin (7.b).
2. **List last week** — `garmin_activities("2026-10-28", "2026-11-03")` — and check it against
   Connect: the five sessions, their names, which ones carry a `workout_id`.
3. **Settle 2.c and 10.a**: take one run's `distance_m` and `duration_s` and compare them with what
   Connect shows for the same activity. That is the reading that turns the unit from inferred into
   measured.
4. **Settle 1.e**: find an activity that did **not** come from a scheduled workout — an unplanned
   run or a class — and see whether `workout_id` is absent or zero.
5. **Then step 0b**, with a **running** activity's id taken from step 2:
   `garmin-mcp probe --explain activity_detail,activity_splits,activity_hr_zones --activity-id <id>`
   and, free in the same run, `hrv_range,sleep_daily,rhr_daily,weekly_stress` for session C.
   **This is the run that settles whether a GPS run's row carries coordinates** (2.a) and which
   call has per-lap HR.

**Reporting rule: the full response, verbatim, every field.** Three defects in three sessions were
found only because output was quoted rather than summarised — and in this one, a field that should
not be there is exactly the kind of thing a summary would hide.
