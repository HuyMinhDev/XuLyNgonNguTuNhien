# Trạng thái triển khai đồ án P1

## Phiên bản 2 – TempLaw-KAG (12/11/2026)

Chưa commit. Chi tiết phương pháp và số liệu ở [`reports/BAO_CAO_NANG_CAP.md`](../reports/BAO_CAO_NANG_CAP.md).

| Hạng mục | Trạng thái | Ghi chú |
| :-- | :-- | :-- |
| Sửa lỗi phân tích câu lệnh bãi bỏ ("các điều 54, 55, 56 và 57") | Hoàn thành | Thêm quan hệ `REPEALS` (18 cạnh); mô-đun `instructions.py` |
| Sửa 2 cạnh `AMENDS` sai do lỗi ngoặc kép Công báo | Hoàn thành | Chỉ đọc dòng câu lệnh; đã đối chiếu từng cạnh với đồ thị phiên bản 1 (3 cặp bị loại, 19 cặp được thêm, đều kiểm tra thủ công) |
| Knowledge Graph hiệu lực theo thời gian | Hoàn thành | 529 sự kiện có ngày; `Timeline.status`, `resolve` |
| Nhận diện biểu thức thời gian tiếng Việt | Hoàn thành | Đúng 22/22 câu theo thời điểm (do cùng người soạn, xem hạn chế) |
| Dense (PhoBERT bi-encoder, tách từ pyvi), Hybrid RRF, PhoRanker | Hoàn thành | Chạy trên CPU hoặc MPS; cache embedding |
| Bộ chọn đoạn trích ngữ nghĩa (cross-encoder) | Hoàn thành | Ngưỡng 0,5, không tinh chỉnh |
| LLM sinh câu trả lời có trích dẫn (tùy chọn) | Hoàn thành mã, **chưa đánh giá** | Không có khóa API; kiểm thử bằng mô hình giả lập |
| Benchmark 62 câu (thêm 11 đời thường, 22 theo thời điểm) | Hoàn thành, **tạm thời** | Tất cả `needs_review` |
| Kiểm định hoán vị + Holm, bootstrap CI, Stale@5, cấp câu trả lời | Hoàn thành | `evaluate`, `evaluate-answers` |
| Giao diện: phương pháp mới, thời điểm áp dụng, nhãn hiệu lực | Hoàn thành | |
| Kiểm thử | Hoàn thành | 64 đạt, 3 bỏ qua (adapter dịch vụ ngoài) |

**Việc người dùng cần làm:**

1. Xác nhận 62 câu benchmark (đặc biệt 33 câu mới T01–T22, C01–C11), rồi chạy lại `vihermes-water evaluate` và `evaluate-answers`.
2. Nếu có khóa API (hoặc máy chủ LLM cục bộ), chạy `vihermes-water ask "…" --llm <model>` để đánh giá phần sinh câu trả lời.
3. Đọc lại bài báo, slide và kịch bản demo đã cập nhật (xem bảng dưới); bản phiên bản 1 được lưu trong `reports/v1/`.

| Sản phẩm | Tệp | Ghi chú |
| :-- | :-- | :-- |
| Bài báo | `reports/NguyenMinhHuy_BaoCao_KnowledgeGraph.docx` (+ `.pdf`) | 14 trang Letter, giữ định dạng mẫu; 4 hình, 6 bảng, 34 tài liệu tham khảo (10 tài liệu mới đã kiểm chứng trên web) |
| Slide | `reports/NguyenMinhHuy_BaoCao_KnowledgeGraph_slide.pptx` (+ `.pdf`) | 21 slide 16:9, biểu đồ gốc PowerPoint, ghi chú thuyết trình tiếng Việt |
| Kịch bản demo | `reports/KICH_BAN_DEMO.md`, ảnh trong `reports/demo/` | 9 ảnh chụp app thật ngày 12/11/2026 |
| Báo cáo kỹ thuật | `reports/BAO_CAO_NANG_CAP.md` | Chi tiết phương pháp, số liệu, kiểm định |

