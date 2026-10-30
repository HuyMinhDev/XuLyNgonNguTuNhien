"""Small retrieval benchmark comparing BM25 seeds with graph-expanded retrieval.

A retrieved unit matches a gold provision when it is that provision or one of its clauses,
so "Điều 9" is found by retrieving "khoản 2 Điều 9". Gold IDs are drafted from the official
text and stay ``needs_review`` until a person verifies them; results computed on unverified
items are reported as provisional.
"""

import csv
import json
import time
from collections import defaultdict
from pathlib import Path

from ..models import VALIDITY

STATUSES = ("needs_review", "verified")
FIELDS = ("id", "question", "expected_context_ids", "expected_answer", "verification_status")


def load_benchmark(path):
    items = []
    with Path(path).open(encoding="utf-8") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            item = json.loads(line)
            missing = [k for k in FIELDS if k not in item]
            if missing:
                raise ValueError(f"Line {number}: missing fields {missing}")
            if item["verification_status"] not in STATUSES:
                raise ValueError(f"Line {number}: unknown status {item['verification_status']}")
            if not item["expected_context_ids"]:
                raise ValueError(f"Line {number}: no expected context IDs")
            items.append(item)
    if len({i["id"] for i in items}) != len(items):
        raise ValueError("Duplicate benchmark IDs")
    return items


def matches(retrieved, gold):
    return retrieved == gold or retrieved.startswith(gold + "::")


def recall_at(ranked, gold, k):
    top = ranked[:k]
    return sum(any(matches(r, g) for r in top) for g in gold) / len(gold)


def first_hit(ranked, gold, k):
    for rank, key in enumerate(ranked[:k], 1):
        if any(matches(key, g) for g in gold):
            return rank
    return None


def amendment_coverage(graph, ranked, k):
    """Share of amended provisions in the top k whose amending provision is also in the top k.

    It needs no gold labels, but graph retrieval follows amendment edges by design, so it
    measures what the graph adds rather than which method answers better.
    """
    top = ranked[:k]
    present = set(top)
    amended = covered = 0
    for key in top:
        # Amendments of an article also govern its clauses.
        provisions = {key, key.split("::clause:")[0]}
        updates = {e.target for p in provisions for e in graph.edges(p) if e.relation in VALIDITY}
        if updates:
            amended += 1
            covered += bool(updates & present)
    return covered / amended if amended else None


def evaluate(runs, items, seeds=10, ks=(5, 10)):
    """Per-question rows for every run, a (name, WaterQA, method) triple."""
    depth = max(ks)
    rows = []
    for item in items:
        gold = item["expected_context_ids"]
        for name, qa, method in runs:
            started = time.perf_counter()
            ranked = qa.ranked_ids(item["question"], method, seeds)
            elapsed = (time.perf_counter() - started) * 1000
            hit = first_hit(ranked, gold, depth)
            rows.append(
                {
                    "id": item["id"],
                    "category": item.get("category", ""),
                    "verification_status": item["verification_status"],
                    "method": name,
                    **{f"recall@{k}": recall_at(ranked, gold, k) for k in ks},
                    f"mrr@{depth}": 1 / hit if hit else 0.0,
                    "first_hit_rank": hit,
                    f"amendment_coverage@{depth}": amendment_coverage(qa.graph, ranked, depth),
                    "latency_ms": elapsed,
                    "expected": gold,
                    "retrieved": ranked[:depth],
                }
            )
    return rows


def summarize(rows, ks=(5, 10)):
    depth = max(ks)
    names = [f"recall@{k}" for k in ks] + [f"mrr@{depth}", "latency_ms"]
    optional = f"amendment_coverage@{depth}"

    def mean(group):
        defined = [r[optional] for r in group if r[optional] is not None]
        return {
            "n": len(group),
            **{name: sum(r[name] for r in group) / len(group) for name in names},
            optional: sum(defined) / len(defined) if defined else None,
            optional + "_n": len(defined),
        }

    summary = {}
    for method in dict.fromkeys(r["method"] for r in rows):
        mine = [r for r in rows if r["method"] == method]
        categories = defaultdict(list)
        for row in mine:
            categories[row["category"]].append(row)
        verified = [r for r in mine if r["verification_status"] == "verified"]
        summary[method] = {
            "all": mean(mine),
            "verified": mean(verified) if verified else None,
            "by_category": {c: mean(g) for c, g in sorted(categories.items())},
        }
    return summary


