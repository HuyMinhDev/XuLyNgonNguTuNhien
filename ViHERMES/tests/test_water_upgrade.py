"""Upgrade of the water-resources project: amendment instructions, repeal edges, temporal
validity, time expressions, fusion, and statistics. Synthetic fixtures ("ví dụ") test the
logic; checks on the real graph skip when it has not been built."""

import json
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import pytest

from vihermes.graph import SQLiteGraph
from vihermes.water.benchmark import holm, load_benchmark, permutation_test, stale_at
from vihermes.water.corpus import build_graph
from vihermes.water.instructions import Target, parse_clause, parse_targets
from vihermes.water.qa import WaterQA, parse_method
from vihermes.water.sources import SourceDocument, SourceFile
from vihermes.water.structure import segment
from vihermes.water.temporal import Timeline, date_filter, resolve

ROOT = Path(__file__).resolve().parents[1]
REAL_DB = ROOT / "outputs" / "water_resources" / "water_graph.db"
needs_db = pytest.mark.skipif(not REAL_DB.exists(), reason="water graph not built")

LAW = SourceDocument(
    id="01/2099/QH99",
    slug="luat-vi-du",
    short_name="Luật ví dụ",
    title="Luật Nước ví dụ số 01/2099/QH99",
    document_type="Luật",
    issuer="Quốc hội",
    signer="",
    issued_date="2099-01-01",
    effective_date="2099-07-01",
    source_url="https://example.org/luat",
    files=(SourceFile("luat.pdf", "https://example.org/luat.pdf"),),
    aliases=("Luật Nước ví dụ",),
)
AMENDING = replace(
    LAW,
    id="03/2100/QH99",
    slug="luat-sua-doi-vi-du",
    short_name="Luật sửa đổi ví dụ",
    title="Luật số 03/2100/QH99 sửa đổi, bổ sung Luật Nước ví dụ",
    issued_date="2099-11-01",
    effective_date="2100-01-01",
    aliases=(),
)
LAW_TEXT = [
    "Điều 1. Phạm vi điều chỉnh",
    "Luật này quy định về bảo vệ nguồn nước ngầm và giếng khoan.",
    "Điều 2. Giấy phép giếng khoan",
    "1. Giếng khoan phải có giấy phép do Bộ Tài nguyên cấp.",
    "2. Thời hạn giấy phép giếng khoan tối đa 05 năm.",
    "Điều 3. Quy hoạch giếng khoan",
    "Quy hoạch giếng khoan do Bộ Tài nguyên lập.",
    "Điều 4. Thẩm định hồ sơ giếng khoan",
    "1. Thời hạn thẩm định hồ sơ giếng khoan là 42 ngày.",
    "Luật này được Quốc hội thông qua.",
]
AMENDING_TEXT = [
    "Điều 1. Sửa đổi, bổ sung một số điều của Luật Nước ví dụ",
    "1. Sửa đổi, bổ sung khoản 2 Điều 2 như sau:",
    "“2. Thời hạn giấy phép giếng khoan tối đa 10 năm.”.",
    "2. Bãi bỏ một số điều, khoản sau đây:",
    "a) Bãi bỏ Điều 3;",
    "b) Thay thế cụm từ “42 ngày” bằng cụm từ “30 ngày làm việc” tại khoản 1 Điều 4.",
    "Điều 2. Hiệu lực thi hành",
    "Luật này có hiệu lực từ ngày 01 tháng 01 năm 2100.",
    "Luật này được Quốc hội thông qua.",
]
OLD = "01/2099/QH99::article:2::clause:2"
NEW = "03/2100/QH99::article:1::clause:1"
REPEAL = "03/2100/QH99::article:1::clause:2"


