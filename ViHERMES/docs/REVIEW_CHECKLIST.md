# Checklist review đồ án P1

Tài liệu này hướng dẫn kiểm tra hệ thống hỏi đáp pháp luật tài nguyên nước trước khi nộp. Chạy mọi lệnh từ thư mục gốc repository `ViHERMES/`.

## 1. Khởi chạy hệ thống

```bash
source .venv/bin/activate
pip install -e '.[water,dev,dataset]'      # đã cài sẵn trong .venv; chạy lại nếu dùng môi trường mới
vihermes-water process                      # khoảng 5 giây
vihermes-water build-graph --force          # khoảng 1 giây
streamlit run app/streamlit_app.py          # mở http://localhost:8501
```

Kết quả mong đợi của `process`: 1.383 contexts. Văn bản 28/2023/QH15 có 86 điều, 418 khoản; 53/2024/NĐ-CP có 98 điều, 378 khoản; 54/2024/NĐ-CP có 59 điều, 210 khoản; 23/2026/NĐ-CP có 4 điều, 98 khoản; 146/2025/QH15 có 3 điều, 29 khoản. Hai cảnh báo của 23/2026/NĐ-CP (`unexpected_article_heading:50`, `quote_resync:33…`) là lỗi ngoặc kép có sẵn trong bản Công báo và đã được xử lý.

## 2. Chức năng cần kiểm tra

- [ ] `vihermes-water download` báo `present` cho 8 tệp PDF (kiểm tra SHA-256).
- [ ] `vihermes-water stats` báo 5 văn bản, 1.383 contexts, 1.412 nút, 2.492 cạnh, 27 dẫn chiếu chưa giải quyết.
- [ ] `vihermes-water build-graph` (không có `--force`) báo lỗi vì cơ sở dữ liệu đã tồn tại.
- [ ] `vihermes-water build-graph --db outputs/vihermes.db --force` báo `refusing to replace it`, và `outputs/vihermes.db` gốc không bị thay đổi.
- [ ] Trang **Hỏi đáp**: câu trả lời, nguồn văn bản, bằng chứng, thời gian truy xuất, quan hệ đồ thị, cảnh báo.
- [ ] Ô **So sánh hai phương pháp** hiển thị hai cột BM25 và Graph.
- [ ] Bấm **Tìm kiếm** với ô câu hỏi trống hiện "Vui lòng nhập câu hỏi.", không lỗi.
- [ ] Trang **Knowledge Graph**: số liệu, bảng loại quan hệ, đồ thị giữa các văn bản, đồ thị một điều luật.
- [ ] Trang **Đánh giá**: bảng 4 cấu hình, biểu đồ, cảnh báo "Kết quả tạm thời", chi tiết từng câu.
- [ ] `pytest -q` báo `49 passed, 3 skipped`.

## 3. Mười câu hỏi demo và kết quả kỳ vọng

Các câu hỏi đều có trong ô "Câu hỏi gợi ý" của giao diện. Cột kết quả kỳ vọng ghi lại đầu ra thật của hệ thống ở chế độ Graph, k = 5.

