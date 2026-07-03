"""Seed all ChromaDB collections from JSON files in /data.

Run this after `init_chroma.py`:
    python scripts/seed_data.py

Each /data/<collection_name>/ folder contains JSON files with this shape:
    {
        "documents": [
            {
                "content": "Full text of the document...",
                "metadata": {"key": "value", ...}
            },
            ...
        ]
    }

Re-running is safe — the script clears each collection before re-seeding,
so you can edit JSON files and re-run without duplicates.
"""

# ── Silence ChromaDB PostHog telemetry (must be BEFORE any other imports) ──
try:
    import posthog
    posthog.capture = lambda *args, **kwargs: None
    posthog.Posthog.capture = lambda *args, **kwargs: None
except ImportError:
    pass
    
import sys
import os
import json
from pathlib import Path

# Allow running this script directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

from app.rag.retriever import RAGRetriever  # noqa: E402
from app.rag.vector_store import VectorStore  # noqa: E402
from app.rag.collections import COLLECTIONS  # noqa: E402


# /data lives at the repo root, not inside /backend
DATA_DIR = Path(__file__).parent.parent.parent / "data"


def load_collection_data(collection_name: str) -> tuple[list[str], list[dict]]:
    """Load all JSON files in /data/<collection_name>/ and flatten into
    (documents, metadatas) parallel lists.
    """
    folder = DATA_DIR / collection_name
    if not folder.exists():
        print(f"   ⚠️  No /data/{collection_name}/ folder — skipping")
        return [], []

    json_files = sorted(folder.glob("*.json"))
    if not json_files:
        print(f"   ⚠️  No .json files in /data/{collection_name}/")
        return [], []

    all_docs: list[str] = []
    all_metas: list[dict] = []

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"   ❌ Invalid JSON in {json_file.name}: {e}")
            continue

        for entry in data.get("documents", []):
            content = entry.get("content", "").strip()
            if not content:
                continue
            meta = entry.get("metadata", {}) or {}
            # Track which source file this came from — useful for debugging
            meta["source_file"] = json_file.name
            all_docs.append(content)
            all_metas.append(meta)

    return all_docs, all_metas


def clear_collection(collection_name: str) -> int:
    """Remove all documents from a collection. Returns count of removed docs."""
    try:
        col = VectorStore.get_collection(collection_name)
        all_ids = col.get()["ids"]
        if all_ids:
            col.delete(ids=all_ids)
        return len(all_ids)
    except Exception as e:
        print(f"   ⚠️  Could not clear {collection_name}: {e}")
        return 0


def main() -> int:
    print("🌱 Seeding ChromaDB collections from /data/")
    print(f"   Data root: {DATA_DIR}")

    if not DATA_DIR.exists():
        print(f"\n❌ Data directory not found: {DATA_DIR}")
        print("   Expected layout:")
        print("     ai-talent-hub/data/interview_experiences/*.json")
        print("     ai-talent-hub/data/learning_resources/*.json")
        print("     ...")
        return 1

    # Verify ChromaDB is reachable before doing anything
    try:
        VectorStore.heartbeat()
    except Exception as e:
        print(f"❌ Cannot reach ChromaDB: {e}")
        print("   Run:  docker compose up -d chromadb")
        return 1

    # Verify collections exist (init_chroma.py should have created them)
    existing = VectorStore.list_collections()
    missing = [c.name for c in COLLECTIONS if c.name not in existing]
    if missing:
        print(f"❌ Missing collections: {missing}")
        print("   Run:  python scripts/init_chroma.py")
        return 1

    retriever = RAGRetriever()

    total_docs = 0
    total_chunks = 0

    for spec in COLLECTIONS:
        print(f"\n📦 {spec.name}")

        # Always clear first — safer than trying to dedupe
        cleared = clear_collection(spec.name)
        if cleared > 0:
            print(f"   🧹 Cleared {cleared} existing docs")

        # Load fresh data from /data/<name>/
        docs, metas = load_collection_data(spec.name)
        if not docs:
            print("\n⏭️  No data to seed")
            continue

        chunks = retriever.add_documents(spec.name, docs, metas)
        total_docs += len(docs)
        total_chunks += chunks

        print(f"   ✅ Seeded {len(docs)} docs → {chunks} chunks")

    print(f"\n✨ Done. {total_docs} documents → {total_chunks} chunks across all collections.")

    print("\n📊 Final collection sizes:")
    for spec in COLLECTIONS:
        try:
            count = VectorStore.count(spec.name)
            print(f"   {spec.name:30s}  {count:>4} chunks")
        except Exception as e:
            print(f"   {spec.name:30s}  error: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())