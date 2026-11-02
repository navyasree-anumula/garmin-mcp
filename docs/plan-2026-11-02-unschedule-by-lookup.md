# v1.7 — unschedule-by-lookup: the literal plan

**Status: PLAN, awaiting Mahi's approval. No code is written.**

Scope ratified 2026-11-02: one code item (`garmin_workout_unschedule`), the §5 tool-split
sentence, and a live test sequence run on the laptop. The CI two-liner and the latest-tag
question are logged as open issues **outside** this version. The four unverified metric rows
are settled against the Connect app, no code, tracked separately in the journal.

**The decision already taken:** `name` and `date` are both required. Date alone would have to
pick when a day holds two sessions, and picking arbitrarily is the failure `resolve()` already
refuses for templates.

---

## 1. The tool contract

```
garmin_workout_unschedule(name: str, date: str) -> UnscheduleResult
```

| Field | Type | Why it is there |
|---|---|---|
| `name` | str | Echoed back. The caller's own word, so a summarising client that drops everything else still shows what was acted on |
| `date` | str | Echoed back, YYYY-MM-DD |
| `workout_id` | int | The template, which is **not** deleted |
| `scheduled_workout_id` | int | The entry that was removed. The id §15.13 exists to recover |
| `removed` | bool | True only after `unschedule_workout` returned |
| `template_kept` | bool | Always True. See A.6 — without it a reader concludes the workout is gone and re-uploads a duplicate |
| `remaining_on_date` | int | How many entries for this workout are **still** on that date, normally 0. Non-zero is said in `warnings` too (1.c) |
| `requests_made` | int | 3 minimum, 12 worst case |
| `warnings` | list[str] | Populated only after the delete, where refusing is no longer available |
| `id_keys` | dict | Which response key carried each id — **including, for the first time, the key inside a calendar item** (A.5) |
| `note` | str | One sentence: the calendar entry is gone, the workout template remains, and how to remove the template |

Annotations: `read_only_hint=False`, `destructive_hint=True`, `idempotent_hint=False`,
`open_world_hint=True`. **This is the first tool in the server where `destructive_hint` is
honestly True**, and it is the same per-action argument the §5 split rests on — which is why
ratifying that split and adding this tool belong in one version.

Gated by `source_write.writes_enabled()`, refused with the existing `WritesDisabled` message.

---

## 2. Mechanism — seven steps, five of them existing code

| # | Step | Code | Requests |
|---|---|---|---|
| 1 | `date_type.fromisoformat(date)` | new, copied from `create_and_schedule` (`write.py:351`) | 0 |
| 2 | Every workout on the account | `list_workouts` (`write.py:219`) | 1–10 |
| 3 | Name → `workout_id` | `resolve` (`write.py:256`) **plus a new refusal** — see 3.a | 0 |
| 4 | Read the month `date` falls in | `api.get_scheduled_workouts(year, month)` | 1 |
| 5 | Find the entry | `_schedule_entry_on` (new, shared — see 7.a) | 0 |
| 6 | Remove it | `api.unschedule_workout(scheduled_workout_id)` | 1 |
| 7 | Report | new | 0 |

**Steps 1–5 raise `WriteRefused`. Step 6 onward reports.** Same rule the write path is built
around: refusing is free right up to the moment the account changes, and worthless after.

Library signatures read, not assumed (`garminconnect/__init__.py`):
`get_scheduled_workouts(year, month)` at `:3426` — validates the month 1–12 and subtracts 1
itself for Garmin's 0-indexed months, so the caller passes a 1-indexed month exactly as
`create_and_schedule` already does; `unschedule_workout(scheduled_workout_id)` at `:3471` —
`DELETE .../schedule/{id}`, takes the schedule id and nothing else.

---

## 3. Files in and out

| File | Change | Est. |
|---|---|---|
| `garmin_mcp/source/write.py` | `_schedule_entry_on`, `_entries_on`, `unschedule`, `UnscheduleOutcome` | +~110 |
| `garmin_mcp/server.py` | `UnscheduleResult` model, `garmin_workout_unschedule` tool | +~90 |
| `tests/test_unschedule.py` | new, the source-level suite | +~200 |
| `tests/test_garmin_workout_unschedule.py` | new, the tool boundary + annotations | +~90 |
| `tests/test_fixture_provenance.py` | both new files added to `PAYLOAD_FILES` | +2 |
| `docs/SCOPE.md` | §5 tool-split sentence; §15 v1.7 record; the fourth deferral named | +~25 |
| `docs/journal.md` | the session entry | +~40 |

