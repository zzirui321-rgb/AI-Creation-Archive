# Integration source manifest

Consolidated 2026-10-19 from AiCA-Team/AiCA into an independent repository. Source snapshots are pinned; no team Git history or private `.aica` state was copied.

| Area | Source |
|---|---|
| Platform / frontend integration | `integration_MVP@1a2216d57c30bf73355bb16334b4b9c27db03d70` |
| Protocol / MCP | `feature/a-protocol-mcp@76f8a632d838ef7a220a29362cfc74edd270968c` |
| B preview / semantic edit / artifact components | `feature/b-preview-complete@a5cfcf1f8760899f0e054c6befcfd93c1d7f4cdb` |
| Execution Worker Runtime / Gateway | `test@6b98aa46fb097a899bbeef48642927824a8f2e2d`, plus B's `d-runtime.patch` and `d-request-providers.patch` |
| Worker-specific protocol compatibility | `a0cc735646a2056a861182e43ccba53622d36f37`, isolated inside `compat/execution-worker` |
| Rodin 3D / model viewer | Machine-local work based on integration snapshot `320c1aedccb9410a23c7709c107081ced25cb81a`, reviewed and transplanted without overwriting the original worktree |

MCP was integrated by module rather than by merging its entire branch. B's additions were integrated only within its two module directories. The latest platform's Agent handoff and field mapping were retained. 3D export was added to the latest handoff exporter, with a combined model-byte/graph/permission regression test.

The parser and executable Runtime use the same Python package names upstream but have different implementations. They remain in separate processes and source roots. The bundled worker removes the old requirement to access private Git objects at runtime. Its baseline and patches are also recorded in `compat/execution-worker/SOURCE.json`.

This repository includes focused integration changes: default bundled Worker discovery, snapshot preparation from included source, Windows-safe post-close credential-leak testing, bounded artifact reads, TLS test dependency paths, and 3D handoff coverage. It does not alter the team repository or imply that every branch was merged wholesale.

## Other branch disposition

The D backend, graph-parser/CLI, artifact-only input, parser diagnostics, image-link reports, MCP field mapping, upload-panel and Agent-handoff tips were already included in the selected integration history. The reusable-graph-modules branch has an equivalent `reusability.py`; integration includes a newer parser with those hints plus page catalogs and large-input handling, so its older parser implementation was not overlaid. The independent executable D Runtime is represented by the explicitly isolated Worker baseline and B patches. Older alternate frontends and the AI-journal experiment were not used to overwrite the current authenticated platform UI.

## Excluded material

- Team `.git` history and private `.aica` state, SQLite files, endpoint/session credentials and outboxes.
- Original root `aica.json` self-capture, unrelated personal project files and task transcripts.
- API keys, local credentials, model weights, environments, installed Node packages, caches and application data.

Original dependencies and module documentation remain attributed to their authors. See `NOTICE.md`; no new root license was selected by this integration.
