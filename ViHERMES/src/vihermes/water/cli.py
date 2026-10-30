"""Command line for the water-resources QA project: download → process → build-graph → ask."""

import argparse
import json
import os

from .qa import METHODS

DATA = os.environ.get("WATER_QA_DATA", "data/water_resources")
OUTPUT = os.environ.get("WATER_QA_OUTPUT", "outputs/water_resources")
DB = os.environ.get("WATER_QA_DB", f"{OUTPUT}/water_graph.db")


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(prog="vihermes-water", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("download", "process", "build-graph"):
        command = sub.add_parser(name)
        command.add_argument("--sources", default=f"{DATA}/sources.json")
    sub.choices["download"].add_argument("--raw", default=f"{DATA}/raw")
    sub.choices["download"].add_argument("--force", action="store_true")
    sub.choices["process"].add_argument("--raw", default=f"{DATA}/raw")
    sub.choices["process"].add_argument("--output", default=DATA)
    build = sub.choices["build-graph"]
    build.add_argument("--processed", default=f"{DATA}/processed")
    build.add_argument("--db", default=DB)
    build.add_argument("--force", action="store_true", help="rebuild an existing water graph")
    stats = sub.add_parser("stats")
    stats.add_argument("--db", default=DB)
    stats.add_argument("--output", help="also write the statistics to this JSON file")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--db", default=DB)
    ask.add_argument("--method", choices=METHODS, default="graph")
    ask.add_argument("-k", type=int, default=5, help="number of BM25 seed provisions")
    ask.add_argument("--full", action="store_true", help="print evidence text and graph edges")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--benchmark", default=f"{DATA}/benchmark.jsonl")
    evaluate.add_argument("--db", default=DB)
    evaluate.add_argument("--output", default=OUTPUT)
    evaluate.add_argument("-k", type=int, default=10, help="number of BM25 seed provisions")
    return p


def run(args):
    if args.command == "download":
        from .sources import download, load_sources

        return download(load_sources(args.sources), args.raw, args.force)
    if args.command == "process":
        from .corpus import process_corpus

        return process_corpus(args.sources, args.raw, args.output)
    if args.command == "build-graph":
        from .corpus import build_graph

        return build_graph(args.sources, args.processed, args.db, args.force)
    if args.command == "stats":
        from pathlib import Path

        from .corpus import graph_stats

        if not Path(args.db).exists():
            raise FileNotFoundError(f"Graph database not found: {args.db} (run build-graph)")
        result = graph_stats(args.db)
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
    if args.command == "ask":
        from .qa import WaterQA

        with WaterQA(args.db, k=args.k) as qa:
            response = qa.ask(args.question, args.method)
        if not args.full:
            response.pop("edges", None)
            for item in response["evidence"]:
                item["text"] = item["text"][:300] + ("…" if len(item["text"]) > 300 else "")
        return response
    from .benchmark import run_benchmark

    report = run_benchmark(args.db, args.benchmark, args.output, seeds=args.k)
    report.pop("questions_detail")
    return report


def main():
    p = parser()
    args = p.parse_args()
    try:
        emit(run(args))
    except (ValueError, KeyError, OSError) as error:
        p.exit(2, f"vihermes-water: {error}\n")