Nothing is deleted. No new dependency. No new module: this lives in `write.py` because it
shares the calendar parser with the create path, and a second module would be a second parser
(6.a).

---

## 4. v1.7 10-dimension stress-test absorption notes

Walked against `sop/stress-test-10-dimensions.md`. Three dimensions are nil for a single small
tool and say why rather than being skipped silently.

### 1. Edge cases

- **1.a — the month boundary, which §15.13 handed down.** `get_scheduled_workouts` takes a
  calendar month. A *date-exact* lookup needs exactly the month the date is in, so the boundary
  that bites a date-*window* lookup never arises here. **Disposition: absorbed, and the
  inherited concern is narrowed rather than carried** — what remains is the reverse case: if
  Garmin's month view includes trailing days of the neighbouring month, an item's date must be
  prefix-matched against the full `YYYY-MM-DD`, which `_scheduled_ids_on` already does
  (`write.py:326`). The live test records whether neighbouring days appear.
- **1.b — the entry is already gone.** `(None, understood=True)` ⇒ refuse, naming the name
  **and** the date. Never a no-op, never a success. §15.13's second inherited requirement.
- **1.c — two entries of the same workout on one date.** Garmin's own UI permits it.
  **Decided by Mahi 2026-11-02: do not refuse.** Remove one entry and *tell the caller another
  remains*, so a second call finishes the job. The plan originally proposed a refusal by analogy
  with `resolve`'s two-match case; the analogy is wrong. Two *templates* sharing a name are
  genuinely different things and picking one is an invisible arbitrary choice, which is why that
  refusal stays. Two *calendar entries* of the same workout on the same date are
  interchangeable — the athlete sees one session either way — so removing one is not a choice
  between different outcomes, and a refusal would dead-end an undo on a state only Connect can
  fix. **The notification is prose as well as a field** (`remaining_on_date`), because a
  returned field is not a delivered one: this repo has watched a summarising client drop five of
  them.
- **1.d — the date is valid but far away** (a year out, a leap day). No special case: the month
  read answers it. 2028-02-29 parses; 2027-02-29 is refused at step 1 by `fromisoformat`.
- **1.e — name whitespace and case.** `resolve` already strips and is case-sensitive, with tests
  at `test_write.py:189` and `:195`. Unschedule inherits both, deliberately: a tool that matched
  more loosely than the writer would remove an entry the writer could not have created.
- **1.f — the template exists, the calendar is empty for the whole month.** Distinct from an
  unreadable month, and the existing `understood` flag is exactly that distinction.

### 2. Unverified assumptions

- **2.a — the calendar item's id key names are NOT measured.** `WORKOUT_ID_KEYS` and
  `SCHEDULE_ID_KEYS` are confirmed for `upload_workout` and `schedule_workout` responses only
  (`write.py:52`, `:64`); their use against a *calendar item* has never seen a real payload.
  **This is the load-bearing assumption of the whole tool.** Disposition: not resolved by
  argument — resolved by A.4's refusal shape (an entry we cannot read must not look like an
  entry that is not there) and by the live test, which records the keys via `id_keys`.
- **2.b — `unschedule_workout`'s response shape is unknown.** It is a DELETE; it may return
  `None`, `{}`, or an envelope. **Nothing may depend on parsing it.** The tool reports success
  on the absence of an exception, not on a field.
- **2.c — the calendar container key.** `CALENDAR_KEYS` has three candidates and the live
  create path has exercised this read once (the 31 Oct update). Which key it was is not recorded
  anywhere; the live test should record it, same argument as `id_keys`.
- **2.d — assumed NOT true: that `unschedule_workout` deletes the template.** The library
  docstring says "without deleting the template" (`:3472`) and §5 keeps `delete_workout`
  separate. Checked, not assumed.

### 3. Actual code checks

- **3.a — `resolve()` returns `Resolution(UPLOADED)` on no match, which for this tool is
  wrong.** It is written for the create path, where "no match" means "upload a new one". For an
  undo, no match must be a **refusal naming the name**. Disposition: a thin wrapper in
  `unschedule`, not a change to `resolve` — the create path's meaning of that return is correct
  and shared code that means two things is how a silent no-op gets built.
- **3.b** — `_scheduled_ids_on` (`write.py:300`) returns `(id, understood)` and already
  prefix-matches `date` or `calendarDate`. Read, not assumed.
