# v1.9 A — reading the calendar: the literal plan

**Status: walked 2026-11-22. Mahi chose this as the session's scope ("calendar read: walk +
build") over v1.8 C and the repo-root `CLAUDE.md`.** The ten dimensions are in §4, Pass A in §5,
Pass B in §6 — before the code. §9 holds the three decisions this plan cannot take for itself.

Scope: a **read-only** tool that answers "what is on the calendar on this date, or across this
window" — date, name, `workout_id`, `scheduled_workout_id` per entry. **Out of scope and
deliberately next:** moving a scheduled session, which this read is the precondition for.

---

## §1 Why this exists, and what the gap actually is

**Twice in one week the server could not say what was on a date.** On 20 November an unknown
workout sat on Thursday 19th and on 22 November another sat on Saturday 21st. Neither could be
identified. The only signal available was the **count inside an unschedule refusal** — `_entries_on`
(`source/write.py:571`) decorates the refusal message with "that date holds N in total" — so the
way to learn anything about the calendar was to **call a destructive tool and read its refusal**.
A write tool used as an instrument is the whole finding.

**The read already exists; what is missing is a surface.** `api.get_scheduled_workouts(year,
month)` is called twice today — `source/write.py:377` on create's update branch and
`source/write.py:682` for the undo's lookup — and one parser reads the payload
(`_schedule_entry_on`, `source/write.py:512`). So this session adds no new endpoint and no new
dependency.

**But today's parser cannot list, and the reason matters.** It takes a `workout_id` and filters to
it (`write.py:546`: `if _first_id(item, CALENDAR_WORKOUT_ID_KEYS) != workout_id: continue`). Both
callers already know which workout they mean. A listing knows nothing and must return **every**
item on the day — including the one kind that filter can never match: an item carrying no
`workoutId` at all, which is a plausible shape for exactly the unidentifiable entries of 19 and 21
November (a course, a race, an event type nobody here has read).

**One field this tool must return has never been measured: the entry's NAME.** `_schedule_entry_on`
reads ids and dates; nothing in this repository has ever read a calendar item's name, because
neither existing caller needed one — the undo resolves a name to a `workout_id` through
`get_workouts` first (`write.py:279`). `workoutName` is known for a workout *list item* and a
workout *detail* from the library's own code (`garminconnect/__init__.py:3406` indexes
`get_workout_by_id(...)["workoutName"]` directly), and that is evidence about **two endpoints that
are not this one**. §9 decision 2 is what to do about it.

---

## §2 The contract

```
garmin_calendar(start_date, end_date=None) -> ScheduledWindow
```

`end_date` defaults to `start_date`, so a single day is the cheap common case and a window is the
same call. Per entry, and **nothing else leaves**:

- `date` — Garmin's own calendar date for the entry, prefix-matched to `YYYY-MM-DD`
- `name` — `None` when no candidate key answered, never a fabricated string
- `name_basis` — which key answered, or how the name was resolved, or why there is none
- `workout_id` — `None` for an item that is not a scheduled workout template
- `scheduled_workout_id` — the id a move or an undo needs
- `sport` — when the item carries one, else `None`

And per response: `requests_made` (**measured** — one per calendar month touched, plus any name
resolution), `requests_basis`, `months_read`, `date_basis`, `keys_read` (which container, date,
name and id keys answered — the `id_keys` pattern of `write.py:177`), `note`.

**`requests_made` is measured here, and that makes three tools with three bases.** `garmin_zones`
measures it (`source/zones.py:180`), `garmin_activities` derives it because the library paginates
invisibly, and the metric tools do not report it. This tool joins the measured column; aligning the
other three stays session C's inherited decision (A.4 below).

---

## §3 Files in and out

**In:** `garmin_mcp/source/scheduled.py` (new — owns the calendar payload parsing),
`garmin_mcp/source/write.py` (its calendar constants and parser move out; `_schedule_entry_on` and
`_entries_on` stay as thin callers so the write path and its tests do not move),
`garmin_mcp/server.py` (one tool), `tests/test_scheduled.py` (new),
`tests/test_garmin_calendar.py` (new), `tests/test_fetch_seam.py` (one allowlist line with its
reason), `tests/test_unschedule.py` (import site only, if the move changes one),
`docs/journal.md` (the entry and the 22 November items), `docs/SCOPE.md` — **not** amended here;
§9 decision 1 is a proposal for ratification.

