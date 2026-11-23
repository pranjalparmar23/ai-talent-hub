"""Saving, loading and comparing golden-set runs. Shared by the two runners.

A saved run is one JSON file:

    {"area": "retrieval", "dataset": "retrieval.v1.jsonl", "version": 1,
     "run_at": "2026-11-23T09:00:00+00:00", "filters": {...}, "settings": {...},
     "report": {"all": {...}, "split=dev": {...}, "tag=technical": {...}},
     "cases": [{"id": "ret-001", "ok": true, ...}, ...]}

`report` is the shape `metrics.deltas()` compares. `cases` says which cases passed, so a
comparison can name the ones that started or stopped passing.

The first reviewed run is kept next to its dataset as the baseline
(`retrieval.v1.baseline.json`), and later runs are compared with it (`--compare`).
No thresholds live here. The team agrees a tolerance after the baseline exists.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.metrics import Delta, deltas, fmt, render_table

# Repository root / data / eval / results (the old Phase 2 script writes there too).
RESULTS_DIR = Path(__file__).resolve().parents[2] / "data" / "eval" / "results"


class ReportError(Exception):
    """A run file that cannot be saved, found or read."""


def stamp(now: datetime) -> str:
    """UTC time for a file name: 20261123T090000Z."""
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_record(
    record: Mapping[str, Any],
    *,
    now: datetime,
    results_dir: Path = RESULTS_DIR,
) -> Path:
    """Write a run to `<results_dir>/<area>_v<N>_<stamp>.json` and return the path."""
    name = f"{record['area']}_v{record['version']}_{stamp(now)}.json"
    path = Path(results_dir) / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise ReportError(f"cannot write {path}: {exc.strerror or exc}") from exc
    return path


def resolve_record_path(name: str, *folders: Path) -> Path:
    """The file as given, or (if that does not exist) the same name in one of `folders`.

    So `--compare retrieval.v1.baseline.json` finds the baseline next to its dataset.
    """
    given = Path(name)
    if given.is_file():
        return given
    for folder in folders:
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate
    raise ReportError(f"no such file: {name}")


def load_record(path: Path) -> dict[str, Any]:
    """Read a saved run and check it has the parts a comparison needs."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc.strerror or exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"{path} is not valid JSON ({exc.msg})") from exc
    if not isinstance(record, dict) or not all(
        k in record for k in ("area", "report", "cases")
    ):
        raise ReportError(f"{path} is not a saved golden-set run")
    return record


def changed_cases(
    before: Sequence[Mapping[str, Any]], after: Sequence[Mapping[str, Any]]
) -> tuple[list[str], list[str]]:
    """(started failing, started passing): case ids that are in both runs and flipped."""
    was = {c["id"]: bool(c["ok"]) for c in before}
    now = {c["id"]: bool(c["ok"]) for c in after}
    failing = sorted(i for i in now if i in was and was[i] and not now[i])
    passing = sorted(i for i in now if i in was and not was[i] and now[i])
    return failing, passing


def format_changes(changes: Sequence[Delta], *, only_moved: bool = True) -> str:
    """A table of how each metric moved. By default only the ones that moved."""
    rows = [
        [d.group, d.metric, fmt(d.before), fmt(d.after), f"{d.change:+.2f}"]
        for d in changes
        if not only_moved or abs(d.change) >= 0.005
    ]
    if not rows:
        return "No metric moved."
    return render_table(["group", "metric", "before", "after", "change"], rows)


def setting_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """The settings (cutoff, weights, model...) that differ between two runs.

    A change in a metric means little until you know whether the code, the data or a
    setting moved, so a comparison always names the settings that are not the same.
    """
    old = before.get("settings") or {}
    new = after.get("settings") or {}
    return [
        f"{key} {old.get(key)} -> {new.get(key)}"
        for key in sorted(set(old) | set(new))
        if old.get(key) != new.get(key)
    ]


def compare_text(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    """Everything a comparison prints, as text. Notes mismatches that make it unfair."""
    lines: list[str] = []
    if before.get("dataset") != after.get("dataset"):
        lines.append(
            f"Note: the earlier run used {before.get('dataset')}, this one {after.get('dataset')}."
        )
    if before.get("filters") != after.get("filters"):
        lines.append(
            "Note: the two runs used different filters, so the 'all' rows cover "
            "different cases. Compare split= and tag= rows."
        )
    changed = setting_changes(before, after)
    if changed:
        lines.append("Settings that differ: " + "; ".join(changed))
    lines.append(f"Compared with the run of {before.get('run_at', 'unknown date')}:")
    lines.append(format_changes(deltas(before["report"], after["report"])))
    failing, passing = changed_cases(before["cases"], after["cases"])
    if failing:
        lines.append(f"Started failing: {', '.join(failing)}")
    if passing:
        lines.append(f"Started passing: {', '.join(passing)}")
    return "\n".join(lines)