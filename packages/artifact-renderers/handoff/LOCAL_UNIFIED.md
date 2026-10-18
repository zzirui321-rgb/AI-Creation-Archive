# Local A/B/C/D Integration

Historical assembly record. The current provider-completion assembly and
verification are documented in `PROVIDER_COMPLETION.md`.

This checkout is a local assembly on `local/abcd-unified`, not a merge into dev.
Existing checkouts and the previous deployment remain intact. The journal branch
is excluded.

| Module | Remote Source |
| --- | --- |
| A protocol and capture | feature/a-protocol-mcp @ 57b02f0 |
| B preview and renderers | feature/b-generic-preview @ a7e26f9 |
| C original application | feature/c-frontend-mvp @ baa6908 |
| D latest semantic parser, evidence and diagnostics | fix/d-graph-provider-diagnostics @ ba0bd85 |
| D executable Fork/Replay Runtime | test @ 6b98aa4, with bundled d-runtime.patch |

D's latest semantic parser and executable Runtime are separate implementations
with conflicting Python package names. They run in separate processes and data
directories. The latest parser sources in this checkout are unchanged. The
launcher exports the pinned Runtime to external local application data and
applies the versioned handoff patch there. No fake execution adapter is created
for semantic graph suggestions or unknown tools.

## Start

From the checkout root, with existing frontend dependencies installed:

```powershell
./apps/gateway/src/preview/scripts/start-unified.ps1
```

Default site: http://127.0.0.1:5176/#/execute

Runtime/B: loopback 8014, same-origin `/api`.
Latest D parser: loopback 8025, same-origin `/parser-api`.
The local bearer is generated for the server proxy; it is not bundled in client
code. Data and logs live outside the checkout under local application data,
`AiCA/unified-abcd`. `logs/processes.json` identifies only this deployment's
processes. Existing port 8000 and the previous deployment are not stopped.

The launcher uses the existing Conda Python and explicitly preloads a compatible
SQLite DLL if available. Current local verification reused Python 3.13.5 and
SQLite 3.53.0; this is not certification of the team's prescribed Python 3.12.15
environment. No global Python, Conda package or SQLite file was replaced.

## Boundaries

- Complete .aica imports retain variables, execution bindings and arbitrary DAG
  topology. Fork/Plan/jobs/archive updates run through D's actual Runtime.
- B materializes real files and truthful source receipts. Local startup selects
  Local Fallback; generated visuals are deterministic schematic previews, not
  model-generated artwork.
- The C community sample/module records are display records. Their default
  preview dock links to the executable archive workspace. An absent protocol
  is never silently reconstructed into executable tool calls.
- A Runtime snapshot can be exported as a complete hash-verified A ZIP and
  imported into the latest D parser. Parser project/import identities are
  distinct from Runtime project/archive identities.
- Preparing and reviewing semantic input makes no model call. Select a smaller
  node scope when complete replay history exceeds D's 128 KiB/400 fact budget.
  Actual parsing requires separately configured D provider credentials and
  explicit external-processing consent. Network retries preserve request ID.
- Semantic GraphDocument types are imported directly from D's generated types;
  candidate relations remain distinct from verified dependencies and
  executable=false is enforced. No semantic edit/save endpoint is fabricated.
- The semantic provider is currently unconfigured in the local deployment.
  Model parsing was not invoked as part of this verification.

## Verification

- B: existing 59 pytest cases passed, plus the new Runtime transfer/export
  authorization, byte/hash and corruption test passed (60 total).
- A/latest D regression: 264 tests passed on the first complete run. Its TLS
  subprocess import issue was resolved with isolated local source discovery;
  the TLS roundtrip then passed (265 passing cases in total). Two existing
  symlink tests could not create Windows symlinks (WinError 1314). Their checks
  were not weakened or replaced with success.
- C: 23 Vitest cases passed, including new parser transport/source contracts.
- B frontend: 32 Vitest cases passed; strict typecheck and both builds passed.
- Ruff check for A/B/D passed. B Ruff format and Git whitespace checks passed.
- Playwright/Edge: three real archive workflows passed draft/final, variable
  Keep/Override/Append, Fork, dirty replay, same-stage repeat cache, reload,
  desktop/mobile and source receipt checks. Reused counts: 3/7, 3/5, 1/4.
- All three executed snapshots passed actual Runtime ZIP export, latest D ZIP
  import and scoped input preparation. Browser refresh retained import mapping.
  No semantic model submission was made. Old C's dock opens the real workspace.

Browser reports and screenshots (local ignored artifacts):

`packages/artifact-renderers/.c-integration/generic-runtime/`

`packages/artifact-renderers/.c-integration/unified/`

Reproduce browser checks after generating the three synthetic acceptance inputs:

```powershell
node packages/artifact-renderers/scripts/verify-generic-runtime.mjs http://127.0.0.1:5176
node packages/artifact-renderers/scripts/verify-unified.mjs http://127.0.0.1:5176
```

Cross-owner local assembly is intentionally separate from the pure B remote
delivery. No remote teammate branch or pull request was modified by this task.
