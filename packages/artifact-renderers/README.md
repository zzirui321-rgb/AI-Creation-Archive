# AiCA Artifact Renderers / Owner B

The main integration now uses A's standard Archive and D's real Fork/Plan/Execution
APIs. `ArchiveClient`, `variableFields`, `operationValue` and `loadArchiveAssets`
in `cIntegration.ts` are dynamic; no fixed CValues or seven-node session remains.
See [GENERIC_ALIGNMENT.md](handoff/GENERIC_ALIGNMENT.md) for selected branches,
separate B/C/D changes and three-case browser acceptance. The historical standalone
seven-node renderer review below is a fixture only, not the application runtime.

当前 C/D 交接以 [CD_HANDOFF_ZH.md](handoff/CD_HANDOFF_ZH.md) 为准。C/D 源码不复制到
B 分支；可审阅的装配代码位于 `handoff/integration/`，其中包含 C/D 目标基线、Provider
请求、Runtime 计划和缓存隔离 patch。

Protocol-driven React components for Image/Comic, Code/Web, Video/Audio and Agent/Skill artifacts. No application routing, graph editing, execution scheduling, variable mutation or model invocation is implemented here; C assembles these components and D supplies archive/run data.

## Install and verify

This package is independently testable before C initializes a workspace. Its dependencies and package lock are local to this directory; no root package configuration is modified. React and ReactDOM are peer dependencies for consumers.

```sh
cd packages/artifact-renderers
npm ci
npm run generate:contract
npm run typecheck
npm test
npm run build:demo
```

`src/schema.generated.ts` is generated directly from A's JSON Schema, never manually maintained. Generation prefers the checked-out shared schema, otherwise reads pinned Git object `958a0a9712ac27191183b893c79c1cbf8c535328`. Regenerate together with B's Python contract when adopting a new A revision. Import public `Archive`, `Artifact`, `Run` and `ArchiveNode` types from this package; they are indexed from the generated Archive type, not a second hand-written protocol.

```tsx
import {
  ArtifactRenderer, ArchiveDiffViewer, ExploreArchiveCard, ProvenanceAndLinksBar,
  type Artifact, type UriResolver,
} from '@aica/artifact-renderers';
import '@aica/artifact-renderers/styles.css';

const resolveUri: UriResolver = artifact => signedArtifactUrls[artifact.id];

<ArtifactRenderer artifact={artifact} archive={archive} resolveUri={resolveUri} />
<ArchiveDiffViewer originalArchive={original} forkedArchive={fork} resolveUri={resolveUri} />
<ExploreArchiveCard archive={archive} onOpen={openArchive} onFork={requestFork} resolveUri={resolveUri} />
<ProvenanceAndLinksBar archive={archive} showEvidence />
```

CSS is opt-in and scoped under `.aica-artifacts`. Consumers can override its CSS variables. The components use restrained layout and responsive widths; they do not own global page styles.

## EditTargetPicker

`EditTargetPicker` displays a semantic edit proposal over its source Artifact.
It renders actual mask overlays and selectable candidates, resets selection for
new proposals, and blocks confirmation for stale sources or unavailable images.
The host provides `resolveUri` and `onConfirm({proposalId, candidateId, sourceSha256})`;
production authentication, proposal APIs and Replay orchestration remain with C/D.
Detector scores are labelled as scores, not calibrated probabilities. Geometric
placement proposals are distinguished from semantic segmentation.
The separate `semantic-edit.html` page exercises B locally and is included in the
demo build. See [SEMANTIC_EDIT_ZH.md](handoff/SEMANTIC_EDIT_ZH.md) for setup and limits.

## ArtifactRenderer

Props: `artifact`, optional `archive`, `previewMode="draft"|"high_quality"|"final"`, `resolveUri`, `loadText`, `metadata`, `interactive=true`, `showReuseRatio=true`, `onPanelSelect`, `className`.

- PNG/JPEG/WebP/SVG and other image MIME types use an image element.
- Composite image metadata enables an image/panels segmented control, current child cache labels, prompt/region inspection and selection callbacks.
- Text/code/JSON are displayed as escaped content, with JSON pretty-printing. Markdown uses react-markdown + GFM with raw HTML disabled.
- Audio/video use native playback controls; HTML/Web uses a sandboxed iframe with no script or same-origin permissions.
- Unknown MIME types offer a file download. Missing/loading/failed URI and text-load states are explicit.
- Draft/HQ comes from Run stage or supplied preview mode. Unspecified stages are labelled Archived artifact, not assumed HQ.
- Actual sources are labelled `Live API`, `Prewarmed Cache`, `Local Fallback`, `Local Composition` or `Local Materialization`; unavailable source information stays unavailable. A known substitute flag overrides a conflicting Live API label. Composite outputs retain `Contains Local Fallback` when an input is a substitute. Recorded/Extracted/Inferred describe process evidence; they do not prove a remote model ran.
- `ReuseRatioBar` is shared with the comparison view: CACHE HIT is green, RECOMPUTE amber, REBUILD blue. The ratio counts only executable nodes on active branches, including active ancestors; dead/hidden/pruned branches are excluded. Diff columns/cards suppress repeated bars. Known Draft metadata is not relabelled as HQ merely because a caller requests HQ.

The default resolver accepts browser-accessible relative/HTTP(S)/blob media URLs and selected media data URLs. It rejects `file://`, JavaScript and unsupported schemes. C must map local paths to authorized gateway URLs or blob URLs. A host resolver must return a browser media URL, not an arbitrary filesystem path. Downloads resolve the full Artifact separately from its optional `preview_uri`.

