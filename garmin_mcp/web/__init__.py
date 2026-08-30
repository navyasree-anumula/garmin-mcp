"""The local credential and configuration UI (docs/SCOPE.md §7a).

A peer adapter over `source/`, exactly like `server.py`. It never imports
`garminconnect` (§11) — a second adapter is precisely the pressure that rule
exists to survive.

Nothing in here starts on its own. It is brought up on demand with
`docker compose up -d web` and stopped when done, because a page that mints
credentials has no business listening while nobody is using it.
"""
