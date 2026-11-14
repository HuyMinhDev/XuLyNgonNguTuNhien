"""Neural retrieval for Vietnamese legal text: bi-encoder seeds and cross-encoder re-ranking.

- Bi-encoders: ``bkai-foundation-models/vietnamese-bi-encoder`` (PhoBERT, trained on
  Vietnamese data including legal QA; expects word-segmented input) and
  ``intfloat/multilingual-e5-small`` (multilingual, raw text with "query:"/"passage:"
  prefixes) as a non-Vietnamese-specific comparison.
- Cross-encoder: ``itdainb/PhoRanker`` (PhoBERT re-ranker; word-segmented input).
- Word segmentation: pyvi (CRF), e.g. "nước ngầm cho gia đình" → "nước_ngầm cho gia_đình".

Legal units are longer than the 256-token window of PhoBERT, so each unit is split into
passages of at most ``PASSAGE_SYLLABLES`` syllables, each prefixed with its article heading;
a unit scores as its best passage (MaxP), and the best passage is what the re-ranker reads.
Passage embeddings are cached in ``outputs/`` keyed by model and corpus fingerprint.
"""

import hashlib
import json
import os
from pathlib import Path

import numpy as np

MODELS = {
    "bkai": {
        "name": "bkai-foundation-models/vietnamese-bi-encoder",
        "segment": True,
        "query": "",
        "passage": "",
    },
    "e5": {
        "name": "intfloat/multilingual-e5-small",
        "segment": False,
        "query": "query: ",
        "passage": "passage: ",
    },
}
RERANKER = "itdainb/PhoRanker"
PASSAGE_SYLLABLES = 150
RERANK_MAX_LENGTH = 256


def word_segment(text):
    """pyvi word segmentation; multi-syllable words are joined by "_"."""
    from pyvi import ViTokenizer

    return ViTokenizer.tokenize(text)


def heading(node):
    meta = node.metadata
    article = meta.get("article", "")
    title = meta.get("article_title", "")
    return f"Điều {article}. {title}".strip() if article else node.title


def passages(node, limit=PASSAGE_SYLLABLES):
    """Split a unit's text into passages of at most ``limit`` syllables, with its heading."""
    lines = [line for line in node.text.split("\n") if line.strip()]
    head = heading(node)
    if node.kind == "Article" and lines and lines[0].startswith("Điều"):
        lines = lines[1:]  # the heading is prepended to every passage instead
    chunks, current, size = [], [], 0
    for line in lines:
        words = line.split()
        while len(words) > limit:  # an over-long paragraph is cut into windows
            if current:
                chunks.append(" ".join(current))
                current, size = [], 0
            chunks.append(" ".join(words[:limit]))
            words = words[limit:]
        if size + len(words) > limit and current:
            chunks.append(" ".join(current))
            current, size = [], 0
        current.extend(words)
        size += len(words)
    if current or not chunks:
        chunks.append(" ".join(current))
    return [f"{head}. {chunk}".strip() for chunk in chunks]


def device():
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def load(factory, name, **options):
    """Load a cached model without network calls; download it on first use only."""
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    try:
        return factory(name, local_files_only=True, **options)
    except OSError:
        return factory(name, **options)


class DenseIndex:
    """MaxP bi-encoder scores for every legal unit, aligned with ``nodes``."""

    def __init__(self, nodes, model="bkai", segment=None, cache_dir=None, batch_size=32):
        from sentence_transformers import SentenceTransformer

        spec = MODELS[model]
        self.key = model
        self.segment = spec["segment"] if segment is None else segment
        self.prefix = (spec["query"], spec["passage"])
        self.encoder = load(SentenceTransformer, spec["name"], device=device())
        self.ids = [n.id for n in nodes]
        self.owner, texts = [], []
        for i, node in enumerate(nodes):
            for passage in passages(node):
                self.owner.append(i)
                texts.append(passage)
        self.owner = np.array(self.owner)
        self.texts = texts
        self.matrix = self._load_or_encode(texts, cache_dir, batch_size)
        self._query = (None, None)

    def _prepare(self, text, kind):
        text = word_segment(text) if self.segment else text
        return (self.prefix[0] if kind == "query" else self.prefix[1]) + text

    def _load_or_encode(self, texts, cache_dir, batch_size):
        signature = hashlib.sha256(
            json.dumps([self.key, self.segment, texts], ensure_ascii=False).encode()
        ).hexdigest()
        path = None
        if cache_dir:
            suffix = "" if self.segment == MODELS[self.key]["segment"] else "_noseg"
            path = Path(cache_dir) / f"dense_{self.key}{suffix}.npz"
            if path.exists():
                with np.load(path, allow_pickle=False) as saved:
                    if str(saved["signature"]) == signature:
                        return saved["vectors"]
        prepared = [self._prepare(t, "passage") for t in texts]
        vectors = self.encoder.encode(
            prepared, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
        ).astype(np.float32)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, vectors=vectors, signature=signature)
        return vectors

    def passage_scores(self, query):
        cached, values = self._query
        if cached != query:
            q = self.encoder.encode(
                [self._prepare(query, "query")], normalize_embeddings=True, show_progress_bar=False
            )[0]
            values = self.matrix @ q
            self._query = (query, values)
        return values

    def scores(self, query):
        """Best passage similarity of every unit (MaxP)."""
        values = self.passage_scores(query)
        result = np.full(len(self.ids), -1.0, dtype=np.float32)
        np.maximum.at(result, self.owner, values)
        return result

    def best_passage(self, query, index):
        values = self.passage_scores(query)
        rows = np.flatnonzero(self.owner == index)
        return self.texts[rows[np.argmax(values[rows])]]


class Reranker:
    """Cross-encoder relevance of (question, passage) pairs."""

    def __init__(self, model=RERANKER, max_length=RERANK_MAX_LENGTH):
        from sentence_transformers import CrossEncoder

        self.model = load(CrossEncoder, model, max_length=max_length, device=device())

    def scores(self, query, texts):
        if not texts:
            return np.array([])
        q = word_segment(query)
        pairs = [(q, word_segment(t)) for t in texts]
        return np.asarray(self.model.predict(pairs, show_progress_bar=False), dtype=float)


def rrf(*rankings, k=60):
    """Reciprocal rank fusion of score vectors (higher is better); zero scores are unranked."""
    size = len(rankings[0])
    fused = np.zeros(size)
    for scores in rankings:
        scores = np.asarray(scores, dtype=float)
        order = np.argsort(-scores, kind="stable")
        ranks = np.empty(size)
        ranks[order] = np.arange(1, size + 1)
        fused += np.where(scores > 0, 1.0 / (k + ranks), 0.0)
    return fused