Thay đổi nhỏ sau khi đánh giá (không ảnh hưởng số liệu): cảnh báo khi điều khoản liên quan nhất đã hết hiệu lực; giao diện mặc định k = 10 (khớp cấu hình đánh giá); câu gợi ý "hiện nay" dùng đúng cách nói của T02.

---

## Phiên bản 1

Cập nhật: 27/10/2026. Toàn bộ thay đổi **chưa được commit**; `git status` liệt kê các tệp ở mục 3.

## 1. Tổng quan theo Definition of Done

| Hạng mục | Trạng thái | Ghi chú |
| :-- | :-- | :-- |
| Bộ dữ liệu tài nguyên nước thật | Hoàn thành | 5 văn bản từ Công báo chính thức, URL và SHA-256 trong `sources.json` |
| Pipeline xử lý văn bản | Hoàn thành | Số điều tách được khớp văn bản gốc (kiểm thử tự động) |
| Knowledge Graph | Hoàn thành | 1.412 nút, 2.492 cạnh, cơ sở dữ liệu riêng |
| BM25 retrieval | Hoàn thành | BM25 của ViHERMES, bổ sung bigram âm tiết |
| Graph-based retrieval | Hoàn thành | `GraphPropagator` của ViHERMES và bước xếp hạng lại |
| Câu trả lời và nguồn | Hoàn thành | Trích nguyên văn, nguồn, ghi chú sửa đổi, từ chối khi độ phủ thấp |
| 20–30 câu benchmark dự thảo | Hoàn thành | 29 câu, tất cả `needs_review` |
| Kết quả đánh giá | Hoàn thành, **tạm thời** | Chưa có câu nào được người dùng xác nhận |
| Giao diện Streamlit | Hoàn thành | 3 trang; đã chạy thật và chụp ảnh |
| README | Hoàn thành | Tiếng Việt; README gốc giữ tại `docs/VIHERMES_README.md` |
| Báo cáo môn học | Hoàn thành, cần bổ sung | Còn trống họ tên học viên, giảng viên |
| Test chức năng chính | Hoàn thành | 20 kiểm thử mới |
| Checklist bàn giao | Hoàn thành | `docs/REVIEW_CHECKLIST.md` |

## 2. Chi tiết theo phase

### Phase 1 — Kiểm tra mã nguồn

Đã đọc README, `docs/architecture.md`, `docs/usage.md`, các mô-đun `models`, `ports`, `graph`, `ingestion`, `retrieval`, `agents`, `cli`, `data`, `evaluation` và các kiểm thử. Những phần tái sử dụng: `Node`, `Edge`, `SQLiteGraph`, `HybridRetriever`/`BM25`, `GraphPropagator`, `Pathfinder`, `Plan`, `DOCUMENT_CODE`, `calibrate`. Những phần không dùng: `parse_document` (lý do ở báo cáo, mục 3.3) và các tác tử LLM.

### Phase 2 — Thu thập dữ liệu

- Tải 8 tệp PDF Công báo cho 5 văn bản, lưu trong `data/water_resources/raw/<văn bản>/`.
- vbpl.vn trả về HTTP 403 với yêu cầu tự động. Đồ án không giả lập trình duyệt để vượt qua mà dùng `vanban.chinhphu.vn`, `datafiles.chinhphu.vn` và `congbaocdn.chinhphu.vn`.
- Tệp đính kèm trên vanban.chinhphu.vn của NĐ 53, NĐ 54 và QĐ 274/QĐ-TTg là bản scan (không có lớp văn bản). Bản scan của NĐ 53 và NĐ 54 đã được thay bằng bản Công báo; QĐ 274 bị loại vì không có bản văn bản (ghi ở mục `excluded` của `sources.json`).
- Đã bổ sung **NĐ 23/2026/NĐ-CP** và **Luật 146/2025/QH15** (Điều 10, 16, 17). Hai văn bản này sửa đổi trực tiếp Luật 28 và hai nghị định, nên là dữ liệu cần thiết cho quan hệ `AMENDS`.