**Out:** the move tool, v1.8 C, the metric map, the write path's behaviour (the refactor must be
behaviour-preserving), `garmin_zones`, `garmin_activities`, the Dockerfile, the dependency pins,
any `.env`, menstrual-cycle endpoints (a SCOPE decision of its own and untouched by this).

**Why a new module and not `write.py`.** That module's first line is a promise about writing
("once the workout exists, nothing raises") and a read tool inside it would inherit a docstring
that does not describe it. The dependency direction is deliberate: **the writer depends on the
reader**, never the reverse, and there is no cycle.

**Why `scheduled.py` and not `calendar.py`.** A module named `calendar.py` inside the package
would make `import calendar` — the stdlib module that answers "how many days has November" —
ambiguous to every human reading it, and this module needs that answer for its month enumeration.

---

## §4 v1.9-A 10-dimension stress-test absorption notes

### §4.1 #1 Edge cases — 11 findings, 11 actionable

- **1.a** `start_date` after `end_date`. — **ACTIONABLE**: refuse before any request, naming both
  dates. `activities._check_window` is the precedent and the wording to match.
- **1.b** A window wide enough to be a scrape. Cost here is **one request per month touched**, not
  per day, so the activity tool's reason for its cap does not transfer. — **ACTIONABLE**: cap at
  **31 days** anyway, which is at most two months and therefore two requests, and name the cap in
  the refusal. A wider window is a second decision, not a default.
- **1.c** A malformed date. The library validates nothing here — `get_scheduled_workouts` takes
  ints and never sees a date string. — **ACTIONABLE**: `date.fromisoformat` locally, refuse with
  both values.
- **1.d** A year before 2000 or a month outside 1–12 raises `ValueError` **inside** the library
  (`__init__.py:3430-3436`). An exception from there would surface as our own wiring fault dressed
  as a Garmin outcome. — **ACTIONABLE**: the local date check makes both unreachable, and the test
  asserts the refusal happens before any call.
- **1.e** **A window crossing a year boundary** — 28 December to 3 January. — **ACTIONABLE**: the
  month enumeration carries `(year, month)` pairs and rolls the year, rather than iterating a
  month number.
- **1.f** **The one-indexed month trap.** The library takes a 1-based month and subtracts one for
  Garmin's 0-indexed URL (`__init__.py:3439`). Passing `month - 1` ourselves reads the previous
  month and would look like an empty calendar. — **ACTIONABLE**: pass 1-based, and
  `tests/test_write.py:321` is the existing precedent to copy the assertion from.
- **1.g** **A month view may contain neighbouring days** — never measured, recorded at
  `write.py:92`. — **ACTIONABLE (by construction)**: every entry is matched by date prefix and the
  window is filtered after the read, so a neighbouring day is dropped rather than reported. Two
  months whose views overlap must not double-count the same entry: entries are **de-duplicated on
  `scheduled_workout_id`** before returning.
- **1.h** **An item with no `workoutId`** — the shape today's filter silently skips, and the
  likeliest shape of the entries that could not be identified on 19 and 21 November. —
  **ACTIONABLE**: listed, with `workout_id=None`. Not an error, not an omission.
- **1.i** **An item with no readable schedule id.** That id is what a move or an undo needs. —
  **ACTIONABLE**: listed with `scheduled_workout_id=None` **and** a warning naming the date, since
  the entry is then visible but not actionable.
- **1.j** The same workout twice on one day — Garmin's UI permits it, and `UnscheduleOutcome`
  already carries a field for it. — **ACTIONABLE**: two rows, each with its own schedule id.
- **1.k** An empty month (container key present, list empty) versus an unreadable payload (no
  container key at all). Identical to look at, opposite conclusions — the distinction
  `write.py:84` exists for. — **ACTIONABLE**: empty is a note; unreadable is a **refusal** (4.e,
  10.a).

### §4.2 #2 Unverified assumptions — 6 findings, 1 blocking