For A's remotely published assets it prefers an accessible HTTP `locations` entry over an unusable original local URI, without rewriting identity or content hash. Composition can preserve nonvisual assets as `attachment_artifact_ids`; related audio/video/text remains independently renderable rather than being falsely embedded in an image.

`loadText(artifact, signal)` can inject authenticated fetches, local buffers or another artifact store. By default the component fetches the resolved URI, supports cancellation, and limits text previews to 2 MB. C owns authenticated transport and URL refresh policies. The library does not execute scripts from artifacts.

## ArchiveDiffViewer

Props: `originalArchive`, `forkedArchive`, optional `previewMode`, `resolveUri`, `loadText`, `nodeMapping`.

The two columns show completed terminal outputs for the selected stage; a node execution table compares content hashes and current cache decisions. Reuse counts derive from the latest current-revision successful Run for each executable node. Missing/failed/running work is unverified and does not inflate cache-hit counts. Different artifact IDs or URIs are not evidence of different content.

Terminal compositions are featured before other terminal outputs such as audio. Dead-end attempts remain visible in the execution comparison, with their reason and failed state, while being excluded from the reuse denominator. Golden Path is a recommendation based on successful history, not a globally shortest or uniquely optimal path; B does not compute it.

Variable changes show baseline/current values and ordered Keep/Override/Append operations. The component never reapplies operations. Node IDs are assumed stable across Forks; pass `nodeMapping` (original ID -> fork ID) if D remaps them. An omitted mode shows the latest run per node across stages; pass a mode to avoid mixing Draft/HQ during stage-specific reviews.

## ExploreArchiveCard and ProvenanceAndLinksBar

`ExploreArchiveCard` (alias `ExploreCard`) accepts `archive`, optional `href`, `onOpen`, `onFork`, `resolveUri`, `previewMode`. Built With tags are derived from execution tools/models. Callbacks are commands for C's application; the component does not Fork an archive itself. Open controls are shown only when a destination/callback exists.

`ProvenanceAndLinksBar` reads node provenance, evidence sources, and `metadata.external_links`. It labels GitHub/Bilibili/YouTube/Hugging Face links by exact host or recognized subdomain, filters unsafe URLs, and opens external sites with `noopener noreferrer`. It does not infer provenance from archive marketing labels.

## B/D extension contract

Components consume `extensions["aica.preview"].runs[run_id]`, as returned by the Python engine's `extension_patch()`:

```text
source, origin_source, is_substitute, contains_substitute, stage, seed, resolved_prompt
artifact_ids, canvas: {width,height}
regions: [{artifact_id, node_id, rect:{x,y,width,height}, prompt, caption}]
```

Keep these outside Artifact objects, which forbid extra properties in A's schema.
The D callback currently captures metadata as a verified preview+json sidecar;
`loadArchiveAssets` creates a nonmutating in-memory extension view for components.
It binds the sidecar to the primary hash/Run and rebinds cached panel references
by content, producer, revision and stage. It does not rewrite the stored Archive.
Components prefer current Run decisions over cached labels. Raw B results alone
are not complete archives: D/A own Run, actor and revision recording.

`ArchiveClient` uses authenticated same-origin `/api` requests, supports typed
variables and the actual D endpoints. It rejects mismatched immutable snapshots,
hashless/tampered assets and unauthorized responses. Blob URLs are revoked on
view teardown; concurrent reads are bounded and aborted on snapshot changes.
The local gallery limits one loaded archive to 256 artifacts/128 MiB; larger
archives need paginated assets. Unknown registered-tool requirements are surfaced
by D, not converted to image generation.

## Local browser review

First build actual offline artifacts from the repository root:

```sh
python apps/gateway/src/preview/scripts/build_review.py --output packages/artifact-renderers/demo/generated
```

Then, in this package, run `npm run dev -- --port 5173`. The internal review has Artifacts, Original/Fork, Execution and Explore views, plus a global Draft/Final switch. Both stages load actual separately generated PNGs, with the same seeds and distinct stage cache keys. The seven-node fixture reports 3/7 reused nodes, three recomputations and one rebuild; a rejected branch is separate. Text, code/JSON, audio, provenance/evidence and all four platform links are visible. The graph table is an internal review surface, not C's React Flow canvas or E's `examples/ai-comic` workflow.

`original.aica.json` and `fork.aica.json` are standalone protocol archives using relative `artifacts/` references. The review injects a URI resolver mapping them to `/demo/generated/artifacts/`; the same archives can be copied with their artifact directory for D's local checks. Generated assets and browser screenshots are ignored by Git. Build the fixture before `build:demo`; the build copies its generated bundle for local serving.

Tests cover stage/revision selection, cache statistics, variable operations, URI handling, multimodal rendering, failure states, panel inspection, card callbacks and provenance links. Browser verification can run with `node scripts/verify-browser.mjs http://127.0.0.1:5173`; see that script for desktop/mobile screenshots and asset checks.

Local verification on 2026-10-16: 23 Vitest cases passed, TypeScript strict checking passed, and the production review build passed. Headless Edge checks both stages at 1440x1000 and 390x844, including source labels, 3/7 reuse ratio, status colors, ordered variable operations, failed branch and real image/audio loads. It captures 20 screenshots across Artifacts/panels, Original/Fork, Execution and Explore. Provider tests are HTTP simulations; external model credentials and C/D integration have not been exercised. No fixed preview latency or extraction-accuracy percentage is promised.
