"""Corpus pipeline: official PDFs → legal units (JSON, CSV) → ViHERMES SQLite graph."""

import csv
import json
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..graph import SQLiteGraph
from ..models import VALIDITY, Edge, Node
from .links import Linker, article_links, document_links
from .pdftext import extract_paragraphs
from .sources import load_sources, sha256
from .structure import Article, Clause, segment
from .temporal import annotate

CONTEXT_FIELDS = [
    "context_id",
    "document_id",
    "kind",
    "article",
    "clause",
    "title",
    "article_title",
    "text",
    "source_url",
]


def article_id(document_id, number):
    return f"{document_id}::article:{number}"


def process_document(document, raw_dir):
    paths = [Path(raw_dir) / f.path for f in document.files]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing source files (run download first): {missing}")
    for path, item in zip(paths, document.files):
        if item.sha256 and sha256(path) != item.sha256:
            raise ValueError(f"{path} does not match its registered checksum")
    result = segment(extract_paragraphs(paths))
    kept = [
        a
        for a in result.articles
        if not document.include_articles or a.number in document.include_articles
    ]
    if document.include_articles and len(kept) != len(document.include_articles):
        raise ValueError(f"{document.id}: some included articles were not found")
    return {
        "document": asdict(document),
        "preamble": result.preamble,
        "articles": [asdict(a) for a in kept],
        "excluded_articles": [a.number for a in result.articles if a not in kept],
        "closing": result.closing[:12],
        "warnings": result.warnings,
    }


def load_articles(record):
    return [
        Article(**{**a, "clauses": [Clause(**c) for c in a["clauses"]]}) for a in record["articles"]
    ]


def units(record):
    """Article and clause rows; article text contains its clauses, as in ViHERMES parsing."""
    document = record["document"]
    rows = []
    for article in load_articles(record):
        aid = article_id(document["id"], article.number)
        common = {
            "document_id": document["id"],
            "article": article.number,
            "title": document["title"],
            "article_title": article.title,
            "source_url": document["source_url"],
        }
        rows.append(
            {"context_id": aid, "kind": "Article", "clause": "", "text": article.text, **common}
        )
        for clause in article.clauses:
            rows.append(
                {
                    "context_id": f"{aid}::clause:{clause.number}",
                    "kind": "Clause",
                    "clause": clause.number,
                    "text": clause.text,
                    **common,
                }
            )
    return rows


def validate_units(rows):
    ids = [r["context_id"] for r in rows]
    duplicates = [k for k, n in Counter(ids).items() if n > 1]
    if duplicates:
        raise ValueError(f"Duplicate context IDs: {duplicates[:5]}")
    for row in rows:
        if not row["text"].strip():
            raise ValueError(f"Empty context: {row['context_id']}")
        if unicodedata.normalize("NFC", row["text"]) != row["text"]:
            raise ValueError(f"Text is not NFC-normalized: {row['context_id']}")
        if not row["context_id"].startswith(row["document_id"] + "::"):
            raise ValueError(f"Context ID does not match its document: {row['context_id']}")