- **2.a** **The name key is unmeasured, and it is this tool's headline field.** No reading of a
  calendar item's name exists anywhere in this repository. — **§9 decision 2**: candidates tried in
  order with the answering key recorded, and a second source for the name. **This is the one
  assumption that changes the design**, not just a label.
- **2.b** The container key (`CALENDAR_KEYS`, three candidates) and the date key
  (`CALENDAR_DATE_KEYS`, two) are **still unmeasured** after the live undo, which reported the ids
  and nothing else (`write.py:501-507`). — carried: both are recorded in `keys_read` on every
  call, so the first live read settles them for free.
- **2.c** Whether a calendar item carries a sport at all, and under which key. — carried:
  `sport=None` when no candidate answers; no inference from the name.
- **2.d** Whether a month view includes neighbouring days (1.g). — carried, harmless by
  construction.
- **2.e** Whether a calendar item carries account identity or location fields. **Unknown shape ⇒
  cannot be enumerated**, which is precisely the argument for an allowlist (4.a).
- **2.f** Whether an entry whose template was deleted still appears. — carried: it would list with
  a `workout_id` that resolves to no name, which `name_basis` states rather than hides.

### §4.3 #3 Actual code checks — 8 findings, 5 actionable

- **3.a** `get_scheduled_workouts(year, month)` validates both ints, rejects a year before 2000 and
  a month outside 1–12, and builds `/year/{year}/month/{month-1}` (`__init__.py:3426-3443`).
  ✓ VERIFIED by reading.
- **3.b** `_schedule_entry_on` filters on `workout_id` (`write.py:546`) and therefore **cannot
  list**. — **ACTIONABLE §3**: the shared parser becomes a lister; both existing callers become
  thin, which is the pattern `_scheduled_ids_on` already follows in that file.
- **3.c** `_entries_on` (`write.py:571`) is the loop this tool needs, returning only a count. —
  **ACTIONABLE**: it becomes `len(items_on(...))`. One parser, three callers.
- **3.d** `CALENDAR_WORKOUT_ID_KEYS` is deliberately fallback-free: a missing `workoutId` must read
  as "no match", never as a guess, because `WORKOUT_ID_KEYS`' `id` fallback would read a **schedule
  id as a template id** (`write.py:60-77`). — ✓ PRESERVED: the lister keeps the fallback-free tuple
  for the template id, and reads the schedule id with `SCHEDULE_ID_KEYS` separately.
- **3.e** `_first_id_with_key` walks one level down and skips bools (`write.py:177`). ✓ reused as
  is, for the schedule id and the template id.
- **3.f** `list_workouts` pages and raises `WriteRefused` past `MAX_PAGES` (`write.py:242-275`). A
  **read** tool surfacing a refusal class whose docstring promises "nothing was written" would be a
  mislabel. — **ACTIONABLE**: if name resolution is adopted (§9 decision 2) it is wrapped, and a
  failed resolution degrades to `name=None` with `name_basis` saying so. The calendar rows are
  already in hand; raising would throw away the answer.
- **3.g** `test_fetch_seam.py:154` allowlists modules that may touch `_garmin_access`. —
  **ACTIONABLE §3**: one line for `source/scheduled.py`, with its reason — a calendar **month** is
  not a `SourceCall` keyed by one date, and there is no second metric sharing this payload.
- **3.h** `zones.py:180` reports `requests_made=1` measured; `activities.py` derives it. ✓ VERIFIED
  — this tool can count honestly, because it issues the loop itself (§2, A.4).

### §4.4 #4 Security and privacy — 6 findings, 5 actionable

- **4.a** **An allowlist, never a denylist**, exactly as `activities.py` argues. Here the argument
  is *stronger*: the activity row's hundred keys were at least measured by step 0, while a calendar
  item's shape has never been printed in full. You cannot deny fields you have not seen. —
  **ACTIONABLE §2**: the projection names what may leave.
- **4.b** A `NEVER_RETURNED`-style tuple, asserted rather than only described — the
  `activities.py:65` pattern, so the protection is testable against real field names.
- **4.c** **The test needs a positive half.** An assertion that no identity leaves passes
  vacuously against a fixture that carries none. — **ACTIONABLE**: one fixture carries a synthetic
  owner GUID, a synthetic `startLatitude` and an unknown extra field through the real projection,
  and the assertion is that none of them come out.
