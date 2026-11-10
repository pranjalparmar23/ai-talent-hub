"""Export every ChromaDB collection to JSONL before the Phase 7 knowledge rebuild.

Run this once, with the ChromaDB container up, BEFORE changing or deleting any
collection:

    python scripts/export_chroma.py

Each collection becomes one file (one JSON object per line: id, document,
metadata) under data/snapshots/pre-rag-rebuild-<UTC timestamp>/, plus a
manifest.json with the counts. Embeddings are not exported: they can be
recomputed from the documents. The snapshots folder is git-ignored; do not
commit it.

The script only reads from ChromaDB. It exits with 1 if a collection could not
be read in full, so a bad snapshot is never mistaken for a good one.
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
from datetime import datetime, timezone
from pathlib import Path

# Allow running this script directly (without `python -m`)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

from app.rag.vector_store import VectorStore  # noqa: E402

PAGE_SIZE = 500
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = REPO_ROOT / "data" / "snapshots"


def export_collection(collection, out_file: Path, page_size: int = PAGE_SIZE) -> int:
    """Write one collection to a JSONL file. Returns the number of rows written."""
    written = 0
    offset = 0
    with out_file.open("w", encoding="utf-8") as fh:
        while True:
            page = collection.get(
                include=["documents", "metadatas"], limit=page_size, offset=offset
            )
            ids = page.get("ids") or []
            if not ids:
                break
            docs = page.get("documents") or [None] * len(ids)
            metas = page.get("metadatas") or [None] * len(ids)
            for item_id, doc, meta in zip(ids, docs, metas):
                row = {"id": item_id, "document": doc, "metadata": meta or {}}
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            offset += len(ids)
    return written


def _name(item) -> str:
    """ChromaDB 0.5 returns Collection objects, newer versions return names."""
    return item if isinstance(item, str) else item.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output folder (default: data/snapshots/pre-rag-rebuild-<timestamp>)",
    )
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    out_dir = args.out or SNAPSHOT_ROOT / f"pre-rag-rebuild-{now:%Y%m%dT%H%M%SZ}"
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"❌ {out_dir} already exists and is not empty. Pick another --out.")
        return 1

    host = os.getenv("CHROMA_HOST", "localhost")
    port = os.getenv("CHROMA_PORT", "8001")
    print(f"🔌 Connecting to ChromaDB at {host}:{port}…")
    try:
        VectorStore.heartbeat()
        client = VectorStore.get_client()
        names = sorted(_name(c) for c in client.list_collections())
    except Exception as e:
        print(f"❌ Cannot reach ChromaDB: {e}")
        print("   Is the container running?  docker compose ps chromadb")
        return 1

    if not names:
        print("ℹ️  ChromaDB has no collections. Nothing to export.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"📦 Exporting {len(names)} collection(s) to {out_dir}\n")

    manifest = {
        "created_at": now.isoformat(),
        "chroma": f"{host}:{port}",
        "collections": [],
    }
    ok = True
    for name in names:
        collection = client.get_collection(name=name)
        expected = collection.count()
        exported = export_collection(collection, out_dir / f"{name}.jsonl")
        matches = exported == expected
        ok = ok and matches
        manifest["collections"].append(
            {
                "name": name,
                "count_in_chroma": expected,
                "rows_exported": exported,
                "metadata": collection.metadata or {},
            }
        )
        mark = "✅" if matches else "❌"
        print(f"   {mark} {name:30s} {exported:6d} exported / {expected} in ChromaDB")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    if not ok:
        print(
            "\n❌ At least one collection was not exported in full. Do NOT rebuild yet."
        )
        return 1
    total = sum(c["rows_exported"] for c in manifest["collections"])
    print(f"\n✨ Done: {total} rows. Snapshot saved in {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