@pytest.fixture
def corpus(tmp_path):
    registry = {"documents": []}
    processed = tmp_path / "processed"
    processed.mkdir()
    for document, text in ((LAW, LAW_TEXT), (AMENDING, AMENDING_TEXT)):
        row = asdict(document)
        row["files"] = [asdict(f) for f in document.files]
        row["aliases"] = list(document.aliases)
        row["include_articles"] = None
        registry["documents"].append(row)
        result = segment(text)
        record = {
            "document": asdict(document),
            "preamble": result.preamble,
            "articles": [asdict(a) for a in result.articles],
            "excluded_articles": [],
            "closing": result.closing,
            "warnings": result.warnings,
        }
        (processed / f"{document.slug}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "sources.json").write_text(json.dumps(registry, ensure_ascii=False))
    db = tmp_path / "graph.db"
    build_graph(tmp_path / "sources.json", processed, db)
    return db


# 1. Amendment instructions
def test_reference_lists_follow_vietnamese_order():
    found = parse_targets(
        "khoản 4 Điều 9; điểm c khoản 1, các khoản 3, 4 và 5 Điều 19; các điều 54, 55, 56 và 57"
    )
    assert Target("9", "4") in found
    assert Target("19", "1", True) in found  # only point c: partial
    assert {Target("19", c) for c in "345"} <= set(found)
    assert {Target(a) for a in ("54", "55", "56", "57")} <= set(found)
    assert parse_targets("khoản 1 và điểm b khoản 2 Điều 12") == [
        Target("12", "1"),
        Target("12", "2", True),
    ]


def test_instruction_kinds_and_header_context():
    operations, replacement = parse_clause(
        [
            "10. Bãi bỏ một số điều, khoản, điểm và cụm từ sau đây:",
            "a) Bãi bỏ khoản 4 Điều 9; các điều 54, 55 và 56;",
            "b) Bỏ cụm từ “cấp huyện,” tại khoản 3 Điều 26;",
        ]
    )
    assert [o.kind for o in operations] == ["repeal", "wording"]
    assert operations[1].targets == [Target("26", "3", True)]
    operations, _ = parse_clause(
        [
            "3. Sửa đổi, bổ sung một số khoản của Điều 17 như sau:",
            "a) Sửa đổi, bổ sung khoản 1 như sau:",
            "“1. Nội dung mới.”;",
        ]
    )
    assert operations[0].targets == [Target("17", "1")]


def test_unquoted_replacement_body_is_not_an_instruction():
    # The Gazette closes the quotation after the new heading; the body below is new text.
    operations, replacement = parse_clause(
        [
            "23. Sửa đổi, bổ sung Điều 33 như sau:",
            "“Điều 33. Nghĩa vụ của tổ chức hành nghề khoan”",
            "1. Thực hiện quy định tại khoản 1 Điều 30 Nghị định này.”.",
        ]
    )
    assert [t.article for o in operations for t in o.targets] == ["33"]
    assert any("Điều 30" in p for p in replacement)


# 2. Graph: repeal edges and dated changes
def test_repeal_and_amend_edges_with_dated_changes(corpus):
    with SQLiteGraph(corpus) as graph:
        relations = {(e.source, e.target): e.relation for e in graph.edges(OLD)}
        assert relations[(OLD, NEW)] == "AMENDS"
        assert {e.relation for e in graph.edges("01/2099/QH99::article:3")} == {"REPEALS"}
        change = graph.get(OLD).metadata["changes"][0]
        assert change == {"date": "2100-01-01", "kind": "amend", "scope": "whole", "by": NEW}
        wording = graph.get("01/2099/QH99::article:4::clause:1").metadata["changes"][0]
        assert (wording["kind"], wording["scope"]) == ("wording", "partial")


# 3. Validity on a day
def test_timeline_status_before_and_after_amendment(corpus):
    with SQLiteGraph(corpus) as graph:
        timeline = Timeline(graph)
    assert timeline.status(OLD, "2099-12-31").state == "in_force"
    assert timeline.status(OLD, "2100-06-01").state == "replaced"
    assert timeline.status("01/2099/QH99::article:3", "2100-06-01").state == "repealed"
    assert timeline.status("01/2099/QH99::article:2", "2100-06-01").state == "amended"
    assert timeline.status(NEW, "2099-12-31").state == "not_yet"
    assert timeline.status("01/2099/QH99::article:1", "2099-01-01").state == "not_yet"


def test_resolution_substitutes_replaced_and_repealed_units(corpus):
    with SQLiteGraph(corpus) as graph:
        timeline = Timeline(graph)
    ranked = [OLD, "01/2099/QH99::article:3", "01/2099/QH99::article:1"]
    after = resolve(ranked, timeline, date(2100, 6, 1))
    assert after[:3] == [NEW, REPEAL, "01/2099/QH99::article:1"]
    assert after[3:] == [OLD, "01/2099/QH99::article:3"]  # outdated, kept last
    before = resolve([NEW, OLD], timeline, date(2099, 12, 31))
    assert before == [OLD, NEW]  # the amendment is not yet in force
    # The graph-free baseline knows document dates only, not that OLD was replaced.
    assert date_filter([OLD, NEW], timeline, date(2100, 6, 1)) == [OLD, NEW]
    assert stale_at(timeline, [OLD, NEW], date(2100, 6, 1)) == 0.5


# 4. Time expressions
def test_time_expressions(corpus):
    with WaterQA(corpus, today=date(2100, 6, 1)) as qa:

        def day(question):
            found = qa.times.extract(question)
            return found.day if found else None

        assert day("Hiện nay thời hạn giấy phép giếng khoan là bao lâu?") == date(2100, 6, 1)
        assert day("Năm 2099 thời hạn là bao lâu?") == date(2099, 12, 31)
        assert day("Ngày 15/3/2100 thời hạn là bao lâu?") == date(2100, 3, 15)
        assert day("Trước ngày 01/01/2100 thì sao?") == date(2099, 12, 31)
        assert day("Trước khi Luật Nước ví dụ được sửa đổi thì sao?") == date(2099, 12, 31)
        assert day("Khi Luật Nước ví dụ mới có hiệu lực thì sao?") == date(2099, 7, 1)
        # A year that names a document is not a point in time.
        assert day("Luật Nước ví dụ năm 2099 quy định gì?") is None
        assert day("Giếng khoan phải có giấy phép không?") is None


def test_answer_applies_the_law_in_force_on_the_day(corpus):
    with WaterQA(corpus, today=date(2100, 6, 1)) as qa:
        now = qa.ask("Hiện nay thời hạn giấy phép giếng khoan tối đa bao nhiêu năm?", "graph")
        then = qa.ask("Năm 2099, thời hạn giấy phép giếng khoan tối đa bao nhiêu năm?", "graph")
        plain = qa.ask("Thời hạn giấy phép giếng khoan tối đa bao nhiêu năm?", "graph")
    assert now["as_of"] == "2100-06-01" and "10 năm" in now["quotes"][0]["text"]
    assert then["as_of"] == "2099-12-31" and "05 năm" in then["quotes"][0]["text"]
    assert "Thời điểm áp dụng: 01/06/2100" in now["answer"]
    assert plain["as_of"] is None and plain["time_expression"] is None


# 5. Methods, fusion and statistics
def test_method_names():
    assert parse_method("graph_hybrid_rerank") == parse_method("graph_hybrid_rerank")
    pipeline = parse_method("graph_hybrid_rerank")
    assert (pipeline.seeder, pipeline.graph, pipeline.rerank) == ("hybrid", True, True)
    assert parse_method("graph").seeder == "bm25"
    assert parse_method("dense").graph is False
    with pytest.raises(ValueError):
        parse_method("graph_magic")


def test_reciprocal_rank_fusion():
    np = pytest.importorskip("numpy")
    from vihermes.water.dense import rrf

    fused = rrf(np.array([3.0, 0.0, 1.0]), np.array([0.1, 0.9, 0.5]))
    assert fused[0] == pytest.approx(1 / 61 + 1 / 63)  # first by BM25, third by dense
    assert fused[1] == pytest.approx(1 / 61)  # unranked by BM25 (score 0), first by dense
    assert fused[2] == pytest.approx(2 / 62)


def test_permutation_test_and_holm():
    assert permutation_test([0.0] * 20, [1.0] * 20) < 0.001
    assert permutation_test([0.5] * 10, [0.5] * 10) == 1.0
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])


