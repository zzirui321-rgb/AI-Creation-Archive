# Owner Assembly Patches

远程核对记录（2026-10-18）：C `feature/c-frontend-mvp@baa6908`；D
`feature/d-backend@78ff5fa`。D 该分支是平台导入/分析实现，不等同于执行 Worker；
执行相关 patch 的目标基线仍以各 patch 头部声明为准。完整交接见
`../CD_HANDOFF_ZH.md`。

Current provider-completion assembly: see `../PROVIDER_COMPLETION.md`.
`c-provider-settings.patch` targets bd_First_Edition_16.29@f0fc294 and supersedes
the earlier C-only assembly for that platform snapshot. `d-request-providers.patch`
applies after `d-runtime.patch` to the pinned test Runtime, adding per-request
provider registries and conditional cache writes for non-substitute output.

These files are review documents shipped within B's directory. They do not apply
themselves to C/D/E source or authorize changes to teammate branches.

| File | Required Baseline | Scope |
| --- | --- | --- |
| d-runtime.patch | test@6b98aa4 | D stage/cache plan and composition REBUILD |
| c-assembly.patch | feature/c-frontend-mvp@baa6908 | C new generic execution workspace and B wiring |
| acceptance-examples.patch | test@6b98aa4 | Three labelled synthetic .aica acceptance inputs |

For an isolated full-stack integration checkout, use D's Runtime from test,
C's original module frontend at baa6908, and this B source. Inspect each patch
with `git apply --stat PATH`, then `git apply --check PATH` before applying the
corresponding owner assembly. No journal branch is required.

After dependencies are available, launch B's `start-latest.ps1` from that
integration checkout and open `http://127.0.0.1:5174/#/execute`. B-only checkout
is a library/test delivery, not a standalone clone of the entire running site.

The original C module preview and the new archive execution page are distinct;
the existing module button has not been converted into a complete Archive flow.
No claim is made that its display-only records can execute automatically.