- **3.c** — the fetch seam: `tests/test_fetch_seam.py` keeps an allowlist of modules permitted
  to call `source._garmin_access` directly, and `write.py` is already on it. Adding a function
  to the same module needs no allowlist change. Verified by reading the test.
- **3.d** — `tests/test_garmin_exercises.py:201` asserts the tool set with `<=`, so a seventh
  tool does not break it. Verified.
- **3.e** — `garmin_auth_status.writes_enabled`'s description says "only the read tools and the
  workout preview can do anything" when writes are off. Still true with a third write tool. No
  edit needed.

### 4. Security — small but not nil

- **4.a** — the gate is the whole security surface: this tool changes a real account and is
  refused before any request when `GARMIN_MCP_ENABLE_WRITES` is unset. Same gate, same test
  shape as the create tool.
- **4.b** — no new data class reaches the wire. The response carries a name the caller supplied,
  two ids, and a date. No credential, no location, no health data.
- **4.c** — the refusal messages echo the workout **name**, which is user-authored text. It goes
  into an exception string, never a shell, a path or a URL. Noted and dismissed.

### 5. Vision alignment

- **5.a** — §15.13 option 2, ratified: Garmin's calendar is the ledger, undo is a read, no new
  store. This builds exactly that and adds no persistence.
- **5.b** — §1's division of labour: the tool decides nothing about training. It does not pick
  which entry to remove, does not remove "the wrong-looking one", and refuses rather than
  choosing.

### 6. Architecture consistency

- **6.a** — the calendar parser must exist **once**. The create path's "is it already on that
  date" question and the undo's "which entry is on that date" question are the same read of the
  same shape. Disposition: `_schedule_entry_on` becomes the one parser and `_scheduled_ids_on`
  is reimplemented on top of it, keeping its signature so the create path and its six tests are
  untouched (7.a).
- **6.b** — `garminconnect` stays imported in `source/` only (`tests/test_layering.py`). The new
  tool in `server.py` calls `source_write.unschedule`, never the library.

### 7. Impact on other features

- **7.a — `_scheduled_ids_on` is shared with `create_and_schedule`.** Six tests depend on it
  (`test_write.py:321`–`:414`). Extending its return tuple would touch the create path for the
  undo's benefit. **Disposition: do not.** New `_schedule_entry_on(payload, workout_id, day)`
  returns `(schedule_id, schedule_key, matches, understood)`; `_scheduled_ids_on` calls it and
  returns the two values it always returned. The create path's behaviour and tests are unchanged
  by construction.
- **7.b — the `scheduled: "already"` branch of create** (`write.py:420`) reads a schedule id out
  of a calendar item, which is the same read the undo makes. After 6.a they share it, so a fix
  to one is a fix to both — which is the point.
- **7.c — the tool count.** A seventh tool reaches every client's tool list and every
  `list_tools` assertion. Checked at 3.d.
- **No state-machine sub-analysis is triggered**: this adds no status column and no enum a row
  moves through. The nearest thing is `Outcome.scheduled`, which the undo does not produce,
  consume or change the meaning of.

### 8. Test coverage

Named below in §7 of this plan. Baseline: **the suite must not shrink.** Current count is
recorded at the top of the session's journal entry and asserted after.

### 9. Deployment & rollback

- **9.a** — the laptop runs the published image, so this tool reaches Desktop only after
  PR → merge → "Build and publish" → re-pin → **a fresh Desktop conversation** (the schema-cache
  lead). The registry is the instrument: a 200 on `/v2/.../manifests/sha-<commit>`, not a green
  tick.
- **9.b — rollback is free and needs no image change**: unset `GARMIN_MCP_ENABLE_WRITES`, or
  re-pin to the previous `sha-` tag. Nothing persists, no migration, no schema.
- **9.c** — the update-branch live test (§8 part 1) needs **no new build** and can run on the
  laptop's current image today. Only the unschedule half waits for the build. Sequencing them
  that way is free information.

### 10. Risks

| Risk | P × I | Mitigation |
|---|---|---|
| **The wrong calendar entry is removed** | Low × High | Name and date both required; the entry is matched by `workout_id`, not by position or by name-on-the-item; two templates of one name still refused. A duplicate entry on the same date is removed one call at a time and the remainder is reported (1.c) |
| **An unreadable calendar item reads as "already gone"** (2.a) | Medium × Medium | A.4: count the entries on that date and say the count. Two different sentences for two different facts |
| **The delete succeeds and the report is lost** | Low × Medium | Nothing after step 6 raises. The response names both ids so a lost report is recoverable from Connect |
| **The template is thought deleted** | Medium × Low | `template_kept` plus the note. A.6 |
| **A 429 mid-sequence** | Low × Low | `RateLimited` re-raised before the delete, reported after — the existing pattern (`write.py:366`) |

