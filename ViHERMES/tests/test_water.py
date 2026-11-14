"""Water-resources pipeline: parsing, linking, graph build, retrieval, QA, benchmark, UI.

Synthetic fixtures (marked "ví dụ") test the logic; tests on the real Gazette corpus skip
when its files have not been downloaded or processed.
"""

import csv
import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from vihermes.water.benchmark import amendment_coverage, first_hit, load_benchmark, recall_at
from vihermes.water.corpus import build_graph, graph_stats, units, validate_units
from vihermes.water.links import Linker, article_links, split_quotes
from vihermes.water.pdftext import Line, clean, paragraphs
from vihermes.water.qa import WaterQA
from vihermes.water.sources import SourceDocument, SourceFile, load_sources
from vihermes.water.structure import segment

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "water_resources"
REAL_DB = ROOT / "outputs" / "water_resources" / "water_graph.db"
needs_raw = pytest.mark.skipif(
    not (DATA / "raw" / "luat-28-2023-qh15" / "luat28.pdf").exists(), reason="raw PDFs missing"
)
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
DECREE = SourceDocument(
    id="02/2099/NĐ-CP",
    slug="nd-vi-du",
    short_name="Nghị định ví dụ",
    title="Nghị định số 02/2099/NĐ-CP sửa đổi Luật Nước ví dụ",
    document_type="Nghị định",
    issuer="Chính phủ",
    signer="",
    issued_date="2099-02-01",
    effective_date="2099-02-01",
    source_url="https://example.org/nghi-dinh",
    files=(SourceFile("nd.pdf", "https://example.org/nd.pdf"),),
)
LAW_TEXT = [
    "LUẬT NƯỚC VÍ DỤ",
    "Chương I",
    "QUY ĐỊNH CHUNG",
    "Điều 1. Phạm vi điều chỉnh",
    "Luật này quy định về bảo vệ nguồn nước ngầm và giếng khoan.",
    "Điều 2. Giấy phép giếng khoan",
    "1. Giếng khoan phải có giấy phép theo quy định tại Điều 1 của Luật này.",
    "2. Thời hạn giấy phép giếng khoan tối đa 05 năm.",
    "Luật này được Quốc hội thông qua.",
]
DECREE_TEXT = [
    "NGHỊ ĐỊNH VÍ DỤ",
    "Căn cứ Luật Nước ví dụ số 01/2099/QH99;",
    "Điều 1. Sửa đổi, bổ sung một số điều của Luật Nước ví dụ",
    "1. Sửa đổi, bổ sung khoản 2 Điều 2 như sau:",
    "“2. Thời hạn giấy phép giếng khoan tối đa 10 năm theo khoản 1 Điều 2 Luật này.”.",
    "Điều 2. Hiệu lực thi hành",
    "Nghị định này có hiệu lực từ ngày ký.",
    "TM. CHÍNH PHỦ",
]


def record(document, text):
    result = segment(text)
    return {
        "document": asdict(document),
        "preamble": result.preamble,
        "articles": [asdict(a) for a in result.articles],
        "excluded_articles": [],
        "closing": result.closing,
        "warnings": result.warnings,
    }


@pytest.fixture
def corpus(tmp_path):
    """A two-document synthetic corpus: a law and a decree amending one of its clauses."""
    registry = {"documents": []}
    for document, text in ((LAW, LAW_TEXT), (DECREE, DECREE_TEXT)):
        row = asdict(document)
        row["files"] = [asdict(f) for f in document.files]
        row["aliases"] = list(document.aliases)
        row["include_articles"] = None
        registry["documents"].append(row)
        processed = tmp_path / "processed"
        processed.mkdir(exist_ok=True)
        (processed / f"{document.slug}.json").write_text(
            json.dumps(record(document, text), ensure_ascii=False), encoding="utf-8"
        )
    (tmp_path / "sources.json").write_text(json.dumps(registry, ensure_ascii=False))
    db = tmp_path / "graph.db"
    stats = build_graph(tmp_path / "sources.json", tmp_path / "processed", db)
    return db, stats


# 1. Reading official documents
@needs_raw
def test_reads_official_gazette_pdf():
    from vihermes.water.pdftext import extract_paragraphs

    found = extract_paragraphs([DATA / "raw" / "luat-28-2023-qh15" / "luat28.pdf"])
    assert "Điều 1. Phạm vi điều chỉnh" in found
    assert not any(p.startswith("CÔNG BÁO/Số") for p in found)
    assert not any("thongtinchinhphu@chinhphu.vn" in p for p in found)


