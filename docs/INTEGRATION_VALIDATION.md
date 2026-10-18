# Integrated source validation

Date: 2026-10-19. Windows, Python 3.12.14, SQLite 3.53.1; existing compatible dependencies were used without changing the original running project. New frontend dependencies were installed only in this checkout from existing lockfiles.

## Actual checks

| Area | Result |
|---|---|
| Shared schema + current MCP connection/sync/Host routing + artifact-read regressions | 79 passed |
| Gateway platform tests, including authentication, version/Fork, TLS, media and 3D handoff | 88 passed |
| Semantic/parser Runtime | 127 passed |
| B preview, editing and compatibility-Worker tests | 118 passed |
| Web Vitest | 54 passed |
| Artifact renderers Vitest | 43 passed |
| Web TypeScript + Vite production build | Passed |
| Renderer strict TypeScript | Passed |
| Ruff on protocol/MCP/B and touched integration files | Passed |
| Integrated account → private version/Fork → authenticated Worker import/plan/execute/archive | Passed; synthetic only |

These are 509 passing test cases across the listed groups, not a claim that every upstream test or every supported client/model was run. The MCP check is targeted; it does not include the full native Host recording suite.

The integration smoke started the included execution Worker through the real account-authenticated platform proxy. Its synthetic text/JSON workflow completed with 4 Runs and 18 Artifacts. Unauthenticated execution was rejected. No paid model was called.

## Integration fixes and diagnostic history

- Retained current MCP field mapping and Agent handoff, while adding Rodin GLB bytes and model-node references to both portable JSON and bundle exports. A combined test checks archive shape, exact model bytes, handoff metadata and cross-user denial.
- Bundled the previously external executable Runtime sources and patches under `compat/execution-worker`. Preparation and platform/transfer tests now use included sources instead of private historical Git objects. The old frontend-patch application test was replaced by a direct, Git-independent worker-preparation acceptance test; the current frontend has its own full tests and production build.
- The original Windows credential-leak test attempted to read a live exclusive recorder lock. It now closes platform/recorder resources before scanning every persisted file; secret-leak assertions remain intact.
- TLS subprocess verification now includes the parser package source path instead of relying on the author's installed editable package.
- Executable-Worker diagnostics exposed huge read allocations for small artifacts. Reads are bounded to the verified file size plus one byte, retaining the maximum-size and content-hash checks. Regression tests check memory-request bounds and rejection of post-verification file growth.
- One parser test still expected the old hard rejection of >128-KiB input. The upstream implementation deliberately introduced labelled bounded previews. Its updated test requires exact original archive retention, the explicit preview warning/selection marker and the actual 2-MiB reference-directory ceiling. The parser suite was then rerun in full.

The first sandboxed Gateway attempt encountered file/connection permission errors and was stopped. Actual verification used ordinary user permissions with isolated temporary data. A Worker diagnostic run failed before the allocation fix; subsequent full B tests passed. The initial platform/parser run had 214 passing cases and the outdated parser assertion; all 127 parser cases passed after the contract-alignment test update. No failure was hidden by skipping a test.

## Limits

- Semantic target detection requires separately installed ML dependencies and DINO/SAM2 weights. The independent B review API/page is not an authenticated public platform endpoint; main-page semantic-target controls still require further integration.
- Actual provider availability, image/video/3D quality, credit consumption and target-recognition accuracy were not retested. Hyper3D tests use a simulated vendor and small synthetic GLB bytes.
- Codex Unix-socket Host discovery in the latest MCP does not operate on Windows; this does not mean all Windows MCP functions are unavailable. Native Host UI trust and end-to-end recording need separate verification.
- The parser produces evidence-backed/inferred graphs; it does not convert arbitrary uploaded records into safe executable tools. Community and executable-workflow flows still have distinct data/API boundaries.
- No cloud deployment, second-device test, production hardening or full MCP suite was completed by this consolidation.
- The production build reports a large optional model-viewer chunk. B tests report Pillow `getdata` deprecation warnings; current outputs pass.

## Disclosure review

The source-only snapshot excludes team history, root self-capture, `.aica`, private databases, endpoint/session state, credentials, installed dependencies and model weights. A high-signal key scan found two intentional synthetic provider-key strings in redaction tests; no real key was identified in the selected source. Legacy workstation paths and a private tracker URL were removed from five copied documents. This is a scoped source review, not a guarantee about every free-text field in future user data.

Original contributors remain credited in `NOTICE.md` and `INTEGRATION_SOURCES.md`. No root open-source license has been selected. The repository is initially private; public visibility is a separate final review decision.