def test_temporal_benchmark_items_need_a_day(tmp_path):
    path = tmp_path / "b.jsonl"
    item = {
        "id": "T",
        "category": "temporal",
        "question": "Hiện nay?",
        "expected_context_ids": ["x"],
        "expected_answer": "",
        "verification_status": "needs_review",
    }
    path.write_text(json.dumps(item))
    with pytest.raises(ValueError):
        load_benchmark(path)


# 6. Real graph
@needs_db
def test_real_graph_repeals_and_removed_false_edges():
    with SQLiteGraph(REAL_DB) as graph:
        timeline = Timeline(graph)
        repealed = {e.source for e in graph.edges("146/2025/QH15::article:10::clause:10", True)}
        wrong = [
            e
            for e in graph.edges("54/2024/NĐ-CP::article:30")
            if e.target == "23/2026/NĐ-CP::article:2::clause:23"
        ]
    assert {f"28/2023/QH15::article:{n}" for n in (54, 55, 56, 57)} <= repealed
    assert not wrong
    assert timeline.status("28/2023/QH15::article:54", "2025-06-01").state == "in_force"
    assert timeline.status("28/2023/QH15::article:54", "2026-11-12").state == "repealed"
    assert timeline.status("54/2024/NĐ-CP::article:31", "2026-01-10").state == "in_force"
    assert timeline.status("54/2024/NĐ-CP::article:31", "2026-01-17").state == "repealed"


