"""Question answering over the water-resources graph.

Retrieval is a pipeline of four optional stages, named by the method string:

1. seeds: ``bm25`` (ViHERMES BM25 on syllables and syllable bigrams), ``dense`` (Vietnamese
   bi-encoder, MaxP over passages) or ``hybrid`` (reciprocal rank fusion of both);
2. ``graph_``: seeds expanded by ViHERMES typed propagation (amendments, repeals,
   references, provenance); each expanded unit is re-scored as the mean of its own seed
   score and the score propagated to it, so a neighbour ranks high only if it is relevant;
3. ``_rerank``: a cross-encoder re-scores the candidate pool (30 units: the top hybrid
   units, or the graph-expanded seeds and neighbours);
4. time: on a given day, ``kg`` resolution uses the dated validity edges of the graph to put
   provisions in force first and substitute replaced or repealed ones by the clause that
   replaced or repealed them; ``filter`` only demotes documents not yet in force.

ViHERMES orders every terminal amendment before all other evidence, which suits assembling
an LLM context but buries relevant seeds when weak seeds have amendments; that ordering is
kept as the ``graph_vihermes_order`` ablation for the benchmark.

Answers are extractive: every quoted sentence is copied verbatim from retrieved evidence,
so the system never states legal content that is absent from the official text.
"""

import math
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from ..graph import SQLiteGraph
from ..ingestion import DOCUMENT_CODE
from ..models import VALIDITY, Edge, Evidence, Retrieval
from ..retrieval import GraphPropagator, HybridRetriever, calibrate
from .lexical import BigramBM25, terms
from .temporal import Timeline, date_filter, extractor_from_graph, resolve

METHODS = (
    "bm25",
    "dense",
    "hybrid",
    "hybrid_rerank",
    "graph",
    "graph_hybrid",
    "graph_hybrid_rerank",
)
ABLATIONS = ("graph_vihermes_order", "graph_dense", "dense_rerank", "graph_rerank")
METHOD_LABELS = {
    "bm25": "BM25",
    "dense": "Dense (bi-encoder tiếng Việt)",
    "hybrid": "Hybrid (BM25 + Dense, RRF)",
    "hybrid_rerank": "Hybrid + Reranker",
    "graph": "Graph (BM25 + ViHERMES)",
    "graph_hybrid": "Graph (Hybrid + ViHERMES)",
    "graph_hybrid_rerank": "Graph + Hybrid + Reranker",
}
TEMPORAL_MODES = ("none", "filter", "kg")
# Bonus ViHERMES gives seeds from documents named in the question.
HINT_BONUS = 0.1
# Equal weight for a neighbour's own relevance and the relevance propagated to it; fixed a
# priori rather than tuned on the small benchmark.
FUSION = 0.5
# Candidates read by the cross-encoder, for both the flat and the graph pipelines.
RERANK_POOL = 30
# Leading units whose loss of validity is stated in the answer.
OUTDATED_TOP = 3
# Frequent question words carry no legal content.
# "bao" stays informative: "bao gồm" states content, while "bao nhiêu" is caught by "nhiêu".
QUESTION_WORDS = frozenset(
    "ai gì hỏi không là làm mà nào những như thế ở sao được của và các có cho với nhiêu "
    "theo về thì trong khi nếu hay hoặc này đó đâu điều luật quy định".split()
)
STATE_LABELS = {
    "in_force": "đang có hiệu lực",
    "amended": "có hiệu lực, đã được sửa đổi một phần",
    "replaced": "đã được thay thế",
    "repealed": "đã bị bãi bỏ",
    "not_yet": "chưa có hiệu lực",
}


@dataclass(frozen=True)
class Pipeline:
    seeder: str
    graph: bool
    rerank: bool
    vihermes_order: bool = False


def parse_method(method):
    if method == "graph_vihermes_order":
        return Pipeline("bm25", True, False, True)
    if method not in METHODS + ABLATIONS:
        raise ValueError(f"method must be one of {METHODS + ABLATIONS}")
    graph = method.startswith("graph")
    rest = method.removeprefix("graph").removeprefix("_") or "bm25"
    rerank = rest.endswith("rerank")
    seeder = rest.removesuffix("rerank").removesuffix("_") or "bm25"
    return Pipeline(seeder, graph, rerank)