### Phase 3 — Xử lý dữ liệu

Đầu ra gồm `processed/*.json`, `processed/*.txt` và `contexts.csv` (1.383 dòng). Các lỗi thực tế đã gặp và xử lý:

| Vấn đề | Văn bản | Cách xử lý |
| :-- | :-- | :-- |
| pypdf chèn khoảng trắng giữa chữ ("b ảo vệ") | Luật 28 | Dùng PyMuPDF |
| Ký tự "Ð" (U+00D0) thay cho "Đ" làm mất Điều 88 | NĐ 53 | Chuẩn hóa ký tự giống nhau |
| Lề trang khác nhau giữa phần nội dung và phụ lục | NĐ 23 | Ước lượng lề theo từng trang, kèm điều kiện có thụt lề |
| Trang chỉ gồm dòng thụt lề làm mất Điều 71–73 | NĐ 53 | Trang không có mẫu lề dùng lề của trang trước |
| Thiếu dấu ngoặc mở trước "Điều 50." | NĐ 23 | Ràng buộc số điều tăng liên tiếp |
| Dấu ngoặc thẳng `"` thay cho `”`; thiếu ngoặc đóng sau Điều 51 | NĐ 23 | Xử lý ngoặc thẳng; đặt lại độ sâu tại câu lệnh sửa đổi |
| Bố cục đổi giữa trang | Luật 146 | Dòng bắt đầu bằng chữ thường được nối vào đoạn trước |

### Phase 4 — Knowledge Graph

Đồ thị nằm ở `outputs/water_resources/water_graph.db`, không ghi đè `outputs/vihermes.db` (đã kiểm tra: lệnh từ chối và checksum của `vihermes.db` không đổi). Thống kê đọc từ cơ sở dữ liệu: 5 văn bản, 1.383 contexts, 1.412 nút, 2.492 cạnh (`HAS_ARTICLE` 250, `HAS_CLAUSE` 1.133, `REFERS_TO` 597, `AMENDS` 503, `SUPPLEMENTS` 9), 27 cạnh tới 24 nút giữ chỗ. Kiểm tra thủ công 20 cạnh ngẫu nhiên: 20/20 đúng.

### Phase 5 — Hỏi đáp

Hai chế độ `bm25` và `graph`, câu trả lời trích nguyên văn, nguồn, ghi chú sửa đổi, cảnh báo. Có bốn trạng thái: `extractive`, `reference_only`, `not_found`, `empty_question`.

### Phase 6 — Benchmark

29 câu (10 trực tiếp, 10 nhiều điều khoản, 9 liên văn bản/sửa đổi). Đánh giá 4 cấu hình. Kết quả tạm thời:

| Cấu hình | R@5 | R@10 | MRR@10 | ms |
| :-- | --: | --: | --: | --: |
| BM25 | 0,684 | 0,816 | 0,658 | 4,1 |
| Graph | 0,684 | 0,816 | 0,658 | 8,3 |
| Graph, thứ tự gốc ViHERMES | 0,385 | 0,764 | 0,238 | 4,9 |
| BM25 chỉ âm tiết | 0,529 | 0,695 | 0,557 | 3,0 |

### Phase 7 — Giao diện

`app/streamlit_app.py` có 3 trang: Hỏi đáp (gồm so sánh hai phương pháp), Knowledge Graph và Đánh giá. Đã chạy server thật và chụp 4 ảnh vào `reports/figures/`.

### Phase 8 — Kiểm thử

Xem mục 5.

### Phase 9–10 — Tài liệu

`README.md`, `reports/BAO_CAO_P1.md`, `docs/REVIEW_CHECKLIST.md` và tài liệu này.