- **4.d** **A workout name is user-written text and can carry a place** — "Thursday threshold at
  <park>". — **ACTIONABLE**: never logged. This module logs counts and months only, the rule
  `activities.py` already follows.
- **4.e** An unreadable month must not read as an empty one: "nothing is scheduled" is a claim a
  human would act on. — **ACTIONABLE**: refuse, naming the month and the container keys looked for
  (1.k, 10.a).
- **4.f** Read-only end to end: no `GARMIN_MCP_ENABLE_WRITES` interaction, `read_only_hint=True`,
  `destructive_hint=False`, `idempotent_hint=True`. The gate stays where the writes are. ✓

### §4.5 #5 Vision alignment — 4 findings, 0 actionable

- **5.a** §1's division of labour: evidence from the server, judgement in the conversation. A list
  of what is scheduled is evidence in its purest form. ✓ ALIGNED.
- **5.b** §5's read sweep already budgets this call at **1–2 requests** for "what is already on the
  calendar" (`docs/SCOPE.md:518`). The sweep has been missing a quarter of itself. ✓ ALIGNED — and
  it is why §9 decision 1 exists.
- **5.c** §(plan continuity): "**Garmin's own calendar is the store**" (`SCOPE.md:70`). A store
  with no read is a store you can only write to. ✓ ALIGNED.
- **5.d** No totals, no verdict on the week, no opinion about whether the plan is right. Rows out.
  ✓ ALIGNED.

### §4.6 #6 Architecture consistency — 7 findings, 3 actionable

**The mandatory reuse probe, per mechanism:**

| Mechanism | What already does this job | Why not reuse it | Disposition |
|---|---|---|---|
| Reading a calendar month | `api.get_scheduled_workouts` via `write.py:377,682` | — it **is** reused; this adds no endpoint | ✓ reused |
| Parsing a calendar payload | `_schedule_entry_on` (`write.py:512`) | it filters to one `workout_id` and cannot list (3.b) — a **constraint of the signature**, removable by making it a thin caller of a lister | ✓ reused, refactored |
| Counting a date's entries | `_entries_on` (`write.py:571`) | same loop, count-only return | ✓ reused, becomes `len(...)` |
| Resolving a name from an id | `list_workouts` + `resolve` (`write.py:242,279`) | reusable, at a request cost and with a `WriteRefused` mislabel to wrap (3.f) | §9 decision 2 |
| Window validation | `activities._check_window` | its refusal text argues from invisible pagination, which is false here (1.b) | own checker, same shape and wording style |
| Request accounting | `zones.py` measured, `activities.py` derived | measured applies | ✓ reused pattern |

- **6.a** §11: every `garminconnect` import stays inside `source/`. ✓ `test_layering.py` enforces.
- **6.b** Not through `fetch.py`: a calendar **month** is not a `SourceCall` keyed by one date, and
  `_args_for` raises on an unknown call shape by design (`fetch.py:62-78`). The same reason
  `write.py`, `zones.py` and `activities.py` reached past the seam. — **ACTIONABLE §3** (the
  allowlist line, reason in the module docstring).
- **6.c** **A new tool, or a second `action` on `garmin_workouts`?** `SCOPE.md:518` specifies
  `garmin_workouts(action=scheduled, window=next_7_days)`. But `garmin_workouts` takes `name` and
  `steps` as **required** parameters (`server.py:1024-1038`), so a second action means making both
  optional — and then "preview with no steps" becomes a runtime refusal where the schema used to
  refuse it. Annotations are not the obstacle for once: both actions are read-only and idempotent.
  — **§9 decision 1**, with a recommendation.
- **6.d** Plain frozen dataclasses in `source/`, pydantic only in `server.py`. ✓
- **6.e** The refactor must be **behaviour-preserving** for the write path: same refusal strings,
  same counts, same `understood` semantics. — **ACTIONABLE §7**: the existing undo and create tests
  are the regression suite and must pass **unchanged**.

### §4.7 #7 Impact on other features — 6 findings, 3 actionable

