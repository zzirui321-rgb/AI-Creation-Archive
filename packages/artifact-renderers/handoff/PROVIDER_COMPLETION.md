# B Provider Completion And Local Owner Assembly

Role: B, confirmed by the user's ongoing assignment. The user authorized latest
A/C/D alignment, finishing user-selected model calls, and tests against their
local image service. Integration work is isolated on `local/b-provider-completion`.
The B-only source delivery is on `feature/b-generic-preview`, PR #11. At the
user's explicit request, the B directories are also delivered directly to
`bd_First_Edition_16.29`; A/C/D source files are not changed by that delivery.
The PR's ready-for-review status does not mean it has merged into dev.

## Selected Sources

Current A/C/D alignment is the user-confirmed `bd_First_Edition_16.29@f0fc294`.
See `BD_ALIGNMENT.md` for direct contract tests. The table below records the
earlier mixed-branch implementation; its supplemental Runtime remains isolated.

| Responsibility | Source |
| --- | --- |
| A protocol/capture/upload | feature/a-protocol-mcp @ a0cc735 |
| C/D actual account/repository/branch frontend assembly | bd_First_Edition_16.29 @ f0fc294 |
| D reusable module parser | feature/d-reusable-graph-modules @ 6e4a3c3 |
| D executable Runtime | test @ 6b98aa4, isolated process |
| B base implementation | feature/b-generic-preview @ a7e26f9, plus local integration work |

The journal branch is excluded. The separate fusion frontend is not installed
over the working account/repository application. D's semantic parser and D's
executable Runtime still have conflicting package namespaces and run separately.
Semantic suggestions remain non-executable; the assembly does not manufacture
execution bindings for them.

## User Settings To Model Execution

Existing C model settings now feed both actual single-node previews and full
Archive executions. `ImageProviderConfig` / Python `ImageConfig` contain selected
provider, URL, model, optional stage models, deadline, fallback policy and session
key. Keys remain in browser/process memory. D excludes `image_config` from job
payloads and persists only a non-secret identity digest. Each job receives an
independent registry; no user changes global model environment variables.

- `/api`: latest C/D account/repository platform, preserves user authentication.
- `/runtime-api`: loopback proxy to D Runtime + B. Includes configured POST plan,
  executions, Fork, authenticated bytes, complete transfer export and previews.
- `/parser-api`: loopback proxy to latest D parser and evidence review.

Source is truthful: Live API / Prewarmed Cache / Local Fallback, and local
composition/materialization are labelled separately. `allow_fallback=false`
returns an error when a model fails. It cannot report a procedural image as a
successful model call. Successful live output is cacheable in D; fallback output
and composites containing fallback are excluded from the real-provider cache.
Provider/URL/stage-model changes alter cache identity without exposing the key.

Image adapter v4 adds native synchronous Qwen protocol and an explicit sdcpp
provider for the tested local service. The latter sends real stage `steps`,
dimensions and seed, uses `/v1/img2img` for a single actual reference image, and
does not drop references silently. OpenAI-compatible, SiliconFlow and Fal
protocol handlers remain. Resolved style and other semantic input/variable
values enter the actual prompt. Unsupported tools/modalities remain explicit.
Text/JSON/code/HTML materialization does not claim to be LLM generation.

The API deadline still defaults to 10 seconds. A user can explicitly select
1-300 seconds for a slower local service. Local acceptance used 180 seconds;
this is an explicit configuration, not a latency promise. Same seed/aspect ratio
are verified across stages; cross-model visual identity is not guaranteed.

## Local Use

Run `apps/gateway/src/preview/scripts/start-unified.ps1` from the checkout root.
Site: http://127.0.0.1:5176/#/execute

In the existing settings page select:

- Provider: local FLUX / stable-diffusion.cpp (`sdcpp`).
- URL: `http://127.0.0.1:8000/v1/images/generations`.
- Model: `flux3-image-klein`.
- Key: any nonempty local test value, such as `sk-local`.
- Timeout: 180 seconds for this local model.
- Disable Local Fallback when verifying real API generation.

The user's model service is independent of the site and is not restarted by the
launcher. The initial LAN address was loopback-only; an optional temporary relay
was used for the first acceptance. Subsequent acceptance uses the direct loopback
URL after the user's restart, and the final deployment does not need the relay.
The optional helper does not modify the model's source or configuration.

Data/logs are outside Git under the local application's `AiCA/provider-completion`
directory. `logs/processes.json` identifies this site's processes. Preserve that
data to retain tested Archives, actual model images and Run/cache history.

## Owner Handoff

Pure B code is in preview-service and artifact-components, at the two established
B directories. Cross-owner assembly is separately delivered as patches:

1. `d-runtime.patch`: existing stage plan/cache and composition integration for
   D test@6b98aa4.
2. `d-request-providers.patch`: apply after that patch; adds ephemeral per-request
   registries and prevents substitute results from poisoning D's live cache.
3. `c-provider-settings.patch`: apply to C/D platform snapshot f0fc294; connects
   settings, real bytes, both workspaces and latest parser review.

The launcher reproducibly exports pinned D/A sources and applies the D patches
outside the checkout. No teammate source is silently overwritten on their remote
branches. Original local checkouts remain intact.

## Verification Evidence

Actual browser/model evidence is generated locally under
`packages/artifact-renderers/.c-integration/live-provider/`:

- `report.json`: full Original Draft/Final, variable Fork Draft/Final, repeat
  Final, API receipts, stage seed/dimensions and cache counts.
- `final-archive.json`: A-compatible Archive with actual image hashes and history.
- `reference-report.json`, `reference.png`: actual reference-image request result.
- `c-module-report.json`: original C Studio button to local model to displayed
  actual file, using the existing user settings and real account/repository flow.
- Desktop/mobile screenshots, verified nonblank and without horizontal overflow.

The live field-guide workflow has five executable nodes. Changing `creature`
reuses three nodes, recomputes one image and rebuilds one composition. Repeating
Final reuses all five. Changed subject image hashes differ; the unaffected
background image hash stays the same. All image receipts say live_api and are
not substitutes. Session keys are absent from exported Archives and browser
persistent settings.

Three separate synthetic acceptance Archives also exercise image/comic, field
guide and self-archive workflows in explicit offline mode; these are labelled
Local Fallback and are not counted as live model tests. Semantic input review
does not make a DeepSeek call. Paid vendor availability was not tested.

Python verification: B's original 60 cases and six request-provider cases passed.
Frontend: C 31 Vitest cases, B 33 Vitest cases, typecheck and builds passed.
Full latest A/D regression: 360 passed, six existing A tests failed. Four require
Windows symlink privilege (WinError 1314). Two native MCP tests report connection
refused while shutting down a coordinator after stdio exit. The import/reconnect
test reaches and passes its functional assertions before its final shutdown
cleanup fails. These results are not represented as a green full-repository run;
no A tests or security checks were weakened. Ruff and whitespace checks are
recorded separately after final edits.

Final verification: all 66 B pytest cases passed in one run; Ruff check and B
format check passed. A's actual CLI validated the generated live-model Archive
with `valid=true` and no errors. All three offline browser workflows and latest
D scoped input preparation passed after the owner alignment. After the final
restart, the live-model project still rendered actual images and its configured
Final plan verified 5/5 cache hits, without making another model call. The model
service and all site services remain bound to loopback in the final deployment.
