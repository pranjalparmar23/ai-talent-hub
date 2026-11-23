"""Shared builders for the evals unit tests (like tests/unit/agents/_fakes.py).

Cases are built as plain dicts so a test can write them to a file, and validated into
case objects when a test needs the object.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals import cases as cases_module
from evals.cases import RetrievalCase, RoadmapCase

FOLDERS = {"retrieval": ("knowledge", "retrieval"), "roadmap": ("roadmap", "links")}


def retrieval_dict(
    case_id: str = "ret-001",
    *,
    query: str = "dynamic programming",
    collection: str = "interview_knowledge",
    expected: dict[str, Any] | None = None,
    tags: tuple[str, ...] = ("technical", "clear"),
    split: str = "dev",
    source: str = "hand-written",
    pair_with: str | None = None,
    **input_extra: Any,
) -> dict[str, Any]:
    case = {
        "id": case_id,
        "area": "retrieval",
        "tags": list(tags),
        "split": split,
        "input": {"query": query, "collection": collection, **input_extra},
        "expected": expected or {"relevant_keywords": ["dynamic programming"]},
        "notes": "A core topic that the guide covers.",
        "source": source,
        "added": "2026-11-23",
    }
    if pair_with:
        case["pair_with"] = pair_with
    return case


def roadmap_dict(
    case_id: str = "rdm-001",
    *,
    skills: tuple[str, ...] = ("Docker",),
    role: str = "Backend Engineer",
    expected: dict[str, Any] | None = None,
    tags: tuple[str, ...] = ("technical", "clear"),
    split: str = "dev",
    source: str = "hand-written",
    pair_with: str | None = None,
) -> dict[str, Any]:
    case = {
        "id": case_id,
        "area": "roadmap",
        "tags": list(tags),
        "split": split,
        "input": {"skill_gaps": list(skills), "target_role": role},
        "expected": expected or {"expect_grounded": True, "min_links": 1},
        "notes": "A normal plan for one technical skill.",
        "source": source,
        "added": "2026-11-23",
    }
    if pair_with:
        case["pair_with"] = pair_with
    return case


def retrieval_case(case_id: str = "ret-001", **kwargs: Any) -> RetrievalCase:
    return RetrievalCase.model_validate(retrieval_dict(case_id, **kwargs))


def roadmap_case(case_id: str = "rdm-001", **kwargs: Any) -> RoadmapCase:
    return RoadmapCase.model_validate(roadmap_dict(case_id, **kwargs))


def chunk(
    title: str = "A page",
    text: str = "",
    *,
    source_id: str = "src",
    domain: str = "backend",
    url: str = "",
    similarity: float = 0.5,
) -> SimpleNamespace:
    """Looks like a KnowledgeChunk: `.text`, `.metadata` and `.similarity`."""
    return SimpleNamespace(
        text=text,
        similarity=similarity,
        metadata={
            "title": title,
            "source_id": source_id,
            "domain": domain,
            "url": url,
        },
    )


def make_plan(
    urls: tuple[str, ...] | list[str] = ("https://kubernetes.io/docs/a",),
    *,
    sources: list[str] | None = None,
    notice: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A learning plan with one week linking `urls`. `sources` defaults to the same urls;
    pass [] for none."""
    listed = list(urls) if sources is None else sources
    plan: dict[str, Any] = {
        "weeks": [{"week": 1, "resources": [{"url": u} for u in urls]}],
        "total_weeks": 1,
        "sources": [{"url": u} for u in listed],
    }
    if notice:
        plan["notice"] = notice
    plan.update(extra)
    return plan


def write_dataset(
    tmp_path: Path,
    monkeypatch: Any,
    area: str,
    rows: list[dict[str, Any]],
    version: int = 1,
) -> Path:
    """Write rows as `<area>.v<N>.jsonl` under tmp_path and point the loader at it."""
    monkeypatch.setattr(cases_module, "DATASETS_DIR", tmp_path)
    folder, name = FOLDERS[area]
    directory = tmp_path / folder
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.v{version}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path