- **7.a** **The write path's parser moves.** The create path's already-scheduled check
  (`write.py:693`) and the undo's lookup both read through it. — **ACTIONABLE**: thin callers,
  unchanged signatures, and `tests/test_unschedule.py:517-534` already asserts the thin-caller
  property for `_scheduled_ids_on` and `_entries_on`. That test is the guard; it must keep passing
  with at most an import change.
- **7.b** A new tool changes the client's tool list ⇒ the **schema-cache lead** applies again. —
  **ACTIONABLE §8**: a fresh Desktop conversation after the re-pin.
- **7.c** **The laptop is five builds behind as of 22 November** — `garmin_auth_status` reported
  `0.2.0+75551ab`, the 2 November image, so `garmin_activities` and `garmin_zones` are not on it
  either. This tool ships into a re-pin that is already owed. ✓ No action here; it is open check A.
- **7.d** No change to the metric map, the composer, `garmin_zones` or `garmin_activities`. The
  only shared file is `write.py`, and only by extraction. ✓
- **7.e** **The move tool inherits this read.** Its refusal when the source entry is missing is
  exactly `scheduled_workout_id` being absent from the listing for that date. —
  **ACTIONABLE (by design)**: the entry carries the schedule id, so the move needs no second
  lookup and no remembered id.
- **7.f** `probe.py` is untouched: no new candidate, no new call shape. ✓

### §4.8 #8 Test coverage — 9 findings, 9 actionable

Named in §7. Three carry the most weight:

- **8.a** **The month-boundary test SCOPE asked for at v1.5 and nothing could carry.**
  `docs/SCOPE.md:955` — "a seven-day calendar window spanning two months issues two
  `get_scheduled_workouts` reads, not one". The write path reads one month, so this requirement has
  been unsatisfiable for four versions. It lands here.
- **8.b** **The vacuity question, asked of every fixture.** A calendar fixture simple enough to be
  a one-key dict cannot exercise the container-key search, the date prefix match, the
  no-`workoutId` item or the projection. Every fixture therefore carries the real **shape**: a
  container key, a date **with a time suffix**, a nested dict, an unknown extra field, and a
  synthetic owner GUID. Ask what each test would still pass with the behaviour removed.
- **8.c** **Provenance labels.** Every calendar fixture in this repository is `# invented:` and
  these are too — the container, date and name keys are unmeasured (2.a, 2.b). The first live read
  promotes them, and nothing is labelled `# recorded:` before then. The v1.8-B lesson is the reason
  to be strict: a fixture labelled `recorded:` carried an **invented type** and five tests agreed
  with the code about a shape neither had seen.

### §4.9 #9 Deployment and rollback — 4 findings, 1 actionable

- No migration, no persisted state, no new dependency, no `.env`, no probe change. ✓
- Rollback is a re-pin to the previous tag. ✓
- Only "Build and publish" publishes, and it publishes nothing on a pull request; the registry is
  the instrument. ✓
- **ACTIONABLE**: this ships on top of a re-pin that is **already owed** (7.c). The tag to re-pin to
  before testing anything live is `main`'s tip, verified against ghcr by digest — not a green tick.

### §4.10 #10 Risks — 5 findings, 4 actionable

- **10.a** **The silent wrongness that matters: an unreadable month reported as an empty one.** A
  human reads "nothing on Thursday" and schedules over something. — **ACTIONABLE**: refuse (1.k,
  4.e), and the test asserts a refusal rather than an empty list.
- **10.b** **The name key being wrong in a way that still returns something** — a key that holds a
  description, a type or a template id rendered as a name. Blast radius: the human identifies the
  wrong session and moves or deletes it. — **ACTIONABLE**: `name_basis` on every entry, candidates
  never silently reordered, and the live read quotes the full response verbatim.
- **10.c** The refactor changing write-path behaviour while all new tests pass. —
  **ACTIONABLE §7**: the existing write and undo tests pass **unchanged**, and that is the
  acceptance criterion, not an afterthought.
- **10.d** De-duplication on `scheduled_workout_id` hiding a genuine duplicate (1.j) if two real
  entries share an id. Low: that id is Garmin's primary key for the entry. — **ACTIONABLE**: the
  de-duplication is **only** across overlapping month reads, never within one month's items.
- **10.e** A listing makes the next write cheaper to get wrong — a move tool built on it could
  unschedule by a remembered id. Mitigated: the move is out of scope here, and the lookup-not-a-
  remembered-id rule is §15.13's ratified decision. ✓ No action.

