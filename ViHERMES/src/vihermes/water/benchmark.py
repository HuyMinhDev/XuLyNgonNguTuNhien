"""Retrieval benchmark: lexical, neural, graph and time-aware retrieval on water-law questions.

Two suites share one question file:

- topical (categories direct, multi_provision, cross_document, colloquial): no point in time;
  methods are compared on Recall@5, Recall@10 and MRR@10;
- temporal (category temporal): each question names a point in time ("năm 2025",
  "hiện nay") and its gold provisions are those in force on that day (``as_of``). Every base
  method is run without time handling, with the graph-free document date filter, and with
  knowledge-graph validity resolution.

A retrieved unit matches a gold provision when it is that provision or one of its clauses,
so "Điều 9" is found by retrieving "khoản 2 Điều 9". Stale@5 is the share of the top five
units that are not in force on the question's day (the reference date for questions without
one). Differences between methods are tested with a two-sided paired permutation (sign-flip)
test and Holm correction; means carry percentile bootstrap 95% intervals.

Gold IDs are drafted from the official text and stay ``needs_review`` until a person
verifies them; results computed on unverified items are reported as provisional.
"""

import csv
import gc
import json
import random
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..models import VALIDITY
from .temporal import REFERENCE_DATE

STATUSES = ("needs_review", "verified")
FIELDS = ("id", "question", "expected_context_ids", "expected_answer", "verification_status")
TOPICAL = ("direct", "multi_provision", "cross_document", "colloquial")
RESAMPLES = 10_000


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
            if item.get("category") == "temporal":
                date.fromisoformat(item.get("as_of", ""))  # raises on a missing or bad date
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


def stale_at(timeline, ranked, day, k=5):
    top = ranked[:k]
    return sum(timeline.status(key, day).stale for key in top) / len(top) if top else 0.0


# Display names. Ablations explain design choices of the main methods.
RUNS = {
    "bm25": "BM25 (âm tiết + bigram)",
    "dense": "Dense (bi-encoder tiếng Việt)",
    "hybrid": "Hybrid (BM25 + Dense, RRF)",
    "hybrid_rerank": "Hybrid + Reranker",
    "graph": "Graph (BM25 + ViHERMES)",
    "graph_hybrid": "Graph (Hybrid + ViHERMES)",
    "graph_hybrid_rerank": "Graph + Hybrid + Reranker",
    "bm25_syllable": "BM25 chỉ âm tiết",
    "graph_vihermes_order": "Graph, thứ tự gốc ViHERMES",
    "dense_e5": "Dense đa ngôn ngữ (multilingual-e5-small)",
    "dense_noseg": "Dense tiếng Việt, không tách từ",
}
MAIN = ("bm25", "dense", "hybrid", "hybrid_rerank", "graph", "graph_hybrid", "graph_hybrid_rerank")
TEMPORAL_BASES = ("bm25", "hybrid_rerank", "graph_hybrid_rerank")
TEMPORAL_LABELS = {
    "none": "không xử lý thời gian",
    "filter": "lọc theo ngày hiệu lực văn bản",
    "kg": "hiệu lực theo Knowledge Graph",
}
TOPICAL_PAIRS = [
    ("bm25", "dense"),
    ("bm25", "hybrid"),
    ("dense", "hybrid"),
    ("hybrid", "hybrid_rerank"),
    ("hybrid_rerank", "graph_hybrid_rerank"),
    ("bm25", "graph"),
    ("hybrid", "graph_hybrid"),
    ("bm25_syllable", "bm25"),
    ("dense_e5", "dense"),
    ("dense_noseg", "dense"),
]


@dataclass
class Run:
    name: str
    method: str
    temporal: str = "none"
    time_source: str = "gold"  # gold: the item's as_of; question: extracted from the text


def temporal_name(method, mode, source="gold"):
    return f"{method}+{mode}" + ("+timex" if source == "question" else "")


