# garmin-mcp

An MCP server over one person's Garmin Connect account — reads, plus structured workout
creation and calendar scheduling.

**Status: scoping. No code exists yet.**

Read [`docs/SCOPE.md`](docs/SCOPE.md) before writing any. It records what this is, what it
deliberately is not, how credentials are handled, and which risks were accepted rather
than solved.

Three things from that document that are easy to get wrong later:

- The Garmin password is entered through a bootstrap **CLI**, never through an MCP tool.
  The serving container holds tokens only and cannot log in.
- The dependency is pinned floor-and-ceiling (`>=0.3.11, <0.4`), never exactly. An exact
  pin is what stranded the most popular existing server on a stale release.
- Deletes are permanently excluded from the tool surface.

Runs on a laptop over stdio. It is not a service and has no listening port.