| # | Câu hỏi | Đoạn trích đầu tiên kỳ vọng | Điểm cần quan sát |
| --: | :-- | :-- | :-- |
| 1 | Giấy phép khai thác nước dưới đất có thời hạn tối đa bao nhiêu năm? | Khoản 1 Điều 54 Luật 28/2023/QH15, điểm c): "tối đa 05 năm, tối thiểu 03 năm…" | Đoạn trích thứ hai (Điều 8a do NĐ 23 bổ sung) nói về nước mặt, chỉ là tham khảo |
| 2 | Mức thu tiền cấp quyền khai thác tài nguyên nước (M) có giá trị trong khoảng bao nhiêu? | Điều 45 NĐ 54/2024: "từ 0,05% đến 2,0%" | BM25 và Graph giống nhau |
| 3 | Các hành vi bị nghiêm cấm trong lĩnh vực tài nguyên nước là gì? | Điều 8 Luật 28 (tiêu đề và các khoản đầu) | Graph ghi chú Điều 8 được sửa bởi khoản 1 Điều 10 Luật 146/2025 |
| 4 | Việc gây mưa nhân tạo phải được ai chấp thuận? | Điều 40 Luật 28: "…phải được chấp thuận của cơ quan nhà nước có thẩm quyền…" | — |
| 5 | Vùng cấm khai thác nước dưới đất được khoanh định đối với khu vực nào? | Khoản 1 Điều 33 NĐ 53/2024: "khu vực đã xảy ra sụt, lún đất…" | Graph ghi chú Điều 34 NĐ 53 bị sửa (thay cụm từ) |
| 6 | Điều kiện cấp giấy phép hành nghề khoan nước dưới đất là gì? | Khoản 21 Điều 2 NĐ 23/2026 (Điều 30 mới của NĐ 54): điều kiện về trình độ, kinh nghiệm | Graph ghi chú Điều 30 NĐ 54 bị sửa. Đoạn trích có thể gồm cả Điều 31 NĐ 54, điều đã bị bãi bỏ (hệ thống chưa nêu rõ việc bãi bỏ) |
| 7 | Thời hạn giấy phép hành nghề khoan nước dưới đất là bao lâu? | Tiêu đề và khoản 1 Điều 30 NĐ 54 gốc; đoạn trích thứ hai là khoản 3: "tối đa là 10 năm…" | Dùng ô so sánh: Graph thêm khoản 21, 23 Điều 2 NĐ 23 và ghi chú hiệu lực, BM25 không có |
| 8 | Thẩm quyền phê duyệt và cơ quan tiếp nhận, thẩm định tiền cấp quyền khai thác tài nguyên nước? | Điều 50 NĐ 54 (bản gốc ghi "Bộ Tài nguyên và Môi trường") và khoản 31 Điều 2 NĐ 23 (bản sửa ghi "Bộ trưởng Bộ Nông nghiệp và Môi trường") | So sánh hai đoạn trích; Graph có ghi chú hiệu lực |
| 9 | Khoản 5 Điều 8 Luật Tài nguyên nước về hành vi lấn, lấp sông, suối đã được sửa đổi như thế nào? | Khoản 1 Điều 10 Luật 146/2025, rồi khoản 5 Điều 8 Luật 28 | Hai đoạn khác nhau ở cụm "nhưng không có biện pháp khắc phục" |
| 10 | Giá vé xem bóng đá năm nay là bao nhiêu? | Không trả lời. "Chưa tìm thấy đoạn văn trả lời trực tiếp…" kèm 3 điều khoản tham khảo | Kiểm tra hệ thống không bịa câu trả lời (độ phủ 0,19 < 0,35) |

Các câu sau minh họa **hạn chế**, có thể dùng khi trình bày phần hạn chế:

- "Tài nguyên nước bao gồm những loại nước nào?": chọn sai đoạn (khoản 3 Điều 49 NĐ 54), dù khoản 1 Điều 2 Luật 28 đứng thứ 5 trong bằng chứng.
- "Nước dưới đất được định nghĩa như thế nào?": không tìm được khoản 4 Điều 2 Luật 28.
- "Những trường hợp nào phải nộp tiền cấp quyền khai thác tài nguyên nước?": đoạn trích đầu là Điều 43 NĐ 54 (trường hợp **không phải** nộp), vì từ "không" bị coi là từ để hỏi.

## 4. Kiểm tra trích dẫn

