"""Optional LLM answers grounded on retrieved provisions (the "G" of KAG).

The extractive answer of :class:`qa.WaterQA` needs no language model. With one, the ViHERMES
agents are reused unchanged: the Conductor drafts claims that must cite node IDs and quote
them verbatim, and the Auditor rejects unknown citations, quotes that are not exact
substrings, and sources whose effective date is after the day of the question; one repair
round is allowed, otherwise the system abstains.

What this module adds is the evidence: the time-aware retrieval of the water project, cut to
the provisions in force on that day, so the model never sees outdated text as current law.
Any OpenAI-compatible endpoint works (``OPENAI_API_KEY``, optional ``OPENAI_BASE_URL`` for a
local server such as Ollama or LM Studio).
"""

from dataclasses import asdict

from ..agents import Auditor, Conductor, Interpreter
from ..models import Retrieval

CONTEXT_UNITS = 8


class WaterPathfinder:
    """ViHERMES Pathfinder role backed by the water retrieval pipeline."""

    def __init__(self, qa, method, day=None, temporal="none", k=None, limit=CONTEXT_UNITS):
        self.qa, self.method, self.day, self.temporal = qa, method, day, temporal
        self.k, self.limit = k, limit

    def retrieve(self, plan):
        result = self.qa.retrieve(plan.query, self.method, self.k, self.day, self.temporal)
        units = [e for e in result.evidence if e.node.kind != "Document" and e.node.text]
        if self.day is not None and self.temporal != "none":
            units = [e for e in units if not self.qa.timeline.status(e.node.id, self.day).stale]
        units = units[: self.limit]
        keep = {e.node.id for e in units}
        documents = {e.node.document_id for e in units}
        evidence = units + [
            e for e in result.evidence if e.node.kind == "Document" and e.node.id in documents
        ]
        edges = [e for e in result.edges if e.source in keep or e.target in keep]
        return Retrieval(evidence, edges, list(result.warnings))


def generate(qa, question, llm, method="graph_hybrid_rerank", as_of=None, k=None, repair=1):
    """Draft, audit and return a cited answer; abstain when the audit fails."""
    found = qa.times.extract(question)
    day = found.day if found else as_of
    pathfinder = WaterPathfinder(qa, method, day, "kg" if day else "none", k)
    conductor = Conductor(
        Interpreter(), pathfinder, Auditor(llm, as_of=day or qa.today), llm, repair
    )
    answer, retrieval = conductor.run(question)
    return {
        "question": question,
        "method": method,
        "as_of": day.isoformat() if day else None,
        "time_expression": found.text if found else None,
        "answer": answer.answer,
        "abstained": answer.abstained,
        "reasons": answer.reasons,
        "claims": [asdict(c) for c in answer.claims],
        "sources": [s["id"] for s in answer.sources],
        "evidence": [e.node.id for e in retrieval.evidence if e.node.kind != "Document"],
        "trace": answer.trace,
    }