@needs_raw
def test_real_documents_segment_into_expected_articles():
    from vihermes.water.pdftext import extract_paragraphs

    counts = {}
    for document in load_sources(DATA / "sources.json"):
        result = segment(extract_paragraphs([DATA / "raw" / f.path for f in document.files]))
        counts[document.id] = [a.number for a in result.articles]
    assert counts["28/2023/QH15"] == [str(n) for n in range(1, 87)]
    assert counts["53/2024/NĐ-CP"] == [str(n) for n in range(1, 99)]
    assert counts["54/2024/NĐ-CP"] == [str(n) for n in range(1, 60)]
    assert counts["23/2026/NĐ-CP"] == ["1", "2", "3", "4"]
    assert len(counts["146/2025/QH15"]) == 17


# 2. Vietnamese text handling
def test_clean_preserves_diacritics_and_fixes_lookalike_letters():
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "Điều 88. Thông số quan trắc") + "�"
    assert decomposed != "Điều 88. Thông số quan trắc"
    assert clean(decomposed) == "Điều 88. Thông số quan trắc"
    assert clean("Ðiều 5") == "Điều 5"


def test_wrapped_lines_join_into_paragraphs():
    lines = [
        Line("Điều 1. Phạm vi điều chỉnh", 87, 0, 400, True, 0),
        Line(
            "1. Luật này quy định về quản lý, bảo vệ, điều hòa, phân phối, phục hồi, phát",
            87,
            1,
            500,
            False,
            0,
        ),
        Line(
            "triển, khai thác, sử dụng tài nguyên nước; phòng, chống tác hại do nước gây ra.",
            64,
            2,
            500,
            False,
            0,
        ),
        Line(
            "2. Nước khoáng, nước nóng thiên nhiên không thuộc phạm vi điều chỉnh của Luật",
            87,
            3,
            500,
            False,
            0,
        ),
        Line(
            "này và các văn bản hướng dẫn chi tiết thi hành Luật Tài nguyên nước hiện hành.",
            64,
            4,
            500,
            False,
            0,
        ),
    ]
    result = paragraphs(lines)
    assert len(result) == 3
    assert result[1].endswith(
        "phục hồi, phát triển, khai thác, sử dụng tài nguyên nước; phòng, chống tác hại do nước gây ra."
    )


def test_quoted_amendment_text_does_not_open_articles():
    result = segment(
        [
            "Điều 1. Sửa đổi, bổ sung một số điều của Nghị định số 53/2024/NĐ-CP",
            "1. Sửa đổi, bổ sung Điều 5 như sau:",
            "“Điều 5. Nội dung mới",
            "1. Khoản mới của điều được sửa đổi.”.",
            "2. Sửa đổi, bổ sung Điều 7 như sau:",
            "Điều 7. Tiêu đề được trích nhưng mất dấu mở ngoặc kép",
            "Điều 2. Hiệu lực thi hành",
        ]
    )
    assert [a.number for a in result.articles] == ["1", "2"]
    assert [c.number for c in result.articles[0].clauses] == ["1", "2"]
    assert "“Điều 5. Nội dung mới" in result.articles[0].clauses[0].text


# 3. Unique, non-empty contexts
def test_units_are_unique_and_traceable():
    rows = units(record(LAW, LAW_TEXT))
    validate_units(rows)
    assert [r["context_id"] for r in rows] == [
        "01/2099/QH99::article:1",
        "01/2099/QH99::article:2",
        "01/2099/QH99::article:2::clause:1",
        "01/2099/QH99::article:2::clause:2",
    ]
    assert all(r["source_url"] == LAW.source_url for r in rows)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_units(rows + rows[:1])
    with pytest.raises(ValueError, match="Empty"):
        validate_units([{**rows[0], "text": " "}])