1. Ở trang Hỏi đáp, mở bằng chứng đầu tiên và đối chiếu đoạn trích với nội dung bằng chứng. Đoạn trích phải là chuỗi con nguyên văn (kiểm thử `test_answer_quotes_source_text_and_cites_sources` kiểm tra tự động).
2. Đối chiếu với văn bản sạch `data/water_resources/processed/<văn bản>.txt` bằng cách tìm theo "Điều N.".
3. Đối chiếu với bản gốc: bấm liên kết "Cơ sở dữ liệu văn bản Chính phủ" trong phần Nguồn văn bản, hoặc mở PDF trong `data/water_resources/raw/` theo số Công báo hiển thị.
4. Kiểm tra mã ngữ cảnh (`28/2023/QH15::article:54::clause:1`) trùng với điều, khoản ghi trong nhãn.
5. Kiểm tra ngẫu nhiên vài quan hệ trong cơ sở dữ liệu:

   ```bash
   sqlite3 outputs/water_resources/water_graph.db \
     "SELECT * FROM edges WHERE source='54/2024/NĐ-CP::article:30' AND relation='AMENDS';"
   ```

   Kết quả có `23/2026/NĐ-CP::article:2::clause:21`. Đối chiếu bằng cách mở khoản 21 Điều 2 Nghị định 23/2026 trong `processed/nd-23-2026-nd-cp.txt`.

## 5. Chạy và xác nhận benchmark

```bash
vihermes-water evaluate
```

Kết quả hiện tại (tạm thời, 0/29 câu đã xác nhận): BM25 và Graph cùng đạt Recall@5 = 0,684, Recall@10 = 0,816, MRR@10 = 0,658.

Quy trình xác nhận:

1. Mở `data/water_resources/benchmark.jsonl`. Mỗi dòng là một câu hỏi.
2. Với mỗi câu, kiểm tra:
   - câu hỏi rõ nghĩa;
   - `expected_context_ids` đúng và đủ (tra bằng `processed/*.txt`);
   - `expected_answer` đúng với văn bản;
   - đọc trường `notes` (một số câu ghi chú điều khoản liên quan cần cân nhắc).
3. Câu đạt yêu cầu: đổi `"verification_status": "needs_review"` thành `"verified"`. Câu cần sửa: sửa nội dung rồi mới đổi trạng thái. Câu không phù hợp: xóa dòng.
4. Chạy lại `vihermes-water evaluate`. Trang Đánh giá sẽ hiện thêm bảng "Chỉ các câu đã xác nhận", và `evaluation.json` có mục `summary.<cấu hình>.verified`.
5. Cập nhật Bảng 4.4–4.5 và mục 4.8 trong `reports/BAO_CAO_P1.md` nếu số liệu thay đổi.

## 6. Kiểm tra Knowledge Graph

- `vihermes-water stats` in số liệu theo văn bản, theo loại quan hệ, quan hệ giữa các văn bản và danh sách 24 nút giữ chỗ.
- Trang Knowledge Graph → "Khám phá một điều luật": chọn *Nghị định 54/2024/NĐ-CP*, *Điều 30*. Đồ thị hiển thị cạnh "Sửa đổi, bổ sung" tới các khoản 21, 23 Điều 2 NĐ 23 và cạnh "Dẫn chiếu".
- Chọn *Luật Tài nguyên nước 2023*, *Điều 8*: có cạnh sửa đổi tới khoản 1 Điều 10 Luật 146.
- Danh sách "Dẫn chiếu chưa giải quyết" gồm văn bản ngoài kho (ví dụ `08/2017/QH14` Luật Thủy lợi), các điều 8a–8d, 10a (chỉ tồn tại trong nội dung trích dẫn của NĐ 23) và Điều 8, 14 Luật 146 (không được trích).

## 7. Hạn chế còn tồn tại

1. Truy xuất và chọn đoạn trả lời dựa trên từ vựng, nên nhạy với cách diễn đạt câu hỏi.
2. Graph không cải thiện Recall/MRR trên benchmark hiện tại; giá trị chính là bổ sung văn bản sửa đổi và ghi chú hiệu lực.
3. Hệ thống không tạo văn bản hợp nhất và không đánh dấu riêng điều khoản đã bị bãi bỏ.
4. Không xử lý phụ lục (mẫu đơn, bảng hệ số K) và văn bản scan; QĐ 274/QĐ-TTg bị loại.
5. Luật 146/2025/QH15 chỉ được trích Điều 10, 16, 17.
6. Benchmark do một người soạn, chưa được xác nhận.
7. Báo cáo còn để trống họ tên học viên và giảng viên hướng dẫn.