---

## §5 Pass A — states, producers and consumers

| State | Producer | Consumers | Risk |
|---|---|---|---|
| The calendar payload (one month) | Garmin, via `get_scheduled_workouts` | the lister; create's already-scheduled check (`write.py:693`); undo's lookup (`write.py:682`) | A.1 |
| `container_key` / `date_key` | the lister, per call | `keys_read` on this tool; `CalendarEntry` on the undo | A.2 — two surfaces recording the same unmeasured fact |
| `name_key` / `name_basis` | the lister, or name resolution | the tool response, the human | 2.a, 10.b |
| `scheduled_workout_id` | the lister | the human; **the move tool, next** | 1.i, 7.e |
| `workout_id` on an entry | the lister (fallback-free keys) | the human; any planned-versus-done comparison | 3.d — a schedule id must never be read as a template id |
| `understood` (shape readable) | the lister | the refusal path | 1.k, 4.e, 10.a |
| `requests_made` / `requests_basis` | this module, counted | the tool response | A.4 — third tool, third basis |
| The window itself | the tool's own checker | the month enumeration | 1.a–1.f |

- **A.1** **One payload, now three consumers** — create, undo, and a listing. Before this session
  two consumers shared one parser *in the same file*; now one of them is in another module. —
  **ACTIONABLE**: the parser and the key constants live in exactly one place and the other module
  imports them. Two parsers that can drift is how a calendar read stays right while the undo goes
  wrong, in the one file where a wrong read removes a real session.
- **A.2** **`keys_read` and `CalendarEntry.container_key`/`date_key` record the same unmeasured
  fact on two surfaces.** They must not be able to disagree. — **ACTIONABLE**: both read the
  lister's output; neither re-derives it.
- **A.3** **Nothing is persisted.** No cache, no store, no `capabilities.json` key, and no workout
  name in any log (4.d). The only state crossing calls is the rate-limit file, untouched.
- **A.4** **`requests_made` means a third thing.** Measured on `garmin_zones`, derived on
  `garmin_activities`, measured here, absent on the metric tools. — **ACTIONABLE**: a
  `requests_basis` string, as v1.8-A established; the alignment decision stays with session C.
- **A.5** **The MCP surface is the thinnest consumer and the one that would be forgotten.** The
  entry dataclass out of `source/` and the pydantic model in `server.py` are two declarations of
  one shape. — **ACTIONABLE §7**: a test asserts the tool's model exposes every field the
  dataclass carries, so a field added to one is not silently dropped by the other.
- **A.6** No web surface consumes this (`garmin_mcp/web` is untouched), no eval surface exists in
  this repo, and tracing is `logging` only. ✓ Stated so the checklist is answered, not skipped.

---

## §6 Pass B — the lifecycles, in order

- **B.1 Request lifecycle.** Validate the window → enumerate `(year, month)` pairs → one
  `_garmin_access` **per month** → parse each payload → filter to the window by date prefix →
  de-duplicate across months → resolve names if adopted → count requests → return. Nothing is read
  before it is written.
- **B.2 The ordering catch, and it is a real one.** Name resolution happens **after** the calendar
  reads and costs its own requests. If `requests_made` is computed from the month count and
  returned, the library pages are invisible — the derived/measured distinction this tool was going
  to get right. — **ACTIONABLE**: the counter is incremented at each `_garmin_access`, including
  resolution, and read at the end. Never computed from the month count.
- **B.3 Partial failure mid-window.** Month one reads, month two 429s. `RateLimited` is re-raised
  by the seam's own rule and nothing is returned — correct for a read, where no artifact exists and
  a partial calendar is the thing that made 22 November ambiguous. **Recorded, because the write
  path's rule is the opposite** and the difference is the point: there, something exists on the
  account and must be reported.
- **B.4 Artifact lifecycle — two surfaces, one date.** `garmin_workout_create` returning
  `scheduled: "already"` is a claim about a date, made by the create path's own read of the same
  month. A listing of that date must show that entry. They read the same parser, so they cannot
  disagree about the shape — only about the moment, which is honest. ✓ No action, recorded as the
  parity that A.1 buys.