def evaluate(qa, runs, items, seeds=10, ks=(5, 10)):
    """Per-question rows for every run of one QA service."""
    depth = max(ks)
    rows = []
    for run in runs if items else []:
        # Warm-up: model loading is not query latency.
        qa.retrieve(items[0]["question"], run.method, seeds)
        for item in items:
            gold = item["expected_context_ids"]
            day = date.fromisoformat(item["as_of"]) if item.get("as_of") else None
            as_of = day
            if run.temporal != "none" and run.time_source == "question":
                found = qa.times.extract(item["question"])
                as_of = found.day if found else None
            started = time.perf_counter()
            result = qa.retrieve(
                item["question"], run.method, seeds, as_of, run.temporal if as_of else "none"
            )
            elapsed = (time.perf_counter() - started) * 1000
            ranked = [e.node.id for e in result.evidence if e.node.kind != "Document"]
            hit = first_hit(ranked, gold, depth)
            rows.append(
                {
                    "id": item["id"],
                    "category": item.get("category", ""),
                    "verification_status": item["verification_status"],
                    "method": run.name,
                    **{f"recall@{k}": recall_at(ranked, gold, k) for k in ks},
                    f"mrr@{depth}": 1 / hit if hit else 0.0,
                    "first_hit_rank": hit,
                    f"amendment_coverage@{depth}": amendment_coverage(qa.graph, ranked, depth),
                    "stale@5": stale_at(qa.timeline, ranked, day or REFERENCE_DATE),
                    "pool": getattr(result, "pool", None),
                    "latency_ms": elapsed,
                    "as_of": as_of.isoformat() if as_of else "",
                    "expected": gold,
                    "retrieved": ranked[:depth],
                }
            )
    return rows


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def bootstrap(values, seed=0):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choice(values) for _ in range(n)) / n for _ in range(RESAMPLES))
    return [means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]]


def permutation_test(a, b, seed=0):
    """Two-sided paired sign-flip test of mean(b - a) = 0."""
    rng = random.Random(seed)
    diffs = [y - x for x, y in zip(a, b)]
    observed = abs(sum(diffs))
    if observed == 0:
        return 1.0
    extreme = sum(
        abs(sum(d if rng.random() < 0.5 else -d for d in diffs)) >= observed - 1e-12
        for _ in range(RESAMPLES)
    )
    return (extreme + 1) / (RESAMPLES + 1)


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted, running = [0.0] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(pvalues) - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def summarize(rows, ks=(5, 10)):
    depth = max(ks)
    names = [f"recall@{k}" for k in ks] + [f"mrr@{depth}", "stale@5", "latency_ms"]
    optional = f"amendment_coverage@{depth}"

    def stats(group):
        defined = [r[optional] for r in group if r[optional] is not None]
        pools = [r["pool"] for r in group if r["pool"] is not None]
        result = {
            "n": len(group),
            **{name: mean(r[name] for r in group) for name in names},
            optional: mean(defined),
            optional + "_n": len(defined),
            "rerank_pool": mean(pools),
        }
        for name in (f"recall@{depth}", f"mrr@{depth}"):
            result[name + "_ci95"] = bootstrap([r[name] for r in group])
        return result

    summary = {}
    for method in dict.fromkeys(r["method"] for r in rows):
        mine = [r for r in rows if r["method"] == method]
        categories = defaultdict(list)
        for row in mine:
            categories[row["category"]].append(row)
        verified = [r for r in mine if r["verification_status"] == "verified"]
        summary[method] = {
            "all": stats(mine),
            "verified": stats(verified) if verified else None,
            "by_category": {c: stats(g) for c, g in sorted(categories.items())},
        }
    return summary


