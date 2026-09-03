# Upstream pins

The upstream commits every task in
[`docs/plans/2026-09-03-microduck-cli-env-teach-operate-rules.md`](plans/2026-09-03-microduck-cli-env-teach-operate-rules.md)
is validated against. Nothing from these repositories is copied into this
repo; the CLI implements their documented commands and wire protocol and
links to their docs. Re-pin deliberately (one PR, all rows at once) and
re-run the on-box verification (t23) when you do.

| Repository | Ref | Commit | Date | Why this ref |
|---|---|---|---|---|
| `pollen-robotics/microduck` | branch `sim-remote-io` | `0cd676d6fbb6e90a762c84aa63abe7a02dbc9495` | 2026-09-02 | The only branch carrying `robotd --sim` / `--fake` and `scripts/duck-sim`; `main` (`bc41fb5`) has no simulator backend. |
| `pollen-robotics/microduck` · `duck-ipc-proto/src/lib.rs` | same commit | blob `33224abe5be065f793904ba5a43380409f2cbd57` | 2026-09-02 | The JSON-RPC contract (`pub const API_VERSION: u32 = 16;`) the `ipc/proto.py` table is transcribed from. |
| `pollen-robotics/microduck_rl` | branch `develop` | `29e887ecfbf5d37144759e5a9f8a176dfb83d547` | 2026-09-02 | `duck-body`, `train`, `publish`, `scripts/export.py`, `scripts/infer_policy.py`. |

Fetch a pinned file without cloning:

```bash
gh api "repos/pollen-robotics/microduck/contents/duck-ipc-proto/src/lib.rs?ref=0cd676d6fbb6e90a762c84aa63abe7a02dbc9495" --jq '.content' | base64 -d
```