- **B.5 Chat/session lifecycle.** A new tool is additive: a conversation holding a cached schema
  does not offer it — an absence, not an error (7.b).
- **B.6 Deployment lifecycle.** Nothing crosses builds but the token file and the rate-limit state,
  neither touched. An older build simply lacks the tool. ✓

---

## §7 Tests, by name

**`tests/test_scheduled.py`** (new — the parser and the window, no network)
- `test_the_window_is_refused_when_the_dates_are_backwards` — 1.a.
- `test_a_window_wider_than_the_cap_is_refused_and_names_it` — 1.b.
- `test_a_bad_date_is_refused_before_any_call` — 1.c, and asserts the fake api was never called
  (1.d).
- `test_a_seven_day_window_across_a_month_boundary_reads_two_months` — **8.a, the requirement from
  `SCOPE.md:955`**.
- `test_a_window_across_a_year_boundary_rolls_the_year` — 1.e.
- `test_the_month_is_passed_one_indexed` — 1.f, mirroring `tests/test_write.py:321`.
- `test_a_neighbouring_day_in_a_month_view_is_dropped` — 1.g.
- `test_the_same_entry_in_two_month_views_is_returned_once` — 1.g, and asserts de-duplication is
  across months only (10.d).
- `test_an_item_with_no_workout_id_is_listed_not_skipped` — **1.h, the 19/21 November shape**.
- `test_an_entry_with_no_schedule_id_is_listed_with_a_warning` — 1.i.
- `test_the_same_workout_twice_on_one_day_is_two_entries` — 1.j.
- `test_an_empty_month_is_a_note_and_an_unreadable_month_is_a_refusal` — **1.k, 4.e, 10.a**.
- `test_no_identity_or_location_or_unknown_field_survives_the_projection` — **4.b, 4.c, with the
  positive half**: synthetic owner GUID, synthetic latitude and an unknown key go in; none come
  out.
- `test_a_missing_name_is_none_and_says_why` — 2.a, 10.b.
- `test_the_answering_keys_are_recorded` — 2.b, A.2.
- `test_the_name_is_never_logged` — 4.d.
- `test_the_request_count_is_measured_at_each_doorway` — B.2.

**`tests/test_garmin_calendar.py`** (new — the tool surface)
- `test_the_tool_model_carries_every_field_the_dataclass_does` — A.5.
- `test_a_refusal_reaches_the_caller_as_a_refusal` — the error surface, matching the undo's.

**`tests/test_fetch_seam.py`** — `source/scheduled.py` added to `ALLOWED` with its reason (3.g).

**`tests/test_unschedule.py` and `tests/test_write.py`** — **pass unchanged** except for an import
site if the extraction moves one. That is the acceptance criterion for the refactor (6.e, 10.c),
and `test_unschedule.py:517-534` is the existing thin-caller guard.

---

## §8 The live check

Everything here needs the re-pin first — `main`'s tip, digest-asserted against ghcr, then a
**fresh** Desktop conversation (7.b, 7.c).

1. **`garmin_calendar("<today>")`** — and compare against Connect's calendar screen for the same
   day. This is the reading that settles the **name key**, the **container key** and the **date
   key** at once (2.a, 2.b), and promotes four fixtures from `# invented:`.
2. **`garmin_calendar("<monday>", "<sunday>")`** — a week, against Connect. Then a week that
   **crosses a month boundary**, which should report `months_read: 2` and `requests_made: 2`.
3. **An entry that is not a scheduled workout template**, if the account has one — a course, a
   race, an event. Does it list, with `workout_id: null`? That is 1.h, and it is the 19/21 November
   question answered directly.
4. **Cross-check one entry against `garmin_workout_unschedule`'s refusal count** on a date with
   several entries: the listing's row count and the refusal's "holds N in total" must agree. Two
   surfaces, one parser, and this is the assertion that A.1 bought.

5. **An empty future month** — a date in, say, next July. Expect the empty note, not a
   refusal. If it refuses, Garmin answers an empty month without a container key and §10.2 is
   the finding to act on.

**Reporting rule: the full response, verbatim, every field.** Four defects in four sessions were
found only because output was quoted rather than summarised, and here the fields most likely to be
wrong — `name_basis`, `keys_read` — are exactly the ones a summary drops.

