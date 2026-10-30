"""Question answering over the water-resources graph.

Two retrieval modes share the ViHERMES BM25 seed ranking:
- ``bm25``: the ranked seed provisions only;
- ``graph``: seeds expanded by ViHERMES typed propagation (amendments, references,
  provenance); each expanded unit is re-scored as the mean of its own BM25 score and the
  score propagated from its seed, so a neighbour ranks high only if it is also relevant.

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
from pathlib import Path

from ..agents import Pathfinder
from ..graph import SQLiteGraph
from ..ingestion import DOCUMENT_CODE
from ..models import VALIDITY, Plan
from ..retrieval import GraphPropagator, HybridRetriever, calibrate
from .lexical import BigramBM25, terms

METHODS = ("bm25", "graph")
ABLATIONS = ("graph_vihermes_order",)
# Bonus ViHERMES gives seeds from documents named in the question.
HINT_BONUS = 0.1
# Equal weight for a neighbour's own relevance and the relevance propagated to it; fixed a
# priori rather than tuned on the small benchmark.
FUSION = 0.5
# Frequent question words carry no legal content.
# "bao" stays informative: "bao gồm" states content, while "bao nhiêu" is caught by "nhiêu".
QUESTION_WORDS = frozenset(
    "ai gì hỏi không là làm mà nào những như thế ở sao được của và các có cho với nhiêu "
    "theo về thì trong khi nếu hay hoặc này đó đâu điều luật quy định".split()
)
MIN_COVERAGE = 0.35
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
    def __init__(self, db_path, k=5, max_depth=4, max_nodes=30, max_chars=100_000, bigrams=True):
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
        # dense weight 0 selects pure BM25 seeds; no embedding model is needed.
        self.retriever = HybridRetriever(self.graph, None, weight=0)
        if bigrams:
            self.retriever.bm25 = BigramBM25(self.retriever.nodes)
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

    def close(self):
        self.graph.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def document_hints(self, question):
        """Documents named in the question ("53/2024/NĐ-CP" or just "53/2024"): ranking hints."""
        codes = {m.upper() for m in re.findall(DOCUMENT_CODE, question, re.I)}
        for number in re.findall(r"\b\d{1,4}/\d{4}\b", question):
            codes |= {key for key in self.documents if key.startswith(number + "/")}
        folded = question.casefold()
        codes |= {key for alias, key in self.aliases if alias in folded}
        return sorted(codes & set(self.documents))

    def lexical_scores(self, question, hints):
        """Calibrated BM25 score of every unit, as used for ViHERMES seed ranking."""
        scores = calibrate(self.retriever.bm25.scores(question))
        return {
            node.id: float(score) + (HINT_BONUS if node.document_id in hints else 0.0)
            for node, score in zip(self.retriever.nodes, scores)
        }

    def retrieve(self, question, method="graph", k=None):
        if method not in METHODS + ABLATIONS:
            raise ValueError(f"method must be one of {METHODS}")
        hints = self.document_hints(question)
        plan = Plan(question, use_graph=method != "bm25", document_ids=hints)
        result = Pathfinder(self.retriever, self.propagator, k or self.k).retrieve(plan)
        if method == "graph":
            own = self.lexical_scores(question, set(hints))
            for item in result.evidence:
                if item.origin != "seed":
                    propagated = item.score
                    item.score = FUSION * own.get(item.node.id, 0.0) + (1 - FUSION) * propagated
            result.evidence.sort(
                key=lambda e: (e.node.kind == "Document", -e.score, not e.terminal, e.node.id)
            )
        return result

    def ranked_ids(self, question, method="graph", k=None):
        """Retrieved legal units in rank order; provenance documents occupy no rank."""
        result = self.retrieve(question, method, k)
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

    def compose(self, question, evidence):
        """Pick verbatim spans that best cover the question's informative terms."""
        query = {t for t in terms(question) if self.weight(t) > 0}
        total = sum(self.weight(t) for t in query)
        if not total:
            return [], 0.0
        candidates = []

        def covered(text):
            return sum(self.weight(t) for t in query & set(terms(text))) / total

        for rank, item in enumerate(evidence[:8]):
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
                candidates.append((score, coverage, quote, self.narrowest(item.node, quote)))
        candidates.sort(key=lambda c: -c[0])
        chosen, used = [], set()
        for score, coverage, quote, node in candidates:
            if node.id in used or (chosen and score < 0.7 * chosen[0][0]):
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
        if len(articles) > 2:
            return f"{label(node)} (sửa đổi {len(articles)} điều của văn bản khác)"
        if all(e.relation == "SUPPLEMENTS" for e in incoming):
            # A supplemented article (e.g. Điều 8a) is anchored to the article it follows.
            return f"{label(node)} (bổ sung điều mới sau {' và '.join(articles)})"
        return f"{label(node)} (sửa đổi, bổ sung {' và '.join(articles)})"

    def amendment_notes(self, result, cited):
        """Explain validity edges that touch the cited units, in plain Vietnamese."""
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
                notes.append(
                    f"{label(original)} được sửa đổi, bổ sung bởi {update}."
                    if edge.relation == "AMENDS"
                    else f"{update} bổ sung điều mới sau {label(original)}."
                )
        return list(dict.fromkeys(notes))

    def ask(self, question, method="graph", k=None):
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
            "elapsed_ms": 0.0,
        }
        if not question:
            response["answer"] = "Vui lòng nhập câu hỏi."
            return response
        started = time.perf_counter()
        result = self.retrieve(question, method, k)
        response["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
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
        quotes, coverage = self.compose(question, evidence)
        response["quotes"] = [
            {"id": node.id, "label": self.describe(node), "text": quote}
            for _, _, quote, node in quotes
        ]
        response["coverage"] = round(coverage, 3)
        if quotes and coverage >= MIN_COVERAGE:
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
        response["amendments"] = self.amendment_notes(result, cited)
        if response["amendments"]:
            lines.append("Lưu ý: " + " ".join(response["amendments"][:3]))
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
