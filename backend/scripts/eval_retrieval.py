"""Evaluate retrieval quality against the hand-crafted eval set.

Computes precision@5 and MRR (Mean Reciprocal Rank) across 20 queries.

Run:
    python scripts/eval_retrieval.py

Output: per-query verdict + aggregate scores. Saves a CSV report under
data/eval/results_<timestamp>.csv for tracking quality over time.
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
import csv
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dotenv import load_dotenv

load_dotenv()

from app.rag.retriever import RAGRetriever, RetrievedDocument  # noqa: E402


# Paths
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "eval"
EVAL_FILE = DATA_DIR / "retrieval_eval.json"
RESULTS_DIR = DATA_DIR / "results"

TOP_K = 5


# ── Relevance judgement ──────────────────────────────────────

def is_relevant(doc: RetrievedDocument, expected_keywords: list[str],
                expected_topics: list[str]) -> bool:
    """A retrieved doc is relevant if EITHER:
       - any expected_keyword appears in the document text (case-insensitive)
       - the chunk's metadata topic/category matches an expected topic
    """
    text_lower = doc.document.lower()
    keyword_hit = any(kw.lower() in text_lower for kw in expected_keywords)
    if keyword_hit:
        return True

    meta = doc.metadata or {}
    # Check both 'topic' and 'category' fields since seed data uses both
    meta_values = {str(meta.get("topic", "")).lower(), str(meta.get("category", "")).lower()}
    expected_lower = {t.lower() for t in expected_topics}
    return bool(meta_values & expected_lower)


# ── Per-query evaluation ─────────────────────────────────────

def evaluate_query(retriever: RAGRetriever, query_spec: dict) -> dict:
    """Run retrieve() for one query, score against expected matches.

    Returns dict with: precision@5, reciprocal rank, hit positions, results.
    """
    results = retriever.retrieve(
        collection_name=query_spec["collection"],
        query=query_spec["query"],
        top_k=TOP_K,
    )

    keywords = query_spec["expected_keywords"]
    topics = query_spec["expected_metadata_topics"]

    # Mark each result as relevant or not
    relevant_flags = [is_relevant(r, keywords, topics) for r in results]
    num_relevant = sum(relevant_flags)
    precision_at_5 = num_relevant / TOP_K

    # Reciprocal rank = 1 / position of first relevant doc (1-indexed)
    rr = 0.0
    for i, is_rel in enumerate(relevant_flags, start=1):
        if is_rel:
            rr = 1.0 / i
            break

    return {
        "id": query_spec["id"],
        "query": query_spec["query"],
        "collection": query_spec["collection"],
        "precision_at_5": precision_at_5,
        "reciprocal_rank": rr,
        "num_relevant": num_relevant,
        "num_results": len(results),
        "relevant_flags": relevant_flags,
        "top_result_doc": results[0].document[:80] + "..." if results else "",
        "top_result_sim": results[0].similarity if results else 0.0,
    }


# ── Output formatting ────────────────────────────────────────

def format_verdict(p_at_5: float) -> str:
    if p_at_5 >= 0.6:
        return "✅"
    elif p_at_5 >= 0.4:
        return "🟡"
    else:
        return "❌"


def save_csv(per_query: list[dict], aggregates: dict) -> Optional[Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"results_{ts}.csv"

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id", "collection", "query", "precision_at_5", "reciprocal_rank",
            "num_relevant", "top_result_sim", "top_result_doc",
        ])
        for r in per_query:
            writer.writerow([
                r["id"], r["collection"], r["query"],
                f'{r["precision_at_5"]:.3f}', f'{r["reciprocal_rank"]:.3f}',
                r["num_relevant"], f'{r["top_result_sim"]:.3f}',
                r["top_result_doc"],
            ])
        writer.writerow([])
        writer.writerow(["AGGREGATE", "", "",
                         f'{aggregates["mean_p_at_5"]:.3f}',
                         f'{aggregates["mrr"]:.3f}',
                         aggregates["total_relevant"], "", ""])

    return path


def main() -> int:
    if not EVAL_FILE.exists():
        print(f"❌ Eval file not found: {EVAL_FILE}")
        return 1

    with open(EVAL_FILE) as f:
        eval_data = json.load(f)
    queries = eval_data["queries"]
    print(f"📋 Loaded {len(queries)} eval queries\n")

    retriever = RAGRetriever()

    # ── Run all queries ──
    per_query: list[dict] = []
    by_collection: dict[str, list[dict]] = {}

    print("─" * 110)
    print(f"{'ID':<5} {'Collection':<24} {'Query':<50} {'p@5':>6} {'MRR':>6}")
    print("─" * 110)

    for q in queries:
        result = evaluate_query(retriever, q)
        per_query.append(result)
        by_collection.setdefault(q["collection"], []).append(result)

        verdict = format_verdict(result["precision_at_5"])
        truncated_q = (result["query"][:47] + "...") if len(result["query"]) > 50 else result["query"]
        print(
            f"{result['id']:<5} {result['collection']:<24} "
            f"{truncated_q:<50} "
            f"{result['precision_at_5']:>5.2f} "
            f"{result['reciprocal_rank']:>5.2f} "
            f"{verdict}"
        )

    # ── Aggregates ──
    print("─" * 110)
    n = len(per_query)
    mean_p5 = sum(r["precision_at_5"] for r in per_query) / n
    mrr = sum(r["reciprocal_rank"] for r in per_query) / n
    total_relevant = sum(r["num_relevant"] for r in per_query)

    print("\n📊 Aggregate scores")
    print(f"   Mean precision@5     : {mean_p5:.3f}  ({mean_p5*100:.1f}%)")
    print(f"   Mean reciprocal rank : {mrr:.3f}")
    print(f"   Total relevant docs  : {total_relevant} / {n * TOP_K}")

    # ── Per-collection breakdown ──
    print("\n📂 By collection")
    for col, results in sorted(by_collection.items()):
        col_p5 = sum(r["precision_at_5"] for r in results) / len(results)
        col_mrr = sum(r["reciprocal_rank"] for r in results) / len(results)
        verdict = format_verdict(col_p5)
        print(f"   {col:<24}  p@5={col_p5:.3f}  MRR={col_mrr:.3f}  ({len(results)} queries) {verdict}")

    # ── Pass/fail threshold ──
    THRESHOLD = 0.50  # baseline for first eval run
    print(f"\n🎯 Threshold (mean p@5 ≥ {THRESHOLD:.2f}): ", end="")
    passed = mean_p5 >= THRESHOLD
    print("✅ PASS" if passed else "❌ FAIL")

    # ── Persist CSV ──
    csv_path = save_csv(per_query, {
        "mean_p_at_5": mean_p5, "mrr": mrr, "total_relevant": total_relevant,
    })
    print(f"\n💾 Results saved to: {csv_path}")

    # ── Lowest-scoring queries (for tuning) ──
    print("\n🔬 Lowest-scoring queries (target for improvement):")
    bottom = sorted(per_query, key=lambda r: r["precision_at_5"])[:3]
    for r in bottom:
        if r["precision_at_5"] < 0.6:
            print(f"   [{r['id']}] {r['query']}")
            print(f"        p@5={r['precision_at_5']:.2f} · top result: {r['top_result_doc']}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())