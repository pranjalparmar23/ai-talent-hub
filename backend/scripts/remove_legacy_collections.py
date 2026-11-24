"""Delete the five original ChromaDB collections (Phase 7, task 15).

The knowledge layer replaced them with `interview_knowledge` and `learning_knowledge`.
Nothing in the app reads the old five any more. Run from backend/, with ChromaDB up:

    python scripts/remove_legacy_collections.py              # show the plan, delete nothing
    python scripts/remove_legacy_collections.py --yes        # delete

Before it deletes anything it checks, and it refuses (exit 1) if any check fails:

1. ChromaDB answers.
2. `interview_knowledge` and `learning_knowledge` exist and hold chunks.
3. A snapshot made by `scripts/export_chroma.py` covers every old collection that is
   still there, with the same number of rows as ChromaDB holds now, and each
   collection's JSONL file has that many lines. So nothing can be lost that is not
   saved. The newest `data/snapshots/pre-rag-rebuild-*` folder is used, or pass
   `--snapshot FOLDER`.

It only ever deletes the five names listed in LEGACY_COLLECTIONS below. Run again
after a delete and it finds nothing to do and exits 0. There is no option to skip the
checks: to take a new snapshot, run `python scripts/export_chroma.py`.
"""

# ── Silence ChromaDB PostHog telemetry (must be BEFORE any other imports) ──
try:
    import posthog

    posthog.capture = lambda *args, **kwargs: None
    posthog.Posthog.capture = lambda *args, **kwargs: None
except ImportError:
    pass

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# Allow running this script directly (without `python -m`)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = REPO_ROOT / "data" / "snapshots"
SNAPSHOT_PREFIX = "pre-rag-rebuild-"

# The collections this script may delete. It is the only place these names are kept.
LEGACY_COLLECTIONS = (
    "behavioral_questions",
    "company_interviews",
    "dsa_notes",
    "interview_experiences",
    "learning_resources",
)
# The collections that must exist and hold data before anything is deleted.
KNOWLEDGE_COLLECTIONS = ("interview_knowledge", "learning_knowledge")


def find_snapshot(root: Path, explicit: Path | None = None) -> Path | None:
    """The folder given, or the newest `pre-rag-rebuild-*` folder that has a manifest."""
    if explicit is not None:
        return explicit
    if not root.is_dir():
        return None
    folders = sorted(
        p
        for p in root.iterdir()
        if p.is_dir()
        and p.name.startswith(SNAPSHOT_PREFIX)
        and (p / "manifest.json").is_file()
    )
    return folders[-1] if folders else None


def _line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def snapshot_problems(snapshot: Path, present: Mapping[str, int]) -> list[str]:
    """Why this snapshot does not cover the old collections that are still in ChromaDB.

    `present` maps each old collection that exists to its current chunk count. An empty
    list means every one of them is saved in full.
    """
    manifest_file = snapshot / "manifest.json"
    if not manifest_file.is_file():
        return [f"{snapshot} has no manifest.json"]
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        saved = {c["name"]: c for c in manifest["collections"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"cannot read {manifest_file}: {exc}"]

    problems: list[str] = []
    for name, count in present.items():
        entry = saved.get(name)
        if entry is None:
            problems.append(f"{name}: not in the snapshot")
            continue
        exported = entry.get("rows_exported")
        if exported != entry.get("count_in_chroma"):
            problems.append(f"{name}: the snapshot says it was not exported in full")
        if exported != count:
            problems.append(
                f"{name}: ChromaDB holds {count} chunks now, the snapshot has {exported}. "
                "It changed after the snapshot. Run scripts/export_chroma.py again"
            )
        data_file = snapshot / f"{name}.jsonl"
        if not data_file.is_file():
            problems.append(f"{name}: {data_file.name} is missing from the snapshot")
        elif _line_count(data_file) != exported:
            problems.append(f"{name}: {data_file.name} does not have {exported} rows")
    return problems


def run(
    store: Any,
    *,
    snapshot_root: Path = SNAPSHOT_ROOT,
    snapshot: Path | None = None,
    delete: bool = False,
    out=print,
) -> int:
    """Check, then (with `delete=True`) remove. Returns the exit code: 0 done, 1 refused."""
    try:
        store.heartbeat()
        names = set(store.list_collections())
    except Exception as exc:
        out(f"Cannot reach ChromaDB: {exc}")
        out("Is the container running?  docker compose ps chromadb")
        return 1

    present = {n: store.count(n) for n in LEGACY_COLLECTIONS if n in names}
    if not present:
        out("None of the old collections are in ChromaDB. Nothing to do.")
        return 0

    out("Old collections found:")
    for name, count in present.items():
        out(f"   {name:24s} {count:5d} chunks")

    problems: list[str] = []
    out("\nNew collections:")
    for name in KNOWLEDGE_COLLECTIONS:
        if name not in names:
            out(f"   {name:24s} MISSING")
            problems.append(f"{name} does not exist. Run: python -m app.ingest.run")
            continue
        count = store.count(name)
        out(f"   {name:24s} {count:5d} chunks")
        if count == 0:
            problems.append(f"{name} is empty. Run: python -m app.ingest.run")

    folder = find_snapshot(snapshot_root, snapshot)
    if folder is None:
        out(f"\nSnapshot: none found under {snapshot_root}")
        problems.append(
            "no snapshot of the old collections. Run: python scripts/export_chroma.py"
        )
    else:
        out(f"\nSnapshot: {folder}")
        problems.extend(snapshot_problems(folder, present))

    if problems:
        out("\nRefusing to delete anything:")
        for problem in problems:
            out(f"   - {problem}")
        return 1

    out("\nAll checks passed.")
    if not delete:
        out(
            "Nothing was deleted. Run again with --yes to delete the collections above."
        )
        return 0

    deleted: list[str] = []
    for name in present:
        try:
            store.delete_collection(name)
        except Exception as exc:
            out(f"Could not delete {name}: {exc}")
            left = [n for n in present if n not in deleted]
            out(
                f"Deleted: {', '.join(deleted) or 'none'}. Still there: {', '.join(left)}."
            )
            return 1
        deleted.append(name)
        out(f"   deleted {name}")

    still = [n for n in store.list_collections() if n in LEGACY_COLLECTIONS]
    if still:
        out(f"Still in ChromaDB after the delete: {', '.join(still)}")
        return 1
    out(f"\nDone: {len(deleted)} old collection(s) removed.")
    return 0


def main(argv: Sequence[str] | None = None, *, store: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--yes",
        action="store_true",
        help="really delete. Without it the script only shows the plan",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="snapshot folder (default: the newest data/snapshots/pre-rag-rebuild-*)",
    )
    args = parser.parse_args(argv)

    if store is None:
        from dotenv import load_dotenv

        load_dotenv()
        from app.rag.vector_store import VectorStore

        store = VectorStore
    return run(store, snapshot=args.snapshot, delete=args.yes)


if __name__ == "__main__":
    sys.exit(main())
