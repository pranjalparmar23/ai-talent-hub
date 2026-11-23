"""Upload a golden set to a LangSmith dataset. Optional: it does nothing without a key.

The JSONL files stay the source of truth. This copies them to LangSmith so a run can be
looked at there. Nothing is read back into the files.

How a case maps to a LangSmith example:

    case.input   -> example inputs
    case.expected -> example reference outputs
    case.split   -> example split (dev or test)
    case.id, area, tags, source, added, notes, pair_with -> example metadata

One dataset per area and version, for example `ai-talent-hub-retrieval-v1`. A published
version is never edited, so a `v2` file becomes a new dataset and `v1` stays as it was.

Examples are matched by case id (stored in the metadata as `case_id`), so running this
again changes nothing that is already the same. It creates the missing examples and
overwrites the ones that differ from the file. It never deletes: an example in LangSmith
that has no case in the file is only reported.

Run from backend/:

    python -m evals.langsmith_upload --dry-run          # show what would be sent, no network
    python -m evals.langsmith_upload                    # both areas, latest versions
    python -m evals.langsmith_upload --area retrieval --version 1 --verbose

It reads `LANGSMITH_API_KEY` from the environment or from backend/.env. Without it the
command prints a note and exits 0, so it is safe in any script. The cases are synthetic
and hold no secrets, but they do leave this machine, so leave the key unset if you do not
want that.
"""

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.cases import DATASETS, Case, DatasetError, dataset_path, load_dataset

DATASET_PREFIX = "ai-talent-hub"
KEY_NAME = "LANGSMITH_API_KEY"
_VERSION_IN_NAME = re.compile(r"\.v(\d+)\.jsonl$")

# The metadata keys this file writes. Any other key LangSmith adds is left alone.
_METADATA_FIELDS = ("area", "tags", "split", "source", "added", "notes", "pair_with")


class UploadError(Exception):
    """Something that stops an upload (a missing package, a LangSmith error)."""


@dataclass
class Result:
    """What one dataset sync did."""

    dataset: str
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # in LangSmith, not in the file
    duplicated: list[str] = field(default_factory=list)  # case id seen twice remotely


def dataset_name(area: str, version: int) -> str:
    return f"{DATASET_PREFIX}-{area}-v{version}"


def version_of(path: Path) -> int:
    """3 from 'links.v3.jsonl'."""
    match = _VERSION_IN_NAME.search(path.name)
    if not match:
        raise UploadError(f"cannot read a version number from {path.name}")
    return int(match.group(1))


def build_example(case: Case) -> dict[str, Any]:
    """One case as the parts of a LangSmith example. Plain JSON values only."""
    metadata: dict[str, Any] = {"case_id": case.id}
    dumped = case.model_dump(mode="json", exclude_none=True)
    for key in _METADATA_FIELDS:
        if key in dumped:
            metadata[key] = dumped[key]
    return {
        "case_id": case.id,
        "inputs": case.input.model_dump(mode="json", exclude_none=True),
        "outputs": case.expected.model_dump(mode="json", exclude_none=True),
        "metadata": metadata,
        "split": case.split,
    }


def differs(example: Mapping[str, Any], remote: Any) -> bool:
    """True when a LangSmith example is not the same as the case it came from.

    Only the keys this file writes are compared, so extra metadata that LangSmith adds
    does not look like a change.
    """
    if (remote.inputs or {}) != example["inputs"]:
        return True
    if (remote.outputs or {}) != example["outputs"]:
        return True
    remote_metadata = remote.metadata or {}
    return any(remote_metadata.get(k) != v for k, v in example["metadata"].items())


def _remote_by_case_id(
    client: Any, dataset_id: Any
) -> tuple[dict[str, Any], list[str]]:
    """The examples already in the dataset, keyed by case id, and ids that appear twice."""
    found: dict[str, Any] = {}
    twice: list[str] = []
    for remote in client.list_examples(dataset_id=dataset_id):
        case_id = (remote.metadata or {}).get("case_id")
        if not case_id:
            continue  # added by hand in LangSmith; not ours
        if case_id in found:
            twice.append(case_id)
        else:
            found[case_id] = remote
    return found, sorted(set(twice))


