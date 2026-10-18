# Generic Archive Preview

Layer-local editing is implemented by independent background/subject/detail
Artifacts and `layout.mode=layer_stack`, or by explicit `masked_replace`.
The composer never regenerates or resizes its base; pixels outside overlay/mask
support are copied exactly. Each comic panel may be a layered scene before outer
grid composition. See `packages/artifact-renderers/handoff/LAYERED_SCENE.md` for
the contract, limitations and real local-model pixel acceptance.

AiCA executes standard v0.1 Archive nodes. Business fixtures are not imported by
the Runtime bridge. Unknown tools, mismatched versions, invalid DAGs and missing
artifacts are explicit blockers, never successful mock runs.

## Interfaces

- `PreviewEngine.execute_node(node, artifacts, context=ExecutionContext(...))`
  executes a single standard node. `execute_plan` remains a helper for D-ordered
  plans; it does not invent edges, variables or dirty decisions.
- `register_preview_tools(registry, settings)` registers B handlers into D's
  existing registry: version `b-generic-3`, kind `model`, one output port.
- Tools: image_gen/image-provider, layout_compose/compose/layout,
  text_materialize, json_materialize. D's default tools remain registered.
- D resolves inputs, controls consent/jobs/cache, records through A, and owns Run
  and Artifact IDs. B reads authorized dependencies through D, verifies hashes,
  executes its adapter and calls D's capture API for primary bytes and metadata.
- `application/vnd.aica.preview+json` sidecars carry source, stage, seed, primary
  SHA256, region coordinates and reference hashes without changing A's schema.
  The read-only `context.engine.recorder` accessor is version-specific to this D
  integration, not a claimed stable public SDK.

The old `/preview/review/sessions` fixed-profile route is removed. Main flow:
imports -> archive -> forks -> plan -> executions -> archive/artifacts.
Variables/questions, node IDs/counts and topology come from the actual Archive.
Only registered tools execute; arbitrary scripts and all possible model APIs
are not supported merely because their records can be displayed.

## Cache And Stages

D calculates node cache identity from tool/version, params, resolved inputs,
upstream hashes, environment and stage. B additionally verifies effective
profile/model/adapter fingerprints. Draft/HQ have separate `:draft`/`:hq` caches,
same Seed and exact aspect ratio. Procedural geometry is normalized across sizes;
stochastic/cross-model APIs cannot guarantee identical composition.

The separate D patch makes composition misses REBUILD and exposes verified cache
predictions on `/plan`. Execution verifies again. The canvas shows actual Run
decisions; the sidebar names the next execution plan so predictions are not
misrepresented as results. Unchanged bytes are copied without calling B.

## Providers

`PREVIEW_PROVIDER=prewarmed_cache|auto`: verified prewarm -> live -> fallback.
`live_api`: live -> fallback. `local_fallback|mock_svg`: procedural PNG only.
Configure AICA_IMAGE_API_BASE, AICA_IMAGE_API_KEY, AICA_DRAFT_MODEL, AICA_HQ_MODEL,
AICA_IMAGE_API_STYLE=openai|siliconflow|fal|qwen|sdcpp; previous IMAGE_* aliases remain.
`AICA_PREWARMED_DIR` selects a trusted server-owned directory of verified prewarm
manifests/objects; the bridge never accepts this path from an uploaded node.
The live operation deadline defaults to ten seconds. Explicit per-request user
configuration can extend it to at most 300 seconds for slower local models.
`allow_fallback=false` requires a verified provider artifact or a failure.
Source-specific B caches keep fallback outputs from suppressing API recovery.
The provider assembly patch lets D cache successful provider results while
preventing fallback results, and compositions containing them, from entering
the real-provider Runtime cache.

`ImageConfig` carries the user's selected provider, URL, model, optional Draft/HQ
models, timeout and session key. Each execution receives an independent registry;
keys are removed before persisting job payloads and are absent from the Archive,
Harness metadata and cache identity. Provider and model changes affect cache
identity. Resolved style/variable values are included in actual image prompts.

The qwen adapter implements the synchronous native multimodal image protocol.
The sdcpp adapter implements the local service's OpenAI-shaped generations API
and single-reference `/v1/img2img`. Providers with no reference support return an
explicit provider error (or a labelled fallback if enabled); they do not silently
discard reference images. Other providers' protocol tests use HTTP mocks; they
are not evidence of paid service availability.

Fallback varies palette, texture, subject geometry and normalized layout using
resolved Prompt, bound values, node/tool, Seed and stage. It is visibly marked
Local Fallback. The image shows node, stage, variables, Seed and an input hash.
Actual file SHA256 appears in Artifact metadata/UI, since a file cannot contain
its own final hash. New composition is a standalone hash-verified PNG; child
regions and nonvisual attachment references are preserved. Text/JSON/code/HTML
materializers save supplied content without inventing a model response.
Audio/video artifacts can be displayed/reused; new synthesis needs an adapter.

## Local Integration

The current A/C/D contract target is `bd_First_Edition_16.29@f0fc294`.
Schema fallback, transfer verification and generated frontend types use that
snapshot. See `packages/artifact-renderers/handoff/BD_ALIGNMENT.md` for checks
against its actual validator, account/repository API and C patch.
It has no native Fork/Replay execution API. The isolated test Runtime and its
newer A compatibility dependency are explicitly supplemental, not silently
substituted for the selected platform sources.

