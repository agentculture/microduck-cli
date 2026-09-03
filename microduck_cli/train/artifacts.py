"""Append-only JSON-lines ledger of what the train lane produced.

One line per artifact: task id, run path, checkpoint, ONNX path, HF repo and
a timestamp. Nothing here executes anything or imports the RL repo — it is
plain bookkeeping under the injected state dir, appended to and read back by
whichever caller (the `policy` noun, later tasks) drives the lane's argv
builders in :mod:`microduck_cli.train.lane`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_LEDGER_RELPATH = Path("train") / "artifacts.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ArtifactRecord:
    """One entry in the train-lane artifact ledger."""

    task_id: str
    timestamp: str
    run_path: str | None = None
    checkpoint: str | None = None
    onnx_path: str | None = None
    hf_repo: str | None = None


def _ledger_path(state_dir: str | os.PathLike[str]) -> Path:
    return Path(state_dir) / _LEDGER_RELPATH


def append_artifact(
    state_dir: str | os.PathLike[str],
    task_id: str,
    *,
    run_path: str | None = None,
    checkpoint: str | None = None,
    onnx_path: str | None = None,
    hf_repo: str | None = None,
    timestamp: str | None = None,
) -> ArtifactRecord:
    """Append one artifact record for `task_id`. Never overwrites prior entries."""
    record = ArtifactRecord(
        task_id=task_id,
        timestamp=timestamp or _now_iso(),
        run_path=run_path,
        checkpoint=checkpoint,
        onnx_path=onnx_path,
        hf_repo=hf_repo,
    )
    path = _ledger_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), ensure_ascii=False))
        handle.write("\n")
    return record


def read_artifacts(
    state_dir: str | os.PathLike[str], task_id: str | None = None
) -> list[ArtifactRecord]:
    """Read all recorded artifacts, optionally filtered to one `task_id`.

    Unreadable/malformed lines are skipped rather than raising — the ledger
    is diagnostic bookkeeping, not a contract callers should crash on.
    """
    path = _ledger_path(state_dir)
    if not path.is_file():
        return []
    records: list[ArtifactRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            record = ArtifactRecord(
                task_id=str(data["task_id"]),
                timestamp=str(data["timestamp"]),
                run_path=data.get("run_path"),
                checkpoint=data.get("checkpoint"),
                onnx_path=data.get("onnx_path"),
                hf_repo=data.get("hf_repo"),
            )
        except (ValueError, KeyError, TypeError):
            continue
        if task_id is not None and record.task_id != task_id:
            continue
        records.append(record)
    return records