def sync(
    client: Any,
    name: str,
    examples: Sequence[Mapping[str, Any]],
    *,
    description: str,
    metadata: Mapping[str, Any],
) -> Result:
    """Make the LangSmith dataset `name` hold these examples. Never deletes anything."""
    result = Result(dataset=name)
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            name, description=description, metadata=dict(metadata)
        )

    remote, result.duplicated = _remote_by_case_id(client, dataset.id)

    missing: list[Mapping[str, Any]] = []
    for example in examples:
        case_id = example["case_id"]
        current = remote.get(case_id)
        if current is None:
            missing.append(example)
        elif differs(example, current):
            client.update_example(
                current.id,
                inputs=example["inputs"],
                outputs=example["outputs"],
                metadata=example["metadata"],
                split=example["split"],
            )
            result.updated.append(case_id)
        else:
            result.unchanged.append(case_id)

    if missing:
        client.create_examples(
            dataset_id=dataset.id,
            inputs=[e["inputs"] for e in missing],
            outputs=[e["outputs"] for e in missing],
            metadata=[e["metadata"] for e in missing],
            splits=[e["split"] for e in missing],
        )
        result.created = [e["case_id"] for e in missing]

    wanted = {e["case_id"] for e in examples}
    result.extra = sorted(set(remote) - wanted)
    return result


def describe(result: Result, *, verbose: bool) -> str:
    """The summary printed for one dataset."""
    lines = [
        f"{result.dataset}: {len(result.created)} created, {len(result.updated)} "
        f"updated, {len(result.unchanged)} unchanged"
    ]
    if verbose:
        for label, ids in (
            ("created", result.created),
            ("updated", result.updated),
        ):
            lines.extend(f"  {label} {i}" for i in ids)
    if result.updated:
        lines.append(
            "  Note: these examples differed from the file and were overwritten with "
            "the file's version. A published version should not change; if this one "
            "is frozen, find out why the file changed."
        )
    if result.extra:
        lines.append(
            f"  Warning: in LangSmith but not in the file (left alone): "
            f"{', '.join(result.extra)}"
        )
    if result.duplicated:
        lines.append(
            f"  Warning: more than one example for the same case id: "
            f"{', '.join(result.duplicated)}. Delete the copies in LangSmith."
        )
    return "\n".join(lines)


def _load_env() -> None:
    """Read backend/.env, so the key can live there like the other keys."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def make_client() -> Any:
    """The real LangSmith client. The package is imported only when it is needed."""
    try:
        from langsmith import Client
    except ImportError as exc:
        raise UploadError(
            "the langsmith package is not installed (pip install langsmith)"
        ) from exc
    return Client()


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals.langsmith_upload",
        description="Copy a golden set to a LangSmith dataset (optional; needs "
        f"{KEY_NAME}).",
    )
    parser.add_argument(
        "--area", choices=sorted(DATASETS), help="one area (default: all)"
    )
    parser.add_argument(
        "--version", type=int, help="a version number (default: latest)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be sent. No network, no key needed.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="list each case created or updated"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[], Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Exit 0: done, or nothing to do without a key. 1: a problem. 2: bad arguments."""
    args = _make_parser().parse_args(argv)
    if environ is None:
        _load_env()
        environ = os.environ

    # Check and build every dataset before sending anything.
    prepared: list[tuple[str, Path, list[dict[str, Any]]]] = []
    problems = False
    for area in [args.area] if args.area else sorted(DATASETS):
        try:
            path = dataset_path(area, args.version)
            cases = load_dataset(area, args.version)
            prepared.append(
                (
                    dataset_name(area, version_of(path)),
                    path,
                    [build_example(c) for c in cases],
                )
            )
        except (DatasetError, UploadError) as exc:
            problems = True
            print(f"{area}: PROBLEMS", file=sys.stderr)
            for line in getattr(exc, "problems", None) or [str(exc)]:
                print(f"  {line}", file=sys.stderr)
    if problems:
        return 1

    if args.dry_run:
        for name, path, examples in prepared:
            test = sum(1 for e in examples if e["split"] == "test")
            print(
                f"{name}: would send {len(examples)} examples from {path.name} "
                f"(dev {len(examples) - test}, test {test})"
            )
        return 0

    if not environ.get(KEY_NAME):
        print(f"{KEY_NAME} is not set, so nothing was uploaded.")
        return 0

    try:
        client = (client_factory or make_client)()
        for name, path, examples in prepared:
            area = examples[0]["metadata"]["area"] if examples else "unknown"
            result = sync(
                client,
                name,
                examples,
                description=(
                    f"AI Talent Hub golden set ({area}). Copy of "
                    f"backend/evals/datasets/.../{path.name}. The file in the "
                    "repository is the source of truth; do not edit here."
                ),
                metadata={"area": area, "file": path.name},
            )
            print(describe(result, verbose=args.verbose))
    except UploadError as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # LangSmith or the network; report it, do not dump a trace
        print(
            f"Upload failed: {type(exc).__name__}: {exc}\n"
            "Some examples may already have been sent. Running it again is safe.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
