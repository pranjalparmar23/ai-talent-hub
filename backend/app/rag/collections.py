"""ChromaDB collection registry.

Single source of truth for which vector collections the platform uses.
Adding a new RAG namespace? Add it here and run `python scripts/init_chroma.py`.

Two collections hold the knowledge the agents search. Both are filled by
app/ingest, and every chunk carries its source, licence and expiry as metadata.
Topic, role and level are filters, so a new domain needs no new collection.

The original five hand-filled collections (interview_experiences,
learning_resources, company_interviews, dsa_notes, behavioral_questions) were
deleted in Phase 7 task 15. A ChromaDB that still has them can drop them by hand.
`scripts/export_chroma.py` makes a snapshot first.

See docs/architecture/knowledge-layer.md.
"""

from dataclasses import dataclass

from app.ingest.schema import INTERVIEW_COLLECTION, LEARNING_COLLECTION


@dataclass(frozen=True)
class CollectionSpec:
    name: str
    description: str
    distance_metric: str = "cosine"  # cosine | l2 | ip


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

# Everything that gets created by init_chroma.py and VectorStore.init_all_collections().
COLLECTIONS: list[CollectionSpec] = list(KNOWLEDGE_COLLECTIONS)

COLLECTION_NAMES = [c.name for c in COLLECTIONS]
KNOWLEDGE_COLLECTION_NAMES = [c.name for c in KNOWLEDGE_COLLECTIONS]
