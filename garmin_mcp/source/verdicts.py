"""What a metric's answer can be, when it is not simply a reading (§5, v1.4).

v1.4 specified a four-value vocabulary -- `unsupported`, `no_data`,
`not_matured`, `error` -- and nothing ever implemented it. `probe.py` still
writes the two-valued `has_data`/`empty` it has written since v1.3.2. These are
the four, and `garmin_training` is their first writer.

**Five constants, not four, and the fifth is not scope creep.** v1.4's four
describe *absence*: they were specified for `capabilities.json`, which records
whether an endpoint answers. A tool response also has to say "here is the
reading", and the probe never needed a word for that because `has_data` was its
whole purpose. `PRESENT` is that word. Every reading in a response carries a
verdict, because a nullable field invites a reader to ignore it.

**Deliberately not imported by `probe.py` yet.** The probe's output has no
reader (one producer, zero consumers), but `capabilities.json` on the laptop
volume holds August verdicts in the old vocabulary. Changing what the writer
emits is a decision about that file, and it belongs with the re-probe that
rewrites it -- not with a tool that does not read it.

**`NOT_MATURED` has no producer in this pass, and that is honest rather than
unfinished.** Only a metric whose map row says it accumulates can earn it
(v1.4: "the accumulation flag lives in the map beside the source, or
`not_matured` can never be emitted"). No row in `metrics.py` has a *measured*
accumulation requirement yet -- the HRV three-week figure is domain knowledge
flagged `[Mahi-verify]` in §13, not a measurement -- so nothing emits it.
"""

from __future__ import annotations

#: There is a reading, and `value` carries it.
PRESENT = "present"

#: A 404. The only reliable "this account or device cannot" signal (§5).
UNSUPPORTED = "unsupported"

#: A well-formed envelope with no reading in it. Says nothing about capability:
#: a day the watch sat on a dresser answers exactly like this.
NO_DATA = "no_data"

#: `no_data` for a metric with a known accumulation requirement -- "not earned
#: yet" rather than "cannot". Requires `Metric.accumulates`.
NOT_MATURED = "not_matured"

#: **Our** call failed. Never Garmin's absence of data. `race_predictions` sat
#: misclassified for a month because the probe folded this into "empty", which
#: blamed the account for our own wrong call shape.
ERROR = "error"

VERDICTS = frozenset({PRESENT, UNSUPPORTED, NO_DATA, NOT_MATURED, ERROR})

#: The verdicts that mean "no number came back". Kept as a set rather than
#: `!= PRESENT` so a reader can be exhaustive without knowing the whole list.
ABSENT = frozenset({UNSUPPORTED, NO_DATA, NOT_MATURED, ERROR})