def compare(rows, pairs, metrics=("mrr@10", "recall@10")):
    """Paired permutation tests with Holm correction across all listed comparisons."""
    by = defaultdict(dict)
    for row in rows:
        by[row["method"]][row["id"]] = row
    tests = []
    for a, b in pairs:
        if a not in by or b not in by:
            continue
        ids = sorted(set(by[a]) & set(by[b]))
        for metric in metrics:
            x = [by[a][i][metric] for i in ids]
            y = [by[b][i][metric] for i in ids]
            tests.append(
                {
                    "a": a,
                    "b": b,
                    "metric": metric,
                    "n": len(ids),
                    "mean_a": mean(x),
                    "mean_b": mean(y),
                    "difference": mean(y) - mean(x),
                    "wins_b": sum(v > u for u, v in zip(x, y)),
                    "wins_a": sum(u > v for u, v in zip(x, y)),
                    "p": permutation_test(x, y),
                }
            )
    for test, adjusted in zip(tests, holm([t["p"] for t in tests])):
        test["p_holm"] = adjusted
    return tests


def time_extraction(qa, items):
    """Accuracy of time expression normalisation, and triggers on undated questions."""
    temporal = [i for i in items if i.get("category") == "temporal"]
    correct, details, triggers = 0, [], []
    for item in temporal:
        found = qa.times.extract(item["question"])
        ok = bool(found) and found.day.isoformat() == item["as_of"]
        correct += ok
        details.append(
            {
                "id": item["id"],
                "gold": item["as_of"],
                "predicted": found.day.isoformat() if found else None,
                "rule": found.rule if found else None,
                "text": found.text if found else None,
                "correct": ok,
            }
        )
    for item in items:
        if item.get("category") != "temporal":
            found = qa.times.extract(item["question"])
            if found:
                triggers.append(
                    {
                        "id": item["id"],
                        "predicted": found.day.isoformat(),
                        "rule": found.rule,
                        "text": found.text,
                    }
                )
    return {
        "temporal_questions": len(temporal),
        "exact_date_accuracy": correct / len(temporal) if temporal else None,
        "details": details,
        "undated_questions_with_time_expression": triggers,
    }