---

## 5. Pass A — states, producers, consumers, across every surface

| State | Producers | Consumers | Defect / disposition |
|---|---|---|---|
| **S1 `scheduled_workout_id`** | `schedule_workout` response (`write.py:440`); a calendar item (`write.py:328`) | `unschedule_workout`; `WriteResult.scheduled_workout_id`; the chat | **A.1** Two producers that can disagree — the schedule response and the calendar item. For the undo the **calendar wins**, because it is the current state and the response is a memory. Stated in the tool's docstring so the model does not pass a remembered id |
| **S2 the entry's existence on (workout_id, date)** | Garmin's calendar only | create's "already" branch; the undo's lookup | **A.2** Not persisted here by design (§15.13). Absence must be **named**, never silent — the one requirement §15.13 handed down |
| **S3 the undo outcome (`removed`)** | the new tool | the chat only | **A.3** No other surface. The web UI touches no workout state (verified: no `workout` reference under `garmin_mcp/web/`), `garmin_auth_status` does not report it, nothing is written to disk |
| **S4 the template's existence** | `upload_workout` / `update_workout` | the undo reads it to resolve the name; the chat | **A.6** The undo leaves it alone, and the reader must be told, or a correct undo produces a duplicate on the next create |
| **S5 `id_keys`** | `_first_id_with_key` on write and schedule responses | the chat; `test_write.py`'s provenance markers; this plan's 2.a | **A.5** The calendar item is a shape with **no measured keys at all**. The undo is the first call that can record them, and it costs nothing — so it must, or the first live undo repeats the create path's mistake of teaching nothing on success |
| **S6 the writes gate** | `GARMIN_MCP_ENABLE_WRITES` | `writes_enabled()`, two call sites today, three after | **A.7** `garmin_auth_status.writes_enabled` is a consumer whose *description* enumerates what still works when writes are off. Re-read: still true. No edit |
| **S7 the tool set** | `@mcp.tool` registrations | every MCP client's tool list; three test assertions | **A.8** Checked at 3.d; all three use `<=` or a subset check |

**The surface checklist, walked:** HTTP API — none, this server has no HTTP write path.
MCP adapter — `server.py`, the only adapter. Chat — the docstring and the note, which is where
A.6 lives. Web UX — `garmin_mcp/web/` serves status and login only; no workout surface, so no
hand-copied type and no parity fixture to drift. Evals — none in this repo. Tracing — stdio
purity is tested (`test_stdio_purity.py`); the undo logs through `logging`, never stdout.
Persistence — **none added**, which is §15.13's whole point; the token volume gains nothing.

---

## 6. Pass B — the lifecycles, in order

- **B.1 the request lifecycle.** resolve → read → delete → report. The id is read at step 5 and
  used at step 6 with nothing in between that could invalidate it except another client. A
  concurrent removal makes step 6 fail; that is reported with the ids, not raised. **No value is
  read before it is written and none is written twice.**
- **B.2 create → undo → create, same name and date.** After the undo, the template still exists,
  so the second create takes the **update** branch, reads the month, finds nothing, and
  schedules new. Correct, and it is the sequence the live test runs.
- **B.3 undo twice.** The second lands on 1.b and refuses naming the date. The failure mode that
  matters is the opposite one — a second undo that reports success — and it cannot occur,
  because the report is produced only after a `unschedule_workout` that did not raise.
- **B.4 the chat lifecycle.** The model may hold a `scheduled_workout_id` from an earlier create
  in the same conversation. **It must not pass it**, and it cannot: the tool takes name and date
  only. That is A.1 enforced by the signature rather than by a sentence.
- **B.5 the deployment lifecycle.** An old container meeting a new one: both read Garmin's
  calendar, neither stores anything, so there is no row written by one that the other misreads.
  Desktop runs two containers against one token volume; an undo from one is visible to the other
  on its next read, because the store is Garmin.
- **B.6 the artifact lifecycle.** The entry may be edited or removed **in Connect** between the
  read and the delete. Covered by B.1. The reverse — an entry created in Connect — resolves
  normally, because the lookup reads Garmin and not a ledger. This is the dividend of option 2.

---

## 7. Tests, by name

