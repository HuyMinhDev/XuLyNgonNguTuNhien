"""Giao diện demo: hỏi đáp pháp luật tài nguyên nước dựa trên Knowledge Graph và ViHERMES.

Chạy từ thư mục gốc của repository:

    streamlit run app/streamlit_app.py

Đường dẫn có thể đổi bằng biến môi trường WATER_QA_DB, WATER_QA_OUTPUT, WATER_QA_DATA.
"""

import json
import os
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from vihermes.water.benchmark import RUNS
from vihermes.water.corpus import graph_stats
from vihermes.water.qa import WaterQA, label

ROOT = Path(__file__).resolve().parents[1]


def setting(name, default):
    path = Path(os.environ.get(name, default))
    return path if path.is_absolute() else ROOT / path


OUTPUT = setting("WATER_QA_OUTPUT", "outputs/water_resources")
DB = setting("WATER_QA_DB", str(OUTPUT / "water_graph.db"))
DATA = setting("WATER_QA_DATA", "data/water_resources")

SAMPLES = [
    "Giấy phép khai thác nước dưới đất có thời hạn tối đa bao nhiêu năm?",
    "Mức thu tiền cấp quyền khai thác tài nguyên nước (M) có giá trị trong khoảng bao nhiêu?",
    "Các hành vi bị nghiêm cấm trong lĩnh vực tài nguyên nước là gì?",
    "Việc gây mưa nhân tạo phải được ai chấp thuận?",
    "Vùng cấm khai thác nước dưới đất được khoanh định đối với khu vực nào?",
    "Điều kiện cấp giấy phép hành nghề khoan nước dưới đất là gì?",
    "Thời hạn giấy phép hành nghề khoan nước dưới đất là bao lâu?",
    "Thẩm quyền phê duyệt và cơ quan tiếp nhận, thẩm định tiền cấp quyền khai thác tài nguyên nước?",
    "Khoản 5 Điều 8 Luật Tài nguyên nước về hành vi lấn, lấp sông, suối đã được sửa đổi như thế nào?",
    "Giá vé xem bóng đá năm nay là bao nhiêu?",
]
METHOD_NAMES = {"graph": "Graph-based (ViHERMES)", "bm25": "BM25"}
ORIGINS = {
    "seed": "BM25 – truy xuất trực tiếp",
    "validity": "Văn bản sửa đổi, bổ sung",
    "reference": "Điều khoản được dẫn chiếu",
    "provenance": "Điều khoản cha",
}
RELATIONS = {
    "HAS_ARTICLE": "Văn bản → Điều",
    "HAS_CLAUSE": "Điều → Khoản",
    "REFERS_TO": "Dẫn chiếu",
    "AMENDS": "Sửa đổi, bổ sung",
    "SUPPLEMENTS": "Bổ sung điều mới",
}
WARNINGS = {
    "unresolved_reference": "Dẫn chiếu tới điều khoản chưa có trong kho dữ liệu",
    "low_coverage": "Bằng chứng chưa bao phủ đủ từ khóa của câu hỏi",
    "no_results": "Không tìm thấy điều khoản liên quan",
    "context_budget": "Một số bằng chứng bị lược bớt do giới hạn ngữ cảnh",
    "graph_discovery_budget": "Mở rộng đồ thị chạm giới hạn số nút",
    "validity_depth_limit": "Chuỗi sửa đổi vượt quá độ sâu cho phép",
}
ANSWER_TYPES = {
    "extractive": "Câu trả lời trích nguyên văn",
    "reference_only": "Chỉ có bằng chứng tham khảo",
    "not_found": "Không tìm thấy",
    "empty_question": "Chưa có câu hỏi",
}


@st.cache_resource(show_spinner="Đang nạp Knowledge Graph…")
def load_qa(db, modified):
    return WaterQA(db)


@st.cache_data(show_spinner=False)
def load_stats(db, modified):
    return graph_stats(db)


def markdown_text(text):
    """Show legal text literally: escape Markdown and keep its line breaks."""
    escaped = re.sub(r"([\\`*_{}\[\]<>()#+\-!|~])", r"\\\1", text)
    # "1. ..." opening a line is a clause number, not a Markdown list item.
    escaped = re.sub(r"(?m)^(\d+)\.", r"\1\\.", escaped)
    return escaped.replace("\n", "  \n")