def vn_date(value):
    value = value.isoformat() if isinstance(value, date) else value
    year, month, day = value.split("-")
    return f"{day}/{month}/{year}"


MIN_COVERAGE = 0.35
# Cross-encoder probability needed to quote a span (the decision boundary of the model,
# not tuned on the benchmark), and how many evidence units the semantic composer reads.
SEMANTIC_MIN = 0.5
SEMANTIC_DEPTH = 6
TOPIC_WEIGHT = 0.25
MAX_QUOTES = 3


def label(node):
    """Human-readable citation such as "khoản 2 Điều 9 Luật Tài nguyên nước 2023"."""
    meta = node.metadata
    if node.kind == "Document":
        return meta.get("short_name", node.id)
    place = f"Điều {meta.get('article', '?')}"
    if meta.get("clause"):
        place = f"khoản {meta['clause']} {place}"
    return f"{place} {meta.get('short_name', node.document_id)}"


HEADING = re.compile(r"^“?Điều\s+\d+[a-z]?\.")
QUOTE_LIMIT = 700


def spans(text):
    """Candidate answer spans as (line index, verbatim text): lines, or sentences of long lines."""
    for i, line in enumerate(text.split("\n")):
        if len(line) <= 400:
            if line.strip():
                yield i, line
            continue
        for sentence in re.split(r"(?<=[.;:])\s+(?=[A-ZĐÀ-Ỹa-zđ0-9“])", line):
            if sentence.strip():
                yield i, sentence


def is_title(span):
    """An article heading, or a short clause title without closing punctuation."""
    return bool(HEADING.match(span)) or (
        len(span) < 120 and not span.rstrip().endswith((".", ";", "”", ":"))
    )


def expand(lines, i, span):
    """A title or a lead-in ending with ':' is quoted together with the lines that follow."""
    if span != lines[i] or not (is_title(span) or span.rstrip().endswith(":")):
        return span
    quoted = [span]
    for line in lines[i + 1 :]:
        if sum(map(len, quoted)) + len(line) > QUOTE_LIMIT:
            break
        quoted.append(line)
    return "\n".join(quoted)