`tests/test_unschedule.py`, source level, `FakeApi` reused from `test_write.py`'s shape:

1. `test_an_unknown_name_is_refused_before_any_calendar_read` (3.a)
2. `test_two_templates_of_that_name_are_refused` (inherits `resolve`)
3. `test_a_malformed_date_is_refused_before_any_request`
4. `test_the_calendar_month_is_read_one_indexed`
5. `test_the_entry_is_found_and_removed_and_the_ids_are_reported`
6. `test_an_entry_already_gone_refuses_and_names_the_date` (1.b, §15.13)
7. `test_an_entry_already_gone_is_not_an_unreadable_calendar` (1.f)
8. `test_an_unreadable_calendar_shape_refuses_with_a_different_message` (A.4)
9. `test_entries_on_that_date_we_cannot_read_are_counted_not_called_absent` (2.a, A.4)
10. `test_two_entries_of_the_same_workout_remove_one_and_say_another_remains` (1.c)
11. `test_another_workout_on_that_date_is_left_alone` (the live test's second half, in a fake)
12. `test_nothing_after_the_delete_raises` (a report, not an exception)
13. `test_a_rate_limit_before_the_delete_is_re_raised`
14. `test_the_delete_records_which_key_carried_the_calendar_item_ids` (A.5)
15. `test_the_template_is_never_deleted` — asserts `delete_workout` is not among the calls
16. `test_the_create_path_calendar_behaviour_is_unchanged_by_the_shared_parser` (7.a)

`tests/test_garmin_workout_unschedule.py`, tool boundary:

17. `test_the_undo_is_refused_when_writes_are_off`
18. `test_the_undo_is_annotated_as_destructive` — the first True in the server
19. `test_the_preview_tool_kept_its_read_only_annotation` — the §5 split, now with three tools
20. `test_the_response_says_the_template_was_kept` (A.6)
21. `test_the_seventh_tool_does_not_perturb_the_six`

Both files go into `PAYLOAD_FILES` in `tests/test_fixture_provenance.py`, and every calendar
fixture is labelled `# invented:` until the live test replaces it with `# recorded:`.

---

## 8. The live test sequence

Both halves run **in Claude Desktop on the laptop**, which is the only place with Garmin
tokens — this box has none (`/data` holds the lock and budget files only). Throwaway names,
deleted by hand in Connect afterwards.

### Part 1 — the update branch. No new build needed; can run now.

| Step | Call | Expected |
|---|---|---|
| 1 | create `zz throwaway A` for date D1 | `action: "uploaded"`, a `workout_id`, `scheduled: "new"`, `requests_made: 3` |
| 2 | create `zz throwaway A` for D2 | `action: "updated"`, **the same `workout_id`**, `scheduled: "new"`, `requests_made: 4` |
| 3 | create `zz throwaway A` for D2 again | `action: "updated"`, same id, `scheduled: "already"`, **no second calendar entry** — confirmed in Connect, not in the response |

### Part 2 — the undo. Needs the new build on the laptop and a fresh Desktop conversation.

| Step | Call | Expected |
|---|---|---|
| 4 | create `zz throwaway B` for D1 | uploaded; D1 now holds two different workouts |
| 5 | **unschedule `zz throwaway A`, D1** | `removed: true`, both ids, `template_kept: true`, `requests_made: 3`, and `id_keys` naming the calendar item's keys |
| 6 | check Connect | `zz throwaway B` **still on D1**; `zz throwaway A` still in the workout library, on no D1 calendar entry |
| 7 | unschedule `zz throwaway A`, D1 again | **refused**, naming the name and the date. Nothing removed |

What part 2 records beyond pass/fail: the calendar item's id keys (2.a), the calendar container
key (2.c), and whether a month view includes neighbouring days (1.a).

---

## 9. Outside this version

Logged as open issues, not built: `paths-ignore: ['docs/**']` in `docker.yml`;
`ignore-error=true` on `cache-to`; and the latest-tag question — a path filter makes `latest`
point at the last **code** commit rather than `main`'s tip, which is a decision and not a
cleanup.

Backlog, unchanged: reps and weight, cassettes, `tools.json`, the provenance question, body
battery.

**The §7 / §7a / §9 documentation corrections are deferred a fourth time, and this is the
explicit record of it.** They were committed to v1.7 on 2026-10-31 and are being moved again by
the 2026-11-02 scope decision, which kept v1.7 to what next week's planning needs. A fourth
deferral is named rather than silent because the third was named a debt.