def dot_text(text, width=34):
    words, lines, line = text.split(), [], ""
    for word in words:
        if line and len(line) + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return "\\n".join(x.replace("\\", "\\\\").replace('"', '\\"') for x in lines)


def short_label(qa, key):
    node = qa.graph.get(key)
    if node is None or node.placeholder:
        return key.replace("::article:", " Điều ").replace("::clause:", " khoản ")
    return label(node)


def chronological(qa):
    """Corpus document IDs ordered by issue date: the Law first, its amendments last."""
    return sorted(qa.documents, key=lambda key: qa.documents[key].metadata.get("issued_date", ""))


def query_index(name, options):
    """Position of a URL query value among options; unknown values select the first."""
    value = st.query_params.get(name)
    return options.index(value) if value in options else 0


def warning_text(warning):
    kind, _, detail = warning.partition(":")
    return f"{WARNINGS.get(kind, kind)}{': ' + detail if detail else ''}"


def render_answer(qa, response):
    kind = response["answer_type"]
    box = {"extractive": st.success, "reference_only": st.warning}.get(kind, st.info)
    if kind == "extractive":
        box(ANSWER_TYPES[kind] + " – mỗi đoạn được sao chép đúng từ văn bản gốc.")
        for quote in response["quotes"]:
            st.markdown(f"**Theo {quote['label']}:**")
            st.markdown("> " + markdown_text(quote["text"]).replace("  \n", "  \n> "))
    else:
        box(markdown_text(response["answer"]))
    if response["amendments"]:
        st.info("**Lưu ý hiệu lực:** " + " ".join(response["amendments"]))

    columns = st.columns(4)
    columns[0].metric("Thời gian truy xuất", f"{response['elapsed_ms']:.1f} ms")
    columns[1].metric("Số bằng chứng", len(response["evidence"]))
    columns[2].metric("Quan hệ đồ thị đã dùng", len(response["edges"]))
    columns[3].metric("Độ phủ từ khóa", f"{response['coverage']:.0%}")

    if response["sources"]:
        st.subheader("Nguồn văn bản")
        for source in response["sources"]:
            articles = ", ".join(str(a) for a in source["articles"] if a)
            st.markdown(
                f"- **{source['title']}** – Điều {articles}  \n"
                f"  {source['gazette']} · [Cơ sở dữ liệu văn bản Chính phủ]({source['source_url']})"
            )

    if response["evidence"]:
        st.subheader("Bằng chứng truy xuất")
        for i, item in enumerate(response["evidence"][:15], 1):
            title = f"{i}. {item['label']} · {ORIGINS.get(item['origin'], item['origin'])} · điểm {item['score']:.2f}"
            with st.expander(title, expanded=i == 1):
                if item["article_title"]:
                    st.caption(f"Điều {item['article']}. {item['article_title']}")
                st.markdown(markdown_text(item["text"]))
                st.caption(f"Mã ngữ cảnh: `{item['id']}`")

    edges = [e for e in response["edges"] if e["relation"] not in {"HAS_ARTICLE", "HAS_CLAUSE"}]
    if edges:
        with st.expander(f"Quan hệ đồ thị được lần theo ({len(edges)})"):
            lines = ["digraph G {", "rankdir=LR; node [shape=box, style=rounded, fontsize=10];"]
            for edge in edges[:30]:
                source, target = short_label(qa, edge["source"]), short_label(qa, edge["target"])
                lines.append(
                    f'"{dot_text(source)}" -> "{dot_text(target)}" '
                    f'[label="{RELATIONS[edge["relation"]]}", fontsize=9];'
                )
            lines.append("}")
            st.graphviz_chart("\n".join(lines))
    if response["warnings"]:
        with st.expander(f"Cảnh báo ({len(response['warnings'])})"):
            for warning in response["warnings"]:
                st.markdown(f"- {markdown_text(warning_text(warning))}")