# Display names; the last two rows are ablations explaining the design of the main methods.
RUNS = {
    "bm25": "BM25",
    "graph": "Graph (ViHERMES + xếp hạng lại)",
    "graph_vihermes_order": "Graph, thứ tự gốc ViHERMES",
    "bm25_syllable": "BM25 chỉ âm tiết",
}


def write_chart(summary, path, ks=(5, 10)):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [f"recall@{k}" for k in ks] + [f"mrr@{max(ks)}"]
    colors = {
        "bm25": "#5b7aa6",
        "graph": "#2f8a73",
        "graph_vihermes_order": "#9fc7bb",
        "bm25_syllable": "#b8c4d6",
    }
    runs = [r for r in RUNS if r in summary]
    fig, ax = plt.subplots(figsize=(8, 3.8), dpi=150)
    width = 0.8 / len(runs)
    for i, run in enumerate(runs):
        values = [summary[run]["all"][n] for n in names]
        positions = [x + (i - (len(runs) - 1) / 2) * width for x in range(len(names))]
        bars = ax.bar(positions, values, width, label=RUNS[run], color=colors[run])
        ax.bar_label(bars, fmt="%.2f", fontsize=7, padding=2)
    ax.set_xticks(range(len(names)), [*(f"Recall@{k}" for k in ks), f"MRR@{max(ks)}"])
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("Điểm (0–1)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left", fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def run_benchmark(db_path, benchmark_path, output_dir, seeds=10, ks=(5, 10)):
    """Evaluate both methods plus the two ablations and write CSV, JSON, and a chart."""
    from .qa import WaterQA

    items = load_benchmark(benchmark_path)
    with WaterQA(db_path) as qa, WaterQA(db_path, bigrams=False) as syllable:
        runs = [
            ("bm25", qa, "bm25"),
            ("graph", qa, "graph"),
            ("graph_vihermes_order", qa, "graph_vihermes_order"),
            ("bm25_syllable", syllable, "bm25"),
        ]
        rows = evaluate(runs, items, seeds, ks)
        budget = {
            "max_depth": qa.propagator.max_depth,
            "max_nodes": qa.propagator.max_nodes,
            "max_chars": qa.propagator.max_chars,
        }
    summary = summarize(rows, ks)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    verified = sum(i["verification_status"] == "verified" for i in items)
    report = {
        "benchmark": str(benchmark_path),
        "questions": len(items),
        "verified_questions": verified,
        "provisional": verified < len(items),
        "note": (
            "Kết quả tạm thời: benchmark còn câu hỏi chưa được người dùng xác nhận (needs_review)."
            if verified < len(items)
            else "Tất cả câu hỏi đã được xác nhận."
        ),
        "methods": {k: v for k, v in RUNS.items() if k in summary},
        "config": {
            "seeds_k": seeds,
            "cutoffs": list(ks),
            "match_rule": "retrieved == gold hoặc retrieved là khoản con của gold",
            "graph_budget": budget,
        },
        "summary": summary,
        "questions_detail": rows,
    }
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    depth = max(ks)
    columns = [
        "id",
        "category",
        "verification_status",
        "method",
        *[f"recall@{k}" for k in ks],
        f"mrr@{depth}",
        "first_hit_rank",
        f"amendment_coverage@{depth}",
        "latency_ms",
        "expected",
        "retrieved",
    ]
    with (output_dir / "evaluation.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **row,
                    "latency_ms": round(row["latency_ms"], 3),
                    "expected": " | ".join(row["expected"]),
                    "retrieved": " | ".join(row["retrieved"]),
                }
            )
    write_chart(summary, output_dir / "evaluation_chart.png", ks)
    return report
