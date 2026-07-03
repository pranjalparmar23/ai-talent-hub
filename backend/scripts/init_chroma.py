"""Initialize all ChromaDB collections.

Run this once after starting the ChromaDB container:

    python scripts/init_chroma.py

Safe to re-run — uses get_or_create semantics.
"""

"""Evaluate retrieval quality against the hand-crafted eval set.
...
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

# Allow running this script directly (without `python -m`)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

from app.rag.vector_store import VectorStore  # noqa: E402
from app.rag.collections import COLLECTIONS  # noqa: E402


def main() -> int:
    print("🔌 Connecting to ChromaDB…")
    try:
        heartbeat = VectorStore.heartbeat()
        print(f"   Heartbeat: {heartbeat}")
    except Exception as e:
        print(f"❌ Cannot reach ChromaDB: {e}")
        print(f"   Check CHROMA_HOST/CHROMA_PORT in .env (currently "
              f"{os.getenv('CHROMA_HOST', 'localhost')}:{os.getenv('CHROMA_PORT', '8001')})")
        print("\nIs the container running?  docker compose ps chromadb")
        return 1

    print(f"\n📦 Initializing {len(COLLECTIONS)} collections…")
    for spec in COLLECTIONS:
        VectorStore.get_or_create_collection(spec)
        print(f"   ✅ {spec.name:30s}  ({spec.description})")

    print("\n📋 Collections currently in ChromaDB:")
    for name in VectorStore.list_collections():
        marker = "✓" if name in [c.name for c in COLLECTIONS] else "?"
        print(f"   {marker} {name}")

    print("\n✨ Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())