def page_qa(qa):
    st.title("Hỏi đáp pháp luật tài nguyên nước")
    st.caption(
        "Kho dữ liệu: Luật Tài nguyên nước 28/2023/QH15, Nghị định 53/2024/NĐ-CP, "
        "Nghị định 54/2024/NĐ-CP, Nghị định 23/2026/NĐ-CP và Điều 10, 16, 17 Luật "
        "146/2025/QH15 (bản Công báo). Câu trả lời chỉ trích nguyên văn, không thay thế tư vấn pháp lý."
    )
    # A link such as ?q=...&method=bm25&compare=1 opens with that question answered.
    linked = st.query_params.get("q", "")
    sample = st.selectbox("Câu hỏi gợi ý", ["(Tự nhập câu hỏi)", *SAMPLES])
    with st.form("ask"):
        question = st.text_area(
            "Câu hỏi",
            value=linked or ("" if sample.startswith("(") else sample),
            height=80,
            placeholder="Ví dụ: Giấy phép khai thác nước dưới đất có thời hạn bao lâu?",
        )
        left, middle, right = st.columns([2, 2, 1.4])
        method = left.radio(
            "Phương pháp truy xuất",
            list(METHOD_NAMES),
            index=query_index("method", list(METHOD_NAMES)),
            format_func=METHOD_NAMES.get,
            horizontal=True,
        )
        k = middle.slider("Số điều khoản hạt giống (k)", 3, 10, 5)
        compare = right.checkbox("So sánh hai phương pháp", value="compare" in st.query_params)
        submitted = st.form_submit_button("Tìm kiếm", type="primary")
    if not (submitted or linked):
        return
    if not question.strip():
        st.warning("Vui lòng nhập câu hỏi.")
        return
    if compare:
        columns = st.columns(2)
        for column, name in zip(columns, METHOD_NAMES):
            response = qa.ask(question, name, k)
            with column:
                st.subheader(METHOD_NAMES[name])
                st.caption(
                    f"{response['elapsed_ms']:.1f} ms · {len(response['evidence'])} bằng chứng · "
                    f"{ANSWER_TYPES[response['answer_type']]}"
                )
                for i, item in enumerate(response["evidence"][:10], 1):
                    st.markdown(
                        f"{i}. **{item['label']}** – {ORIGINS.get(item['origin'], '')} "
                        f"({item['score']:.2f})"
                    )
                if response["amendments"]:
                    st.info(" ".join(response["amendments"]))
        st.divider()
    render_answer(qa, qa.ask(question, method, k))


def document_graph(stats, documents):
    names = {key: node.metadata.get("short_name", key) for key, node in documents.items()}
    lines = ["digraph G {", 'rankdir=LR; node [shape=box, style="rounded,filled", fontsize=11];']
    for key, name in names.items():
        lines.append(f'"{key}" [label="{dot_text(name, 22)}", fillcolor="#dbeee8"];')
    colors = {"REFERS_TO": "#5b7aa6", "AMENDS": "#c0533a", "SUPPLEMENTS": "#d38b2c"}
    for pair, counts in stats["cross_document"].items():
        source, target = pair.split(" → ")
        for node in (source, target):
            if node not in names:
                names[node] = node
                lines.append(
                    f'"{node}" [label="{node}\\n(ngoài kho)", fillcolor="#eeeeee", style="rounded,dashed,filled"];'
                )
        for relation, count in counts.items():
            lines.append(
                f'"{source}" -> "{target}" [label="{RELATIONS[relation]} ×{count}", '
                f'color="{colors[relation]}", fontcolor="{colors[relation]}", fontsize=9];'
            )
    lines.append("}")
    return "\n".join(lines)


def article_graph(qa, article_id):
    """One-hop neighbourhood of an article and its clauses."""
    article = qa.graph.get(article_id)
    members = [article_id] + [
        e.target for e in qa.graph.edges(article_id) if e.relation == "HAS_CLAUSE"
    ]
    edges = []
    for key in members:
        edges += [e for e in qa.graph.edges(key) if e.relation not in {"HAS_CLAUSE"}]
        edges += [
            e
            for e in qa.graph.edges(key, incoming=True)
            if e.relation not in {"HAS_CLAUSE", "HAS_ARTICLE"}
        ]
    lines = ["digraph G {", 'rankdir=LR; node [shape=box, style="rounded,filled", fontsize=10];']
    center = dot_text(label(article))
    lines.append(f'"{center}" [fillcolor="#bfe3d7"];')
    colors = {"REFERS_TO": "#5b7aa6", "AMENDS": "#c0533a", "SUPPLEMENTS": "#d38b2c"}
    shown = set()
    for edge in edges[:40]:
        # Clauses of the selected article are drawn as the article itself.
        source = center if edge.source in members else dot_text(short_label(qa, edge.source))
        target = center if edge.target in members else dot_text(short_label(qa, edge.target))
        if source == target or (source, target, edge.relation) in shown:
            continue
        shown.add((source, target, edge.relation))
        for name in (source, target):
            if name != center:
                placeholder = qa.graph.get(edge.target if name == target else edge.source)
                fill = "#eeeeee" if placeholder is None or placeholder.placeholder else "#ffffff"
                lines.append(f'"{name}" [fillcolor="{fill}"];')
        lines.append(
            f'"{source}" -> "{target}" [label="{RELATIONS[edge.relation]}", '
            f'color="{colors[edge.relation]}", fontcolor="{colors[edge.relation]}", fontsize=9];'
        )
    lines.append("}")
    return "\n".join(lines), len(shown), len(edges)