class WaterQA:
    # No LLM consumes the context, so the character budget only guards against runaway
    # expansion instead of fitting a prompt (the ViHERMES default is 16,000).
    def __init__(
        self,
        db_path,
        k=5,
        max_depth=4,
        max_nodes=30,
        max_chars=100_000,
        bigrams=True,
        dense_model="bkai",
        segment=None,
        cache_dir=None,
        today=None,
    ):
        db_path = Path(db_path)
        if not db_path.exists():
            raise FileNotFoundError(f"Graph database not found: {db_path} (run build-graph)")
        self.graph = SQLiteGraph(db_path)
        # Answering only reads the graph; a read-only connection shared across threads lets
        # a web app (Streamlit reruns in worker threads) cache one service instance.
        self.graph.db.close()
        self.graph.db = sqlite3.connect(
            db_path.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False
        )
        self.k = k
        # dense weight 0 selects pure BM25 seeds in ViHERMES; dense scores are fused here.
        self.retriever = HybridRetriever(self.graph, None, weight=0)
        if bigrams:
            self.retriever.bm25 = BigramBM25(self.retriever.nodes)
        self.position = {n.id: i for i, n in enumerate(self.retriever.nodes)}
        self.propagator = GraphPropagator(self.graph, max_depth, max_nodes, max_chars)
        bm25 = self.retriever.bm25
        size = len(bm25.ids)
        self.idf = {
            term: math.log(1 + (size - len(p) + 0.5) / (len(p) + 0.5))
            for term, p in bm25.postings.items()
        }
        self.max_idf = math.log(1 + (size + 0.5) / 0.5)
        self.documents = {
            n.id: n for n in self.graph.nodes() if n.kind == "Document" and not n.placeholder
        }
        # A law calls itself "Luật này", so its registered name only appears in questions.
        self.aliases = [
            (alias.casefold(), key)
            for key, node in self.documents.items()
            for alias in node.metadata.get("aliases", [])
        ]
        self.timeline = Timeline(self.graph)
        self.today = today or date.today()
        self.times = extractor_from_graph(
            self.documents.values(), self.retriever.nodes, today=self.today
        )
        # Neural models load on first use, so BM25 and graph retrieval need no torch.
        self.dense_model, self.segment = dense_model, segment
        self.cache_dir = Path(cache_dir) if cache_dir else db_path.parent
        self._dense = self._reranker = None

    def close(self):
        self.graph.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def dense(self):
        if self._dense is None:
            from .dense import DenseIndex

            self._dense = DenseIndex(
                self.retriever.nodes, self.dense_model, self.segment, self.cache_dir
            )
        return self._dense

    def reranker(self):
        if self._reranker is None:
            from .dense import Reranker

            self._reranker = Reranker()
        return self._reranker

    def document_hints(self, question):
        """Documents named in the question ("53/2024/NĐ-CP" or just "53/2024"): ranking hints."""
        codes = {m.upper() for m in re.findall(DOCUMENT_CODE, question, re.I)}
        for number in re.findall(r"\b\d{1,4}/\d{4}\b", question):
            codes |= {key for key in self.documents if key.startswith(number + "/")}
        folded = question.casefold()
        codes |= {key for alias, key in self.aliases if alias in folded}
        return sorted(codes & set(self.documents))

    def seed_scores(self, question, seeder="bm25", hints=()):
        """Score of every unit in [0, 1] plus the document hint bonus."""
        if seeder == "bm25":
            scores = calibrate(self.retriever.bm25.scores(question))
        elif seeder == "dense":
            raw = self.dense().scores(question)
            scores = (raw - raw.min()) / max(float(raw.max() - raw.min()), 1e-9)
        elif seeder == "hybrid":
            from .dense import rrf

            fused = rrf(self.retriever.bm25.scores(question), self.dense().scores(question))
            scores = fused / max(float(fused.max()), 1e-9)
        else:
            raise ValueError(f"unknown seeder: {seeder}")
        bonus = np.array(
            [HINT_BONUS if n.document_id in hints else 0.0 for n in self.retriever.nodes]
        )
        return np.asarray(scores, dtype=float) + bonus

    def lexical_scores(self, question, hints):
        """Calibrated BM25 score of every unit, as used for ViHERMES seed ranking."""
        scores = self.seed_scores(question, "bm25", hints)
        return {node.id: float(s) for node, s in zip(self.retriever.nodes, scores)}

    def retrieve(self, question, method="graph", k=None, as_of=None, temporal="none"):
        pipeline = parse_method(method)
        if temporal not in TEMPORAL_MODES:
            raise ValueError(f"temporal must be one of {TEMPORAL_MODES}")
        k = k or self.k
        hints = set(self.document_hints(question))
        scores = self.seed_scores(question, pipeline.seeder, hints)
        nodes = self.retriever.nodes
        order = sorted(range(len(nodes)), key=lambda i: (-scores[i], nodes[i].id))
        if not pipeline.graph:
            pool = max(k, RERANK_POOL) if pipeline.rerank else k
            result = Retrieval(
                [Evidence(nodes[i], float(scores[i])) for i in order[:pool] if scores[i] > 0]
            )
        else:
            seeds = [Evidence(nodes[i], float(scores[i])) for i in order[:k] if scores[i] > 0]
            result = self.propagator.expand(seeds)
            if not pipeline.vihermes_order:
                for item in result.evidence:
                    if item.origin != "seed" and item.node.id in self.position:
                        own = float(scores[self.position[item.node.id]])
                        item.score = FUSION * own + (1 - FUSION) * item.score
                result.evidence.sort(
                    key=lambda e: (e.node.kind == "Document", -e.score, not e.terminal, e.node.id)
                )
            if pipeline.rerank:
                # The graph adds candidates to the flat pool instead of replacing its tail:
                # the cross-encoder then reads the top units plus their legal neighbours.
                flat = [
                    Evidence(nodes[i], float(scores[i]))
                    for i in order[:RERANK_POOL]
                    if scores[i] > 0
                ]
                present = {e.node.id for e in flat}
                extra = [e for e in result.evidence if e.node.id not in present]
                result.evidence = flat + extra
        if pipeline.rerank:
            self.rerank(question, result, limit=None if pipeline.graph else RERANK_POOL)
        if as_of is not None and temporal != "none":
            self.apply_time(result, as_of, temporal)
        return result

    def rerank(self, question, result, limit=RERANK_POOL):
        """Order the candidate pool by cross-encoder relevance of each unit's best passage."""
        units = [e for e in result.evidence if e.node.kind != "Document" and e.node.text]
        pool, rest = (units[:limit], units[limit:]) if limit else (units, [])
        result.pool = len(pool)
        documents = [e for e in result.evidence if e.node.kind == "Document"]
        dense = self.dense()
        texts = [dense.best_passage(question, self.position[e.node.id]) for e in pool]
        for item, score in zip(pool, self.reranker().scores(question, texts)):
            item.score = float(score)
        pool.sort(key=lambda e: (-e.score, e.node.id))
        result.evidence = pool + rest + documents

    def apply_time(self, result, day, mode="kg"):
        """Re-order evidence for the law in force on ``day`` (see temporal.resolve)."""
        units = [e for e in result.evidence if e.node.kind != "Document"]
        documents = [e for e in result.evidence if e.node.kind == "Document"]
        ids = [e.node.id for e in units]
        # The most relevant units that are no longer law on that day are reported explicitly:
        # for "… còn hiệu lực không?" the repeal itself is the answer.
        result.outdated = [
            key
            for key in ids[:OUTDATED_TOP]
            if self.timeline.status(key, day).state in {"replaced", "repealed"}
        ]
        order = (
            resolve(ids, self.timeline, day)
            if mode == "kg"
            else date_filter(ids, self.timeline, day)
        )
        known = {e.node.id: e for e in units}
        evidence, previous = [], 0.0
        for key in order:
            item = known.get(key)
            if item is None:
                node = self.graph.get(key)
                if node is None:
                    continue
                item = Evidence(node, previous, "temporal", True)
            previous = item.score
            evidence.append(item)
        if mode == "kg":
            # Expose the validity edges that justified substitutions.
            present = {e.node.id for e in evidence}
            edges = set(result.edges)
            for key in present:
                for source in (key, key.split("::clause:")[0]):
                    for edge in self.graph.edges(source):
                        if edge.relation in VALIDITY and edge.target in present:
                            edges.add(Edge(edge.source, edge.target, edge.relation))
            result.edges = sorted(edges, key=lambda e: (e.source, e.target, e.relation))
        result.evidence = evidence + documents
        return result

    def ranked_ids(self, question, method="graph", k=None, as_of=None, temporal="none"):
        """Retrieved legal units in rank order; provenance documents occupy no rank."""
        result = self.retrieve(question, method, k, as_of, temporal)
        return [e.node.id for e in result.evidence if e.node.kind != "Document"]

    def weight(self, term):
        """IDF of an informative term. An unseen syllable is maximally informative (it cannot
        be matched, so it lowers coverage); an unseen bigram usually spans two words and is
        ignored."""
        parts = term.split("_")
        if any(p in QUESTION_WORDS for p in parts):
            return 0.0
        return self.idf.get(term, self.max_idf if len(parts) == 1 else 0.0)

    def narrowest(self, node, quote):
        """Cite the clause that contains a quote taken from its article's full text."""
        if node.kind == "Article":
            for edge in self.graph.edges(node.id):
                clause = self.graph.get(edge.target) if edge.relation == "HAS_CLAUSE" else None
                if clause is not None and quote in clause.text:
                    return clause
        return node

    def outdated(self, node, quote, day):
        """True when the quote is not law on ``day``: its unit is not in force, or it is an
        article-level quote that carries the old text of a replaced or repealed clause."""
        if self.timeline.status(node.id, day).stale:
            return True
        if node.kind != "Article":
            return False
        for edge in self.graph.edges(node.id):
            if edge.relation == "HAS_CLAUSE" and self.timeline.status(edge.target, day).stale:
                clause = self.graph.get(edge.target)
                if clause is not None and clause.text.split("\n")[0][:80] in quote:
                    return True
        return False

    def compose(self, question, evidence, day=None, semantic=False):
        """Pick verbatim spans that answer the question.

        Lexical mode scores spans by the IDF-weighted share of the question's informative
        terms they contain. Semantic mode scores the same spans with the cross-encoder, so a
        question in everyday words ("nước ngầm", "khai báo") can still select the legal
        wording ("nước dưới đất", "kê khai"). On a given day, a span is skipped when the
        clause it belongs to is not in force: an article amended in part still contains the
        old text of its replaced clauses."""
        query = {t for t in terms(question) if self.weight(t) > 0}
        total = sum(self.weight(t) for t in query)
        if not total and not semantic:
            return [], 0.0
        candidates = []

        def covered(text):
            return sum(self.weight(t) for t in query & set(terms(text))) / total if total else 0.0

        for rank, item in enumerate(evidence[: SEMANTIC_DEPTH if semantic else 8]):
            lines = item.node.text.split("\n")
            # Article titles summarise their provisions and separate otherwise tied spans.
            topic = covered(item.node.metadata.get("article_title", ""))
            for i, span in spans(item.node.text):
                coverage = covered(span)
                # Earlier evidence breaks ties; fragments rarely stand alone, and a title
                # is quoted with the lines that follow it.
                score = TOPIC_WEIGHT * topic + (1 - TOPIC_WEIGHT) * coverage - 0.02 * rank
                score -= 0.03 if is_title(span) else 0.1 if len(span) < 40 else 0
                quote = expand(lines, i, span)
                node = self.narrowest(item.node, quote)
                if day is not None and self.outdated(node, quote, day):
                    continue
                candidates.append((score, coverage, quote, node, rank))
        if semantic and candidates:
            from .dense import heading

            unique = list(dict.fromkeys((c[2], c[3].id) for c in candidates))
            nodes = {c[3].id: c[3] for c in candidates}
            texts = [f"{heading(nodes[key])}. {quote}" for quote, key in unique]
            relevance = dict(zip(unique, self.reranker().scores(question, texts)))
            candidates = [
                (relevance[(q, n.id)] - 0.01 * rank, relevance[(q, n.id)], q, n, rank)
                for _, _, q, n, rank in candidates
            ]
        candidates.sort(key=lambda c: -c[0])
        chosen, used = [], set()
        for score, coverage, quote, node, _ in candidates:
            if node.id in used or (chosen and score < 0.7 * chosen[0][0]):
                continue
            if semantic and chosen and coverage < SEMANTIC_MIN:
                continue
            # A clause repeats text from its article; quote each passage once.
            if any(quote in q or q in quote for _, _, q, _ in chosen):
                continue
            chosen.append((score, coverage, quote, node))
            used.add(node.id)
            if len(chosen) == MAX_QUOTES:
                break
        return chosen, (chosen[0][1] if chosen else 0.0)

    def describe(self, node):
        """Citation label; an amending clause also names the provisions it changes."""
        incoming = [e for e in self.graph.edges(node.id, incoming=True) if e.relation in VALIDITY]
        articles = sorted(
            {
                f"Điều {n.metadata.get('article')} {n.metadata.get('short_name', '')}"
                for n in (self.graph.get(e.source) for e in incoming)
                if n
            }
        )
        if not articles:
            title = node.metadata.get("article_title", "")
            return f"{label(node)} ({title})" if title and node.kind == "Clause" else label(node)
        relations = {e.relation for e in incoming}
        if relations == {"SUPPLEMENTS"}:
            # A supplemented article (e.g. Điều 8a) is anchored to the article it follows.
            verb = "bổ sung điều mới sau"
        elif relations == {"REPEALS"}:
            verb = "bãi bỏ"
        elif "REPEALS" in relations:
            verb = "bãi bỏ, sửa đổi"
        else:
            verb = "sửa đổi, bổ sung"
        if len(articles) > 2:
            return f"{label(node)} ({verb} {len(articles)} điều của văn bản khác)"
        return f"{label(node)} ({verb} {' và '.join(articles)})"

    def amendment_notes(self, result, cited, day=None):
        """Explain validity edges that touch the cited units, in plain Vietnamese, dated."""
        nodes = {e.node.id: e.node for e in result.evidence}
        # A clause is affected by amendments to its article, and vice versa.
        related = cited | {key.split("::clause:")[0] for key in cited}
        notes = []
        for edge in result.edges:
            if (
                edge.relation in VALIDITY
                and edge.target in nodes
                and ({edge.source, edge.target} & related)
            ):
                original = self.graph.get(edge.source)
                if original is None or original.placeholder:
                    continue
                update = label(nodes[edge.target])
                start = nodes[edge.target].metadata.get("valid_from")
                since = f" (từ ngày {vn_date(start)})" if start else ""
                if day is not None and start and start > day.isoformat():
                    notes.append(
                        f"{update} thay đổi {label(original)} nhưng chỉ có hiệu lực từ ngày "
                        f"{vn_date(start)}, sau thời điểm áp dụng {vn_date(day)}."
                    )
                elif edge.relation == "REPEALS":
                    notes.append(f"{label(original)} đã bị bãi bỏ bởi {update}{since}.")
                elif edge.relation == "SUPPLEMENTS":
                    notes.append(f"{update} bổ sung quy định mới sau {label(original)}{since}.")
                else:
                    notes.append(f"{label(original)} được sửa đổi, bổ sung bởi {update}{since}.")
        return list(dict.fromkeys(notes))

    def outdated_notes(self, result, day):
        """ "khoản 4 Điều 7 … đã bị bãi bỏ bởi … từ ngày …" for leading outdated units."""
        notes = []
        for key in getattr(result, "outdated", []):
            status = self.timeline.status(key, day)
            node = self.graph.get(key)
            updates = [self.graph.get(b) for b in status.by]
            names = " và ".join(label(u) for u in updates if u is not None)
            verb = "đã bị bãi bỏ bởi" if status.state == "repealed" else "đã được thay thế bởi"
            notes.append(f"{label(node)} {verb} {names} (từ ngày {vn_date(status.since)}).")
        return list(dict.fromkeys(notes))

    def validity(self, key, day):
        status = self.timeline.status(key, day)
        return {
            "state": status.state,
            "label": STATE_LABELS[status.state],
            "since": status.since,
            "by": status.by,
        }

    def ask(self, question, method="graph", k=None, as_of=None, temporal="auto", composer="auto"):
        """Answer with verbatim quotes. A time expression in the question ("hiện nay",
        "năm 2025", "trước khi Luật 146/2025/QH15 có hiệu lực") sets the day of the law to
        apply; otherwise ``as_of`` does; with neither, validity is not checked."""
        question = (question or "").strip()
        response = {
            "question": question,
            "method": method,
            "answer": "",
            "answer_type": "empty_question",
            "quotes": [],
            "coverage": 0.0,
            "sources": [],
            "evidence": [],
            "edges": [],
            "amendments": [],
            "warnings": [],
            "as_of": None,
            "time_expression": None,
            "outdated": [],
            "elapsed_ms": 0.0,
        }
        if not question:
            response["answer"] = "Vui lòng nhập câu hỏi."
            return response
        expression = self.times.extract(question)
        day = expression.day if expression else as_of
        if temporal == "auto":
            temporal = "kg" if day else "none"
        if day is not None and not self.timeline.in_corpus_period(day):
            # The corpus holds no law in force before its earliest document (Luật 17/2012 is
            # not collected): warn instead of filtering every provision out.
            temporal = "none"
        if expression:
            response["time_expression"] = {
                "text": expression.text,
                "rule": expression.rule,
                "date": expression.day.isoformat(),
            }
        started = time.perf_counter()
        result = self.retrieve(question, method, k, day, temporal)
        response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        if day is not None:
            response["as_of"] = day.isoformat()
            if not self.timeline.in_corpus_period(day):
                result.warnings.append("before_corpus_period")
        evidence = [e for e in result.evidence if e.node.kind != "Document" and e.node.text]
        response["warnings"] = list(result.warnings)
        response["evidence"] = [
            {
                "id": e.node.id,
                "label": label(e.node),
                "document_id": e.node.document_id,
                "title": e.node.title,
                "article": e.node.metadata.get("article"),
                "clause": e.node.metadata.get("clause"),
                "article_title": e.node.metadata.get("article_title", ""),
                "score": round(e.score, 4),
                "origin": e.origin,
                "terminal": e.terminal,
                "text": e.node.text,
                "source_url": e.node.metadata.get("source_url", ""),
                "validity": self.validity(e.node.id, day) if day else None,
            }
            for e in evidence
        ]
        response["edges"] = [
            {"source": e.source, "target": e.target, "relation": e.relation} for e in result.edges
        ]
        if not evidence:
            response["answer_type"] = "not_found"
            response["answer"] = (
                "Không tìm thấy điều khoản liên quan trong các văn bản đã thu thập. "
                "Hãy diễn đạt lại câu hỏi bằng thuật ngữ trong văn bản pháp luật."
            )
            response["warnings"].append("no_results")
            return response
        semantic = composer == "semantic" or (composer == "auto" and parse_method(method).rerank)
        response["composer"] = "semantic" if semantic else "lexical"
        quotes, coverage = self.compose(
            question, evidence, day if temporal != "none" else None, semantic
        )
        response["quotes"] = [
            {"id": node.id, "label": self.describe(node), "text": quote}
            for _, _, quote, node in quotes
        ]
        response["coverage"] = round(coverage, 3)
        if quotes and coverage >= (SEMANTIC_MIN if semantic else MIN_COVERAGE):
            response["answer_type"] = "extractive"
            cited = {q["id"] for q in response["quotes"]}
            lines = [
                f"Theo {q['label']}: "
                + (q["text"] if q["text"].startswith("“") else f"“{q['text']}”")
                for q in response["quotes"]
            ]
        else:
            response["answer_type"] = "reference_only"
            response["warnings"].append("low_coverage")
            cited = {e.node.id for e in evidence[:3]}
            lines = [
                "Chưa tìm thấy đoạn văn trả lời trực tiếp câu hỏi. "
                "Các điều khoản dưới đây là bằng chứng tham khảo gần nhất:",
                *(f"- {self.describe(e.node)}" for e in evidence[:3]),
            ]
        response["amendments"] = self.amendment_notes(result, cited, day)
        response["outdated"] = []
        if day is not None and temporal == "kg":
            response["outdated"] = self.outdated_notes(result, day)
            response["amendments"] = response["outdated"] + response["amendments"]
        if day is not None:
            # A quoted provision that is not in force on that day is flagged, never hidden.
            for q in response["quotes"]:
                status = self.timeline.status(q["id"], day)
                if status.stale:
                    q["validity"] = STATE_LABELS[status.state]
                    response["warnings"].append(f"stale_quote:{q['id']}")
                    lines.append(
                        f"Cảnh báo: {label(self.graph.get(q['id']))} {STATE_LABELS[status.state]}"
                        f" tại ngày {vn_date(day)}."
                    )
        if response["amendments"]:
            lines.append("Lưu ý: " + " ".join(response["amendments"][:3]))
        if day is not None:
            basis = (
                f"theo “{expression.text}” trong câu hỏi" if expression else "do người dùng chọn"
            )
            lines.append(f"Thời điểm áp dụng: {vn_date(day)} ({basis}).")
        response["answer"] = "\n".join(lines)
        # Quoted units first, then the leading evidence, grouped by document.
        ordered = [q["id"] for q in response["quotes"]] + [e.node.id for e in evidence[:5]]
        seen = {}
        for key in dict.fromkeys(ordered):
            node = self.graph.get(key)
            meta = node.metadata
            entry = seen.setdefault(
                node.document_id,
                {
                    "document_id": node.document_id,
                    "title": node.title,
                    "articles": [],
                    "source_url": meta.get("source_url", ""),
                    "gazette": meta.get("gazette", ""),
                },
            )
            if meta.get("article") not in entry["articles"]:
                entry["articles"].append(meta.get("article"))
        response["sources"] = list(seen.values())
        return response