def process_corpus(sources_path, raw_dir, output_dir):
    """Write processed/<slug>.json, processed/<slug>.txt and contexts.csv."""
    output_dir = Path(output_dir)
    processed = output_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    rows, report = [], []
    for document in load_sources(sources_path):
        record = process_document(document, raw_dir)
        (processed / f"{document.slug}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        articles = load_articles(record)
        (processed / f"{document.slug}.txt").write_text(
            "\n\n".join([document.title, *(a.text for a in articles)]) + "\n", encoding="utf-8"
        )
        document_rows = units(record)
        rows.extend(document_rows)
        report.append(
            {
                "id": document.id,
                "articles": len(articles),
                "clauses": sum(len(a.clauses) for a in articles),
                "contexts": len(document_rows),
                "excluded_articles": len(record["excluded_articles"]),
                "warnings": record["warnings"],
            }
        )
    validate_units(rows)
    with (output_dir / "contexts.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CONTEXT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {"documents": report, "contexts": len(rows)}


def is_water_graph(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='water_meta'").fetchone())


def graph_records(sources_path, processed_dir):
    documents = load_sources(sources_path)
    records = []
    for document in documents:
        path = Path(processed_dir) / f"{document.slug}.json"
        if not path.exists():
            raise FileNotFoundError(f"{path} not found (run process first)")
        records.append((document, json.loads(path.read_text(encoding="utf-8"))))
    return documents, records


def build_graph(sources_path, processed_dir, db_path, force=False):
    """Create a dedicated water-resources graph; never overwrites a foreign database."""
    db_path = Path(db_path)
    if db_path.exists():
        if not is_water_graph(db_path):
            raise ValueError(f"{db_path} is not a water-resources graph; refusing to replace it")
        if not force:
            raise ValueError(f"{db_path} exists; pass --force to rebuild it")
    documents, records = graph_records(sources_path, processed_dir)
    nodes, edges = [], []
    for document, record in records:
        meta = document.metadata()
        articles = load_articles(record)
        nodes.append(
            Node(
                document.id,
                document.id,
                "Document",
                document.title,
                "\n".join(record["preamble"]),
                {
                    **meta,
                    "aliases": list(document.aliases),
                    "excluded_articles": record["excluded_articles"],
                },
            )
        )
        for article in articles:
            aid = article_id(document.id, article.number)
            located = {
                **meta,
                "article": article.number,
                "article_title": article.title,
                "chapter": article.chapter,
                "section": article.section,
            }
            nodes.append(Node(aid, document.id, "Article", document.title, article.text, located))
            edges.append(Edge(document.id, aid, "HAS_ARTICLE"))
            for clause in article.clauses:
                cid = f"{aid}::clause:{clause.number}"
                nodes.append(
                    Node(
                        cid,
                        document.id,
                        "Clause",
                        document.title,
                        clause.text,
                        {**located, "clause": clause.number},
                    )
                )
                edges.append(Edge(aid, cid, "HAS_CLAUSE"))
    linker = Linker(documents, [n.id for n in nodes])
    for document, record in records:
        edges.extend(document_links(document, record["preamble"], linker))
        edges.extend(article_links(document, load_articles(record), linker))
    # Dated amendment events make every provision's validity answerable for a given day.
    nodes = annotate(nodes, linker.events, {d.id: d.effective_date for d in documents})

    staging = db_path.with_name(db_path.name + ".building")
    staging.unlink(missing_ok=True)
    with SQLiteGraph(staging) as graph:
        # Bulk load: one transaction instead of a commit per upsert.
        graph.db.execute("PRAGMA synchronous=OFF")
        for node in nodes:
            graph.upsert_node(node)
        for edge in dict.fromkeys(edges):
            graph.upsert_edge(edge)
        graph.db.execute("CREATE TABLE water_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        meta = {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": [d.id for d in documents],
        }
        with graph.db:
            graph.db.executemany(
                "INSERT INTO water_meta VALUES (?,?)",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()],
            )
    staging.replace(db_path)
    return graph_stats(db_path)


def graph_stats(db_path):
    """Corpus and graph statistics read back from the SQLite database."""
    with SQLiteGraph(db_path) as graph:
        nodes = graph.nodes()
        rows = graph.db.execute("SELECT source, target, relation FROM edges").fetchall()
    by_id = {n.id: n for n in nodes}
    real = [n for n in nodes if not n.placeholder]
    placeholders = [n for n in nodes if n.placeholder]
    corpus_documents = {n.id for n in real if n.kind == "Document"}
    unresolved = [(s, t, r) for s, t, r in rows if by_id[t].placeholder]
    pairs = defaultdict(Counter)
    for source, target, relation in rows:
        a, b = by_id[source].document_id, by_id[target].document_id
        if a != b and relation not in {"HAS_ARTICLE", "HAS_CLAUSE"}:
            # Validity edges point affected → update; report them as amending → amended.
            key = f"{b} → {a}" if relation in VALIDITY else f"{a} → {b}"
            pairs[key][relation] += 1
    return {
        "documents": len(corpus_documents),
        "articles": sum(n.kind == "Article" for n in real),
        "clauses": sum(n.kind == "Clause" for n in real),
        "contexts": sum(n.kind != "Document" and bool(n.text) for n in real),
        "nodes": len(nodes),
        "edges": len(rows),
        "relations": dict(sorted(Counter(r for _, _, r in rows).items())),
        "placeholders": len(placeholders),
        "unresolved_references": len(unresolved),
        "unresolved_in_corpus_documents": sum(
            by_id[t].document_id in corpus_documents for _, t, _ in unresolved
        ),
        "unresolved_targets": sorted(n.id for n in placeholders),
        "per_document": {
            d: {
                "articles": sum(n.kind == "Article" and n.document_id == d for n in real),
                "clauses": sum(n.kind == "Clause" and n.document_id == d for n in real),
            }
            for d in sorted(corpus_documents)
        },
        "cross_document": {k: dict(v) for k, v in sorted(pairs.items())},
        "changes": change_stats(real),
    }


def change_stats(nodes):
    """Dated amendment events by kind and scope, as stored on the provisions they change."""
    counts = Counter()
    for node in nodes:
        for change in node.metadata.get("changes", []):
            counts[f"{change['kind']}:{change['scope']}"] += 1
    return dict(sorted(counts.items()))