def page_graph(qa, stats):
    st.title("Knowledge Graph văn bản pháp luật")
    columns = st.columns(5)
    columns[0].metric("Văn bản", stats["documents"])
    columns[1].metric("Contexts (điều + khoản)", stats["contexts"])
    columns[2].metric("Nút đồ thị", stats["nodes"])
    columns[3].metric("Cạnh đồ thị", stats["edges"])
    columns[4].metric("Dẫn chiếu chưa giải quyết", stats["unresolved_references"])
    st.caption(
        f"{stats['articles']} điều, {stats['clauses']} khoản; {stats['placeholders']} nút giữ chỗ "
        "cho điều khoản được dẫn chiếu nhưng không có trong kho dữ liệu."
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Loại quan hệ")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Quan hệ": key, "Ý nghĩa": RELATIONS[key], "Số cạnh": count}
                    for key, count in stats["relations"].items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    with right:
        st.subheader("Theo văn bản")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Văn bản": qa.documents[key].metadata.get("short_name", key),
                        "Số điều": row["articles"],
                        "Số khoản": row["clauses"],
                    }
                    for key, row in ((k, stats["per_document"][k]) for k in chronological(qa))
                ]
            ),
            hide_index=True,
            width="stretch",
        )

    st.subheader("Quan hệ giữa các văn bản")
    st.caption("Số trên mỗi cạnh là số quan hệ cấp điều/khoản được tổng hợp lên cấp văn bản.")
    st.graphviz_chart(document_graph(stats, qa.documents))

    st.subheader("Khám phá một điều luật")
    documents = {
        qa.documents[key].metadata.get("short_name", key): key for key in chronological(qa)
    }
    chosen = st.selectbox("Văn bản", list(documents))
    document_id = documents[chosen]
    articles = [
        qa.graph.get(e.target) for e in qa.graph.edges(document_id) if e.relation == "HAS_ARTICLE"
    ]
    articles.sort(key=lambda n: int(re.match(r"\d+", n.metadata["article"]).group()))
    options = {
        f"Điều {n.metadata['article']}. {n.metadata['article_title']}": n.id for n in articles
    }
    article_id = options[st.selectbox("Điều", list(options))]
    dot, shown, total = article_graph(qa, article_id)
    if shown:
        st.graphviz_chart(dot)
        if total > 40:
            st.caption(f"Hiển thị 40/{total} quan hệ đầu tiên.")
    else:
        st.info("Điều này không có quan hệ dẫn chiếu hoặc sửa đổi nào.")
    with st.expander("Nội dung điều luật"):
        st.markdown(markdown_text(qa.graph.get(article_id).text))
    with st.expander(f"Dẫn chiếu chưa giải quyết ({len(stats['unresolved_targets'])} điều khoản)"):
        st.markdown(
            "Các điều khoản dưới đây được nhắc tới trong văn bản nhưng không có trong kho dữ liệu "
            "(văn bản khác, điều mới được bổ sung bằng văn bản sửa đổi, hoặc điều không được trích):"
        )
        st.markdown("\n".join(f"- `{key}`" for key in stats["unresolved_targets"]))


