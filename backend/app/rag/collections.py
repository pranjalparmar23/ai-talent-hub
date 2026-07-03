"""ChromaDB collection registry.

Single source of truth for which vector collections the platform uses.
Adding a new RAG namespace? Add it here and run `python scripts/init_chroma.py`.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    distance_metric: str = "cosine"  # cosine | l2 | ip


# ── The 5 collections ────────────────────────────────────────
COLLECTIONS: list[CollectionSpec] = [
    CollectionSpec(
        name="interview_experiences",
        description="Real interview experiences shared by candidates — questions asked, format, difficulty",
    ),
    CollectionSpec(
        name="learning_resources",
        description="Curated tutorials, courses, roadmaps, docs for skill development",
    ),
    CollectionSpec(
        name="company_interviews",
        description="Company-specific interview patterns — Amazon LP, Google system design, Meta behavioural",
    ),
    CollectionSpec(
        name="dsa_notes",
        description="Data structures & algorithms notes, patterns, problem explanations",
    ),
    CollectionSpec(
        name="behavioral_questions",
        description="STAR-method behavioural questions and example answers across role levels",
    ),
]

COLLECTION_NAMES = [c.name for c in COLLECTIONS]