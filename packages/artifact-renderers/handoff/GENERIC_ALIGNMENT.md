# Generic Runtime Alignment

## Selected Sources

GitHub checked before implementation on 2026-10-17. A named branch: 57b02f0.
C latest: feature/c-frontend-mvp@baa6908 (dynamic module DAG/import/settings).
D Runtime/Fork/jobs/cache is in test@6b98aa4, originally d-backend-mvp-xh;
feature/d-backend@78ff5fa is a different import/semantic-analysis implementation.
They are not interchangeable. This checkout uses A with portable-JSON additions
from test, C baa6908, D Runtime test, B 5d7c9a2 plus this correction. No journal
branch is used. The submission branch `feature/b-generic-preview` starts from
5d7c9a2 and contains changes only in B's two directories. The original B-only
checkout and the running full-stack local checkout remain untouched.

## Owner Separation

B: generic semantic fallback, image adapter version, runtime_bridge.py, generic
platform assembly, archive API client, sidecar hydration, tests and docs. Fixed
CValues, node ID translations, ReviewSession and client-supplied dirty decisions
are removed. Historical build_review remains an isolated fixture, not main flow.

C assembly relative to baa6908: App route, ExecutableWorkspace/executable.css,
ImportPage (formal Archive uses actual import), preview-service (missing gateway
does not produce a successful simulation), Vite and local B dependency/lock.
C's module parser, graph, settings and community remain; display-only module
records do not gain invented executors or dependencies.

D patch relative to 6b98aa4: engine.py adds replay_plan with verified cache
predictions and composition-miss REBUILD; gateway app.py exposes stage/decisions
on /plan. B launcher supplies the existing registry parameter. A source is read
only. Examples are separate E-owned local acceptance inputs authorized here.

The export_alignment.py script writes separate B/C/D/example candidate patches
under ignored handoff/patches. This is reviewable local assembly, not teammate
approval, shared-branch integration or remote publication.

For this B-only GitHub submission, reviewed candidate files are included under
`handoff/integration/`: d-runtime.patch (base test@6b98aa4), c-assembly.patch
(base C@baa6908) and acceptance-examples.patch. These are patch documents; this
branch does not modify C/D/E source files. They require separate owner review and
assembly. C's original module-generation button is still a separate entrance;
the working end-to-end flow is the new `#/execute` workspace, not every old button.

When this B-only branch lacks A/D packages, pytest exports the fixed 6b98aa4
contract sources into a temporary directory and applies the included D patch
there. No network download, test skip, dependency-directory write or schema copy
is used. Run git fetch origin test if that Git object is missing in a shallow
clone. Tests require the ordinary team Python dependencies and compatible SQLite.

## Actual Data Flow

Schema-valid supported Archive -> A/D import -> actual graph/questions -> typed
Keep/Override/Append -> D Fork/dirty propagation -> verified cache plan -> explicit
execution/recording consent -> D background job -> B callback -> A capture -> new
Run/Artifact and D immutable snapshot -> generic rendering/Original-Fork.

Unsupported executors, version/environment mismatch, invalid graph, unavailable
bytes or recording gaps block execution. Unrelated variables are not injected
into every node's cache. Images and sidecars are hash verified. Cached child
regions are rebound by content/producer/current revision/stage without changing
the stored sidecar. Primary source badges stay truthful. Actual file SHA256 is
separate from the input fingerprint printed in the fallback bitmap.

## Acceptance Cases

| Case | Active Nodes | Variables | Modified/Reusable |
| --- | --- | --- | --- |
| AI Comic/Image | 7 | character/style/detail | 4 / 3 |
| Watercolor Field Guide | 5 | environment/creature/color_palette | 2 / 3 |
| Self-Archive Record | 4 plus dead end | framework/review_note | 3 / 1 |

The Self-Archive sample contains all provenance modes/evidence and a failed
branch reason. All three explicitly identify synthetic acceptance data, not
the actual team chat/Git reconstruction. New Runs are genuinely recorded by A.
Golden Path is read from D's successful-history recommendation.

Backend tests verify baseline Draft/Final, changed dirty sets, real CACHE_HIT/
RECOMPUTE/REBUILD, mixed composition bytes/hash, immutable old snapshots, all-hit
repeats, dead-end exclusion and no legacy route. Fallback tests compare pixel
differences and same-seed stage promotion. Frontend tests cover dynamic variable
counts/types, wrong snapshots, tampered bytes, cached metadata and URL cleanup.

Final verification: B full suite 58 passed, followed by the new structured-JSON
regression and nested-reference Seed test (both passed; 59 unique B tests in the
final tree). A schema/D Runtime/gateway: 67 passed. Renderer: 32 Vitest; latest C:
19 Vitest. Ruff check/format, strict TS and both builds passed. Three complete
browser scenarios and six final desktop/mobile inspection views passed, with
real network calls, correct partial/all-hit counts and no errors/overflow.

Default local deployment: http://127.0.0.1:5174/#/execute, gateway 8013. Port 8000
was already owned by an unrelated local image service and was left untouched.
Data/logs: LocalAppData/AiCA/executable-platform. Previous deployment directories
and data remain available; the original B-only repo is unchanged.

```sh
node scripts/verify-generic-runtime.mjs http://127.0.0.1:5174
```

Run in renderer package. It imports actual files, runs both baseline stages,
Keep/Override/Append, Fork, both changed stages, comparison, repeat Final and
refresh. Desktop/mobile screenshots and JSON endpoint reports are ignored under
.c-integration/generic-runtime. It asserts real /forks,/plan,/executions,/archive,
no fixed-session route, no browser errors/overflow and honest source badges.

## Project Plan Mapping

02/05: Archive-driven nodes, variables and multimodal artifacts. 03/04: A records
actual executions, evidence/dead ends remain visible, Golden Path from history.
06: typed three-state operations, actual Fork revision lineage. 07: stage-separated
verified cache keys, partial replay, new composite file and real counts. 08: C
community shell plus generic artifact/provenance/external-link display. 10 and
Appendix B: labelled fallback, no fixed latency/accuracy or fictitious history.

Remaining boundaries: registered tools only (one B output port), no arbitrary
third-party scripts, no new voice/video synthesis adapter, bounded artifact
preview (256 items/128 MiB), no public deployment or production auth. This work
does not claim all .aica tools can execute just because the schema accepts them.
