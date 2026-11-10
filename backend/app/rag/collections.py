"""ChromaDB collection registry.

Single source of truth for which vector collections the platform uses.
Adding a new RAG namespace? Add it here and run `python scripts/init_chroma.py`.

Two generations live side by side during Phase 7:

- KNOWLEDGE_COLLECTIONS: the new, licence-aware collections filled by
  app/ingest. Every chunk carries source, licence and expiry metadata.
- LEGACY_COLLECTIONS: the original five, filled by hand with no recorded
  licence. They stay until Phase 7 task 15, which deletes them only after
  the new evaluation set passes.

See docs/architecture/knowledge-layer.md.
"""

from dataclasses import dataclass

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    distance_metric: str = "cosine"  # cosine | l2 | ip


# ── New knowledge collections (Phase 7) ──────────────────────
KNOWLEDGE_COLLECTIONS: list[CollectionSpec] = [
    CollectionSpec(
        name=INTERVIEW_COLLECTION,
        description=(
            "Interview questions, experiences, company patterns and concept "
            "notes, with source, licence and expiry on every chunk"
        ),
    ),
    CollectionSpec(
        name=LEARNING_COLLECTION,
        description=(
            "Courses, articles, videos, roadmaps and docs for skill "
            "development, with source, licence and expiry on every chunk"
        ),
    ),
]

# ── Original five collections (removed in Phase 7 task 15) ───
LEGACY_COLLECTIONS: list[CollectionSpec] = [
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

# Everything that gets created by init_chroma.py and VectorStore.init_all_collections().
COLLECTIONS: list[CollectionSpec] = KNOWLEDGE_COLLECTIONS + LEGACY_COLLECTIONS

COLLECTION_NAMES = [c.name for c in COLLECTIONS]
KNOWLEDGE_COLLECTION_NAMES = [c.name for c in KNOWLEDGE_COLLECTIONS]
LEGACY_COLLECTION_NAMES = [c.name for c in LEGACY_COLLECTIONS]