The current A/C/D assembly uses `scripts/start-unified.ps1` and opens
http://127.0.0.1:5176/#/execute. Configure provider/model/key in the existing C
settings screen. `/api` remains C/D's account/repository platform; `/runtime-api`
is D's Fork/Plan/Replay worker with B; `/parser-api` accesses latest D parsing.

C's single-node preview uses `/runtime-api/previews` when no external gateway is
selected. It returns a real hash-addressed image and actual source. This is a
single-node preview, not a claim that display-only records have executable DAGs.
Complete .aica imports enter the genuine Archive workspace without losing fields.
Semantic graphs remain non-executable.

Current verified local image setup: select sdcpp, configure the local model's
generations URL and actual model ID, use any nonempty test key, choose a suitable
local timeout and disable fallback for live acceptance. No business-specific
model, scene or endpoint is hardcoded in the engine.

## Previous Runtime Checkout

```powershell
& ./apps/gateway/src/preview/scripts/start-latest.ps1
```

URL: http://127.0.0.1:5174/#/execute. D/B worker: 8013. Data/logs live outside
checkout in LocalAppData/AiCA/executable-platform. Development bearer is held in
process memory and injected by a same-origin loopback proxy. Existing compatible
Conda SQLite is loaded before A imports; its version check is not bypassed.

```sh
python -m preview.scripts.generic_cases --output packages/artifact-renderers/demo/generated/generic-cases
python -m pytest -c apps/gateway/src/preview/pyproject.toml apps/gateway/src/preview/tests -q
python -m ruff check apps/gateway/src/preview
python -m ruff format --check apps/gateway/src/preview
```

Tests call real D background jobs, A validation/recording and actual bytes. Local
ignored .venv reuses installed Conda packages/source mappings for subprocesses.
On the B-only submission branch, pytest instead exports pinned A/D Git sources
to temporary storage and applies the supplied D patch there. For Windows Conda
with older SQLite, set AICA_TEST_SQLITE_DLL to an already-installed compatible
DLL before pytest; A's version check remains active. No personal path is committed.
Historical `scripts/build_review.py` is only an isolated renderer fixture.
Three synthetic acceptance archives are under examples/ai-comic,
examples/workflows and examples/self-archive. They are labelled synthetic,
not presented as the team's actual captured history. No paid API is called.

See packages/artifact-renderers/handoff/GENERIC_ALIGNMENT.md for owner patches,
selected branches, limitations, browser acceptance and plan mapping.

## Single-Image Local Edits

An image node may output a complete image. Local editing does not require splitting
the Graph into layer nodes. Use `execution.params.edit_mode="masked_replace"`,
an `original_image` Artifact input and either an `edit_mask` Artifact input or a
normalized `execution.params.edit_region=[x,y,width,height]`.
`PreviewEngine.execute_image_edit()` provides the equivalent convenience API.
Edits retain the original dimensions in both stages so protected pixels remain
exact; ordinary full-image generation keeps its existing resolution profiles.
See `packages/artifact-renderers/handoff/SINGLE_IMAGE_EDIT_ZH.md` for Runtime,
provider, persistence and UI contracts and the explicit segmentation limitations.

## Semantic Target Proposals

`PreviewEngine.propose_image_edit(original_artifact, EditIntent(...))` calls a
configurable segmentation service and saves original-resolution PNG masks with
source hashes and model revisions. `execute_proposed_edit` accepts one confirmed
candidate; ambiguous targets, stale sources, missing objects and unavailable
segmentation never trigger a full-image fallback. Explicit masks remain usable.
The optional local service uses pinned Grounding DINO and SAM 2 snapshots through
an existing compatible ML environment, independently of the image provider.
Set `AICA_SEGMENT_API_URL` and optional `AICA_SEGMENT_API_KEY` to configure it.
See `packages/artifact-renderers/handoff/SEMANTIC_EDIT_ZH.md` for C/D integration,
standalone local review, persistence requirements, tests and language limitations.

## Comic Panel Continuity

For a comic archive where each image node is one Panel, D should resolve the
Panel's upstream character, style and environment Artifacts before calling B:

```json
{
  "reference_artifact_ids": ["character-v2", "style-v1", "environment-v1"],
  "consistency_context": {
    "identity_hash": "character-v2-style-v1-environment-v1",
    "seed": 4202,
    "camera": "medium-shot",
    "composition": "character-left-environment-right",
    "character_ref": "character-v2",
    "style_ref": "style-v1",
    "background_ref": "environment-v1"
  }
}
```

B verifies every reference as an existing image Artifact, merges role references
into `reference_artifact_ids`, preserves the requested Seed, adds continuity
constraints to the resolved prompt, and includes the normalized context plus
reference content hashes in the cache fingerprint. A character change therefore
changes only Panels whose context references that character; an unchanged Panel
can remain `CACHE_HIT`. A changed Panel can be followed by a `REBUILD` layout
step through `execute_plan` without re-running cached Panels. B does not infer
dirty propagation from prompt text; D remains responsible for the actual plan.