---

## §9 Decisions this plan needed before the code — **all three ratified 2026-11-22**

**Decision 1 — a new `garmin_calendar` tool, or `garmin_workouts(action="scheduled")`?**
**RATIFIED 2026-11-22: a new tool**, and `docs/SCOPE.md` §5 is amended in the same commit.
`SCOPE.md:518` specifies the second. **Recommendation: a new tool**, because `garmin_workouts`
requires `name` and `steps` (`server.py:1024-1038`) and a second action would make both optional —
moving "a preview with no steps" from a schema error to a runtime refusal, and weakening the one
tool whose schema the agent composes against. The three-tools paragraph of §5 already settled that
this surface splits by *honesty per action*; this is the same argument one step further, and it
needs a one-paragraph SCOPE amendment rather than silence. **If ratified, §5's cost-table row
changes from `garmin_workouts(action=scheduled, window=next_7_days)` to `garmin_calendar(window)`,
same 1–2 requests.**

**Decision 2 — how a name is resolved when the item's own keys do not answer (2.a).**
**RATIFIED 2026-11-22: option (b)**, candidates then one library join. Three
options:
- **(a) Candidates only.** Try `title`, `workoutName`, `name`, a nested `workout.workoutName`;
  record which answered; `None` when none does. Zero extra requests. **Fails the 19/21 November
  case if the name lives nowhere obvious.**
- **(b) Candidates, then one library join.** If any entry lacks a name, walk `get_workouts` once
  (`list_workouts`, 1+ requests) and fill names by `workout_id` — a key the library's own code
  asserts (`__init__.py:3406`). Says so in `name_basis`. **An entry with no `workout_id` still gets
  no name**, and `WriteRefused` must be wrapped (3.f).
- **(c) Candidates, then `get_scheduled_workout_by_id`** per nameless entry (`__init__.py:3445`) —
  one request each, unbounded in a wide window, and the response shape is unmeasured too.
  **Recommendation: (b)**, with the join attempted at most once per call and never for an entry
  that has no template id.

**Decision 3 — the 31-day cap (1.b).** **RATIFIED 2026-11-22: 31 days.** The activity tool's 31 days comes from invisible
pagination; here the cost is one request per month, so a 92-day window is three requests and
perfectly affordable. **Recommendation: keep 31 days for v1.9-A** — the asked-for use is "this
date" and "this week", one convention across the two window tools is easier to hold in a client's
head than two, and widening it later is additive. Say so in the refusal rather than implying the
cost is the reason.

---

## §10 What building it changed in this plan

Two findings the walk did not have, both recorded here rather than silently absorbed.

- **10.1 De-duplication across months turned out to be unnecessary, and the reason is better
  than the mechanism.** 1.g and 10.d planned to de-duplicate entries on
  `scheduled_workout_id` across overlapping month views. The implementation reads **each date
  from its own month's payload**, so an entry cannot arrive twice and a neighbouring day is
  never read out of the wrong month at all. The two tests stayed —
  `test_a_neighbouring_day_in_a_month_view_is_dropped` and
  `test_the_same_entry_in_two_month_views_is_returned_once` — and they are not vacuous: an
  implementation that iterated payload items instead of dates fails both.
- **10.2 One live-check item the walk did not name: whether an empty month carries its
  container key.** The tool refuses a payload with no container key (1.k, 4.e, 10.a), which is
  the create path's shipped behaviour and the only honest reading of "none of these keys
  exist". If Garmin answers an empty month with a bare `{}`, this tool refuses where it should
  say "nothing is scheduled" — a false alarm rather than a false answer, and the right way
  round. **It is measurable in one call**: ask for a date in an empty future month. Added to
  §8 as check 5.

### §10.3 Net changes

| Finding | Where it landed |
|---|---|
| 10.1 | `source/scheduled.py:read_window` — per-date lookup, no de-duplication step |
| 10.2 | §8 check 5, below |
| Decisions 1–3 | `server.garmin_calendar`, `_resolve_names`, `MAX_WINDOW_DAYS` |
| Decision 1's doc half | `docs/SCOPE.md` §5 — the tool table, a fifth note, and the read sweep's cost row |