# 7. Optional LLM generation (a scripted model; no network)
class ScriptedModel:
    """Quotes the first evidence passage verbatim; the Auditor's verdict is supported."""

    def __init__(self, invent=False):
        self.invent = invent

    def json(self, system, payload):
        if "Auditor" in system:
            return {"supported": True, "reasons": []}
        first = payload["evidence"][0]
        quote = "tối đa 99 năm" if self.invent else first["text"].split("\n")[-1][:60]
        return {
            "abstain": False,
            "claims": [{"text": quote, "citations": [first["id"]], "quotes": {first["id"]: quote}}],
        }


def test_generation_cites_provisions_in_force(corpus):
    from vihermes.water.generate import generate

    with WaterQA(corpus, today=date(2100, 6, 1)) as qa:
        result = generate(
            qa,
            "Hiện nay thời hạn giấy phép giếng khoan tối đa bao nhiêu năm?",
            ScriptedModel(),
            "graph",
        )
        invented = generate(
            qa, "Hiện nay thời hạn giấy phép giếng khoan?", ScriptedModel(True), "graph"
        )
    assert result["as_of"] == "2100-06-01" and not result["abstained"]
    assert OLD not in result["evidence"]  # the replaced clause is never shown as current law
    assert result["sources"] and all(s in result["evidence"] for s in result["sources"])
    # The invented quote is rejected by the Auditor; the repair round fails, so it abstains.
    assert invented["abstained"]
    assert any("unsupported_quote" in step for step in invented["trace"])


def test_answer_states_that_the_leading_provision_was_repealed(corpus):
    with WaterQA(corpus, today=date(2100, 6, 1)) as qa:
        response = qa.ask("Hiện nay quy hoạch giếng khoan do ai lập?", "graph")
    assert response["amendments"][0].startswith("Điều 3 Luật ví dụ đã bị bãi bỏ bởi")
    assert "01/01/2100" in response["amendments"][0]
