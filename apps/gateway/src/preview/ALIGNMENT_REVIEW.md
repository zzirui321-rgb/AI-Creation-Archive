# B alignment and integration review

Historical review below describes the retired fixed-session development harness.
Current implementation/owner boundaries and actual Runtime evidence are in
`packages/artifact-renderers/handoff/GENERIC_ALIGNMENT.md`. Do not follow the old
six-file patch or interpret its test counts as the current end-to-end result.

Scope: B's two directories plus six C assembly files explicitly approved by the user: Studio, PreviewDock, package manifest/lock, Vite config and README. The core remains protocol-driven; seven-node content lives in review/integration fixtures. This does not claim that the entire platform MVP or D's production Runtime is complete.

## Remote compatibility

`git fetch --all --prune`, GitHub PR listing and branch listing were checked on 2026-10-16.

| Ref | State at review |
| --- | --- |
| `dev` | `f70cbc0`, including C's PR #3; local B branch fast-forwarded without losing B files |
| `feature/a-protocol-mcp` | `57b02f0`; A migration integrated into this feature branch |
| `feature/a-agent-coop-migration` | Remote branch removed after integration into A |
| `agent_coop` | Collaboration framework baseline |
| `feature/b-preview-renderers` | Local B working branch; not pushed |

The core archive schema and validator still match B's pinned contract. A also added `transfer.schema.json` and portable ZIP/upload helpers; B's fixture bundles now follow this transfer format and are checked against A's verifier. No root policy, task files or shared schema were modified. C's six approved assembly files connect the actual application to B's HTTP development harness and A's variable/archive validation. D submitted PR #5 during final verification; its callback contract was inspected, but that separate Runtime has not been substituted into this A/C integration. Current evidence and startup instructions are in `packages/artifact-renderers/handoff/AC_INTEGRATION.md`.

## Project-plan mapping

| Project section | B behavior and evidence |
| --- | --- |
| 03 / authorized capture and extraction | B does not open recording sessions or reconstruct history. Node provenance/evidence remains distinct from actual generation source. The fixture explicitly marks synthetic content and includes recorded/extracted/inferred examples. |
| 04 / branches and Golden Path | The full fixture keeps an actual failed dimension-validation attempt on a dead-end branch with reason. Its Golden Path is a recommendation from successful local fixture history. B neither chooses a global optimum nor implements D's path algorithm. |
| 05 / `.aica` carrier | Shared A fields are used without adding extra Artifact properties. Standalone Original/Fork archives, relative files, hashes, Run references, old definition snapshot and namespaced extensions pass A's original validator. |
| 06 / Fork and variable operations | A's own ordered operation resolver applies Keep(style), Override(character), then Append(character). Original defaults remain intact. B only receives resolved variables; it does not take over Fork semantics. |
| 07 / incremental reuse and two stages | D supplies the dirty plan and cached output ports. B injects character -> panels -> layout outputs. Story/style/music are reused, three image nodes are recomputed, layout rebuilt. Both stages show 3/7 reused nodes and independent `:draft` / `:hq` keys with stable seeds. |
| 08 / distribution and external references | Generic MIME renderers, Explore cards, evidence labels and GitHub/Bilibili/YouTube/Hugging Face links. Platform homepages are references, not fabricated hosted-work URLs. |
| 09 / a verifiable showcase | Real local PNG, text/code/JSON and WAV files are created. An original/fork comparison, panel coordinates and child inspection demonstrate a case without hardcoding it in the engine. E retains ownership of the final showcase. |
| 10 / rehearsal and transparent fallback | Explicit Local Fallback labels and inherited-substitute warnings; local composition/materialization is not labelled Live API. Stage switching loads separately generated files. Provider transport tests do not masquerade as paid-model validation. |
| 11 / P0 and owner boundary | B delivers Draft/HQ execution, composition, component and plan contracts. A's actual capture, D's complete Fork/Dirty scheduler, C's application and E's Self-Archive remain separate integration work. |
| 12 / engineering collaboration | A schema is the sole protocol source; TypeScript types are generated from it. All changes stay within B. Validation commands and teammate handoff are documented; no unconfigured CI or merged completion is claimed. |
| Appendix A | Field names follow the actual shared schema: `mime_type`, `sha256`, `producer_node_id`, `producer_run_id`, `runs.cache`, `preview_stage=draft|final`. Planbook illustrative JSON is not treated as a second schema. |
| Appendix B | No all-Agent interception claim, fixed reconstruction rate, fixed preview latency, or globally shortest Golden Path promise. Same-seed staging improves continuity but is not a guarantee across model implementations. |

## Hardening findings resolved

1. Content identity: new Artifact IDs/URIs with identical bytes no longer invalidate B's cache solely because instance identity changed. This does not redefine D's final shared cache-key specification.
2. Cached composition metadata: region and reference IDs are rebound to current instances, avoiding missing panels after a content-cache hit.
3. Source transparency: known substitute flags take priority over conflicting source labels; a composite reports inherited substitutes without mislabelling its actual composition operation.
4. Reuse denominator: inactive branches and descendants are excluded while their attempts/reasons remain visible in the full comparison.
5. MIME dispatch: image/svg+xml follows the image path, rather than being mistaken for XML text.
6. Plan failure behavior: ordering is checked before dispatch, cached inputs stay unchanged, partial results survive failures, cancellation propagates, and failed completion recording does not trigger model replay.

## Validation

Backend regression: 55 tests, including sequential injection, cached/new composition, stage/cache isolation, hash-equivalent instances, lifecycle hooks, cancellation, real file hashes, A archive and latest transfer verification, C HTTP execution and idempotency. Ruff and format check pass.

Frontend regression: 30 B tests plus C's 11 original tests, strict TypeScript and both builds. The standalone review still verifies two stages at desktop/mobile with 20 screenshots. The actual C app additionally verifies desktop/tablet/mobile Draft, Final, panels, comparison and modal controls. Composite widths differ (1588 Draft / 2068 Final); seeds remain stable per image node.

The build creates `demo/generated/original.aica.json`, `fork.aica.json`, `archives.json`, `validation.json`, and their `artifacts/` directory. These local verification files and screenshots are Git-ignored and regenerable. A's original validator is invoked before publishing. No external paid image API was invoked; real provider credentials and actual C/D application integration remain unverified.

## Submission commands

Run from the repository root after reviewing the working tree. These commands are prepared, not executed by this review:

```sh
git status --short --branch
git add -- apps/gateway/src/preview packages/artifact-renderers
git diff --cached --name-only
git diff --cached --check
git diff --cached --stat
git commit -m "feat(b): add generic staged preview replay and artifact renderers"
git push -u origin feature/b-preview-renderers
```

Before committing, the staged path listing must contain only B's two directories. The feature branch is based on dev and has a documented contract dependency on A's PR #1; it does not copy or modify A's implementation. PR description should distinguish local synthetic validation from actual vendor/API and full-platform integration.