@pytest.mark.skipif(not (DATA / "contexts.csv").exists(), reason="contexts.csv missing")
def test_real_contexts_csv_has_unique_ids_and_sources():
    with (DATA / "contexts.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    validate_units(rows)
    assert len({r["document_id"] for r in rows}) == 5
    assert all(r["source_url"].startswith("https://vanban.chinhphu.vn/") for r in rows)


# Links between provisions
def test_references_resolve_named_and_numbered_documents():
    linker = Linker(
        [LAW, DECREE],
        [
            "01/2099/QH99::article:2",
            "01/2099/QH99::article:2::clause:1",
            "02/2099/NĐ-CP::article:1",
        ],
    )
    edges = linker.references(
        "02/2099/NĐ-CP::article:1",
        "theo khoản 1 Điều 2 của Luật Nước ví dụ và Điều 9 Luật Nước ví dụ năm 1990; "
        "Điều 3 Nghị định số 99/2020/NĐ-CP",
        "02/2099/NĐ-CP",
    )
    assert {e.target for e in edges} == {
        "01/2099/QH99::article:2::clause:1",
        "99/2020/NĐ-CP::article:3",
    }


def test_amendment_instruction_points_from_original_to_amending_clause():
    outside, quoted = split_quotes("1. Sửa đổi khoản 2 Điều 2 như sau: “2. Nội dung mới.”.")
    assert "Nội dung" not in outside and "Nội dung mới" in quoted
    seg = segment(DECREE_TEXT)
    ids = [
        "01/2099/QH99::article:2",
        "01/2099/QH99::article:2::clause:2",
        "02/2099/NĐ-CP::article:1",
    ]
    edges = article_links(DECREE, seg.articles, Linker([LAW, DECREE], ids))
    amends = {(e.source, e.target) for e in edges if e.relation == "AMENDS"}
    assert ("01/2099/QH99::article:2::clause:2", "02/2099/NĐ-CP::article:1::clause:1") in amends
    assert ("01/2099/QH99::article:2", "02/2099/NĐ-CP::article:1::clause:1") in amends


# 4. Graph ingestion
def test_graph_build_counts_and_protects_foreign_databases(corpus, tmp_path):
    db, stats = corpus
    assert stats["documents"] == 2
    assert stats["contexts"] == 7
    assert stats["relations"]["HAS_ARTICLE"] == 4
    assert stats["relations"]["AMENDS"] >= 1
    assert graph_stats(db) == stats
    with pytest.raises(ValueError, match="--force"):
        build_graph(tmp_path / "sources.json", tmp_path / "processed", db)
    foreign = tmp_path / "vihermes.db"
    from vihermes.graph import SQLiteGraph

    SQLiteGraph(foreign).close()
    with pytest.raises(ValueError, match="refusing"):
        build_graph(tmp_path / "sources.json", tmp_path / "processed", foreign, force=True)


@needs_db
def test_real_graph_statistics_and_amendment_edge():
    stats = graph_stats(REAL_DB)
    assert stats["documents"] == 5
    assert stats["contexts"] == stats["articles"] + stats["clauses"]
    from vihermes.graph import SQLiteGraph

    with SQLiteGraph(REAL_DB) as graph:
        targets = {
            e.target for e in graph.edges("54/2024/NĐ-CP::article:30") if e.relation == "AMENDS"
        }
    assert "23/2026/NĐ-CP::article:2::clause:21" in targets


# 5-9. Retrieval and question answering
def test_bm25_and_graph_retrieval(corpus):
    db, _ = corpus
    with WaterQA(db, k=2) as qa:
        bm25 = qa.ranked_ids("thời hạn giấy phép giếng khoan", "bm25")
        graph = qa.retrieve("thời hạn giấy phép giếng khoan", "graph")
    assert bm25 and bm25[0].startswith("01/2099/QH99::article:2")
    origins = {e.node.id: e.origin for e in graph.evidence}
    assert origins.get("02/2099/NĐ-CP::article:1::clause:1") in {"seed", "validity"}
    assert any(e.relation == "AMENDS" for e in graph.edges)


def test_answer_quotes_source_text_and_cites_sources(corpus):
    db, _ = corpus
    with WaterQA(db) as qa:
        response = qa.ask("Thời hạn giấy phép giếng khoan tối đa bao nhiêu năm?", "graph")
        assert response["answer_type"] == "extractive"
        for quote in response["quotes"]:
            assert quote["text"] in qa.graph.get(quote["id"]).text
    assert response["sources"][0]["source_url"].startswith("https://example.org/")
    assert any("sửa đổi" in note for note in response["amendments"])


def test_empty_and_unanswerable_questions_do_not_crash(corpus):
    db, _ = corpus
    with WaterQA(db) as qa:
        assert qa.ask("   ", "bm25")["answer_type"] == "empty_question"
        missing = qa.ask("xyzzy quux", "graph")
        unrelated = qa.ask("Giá vé xem bóng đá năm nay?", "bm25")
    assert missing["answer_type"] == "not_found" and "no_results" in missing["warnings"]
    assert unrelated["answer_type"] in {"reference_only", "not_found"}
    with pytest.raises(FileNotFoundError):
        WaterQA(Path(db).with_name("missing.db"))


@needs_db
def test_real_corpus_answers_with_official_sources():
    with WaterQA(REAL_DB) as qa:
        response = qa.ask("Mức thu tiền cấp quyền khai thác tài nguyên nước (M)", "graph")
    assert response["answer_type"] == "extractive"
    assert response["quotes"][0]["id"] == "54/2024/NĐ-CP::article:45"
    assert "0,05% đến 2,0%" in response["quotes"][0]["text"]
    assert response["sources"][0]["source_url"].startswith("https://vanban.chinhphu.vn/")


# Benchmark metrics
def test_metrics_count_clauses_as_hits_for_their_article(corpus):
    ranked = ["a::article:2::clause:1", "b::article:1", "a::article:5"]
    assert recall_at(ranked, ["a::article:2", "a::article:5"], 1) == 0.5
    assert recall_at(ranked, ["a::article:2", "a::article:5"], 3) == 1.0
    assert first_hit(ranked, ["b::article:1"], 10) == 2
    assert first_hit(ranked, ["a::article:20"], 10) is None
    db, _ = corpus
    with WaterQA(db) as qa:
        original, amending = (
            "01/2099/QH99::article:2::clause:2",
            "02/2099/NĐ-CP::article:1::clause:1",
        )
        assert amendment_coverage(qa.graph, [original], 10) == 0.0
        assert amendment_coverage(qa.graph, [original, amending], 10) == 1.0


def test_benchmark_file_is_valid_and_unverified_by_default():
    items = load_benchmark(DATA / "benchmark.jsonl")
    assert 60 <= len(items) <= 70
    temporal = [i for i in items if i.get("category") == "temporal"]
    assert len(temporal) >= 20 and all(i.get("as_of") for i in temporal)
    # Only a person may mark a question verified.
    assert {i["verification_status"] for i in items} <= {"needs_review", "verified"}


# 10. User interface
def test_streamlit_app_runs(corpus, tmp_path, monkeypatch):
    testing = pytest.importorskip("streamlit.testing.v1")
    db, _ = corpus
    monkeypatch.setenv("WATER_QA_DB", str(db))
    monkeypatch.setenv("WATER_QA_OUTPUT", str(tmp_path / "no-evaluation"))
    app = testing.AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    app.text_area[0].set_value("Thời hạn giấy phép giếng khoan?")
    app.button[0].click().run()
    assert not app.exception and app.success
    for page in ("Knowledge Graph", "Đánh giá"):
        app.sidebar.radio[0].set_value(page).run()
        assert not app.exception
    assert any("Chưa có kết quả" in i.value for i in app.info)


def test_streamlit_app_reports_missing_graph(tmp_path, monkeypatch):
    testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("WATER_QA_DB", str(tmp_path / "missing.db"))
    app = testing.AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=60)
    app.run()
    assert not app.exception
    assert any("build-graph" in e.value for e in app.error)


def test_cli_stats_and_ask(corpus):
    import subprocess
    import sys

    db, _ = corpus
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    run = [sys.executable, "-m", "vihermes.water"]
    stats = subprocess.run([*run, "stats", "--db", db], capture_output=True, text=True, env=env)
    assert stats.returncode == 0, stats.stderr
    assert json.loads(stats.stdout)["documents"] == 2
    ask = subprocess.run(
        [*run, "ask", "--db", db, "--method", "bm25", "Thời hạn giấy phép giếng khoan?"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert ask.returncode == 0, ask.stderr
    assert json.loads(ask.stdout)["sources"]
    missing = subprocess.run(
        [*run, "stats", "--db", Path(db).with_name("none.db")], capture_output=True, text=True
    )
    assert missing.returncode == 2