## 3. Tệp đã thêm hoặc sửa

**Sửa**

- `README.md`: thay bằng README tiếng Việt của đồ án. Bản gốc được giữ nguyên nội dung tại `docs/VIHERMES_README.md`, chỉ chỉnh đường dẫn tương đối; bản gốc cũng còn trong lịch sử Git (commit `94622ac`).
- `pyproject.toml`: thêm nhóm phụ thuộc `water` và lệnh `vihermes-water`.

**Thêm**

- `src/vihermes/water/`: `__init__.py`, `__main__.py`, `sources.py`, `pdftext.py`, `structure.py`, `links.py`, `corpus.py`, `lexical.py`, `qa.py`, `benchmark.py`, `cli.py`.
- `app/streamlit_app.py`.
- `tests/test_water.py`.
- `data/water_resources/`: `sources.json`, `benchmark.jsonl`, `contexts.csv`, `processed/` (10 tệp), `raw/` (8 PDF, khoảng 12 MB).
- `reports/BAO_CAO_P1.md`, `reports/figures/` (4 ảnh giao diện, 1 biểu đồ).
- `docs/REVIEW_CHECKLIST.md`, `docs/IMPLEMENTATION_STATUS.md`, `docs/VIHERMES_README.md`.

**Sinh ra khi chạy** (thư mục `outputs/` đã có trong `.gitignore`): `outputs/water_resources/water_graph.db`, `graph_stats.json`, `evaluation.csv`, `evaluation.json`, `evaluation_chart.png`.

Không có tệp nào của ViHERMES trong `src/vihermes/*.py`, `tests/test_*.py` (trừ tệp mới), `dataset/` hay `outputs/vihermes.db` bị sửa hoặc xóa.

## 4. Lệnh đã chạy

```bash
pip install pytest ruff streamlit pypdf beautifulsoup4 networkx pyvis matplotlib pymupdf
pip install -e '.[dev,dataset,water]'
vihermes-water download --raw <thư mục tạm>     # 8 tệp tải mới, SHA-256 khớp
vihermes-water download                          # 8 tệp "present"
vihermes-water process
vihermes-water build-graph --force
vihermes-water stats --output outputs/water_resources/graph_stats.json
vihermes-water ask "…"; vihermes-water ask --method bm25 "…"
vihermes-water evaluate
vihermes-water build-graph --db outputs/vihermes.db --force   # bị từ chối như mong đợi
streamlit run app/streamlit_app.py --server.headless true --server.port 8765
pytest -q
ruff check src tests app && ruff format --check src tests app
```

Ảnh giao diện được chụp bằng Playwright cài trong một môi trường ảo tạm ngoài repository, dùng Google Chrome có sẵn trên máy.

Về môi trường `.venv`: `pypdf`, `beautifulsoup4`, `networkx` và `pyvis` đã được cài trong quá trình khảo sát nhưng mã nguồn cuối cùng không dùng, và chúng không có trong `pyproject.toml`. Có thể gỡ bằng `pip uninstall pypdf beautifulsoup4 networkx pyvis`.

## 5. Kết quả kiểm thử

| Bộ kiểm thử | Kết quả |
| :-- | :-- |
| `tests/test_water.py` (mới) | 20 passed |
| Kiểm thử gốc ViHERMES | 29 passed, 3 skipped |
| Tổng `pytest -q` | **49 passed, 3 skipped, 0 failed** |
| `ruff check src tests app` | Không có lỗi |
| `ruff format --check src tests app` | Đã định dạng |

Ba kiểm thử bị bỏ qua: `test_adapters.py` cần gói `openai`, gói `pymilvus` và dịch vụ Neo4j. Đây là phần ngoài phạm vi đồ án và bị bỏ qua giống như trước khi có thay đổi.

