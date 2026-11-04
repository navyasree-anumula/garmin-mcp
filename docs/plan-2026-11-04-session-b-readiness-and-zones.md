# v1.8 session B — readiness fixed, and the zones read: the literal plan

**Status: walked and approved in-session by Mahi, 2026-11-04 ("Walk B and fix it now, B before A
confirmed"). The ten dimensions plus passes A and B are in §4, §5 and §6 — before the code**,
which is the order v1.6 got wrong (SCOPE §15.1) and v1.7 got right.

Scope decided in `docs/plan-2026-11-04-v1.8-candidates.md` §1, where B is "readiness fixed + HR
zones read" and decision 4 makes the morning snapshot the source that wins. **B runs before A on
Mahi's confirmation**, reversing the priority order he first gave, because step 0 shrank B to a
known defect and grew A by a step 0b (that doc's §10 records the reversal and why).

**What step 0 established, and this plan does not re-derive** (that doc's §2.1–§2.2): `score` and
`recoveryTime` are the right paths, the endpoint answers HAS DATA with 26 populated fields,
`timestamp` is a **`str`**, the root is `list(1)`, `level`/`feedbackShort`/`feedbackLong` are in
the same payload, and the zones payload carries five floors plus `maxHeartRateUsed`,
`lactateThresholdHeartRateUsed`, `restingHeartRateUsed`, `trainingMethod` and `sport`.

---

## §1 The contracts

### B.1 — readiness, four rows out of one request

| Row | Source | Decoder | Path | Kind |
|---|---|---|---|---|
| `readiness` | `get_training_readiness` | `morning_snapshot` | `score` | number, scale unconfirmed |
| `readiness_level` | same payload | `morning_snapshot` | `level` | **phrase** |
| `recovery_time` | same payload | `morning_snapshot` | `recoveryTime` | number, unit inferred |
| `readiness_latest` | same payload | `latest_timestamp` | `score` | number, scale unconfirmed |

All four share one `SourceCall`, so `sources_for` coalesces them and the training group stays at
**three requests** — the number `test_the_whole_training_group_is_three_requests` asserts.

### B.2 — `garmin_zones`, one request, read-only

```
garmin_zones() -> ZoneProfiles
```

Per sport profile: `sport`, `training_method`, `max_hr_bpm`, `threshold_hr_bpm`,
`resting_hr_used_bpm`, and five zones each carrying `zone`, `floor_bpm`, `ceiling_bpm` and
**`ceiling_basis`** — the string that says whether the ceiling is the next zone's floor or the
inferred `maxHeartRateUsed`. Plus `requests_made`.

---

## §2 Mechanism

1. **`latest_by_timestamp` compares within one kind.** Collect the dict snapshots whose timestamp
   key is present and not `None`; if every one is `int`/`float` (and **not `bool`**), take the
   numeric maximum; if every one is `str`, take the lexicographic maximum; **if they are mixed,
   return `MISSING`.** A `max()` over mixed types is either a `TypeError` or an arbitrary winner,
   and both are worse than "no data".
2. **`morning_snapshot` is a new decoder**: keep the snapshots whose `inputContext` is
   `"AFTER_WAKEUP_RESET"`, then hand them to `latest_by_timestamp`. **No positional fallback** —
   see finding 3.e, which is why we do not call the library's own morning method.
3. **The map rows** are rewired to `morning_snapshot`, promoted to `verified=True` (the paths were
   read off a real payload on 2026-11-04), and their notes rewritten to say what was measured and
   what still is not.
4. **`source/zones.py`** calls `get_heart_rate_zones()` through `client._garmin_access` — one
   doorway, lock then budget — and returns a frozen dataclass. Pydantic stays in `server.py`
   (the `AuthStatus` precedent).
5. **The zone pairing** is arithmetic over Garmin's own numbers: zone N runs from `zoneNFloor` to
   `zone(N+1)Floor`, and zone 5's ceiling is `maxHeartRateUsed` **labelled as inferred in the
   response itself**, not only in a doc.

---

## §3 Files in and out

**In:** `garmin_mcp/source/extract.py` (one fix, one new decoder), `garmin_mcp/source/metrics.py`
(two rows rewired, two added), `garmin_mcp/source/zones.py` (new),
`garmin_mcp/server.py` (one tool), `tests/test_extract.py`, `tests/test_garmin_training.py`,
`tests/test_metrics_map.py`, `tests/test_zones.py` (new), `tests/test_fetch_seam.py` (one line in
`ALLOWED`, deliberately).

**Out:** everything in A and C. No change to `fetch.py`, the write path, the web UI, the probe,
the Dockerfile, the dependency pins or any `.env`.

---

## §4 v1.8-B 10-dimension stress-test absorption notes

### §4.1 #1 Edge cases — 8 findings, 7 actionable

- **1.a** Mixed timestamp types across snapshots: `max()` raises `TypeError` on `str` vs `int`, or
  picks an arbitrary winner if coerced. — **ACTIONABLE §2.1**: all-one-kind or `MISSING`.
- **1.b** `isinstance(True, int)` is `True` in Python, so a `timestamp: bool` passes a numeric
  filter and compares as 1. — **ACTIONABLE §2.1**: exclude `bool` explicitly.
- **1.c** Lexicographic comparison of ISO-8601 is correct only if every string shares one format;
  a mixed offset or precision would mis-order. — **ACTIONABLE §2.1 (partial)**: read `timestamp`
  (UTC) and never `timestampLocal`, refuse a mix, and label the residual in §9.
- **1.d** The day holds snapshots but none is the morning one → `MISSING` → `no_data`, which is
  **indistinguishable from a day with no snapshots at all**, and the two need opposite responses
  (ask for the latest, versus wait). — **ACTIONABLE §1**: `readiness_latest` is the explicit
  alternative, in the same request.
- **1.e** Several morning snapshots on one day (a device reset, a re-wear). — **ACTIONABLE §2.2**:
  the morning filter hands its matches to `latest_by_timestamp` rather than taking the first.
- **1.f** The zones call returns `list(0)`, or a list with no running profile. — **ACTIONABLE
  §2.4**: report every profile that came back and never assume row 0; an empty list is `no_data`
  for the whole tool, stated.
- **1.g** A zone floor missing or out of order (`zone3Floor` absent) breaks the pairing. —
  **ACTIONABLE §2.5**: pair only adjacent floors that are both present and ascending; emit the
  floor with **no** ceiling and a `ceiling_basis` saying why, rather than a wrong range.
- **1.h** `maxHeartRateUsed` absent, or not greater than `zone5Floor`. — **ACTIONABLE §2.5**: zone
  5 keeps its floor and reports no ceiling. ✓ A missing ceiling is an answer; a fabricated one is
  a wrong target in v1.9.

### §4.2 #2 Unverified assumptions — 6 findings, 6 carried as labels

- **2.a** `inputContext`'s **value** is `"AFTER_WAKEUP_RESET"` on this account. The key is
  measured as a `str`; the value was not printed, because `--explain` prints types and never
  values. — **[Mahi-verify] at the live check.** This is the one assumption that can make the fix
  look like a failure (risk 10.b).
- **2.b** `recoveryTime`'s unit — hours or minutes. — carried: the unit string stays
  `"hours (inferred)"` and the Connect app settles it.
- **2.c** The readiness score's scale. — carried, and **no longer blocking**: `readiness_level`
  carries Garmin's own word.
- **2.d** Zone 5's ceiling is `maxHeartRateUsed`. — carried, and labelled **in the response**.
- **2.e** Every `timestamp` string shares one ISO format (1.c).
- **2.f** Which sport the single returned profile belongs to. — carried: the response states
  `sport` and D refuses rather than assuming.

### §4.3 #3 Actual code checks — 7 findings, 5 actionable. **The dimension that found the bug.**

- **3.a** `extract.py:110` — `isinstance(snap.get(key), int | float)` against a measured
  `timestamp: str`. ✓ **VERIFIED as the cause**: the filter empties, `stamped` is falsy,
  `MISSING` is returned, and `readings.read` reports `no_data` for a payload holding the data.
- **3.b** `metrics.py:144` and `:162` carry `decoder="latest_timestamp"`. ✓ VERIFIED — the rows to
  rewire.
- **3.c** **`tests/test_garmin_training.py:54`'s `READINESS` fixture is labelled `# recorded: 5a/5b
  probe sessions` and carries integer timestamps** — a `recorded:` label over an invented type.
  The provenance system worked everywhere except on itself. — **ACTIONABLE §3**: relabel as
  `# recorded in part:` with the 2026-11-04 shape, and carry string timestamps and
  `inputContext`.
- **3.d** `tests/test_extract.py` holds **five** tests over this decoder and every one uses a
  numeric timestamp. **A test written from the same assumption as the code cannot catch the
  assumption.** — **ACTIONABLE §7**: the new tests drive the real type, and one asserts the mixed
  case refuses.
- **3.e** `get_morning_training_readiness` **returns `data[0]` when no entry matches**
  (`garminconnect/__init__.py:2102`, with its own docstring admitting it). That is a **silent
  positional fallback**, which §5 forbids — *"fallback is explicit or absent, never silent"* —
  and it selects by position, which `latest_by_timestamp`'s own docstring calls out as a property
  of one response. — **ACTIONABLE §2.2**: implement decision 4's semantics in our decoder and do
  not call the library method. It costs nothing: that method calls `get_training_readiness`
  internally, so both shapes are one request.
- **3.f** All four rows share one `SourceCall`, so the training group stays three requests. ✓
  VERIFIED against `test_the_whole_training_group_is_three_requests`.
- **3.g** `readings._decode` collapses a container miss, a decoder miss and a path miss into one
  `MISSING` (`readings.py:49-63`), so a response cannot say which stage failed. — **(no action)**:
  1.d is solved by an explicit second row rather than by new plumbing, which is the cheaper and
  more honest shape. Recorded because the next decoder-shaped bug will want it.

### §4.4 #4 Security — 5 findings, 3 actionable

- **4.a** Readiness and zones are health data reaching the model provider. — **(no action)**: §9's
  standing row covers it exactly, and nothing here is a new class.
- **4.b** The zones payload carries **no identity fields** — no `ownerDisplayName`, no profile
  URLs — unlike the activity list. ✓ VERIFIED from step 0's output, and it is why B needs no
  allowlist while A does.
- **4.c** `source/zones.py` reaching `client._garmin_access` fails `test_fetch_seam.py` until it
  is added to `ALLOWED`. — **ACTIONABLE §3**: add the one line **with a reason**, which is what
  that list is for.
- **4.d** No value may be logged. — **ACTIONABLE §7**: `test_logging_hygiene.py` already sweeps;
  the new module logs a method name and a count, never a reading.
- **4.e** `maxHeartRateUsed` and `restingHeartRateUsed` are **health numbers inside a payload that
  reads as configuration**, and a tool named for zones is where a reader least expects them. —
  **ACTIONABLE (doc)**: the amendment says so in §9 rather than leaving the tool's name to imply
  otherwise.

### §4.5 #5 Vision alignment — 4 findings, 1 actionable

- **5.a** Readiness is reported, never interpreted; the phrase is Garmin's. ✓ ALIGNED with §1's
  "measurements, not advice" and with the non-goal on clinical interpretation.
- **5.b** `readiness_level` **strengthens** alignment: §5 prefers Garmin's own wording to a number
  whose scale we cannot source. ✓ ALIGNED.
- **5.c** Zones are an *input to a target*, not a recommendation. The server never picks a zone. ✓
  ALIGNED — and it is what makes v1.9's refusal satisfiable instead of unsatisfiable.
- **5.d** A tool called `garmin_zones` invites "which zone should I run in", which is conversation
  work. — **ACTIONABLE §1**: the description says it reports configuration and that choosing a
  zone needs what no endpoint holds.

### §4.6 #6 Architecture consistency — 5 findings, 3 actionable

- **6.a** §11: every `garminconnect` import stays in `source/`. ✓ VERIFIED, and
  `test_layering.py` enforces it.
- **6.b** Zones deliberately do **not** go through `fetch.py`. That seam coalesces *metric* reads
  keyed by `SourceCall`; a profile is not a metric row and has no date. — **ACTIONABLE §2.4**: say
  it in the module docstring, the way `write.py` does for the same reason.
- **6.c** **§5's tool table does not contain `garmin_zones`.** This is a documented deviation of
  exactly the shape the §5 tool-split took, which then sat undecided for a whole version. —
  **[Mahi-verify]**: ratify the tool in §5, or fold the scalars into `garmin_training` as rows and
  drop the zone table until v1.9 needs it. The plan proceeds on option (ii) from the candidates
  doc §4/B.2, which is the recommendation Mahi has already read.
- **6.d** The decoder is a pure function over a literal dict, beside the others in `extract.py` —
  no client, no tokens, no budget. ✓ ALIGNED with that module's stated split.
- **6.e** `source/` must not know MCP exists. — **ACTIONABLE §2.4**: a frozen dataclass out of
  `zones.py`, pydantic only in `server.py`.

### §4.7 #7 Impact on other features — 5 findings, 2 actionable. **State sub-analysis → Pass A**

- **7.a** `latest_by_timestamp` is used by **no other row** — `readiness` and `recovery_time` are
  its only callers (grep across the package). So fixing it cannot regress another metric. ✓
  VERIFIED.
- **7.b** The training group's request count is unchanged at three. ✓ VERIFIED.
- **7.c** `MetricKey` is `Literal[tuple(keys_for_group("training"))]` (`server.py:112`) and
  `_metrics_help` iterates `METRICS` (`server.py:127`), so **two new rows change the tool
  schema**. A Desktop conversation open across the re-pin will offer the old metric list — the
  standing schema-cache lead. — **ACTIONABLE §8**: the live check starts a **fresh** conversation,
  and says so.
- **7.d** The web UI does not consume the metric map at all (no import under `web/`). ✓ VERIFIED —
  no parity copy to drift.
- **7.e** v1.9/D consumes `garmin_zones`' output, which makes that shape an interface rather than
  a response. — **ACTIONABLE (Pass A.3)**: one producer for the ceiling, named.

### §4.8 #8 Test coverage — 6 findings, 6 actionable

Every one is a named test in §7. The two that matter most: **the mixed-type refusal** (which no
existing test could have expressed, 3.d) and **the no-morning-snapshot case** (1.d), because that
is the state the live check is most likely to meet.

### §4.9 #9 Deployment & rollback — 4 findings, 1 actionable

- **9.a** No migration, no persisted state, no new dependency, no `.env` change. The map ships
  inside the image. Rollback is a re-pin to the previous tag. ✓
- **9.b** The old-reader/new-row case cannot arise: nothing is persisted and nothing is shared
  between builds except the token file and the rate-limit state, neither touched. ✓
- **9.c** The **new**-image/old-schema case can arise and is 7.c. — **ACTIONABLE §8**.
- **9.d** Only "Build and publish" makes an image pullable; a green Tests tick does not. ✓
  Recorded so the re-pin is checked against the registry, not the workflow.

### §4.10 #10 Risks — 4 findings, 3 actionable

- **10.a** **The fix cannot be verified on this box.** The real payload lives behind the laptop's
  tokens; the suite can only prove the decoder's behaviour against a fixture whose *shape* is now
  measured. — **ACTIONABLE §8**: the live read is what closes it, and the plan says the suite
  going green is not the claim.
- **10.b** If `inputContext` never equals `"AFTER_WAKEUP_RESET"` on this account, `readiness`
  still reads `no_data` after the fix, which **looks like a failed fix**. — **ACTIONABLE §1**:
  `readiness_latest` ships in the same call, so one request distinguishes "no reading today" from
  "no morning reading today".
- **10.c** A wrong zone ceiling would propagate into v1.9's bpm targets, where it becomes a wrong
  number on a watch. — Mitigated: zones are read-only here, the inference is labelled in the
  response, and D has its own live check. Accepted for B.
- **10.d** Promoting two rows to `verified=True` on the strength of a schema dump. ✓ Correct by
  this repo's own definition — `verified` means the dotted path was read off a real payload, and
  `--explain` reads a real payload. The *scale* is a different claim and stays unverified.

---

## §5 Pass A — states, producers and consumers across every surface

| State | Producer | Consumers | Risk |
|---|---|---|---|
| The snapshot-selection rule | `extract.latest_by_timestamp`, `extract.morning_snapshot` | `readings._decode` via `DECODERS` | Used by no other row (7.a) |
| `readiness`, `recovery_time`, `readiness_level`, `readiness_latest` | `metrics.METRICS` | `readings.read`; `server.MetricKey` (schema); `server._metrics_help` (description); tests | Schema changes ⇒ 7.c |
| The readiness payload itself | Garmin, via `fetch.fetch` | one `SourceCall` shared by four rows | Coalesced; 3.f |
| `ZoneProfiles` + `ceiling_basis` | `source/zones.py` only | `server.garmin_zones`; **v1.9/D's converter** | A.3 below |
| `requests_made` (zones) | `source/zones.py` | the tool response | A.2 below |
| `trainingMethod` | `source/zones.py` | the response, and D's converter | A.4 below |

- **A.1** The metric map has exactly three consumers — `readings.read`, `server.MetricKey`,
  `server._metrics_help` — and no fourth anywhere, including the web UI and the probe (the probe
  keeps its own candidate list). ✓ So two added rows reach every consumer automatically, and the
  only surface that can lag is a cached client schema (7.c).
- **A.2** **Two read tools now disagree about whether they tell you the cost.** `garmin_zones`
  reports `requests_made`; `garmin_training` and `garmin_health` do not report it at all
  (`server.py:208` returns only a date and readings). — **ACTIONABLE, not in B**: session C adds
  it to the trends tool and should align the other two in the same commit. Named here so it is
  not discovered twice.
- **A.3** The zone ceiling has **one** producer, the projection in `zones.py`. If D recomputed it
  from floors, two producers could disagree about the same bpm. — **ACTIONABLE (D's plan)**: D
  consumes `ceiling_bpm`, never recomputes it.
- **A.4** `trainingMethod` is **produced but has no consumer yet** — nothing in B uses it. It is
  carried deliberately, because D's converter is wrong without it: the same zone number means
  different bpm under percentage-of-max than percentage-of-threshold. Recorded so a future tidy-up
  does not delete an unused field that is load-bearing one version out.
- **A.5** **Nothing here is persisted.** No cache, no store, no log of a value. §6's cache is
  still "the seam now, the store with the second consumer" — and **D is that second consumer**:
  the moment a target is composed from zones, the zone table acquires a staleness question it does
  not have today. Flagged for D, not for B.
- **A.6** `zones.py` is the first **non-metric read** in `source/`, so its failure vocabulary is
  new: it translates `GarminSourceError` to `ToolError` at the tool layer, as the write path does,
  rather than inventing a verdict. ✓ ALIGNED.

---

## §6 Pass B — the lifecycles, in order

- **B.1 Request lifecycle.** Date defaults to today's UTC calendar date (`readings.py:212-217`) →
  one request → decode → four readings. No value is read before it is written, nothing is
  recomputed, nothing is patched after the fact. ✓
- **B.2 Two rows, one payload, one moment.** `readiness` and `readiness_latest` are decoded from
  the **same** response, so they cannot disagree about which day they describe — which is the
  whole reason the alternative is a second row rather than a second request. ✓
- **B.3 Session lifecycle.** The new keys are **additive**: a conversation holding a pre-re-pin
  schema simply will not offer them, and a client that never asks for a key cannot get a wrong
  answer. No error state, only an absence. ✓ The reverse — an old image asked for a new key —
  cannot happen, because the schema comes from the same image as the map.
- **B.4 Deployment lifecycle.** Nothing crosses builds except the token file and the rate-limit
  state. ✓
- **B.5 The wrong-order finding, and it is about the live check rather than the code.** Reading
  today's readiness **before the watch has synced** returns no morning snapshot, which looks
  exactly like the bug this session fixes. — **ACTIONABLE §8**: the live check names an explicit
  date, is run after a sync, and asks for `readiness` and `readiness_latest` **together** so the
  two answers distinguish the three states.

---

## §7 Tests, by name

**`tests/test_extract.py`**
- `test_picks_the_newest_string_timestamp` — the real type, measured 2026-11-04.
- `test_mixed_timestamp_types_are_missing` — the refusal 1.a.
- `test_a_boolean_timestamp_is_not_a_number` — 1.b.
- `test_a_none_timestamp_is_skipped`.
- the five existing numeric tests stay: both kinds are supported, and the old behaviour is still
  the contract for a numeric payload.
- `test_morning_snapshot_picks_the_wakeup_entry` — 2.a's mechanism.
- `test_morning_snapshot_is_missing_when_no_entry_is_morning` — **1.d, and it is the test that
  refuses the library's `data[0]`**.
- `test_morning_snapshot_picks_the_latest_of_several_mornings` — 1.e.

**`tests/test_garmin_training.py`**
- the `READINESS` fixture relabelled `# recorded in part:` with string timestamps and
  `inputContext` — 3.c.
- `test_readiness_comes_from_the_morning_snapshot` (replaces the newest-snapshot test).
- `test_readiness_is_no_data_when_the_day_has_no_morning_snapshot`.
- `test_readiness_latest_answers_when_the_morning_row_cannot`.
- `test_readiness_level_is_garmins_own_phrase`.

**`tests/test_zones.py`** (new)
- `test_zone_floors_pair_into_ranges`.
- `test_zone_five_ceiling_is_max_hr_and_says_it_is_inferred` — 2.d.
- `test_a_missing_floor_leaves_the_ceiling_unset_rather_than_guessing` — 1.g.
- `test_a_missing_max_hr_leaves_zone_five_open` — 1.h.
- `test_an_empty_profile_list_is_reported_not_assumed` — 1.f.
- `test_one_request_and_it_says_so`.
- `test_the_response_carries_sport_and_training_method` — A.4.

**`tests/test_metrics_map.py`** — the group-count docstring moves from nine metrics to eleven; the
assertion of three requests is unchanged and is the point.

**`tests/test_fetch_seam.py`** — `source/zones.py` added to `ALLOWED`, 4.c.

---

## §8 The live check, and what the suite does not prove

**The suite going green proves the decoder's behaviour against a fixture. It does not prove the
fix** (10.a). What does:

1. **A fresh Desktop conversation** after the re-pin (7.c), because the tool schema is cached per
   conversation and two new metric keys have to be offered before they can be asked for.
2. **After the watch has synced**, and naming today's date explicitly (B.5):
   `garmin_training(["readiness", "readiness_level", "recovery_time", "readiness_latest"])`.
3. **The three states to distinguish**, and all three are informative:
   - `readiness` and `readiness_latest` both answer ⇒ fixed, and the day has a morning snapshot.
   - `readiness` is `no_data` and `readiness_latest` answers ⇒ the decoder is right and
     `inputContext` is not `"AFTER_WAKEUP_RESET"` on this account (2.a). **Then decision 4 needs
     revisiting, not the code.**
   - both `no_data` ⇒ the day genuinely has no snapshot, or the fix is wrong. The probe's
     `--explain` on the same date separates those two for free.
4. **`garmin_zones()`**, and compare the five floors, max HR and threshold HR against Connect's
   own zone screen. This also settles 2.f — which sport the one profile is.
5. **Read the readiness score against the Connect app** (2.c) and the recovery time's unit (2.b).

**Reporting rule, as always: the full tool response, verbatim, every field.** The last two live
sessions each found a defect only because the output was quoted rather than summarised.

---

## §9 What this plan leaves open, deliberately

- **2.a, the one that can masquerade as a failure** — `inputContext`'s value. Handled by shipping
  `readiness_latest` beside it rather than by guessing.
- **6.c, `garmin_zones` is not in §5's table** — [Mahi-verify] at ratification.
- **A.2, two read tools disagreeing about `requests_made`** — session C's to align.
- **A.5, the zone table's staleness question** — arrives with D, not before.
- **The readiness scale and the recovery-time unit** — values, and only the Connect app settles a
  value.