def write_chart(summary, path, runs, labels, title=None, ks=(5, 10)):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = [f"recall@{k}" for k in ks] + [f"mrr@{max(ks)}"]
    palette = ["#b8c4d6", "#5b7aa6", "#8e6bb0", "#c0703a", "#9fc7bb", "#4ea38b", "#1f6b57"]
    runs = [r for r in runs if r in summary]
    fig, ax = plt.subplots(figsize=(9, 4), dpi=150)
    width = 0.84 / len(runs)
    for i, run in enumerate(runs):
        values = [summary[run]["all"][n] for n in names]
        positions = [x + (i - (len(runs) - 1) / 2) * width for x in range(len(names))]
        bars = ax.bar(positions, values, width, label=labels.get(run, run), color=palette[i % 7])
        ax.bar_label(bars, fmt="%.2f", fontsize=6, padding=2)
    ax.set_xticks(range(len(names)), [*(f"Recall@{k}" for k in ks), f"MRR@{max(ks)}"])
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("Điểm (0–1)")
    if title:
        ax.set_title(title, fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper left", fontsize=7, ncol=3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_temporal_chart(summary, path):
    """MRR@10 and Stale@5 of each base method without time handling, with the document
    date filter, and with knowledge-graph validity."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    modes = {"none": "#b8c4d6", "filter": "#5b7aa6", "kg": "#1f6b57"}
    bases = [m for m in TEMPORAL_BASES if temporal_name(m, "none") in summary]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), dpi=150)
    for ax, (metric, title) in zip(
        axes, (("mrr@10", "MRR@10 (cao hơn là tốt)"), ("stale@5", "Stale@5 (thấp hơn là tốt)"))
    ):
        width = 0.26
        for i, (mode, color) in enumerate(modes.items()):
            values = [summary[temporal_name(m, mode)]["all"][metric] for m in bases]
            positions = [x + (i - 1) * width for x in range(len(bases))]
            bars = ax.bar(positions, values, width, color=color, label=TEMPORAL_LABELS[mode])
            ax.bar_label(bars, fmt="%.2f", fontsize=7, padding=2)
        ax.set_xticks(
            range(len(bases)), [RUNS[m].replace(" + ", "\n+ ") for m in bases], fontsize=7
        )
        ax.set_ylim(0, 1.05)
        ax.set_title(title, fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, fontsize=8)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def run_benchmark(db_path, benchmark_path, output_dir, seeds=10, ks=(5, 10), neural=True):
    """Evaluate every configuration and write CSV, JSON, and charts."""
    from .qa import WaterQA

    items = load_benchmark(benchmark_path)
    topical = [i for i in items if i.get("category") != "temporal"]
    temporal = [i for i in items if i.get("category") == "temporal"]
    main_runs = [Run(m, m) for m in MAIN if neural or m in {"bm25", "graph"}]
    main_runs.append(Run("graph_vihermes_order", "graph_vihermes_order"))
    time_runs = [
        Run(temporal_name(m, mode), m, mode)
        for m in TEMPORAL_BASES
        if neural or m == "bm25"
        for mode in ("none", "filter", "kg")
    ]
    best = TEMPORAL_BASES[-1] if neural else "bm25"
    time_runs.append(Run(temporal_name(best, "kg", "question"), best, "kg", "question"))

    rows, temporal_rows, device = [], [], None
    # One service at a time keeps at most one bi-encoder in memory.
    services = [
        (dict(), main_runs, time_runs),
        (dict(bigrams=False), [Run("bm25_syllable", "bm25")], []),
    ]
    if neural:
        services += [
            (dict(dense_model="e5"), [Run("dense_e5", "dense")], []),
            (dict(segment=False), [Run("dense_noseg", "dense")], []),
        ]
    with WaterQA(db_path, today=REFERENCE_DATE) as qa:
        budget = {
            "max_depth": qa.propagator.max_depth,
            "max_nodes": qa.propagator.max_nodes,
            "max_chars": qa.propagator.max_chars,
        }
        timex = time_extraction(qa, items)
    for options, flat, timed in services:
        with WaterQA(db_path, today=REFERENCE_DATE, **options) as qa:
            rows += evaluate(qa, flat, topical, seeds, ks)
            temporal_rows += evaluate(qa, timed, temporal, seeds, ks)
            if qa._dense is not None and device is None:
                from .dense import device as torch_device

                device = torch_device()
        gc.collect()

    summary = summarize(rows, ks)
    temporal_summary = summarize(temporal_rows, ks)
    temporal_pairs = [
        (temporal_name(m, a), temporal_name(m, b))
        for m in TEMPORAL_BASES
        for a, b in (("none", "kg"), ("filter", "kg"))
    ]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    verified = sum(i["verification_status"] == "verified" for i in items)
    labels = {
        **RUNS,
        **{
            temporal_name(m, mode, source): f"{RUNS[m]} – {TEMPORAL_LABELS[mode]}"
            + (" (thời điểm trích từ câu hỏi)" if source == "question" else "")
            for m in TEMPORAL_BASES
            for mode in TEMPORAL_LABELS
            for source in ("gold", "question")
        },
    }
    report = {
        "benchmark": str(benchmark_path),
        "questions": len(items),
        "topical_questions": len(topical),
        "temporal_questions": len(temporal),
        "verified_questions": verified,
        "provisional": verified < len(items),
        "note": (
            "Kết quả tạm thời: benchmark còn câu hỏi chưa được người dùng xác nhận (needs_review)."
            if verified < len(items)
            else "Tất cả câu hỏi đã được xác nhận."
        ),
        "methods": {k: labels[k] for k in [*summary, *temporal_summary] if k in labels},
        "config": {
            "seeds_k": seeds,
            "cutoffs": list(ks),
            "match_rule": "retrieved == gold hoặc retrieved là khoản con của gold",
            "graph_budget": budget,
            "reference_date": REFERENCE_DATE.isoformat(),
            "rerank_pool": 30,
            "permutation_resamples": RESAMPLES,
            "device": device,
        },
        "summary": summary,
        "significance": compare(rows, TOPICAL_PAIRS),
        "temporal": {
            "summary": temporal_summary,
            "significance": compare(temporal_rows, temporal_pairs),
        },
        "time_extraction": timex,
        "questions_detail": rows + temporal_rows,
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
        "stale@5",
        "pool",
        "latency_ms",
        "as_of",
        "expected",
        "retrieved",
    ]
    with (output_dir / "evaluation.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows + temporal_rows:
            writer.writerow(
                {
                    **row,
                    "latency_ms": round(row["latency_ms"], 3),
                    "expected": " | ".join(row["expected"]),
                    "retrieved": " | ".join(row["retrieved"]),
                }
            )
    write_chart(summary, output_dir / "evaluation_chart.png", MAIN, RUNS, ks=ks)
    write_temporal_chart(temporal_summary, output_dir / "temporal_chart.png")
    return report


# Answer level: what the user reads, end to end (the day comes from the question itself).
ANSWER_CONFIGS = (
    ("bm25", "lexical"),
    ("graph_hybrid_rerank", "lexical"),
    ("graph_hybrid_rerank", "semantic"),
)


def answer_rows(qa, items, method, composer, seeds=10):
    rows = []
    for item in items:
        response = qa.ask(item["question"], method, seeds, composer=composer)
        gold = item["expected_context_ids"]
        quotes = [q["id"] for q in response["quotes"]]
        answered = response["answer_type"] == "extractive"
        day = date.fromisoformat(item["as_of"]) if item.get("as_of") else REFERENCE_DATE
        rows.append(
            {
                "id": item["id"],
                "category": item.get("category", ""),
                "config": f"{method}+{composer}",
                "answered": answered,
                "first_quote_correct": answered and any(matches(quotes[0], g) for g in gold),
                "any_quote_correct": answered and any(matches(q, g) for q in quotes for g in gold),
                "first_quote_stale": answered and qa.timeline.status(quotes[0], day).stale,
                "as_of": response["as_of"] or "",
                "quotes": quotes,
            }
        )
    return rows


def run_answer_benchmark(db_path, benchmark_path, output_dir, seeds=10):
    """Answer rate and citation accuracy of the extractive answers; merged into
    evaluation.json under "answers"."""
    from .qa import WaterQA

    items = load_benchmark(benchmark_path)
    rows = []
    with WaterQA(db_path, today=REFERENCE_DATE) as qa:
        for method, composer in ANSWER_CONFIGS:
            rows += answer_rows(qa, items, method, composer, seeds)

    def stats(group):
        n = len(group)
        answered = [r for r in group if r["answered"]]
        return {
            "n": n,
            "answer_rate": len(answered) / n,
            "first_quote_accuracy": sum(r["first_quote_correct"] for r in group) / n,
            "any_quote_accuracy": sum(r["any_quote_correct"] for r in group) / n,
            "precision_when_answered": (
                sum(r["first_quote_correct"] for r in answered) / len(answered)
                if answered
                else None
            ),
            "stale_first_quote_rate": sum(r["first_quote_stale"] for r in group) / n,
        }

    summary = {}
    for config in dict.fromkeys(r["config"] for r in rows):
        mine = [r for r in rows if r["config"] == config]
        suites = {
            "topical": [r for r in mine if r["category"] != "temporal"],
            "temporal": [r for r in mine if r["category"] == "temporal"],
        }
        summary[config] = {"all": stats(mine), **{k: stats(v) for k, v in suites.items() if v}}
    pairs = [(f"{a}+{x}", f"{b}+{y}") for (a, x), (b, y) in zip(ANSWER_CONFIGS, ANSWER_CONFIGS[1:])]
    tests = compare(
        [{**r, "method": r["config"], "correct": float(r["first_quote_correct"])} for r in rows],
        pairs,
        metrics=("correct",),
    )
    report = {"summary": summary, "significance": tests, "rows": rows}
    path = Path(output_dir) / "evaluation.json"
    if path.exists():
        full = json.loads(path.read_text(encoding="utf-8"))
        full["answers"] = report
        path.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary, "significance": tests}