Kiểm thử gốc `test_construction.py` cần nhóm phụ thuộc `dataset` (scikit-learn). Khi chưa cài nhóm này, kiểm thử báo `ModuleNotFoundError`; sau khi cài như README gốc hướng dẫn thì đạt. Đây không phải lỗi do đồ án gây ra.

## 6. Quyết định kỹ thuật quan trọng

1. **Thêm gói con `vihermes.water`, không sửa mã ViHERMES.** Chức năng gốc giữ nguyên, kiểm thử gốc vẫn đạt.
2. **Bộ tách cấu trúc riêng** thay cho `parse_document`: cần xử lý văn bản trích dẫn trong văn bản sửa đổi, tiêu đề trang, chữ ký số và phụ lục.
3. **Đưa văn bản sửa đổi (NĐ 23/2026, Luật 146/2025) vào kho.** Nếu không có chúng, hệ thống sẽ trả lời theo văn bản đã bị sửa mà không có cảnh báo.
4. **Bigram âm tiết cho BM25**: kỹ thuật chuẩn cho tiếng Việt, áp dụng cho cả hai chế độ. Được thử trên benchmark và giữ lại; kết quả chỉ dùng âm tiết vẫn được báo cáo dưới dạng ablation.
5. **Xếp hạng lại kết quả Graph** bằng trung bình điểm BM25 riêng và điểm lan truyền. Hệ số 0,5 không được tinh chỉnh, nhưng dạng trung bình được chọn sau khi thử dạng "lấy giá trị lớn nhất" trên benchmark. Thứ tự gốc của ViHERMES vẫn được báo cáo dưới dạng ablation.
6. **Gợi ý văn bản theo tên** ("Luật Tài nguyên nước") theo cơ chế gợi ý xếp hạng có sẵn của ViHERMES, bổ sung sau khi thấy Luật tự gọi mình là "Luật này".

Các quyết định 4–6 được đưa ra khi đã xem kết quả trên chính benchmark này, và benchmark không có tập kiểm tra riêng, nên các con số có thể lạc quan hơn kết quả trên câu hỏi mới.
7. **Không dùng LLM.** Câu trả lời là đoạn trích nguyên văn, có ngưỡng độ phủ 0,35 để từ chối trả lời. Ngưỡng và trọng số tiêu đề 0,25 được chọn bằng cách xem kết quả trên một số câu hỏi demo, **không** tối ưu theo benchmark (benchmark chỉ đo truy xuất, không đo chọn đoạn).
8. **Benchmark để ở trạng thái `needs_review`.** Mọi kết quả được ghi rõ là tạm thời.

## 7. Vấn đề còn tồn tại

| Mức độ | Vấn đề |
| :-- | :-- |
| Cần người dùng xử lý | Xác nhận 29 câu benchmark và cập nhật lại số liệu báo cáo nếu thay đổi |
| Cần người dùng xử lý | Điền họ tên học viên, giảng viên trong báo cáo; quyết định commit (dữ liệu PDF khoảng 12 MB) |
| Hạn chế đã biết | Chọn đoạn trả lời dựa trên từ vựng có thể chọn sai (ví dụ "Tài nguyên nước bao gồm…", "phải nộp" và "không phải nộp") |
| Hạn chế đã biết | Graph không tăng Recall/MRR trên benchmark hiện tại |
| Hạn chế đã biết | Chưa đánh dấu riêng điều khoản bị bãi bỏ (ví dụ Điều 31 NĐ 54) |
| Hạn chế đã biết | Không xử lý phụ lục và văn bản scan; chưa có Thông tư hướng dẫn |
| Hạn chế đã biết | Mới kiểm thử trên Python 3.14; mã tương thích cú pháp 3.10 (ruff kiểm tra) nhưng chưa chạy thử trên các phiên bản khác |

Hệ thống chạy được đầy đủ và kiểm thử đều đạt, nhưng đồ án **chưa thể coi là hoàn tất 100%** cho đến khi benchmark được xác nhận và báo cáo được điền thông tin học viên.