def page_evaluation():
    st.title("Đánh giá BM25 và Graph-based Retrieval")
    path = OUTPUT / "evaluation.json"
    if not path.exists():
        st.info(
            "Chưa có kết quả đánh giá. Chạy lệnh sau từ thư mục gốc repository rồi tải lại trang:\n\n"
            "`python -m vihermes.water evaluate`"
        )
        return
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["provisional"]:
        st.warning(
            f"{report['note']} Đã xác nhận {report['verified_questions']}/{report['questions']} câu."
        )
    depth = max(report["config"]["cutoffs"])
    metrics = {
        **{f"recall@{k}": f"Recall@{k}" for k in report["config"]["cutoffs"]},
        f"mrr@{depth}": f"MRR@{depth}",
        f"amendment_coverage@{depth}": f"Độ phủ sửa đổi@{depth}",
        "latency_ms": "Thời gian (ms)",
    }

    def summary_table(part):
        return pd.DataFrame(
            [
                {
                    "Cấu hình": RUNS.get(run, run),
                    **{v: summary[part][k] for k, v in metrics.items()},
                }
                for run, summary in report["summary"].items()
                if summary[part]
            ]
        )

    table = summary_table("all")
    st.subheader("Kết quả tổng hợp")
    st.dataframe(table.style.format(precision=3), hide_index=True, width="stretch")
    if report["verified_questions"]:
        st.subheader(f"Chỉ các câu đã xác nhận ({report['verified_questions']} câu)")
        st.dataframe(
            summary_table("verified").style.format(precision=3), hide_index=True, width="stretch"
        )
    st.caption(
        "Hai dòng đầu là hai phương pháp chính; hai dòng cuối là thí nghiệm loại bỏ (ablation). "
        "Độ phủ sửa đổi: tỷ lệ điều khoản đã bị sửa đổi trong top-10 mà văn bản sửa đổi tương ứng "
        "cũng có mặt trong top-10 (không cần nhãn, nhưng Graph có lợi thế theo thiết kế)."
    )
    chart = OUTPUT / "evaluation_chart.png"
    if chart.exists():
        # The chart written by `evaluate`, identical to the figure in the report.
        st.image(str(chart), width=820)

    st.subheader("Theo nhóm câu hỏi")
    category_names = {
        "direct": "Tra cứu trực tiếp",
        "multi_provision": "Nhiều điều khoản",
        "cross_document": "Liên văn bản / sửa đổi",
    }
    rows = []
    for run in ("bm25", "graph"):
        for category, values in report["summary"][run]["by_category"].items():
            rows.append(
                {
                    "Phương pháp": RUNS[run],
                    "Nhóm": category_names.get(category, category),
                    "Số câu": values["n"],
                    **{metrics[k]: values[k] for k in metrics if k.startswith(("recall", "mrr"))},
                }
            )
    st.dataframe(pd.DataFrame(rows).style.format(precision=3), hide_index=True, width="stretch")

    st.subheader("Chi tiết từng câu hỏi")
    detail = pd.read_csv(OUTPUT / "evaluation.csv")
    run = st.selectbox("Cấu hình", list(report["summary"]), format_func=lambda r: RUNS.get(r, r))
    benchmark = DATA / "benchmark.jsonl"
    questions = {}
    if benchmark.exists():
        for line in benchmark.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                questions[item["id"]] = item["question"]
    view = detail[detail["method"] == run].copy()
    view.insert(1, "question", view["id"].map(questions))
    st.dataframe(view.drop(columns=["method"]), hide_index=True, width="stretch")


def main():
    st.set_page_config(page_title="Hỏi đáp tài nguyên nước", page_icon="💧", layout="wide")
    st.sidebar.title("Đồ án P1")
    st.sidebar.caption(
        "Hỏi đáp văn bản pháp luật tài nguyên nước dựa trên Knowledge Graph và ViHERMES"
    )
    pages = {"qa": "Hỏi đáp", "graph": "Knowledge Graph", "evaluation": "Đánh giá"}
    page = st.sidebar.radio(
        "Trang",
        list(pages.values()),
        index=query_index("page", list(pages)),
    )
    st.sidebar.divider()
    st.sidebar.caption(
        f"Cơ sở dữ liệu: `{DB.relative_to(ROOT) if DB.is_relative_to(ROOT) else DB}`"
    )
    if page == "Đánh giá":
        page_evaluation()
        return
    if not DB.exists():
        st.error(
            "Chưa có Knowledge Graph. Chạy các lệnh sau từ thư mục gốc repository:\n\n"
            "`python -m vihermes.water process`\n\n`python -m vihermes.water build-graph`"
        )
        return
    modified = DB.stat().st_mtime
    qa = load_qa(str(DB), modified)
    if page == "Hỏi đáp":
        page_qa(qa)
    else:
        page_graph(qa, load_stats(str(DB), modified))


main()
