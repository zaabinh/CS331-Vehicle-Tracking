# Rà soát báo cáo tracking và truy vết thay đổi

Ngày: 28/11/2026. Phạm vi rà soát là các báo cáo thực nghiệm tracking v1/v2/v3,
hướng dẫn chạy và ghi chú patch upstream; không phải audit toàn bộ báo cáo detection.

**Các báo cáo trước đủ bằng chứng cho phạm vi lịch sử của chúng, nhưng chưa đủ
để mô tả hệ thống hiện tại.** Đã bổ sung liên kết giữa các mốc, bảng định vị code
cho v2/v3 và ghi rõ phần đã sửa sau mỗi mốc. Không thay số liệu lịch sử bằng điểm mới.

| Tài liệu | Đã có | Thiếu/giới hạn được xử lý trong đợt này |
| --- | --- | --- |
| [Local execution ban đầu](../outputs/tracking/LOCAL_EXECUTION_REPORT.md) | Smoke 3 sequence, baseline 12 sequence, thời gian, camera chưa review, resume và kiểm thử | Là feasibility report với protocol v1, chưa tuning/test/KRT. Giữ nguyên artifact; không dùng như mô tả trạng thái hiện tại. |
| [Local tuning v1](../outputs/tracking/tuning_h_approved_v1/LOCAL_TUNING_REPORT.md) | 15 lượt, cấu hình thắng, count, thời gian/cache CMC, các giới hạn | Là tuning summary protocol v1, không có phân tích lỗi sâu hay bản đồ code. Phần này được bổ sung bởi error analysis, v2/v3 và bảng định vị bên dưới; không sửa điểm lịch sử. |
| [Phân tích lỗi v1](TRACKING_ERROR_ANALYSIS_REPORT.md) | Run ID, protocol v1, ảnh/events, đối chiếu count, ví dụ FP/FN/class/IDS và giả thuyết có giới hạn | Thêm cảnh báo là mốc lịch sử và đường dẫn v2/v3. Nghi vấn ignore/lifecycle không còn được trình bày như lỗi chưa xử lý ở bản mới. Không đổi điểm v1. |
| [Validation v2](TRACKING_VAL_V2_REPORT.md) | Bằng chứng sửa ignore, so cùng prediction v1/v2, 19 cấu hình/biến thể, lifecycle regression, trace biên, checksum và cách chạy | Bổ sung bảng vị trí hàm/bản vá và động cơ; dẫn tới v3. Các FN buckets trong báo cáo này thuộc run tham chiếu 14.401 FN, không phải nhánh v3 có 13.870 FN. |
| [Refinement v3](TRACKING_REFINEMENT_V3_REPORT.md) | 1 đối chứng tái lập + 8 biến thể full-val, thay đổi code/lý do, số tổng/per-class/per-sequence, regression, thời gian và artifact | Bổ sung đầy đủ cả 3 sequence giảm HOTA, FP tăng, tính offline và giới hạn vật lý camera; không chỉ báo kết quả tốt. |
| [Hướng dẫn](TRACKING_USAGE_GUIDE.md) | Local/Colab, calibration, adapter/tracker/evaluation, provenance/resume | Bổ sung tùy chọn biên/voting, chạy test đã khóa, difficulty và gói TrackEval dùng chung; cấu hình v2 được giữ như ví dụ lịch sử. |
| [Patch upstream](UCMC_LOCAL_PATCHES.md) | Các patch tương thích NumPy/TrackEval trong clone | Phân biệt patch clone với wrapper/heuristic trong `src/tracking`; không tuyên bố đã thay Kalman upstream. |
| [Test v3](TRACKING_TEST_V3_REPORT.md) | Báo cáo riêng cho lần đánh giá test sau khi khóa val | Phải ghi rõ 30 calibration draft, lỗi danh sách difficulty Tracking và giới hạn so với PR-MOTA. Không dùng test để chọn/tuning cấu hình. |

## Tóm tắt các thay đổi kể từ mốc v1

| Phần | Định vị | Thay đổi và lý do |
| --- | --- | --- |
| Protocol ignore | [`evaluation.py`](../src/tracking/evaluation.py#L40) | Lọc đối xứng GT/prediction bằng cùng union ignore, version=2; khắc phục FN do bộ lọc bất đối xứng. |
| Lifecycle | [`lifecycle.py`](../src/tracking/lifecycle.py#L4) | Chuyển unmatched Confirmed sang Coasted và hết hạn đúng khi thiếu low-score pairs; tùy chọn `fix_coasting`. |
| Measurement ở biên | [`detections.py`](../src/tracking/detections.py#L152) | Sàn noise theo trục ảnh bị cắt; không sửa bbox hoặc tự dựng detection. |
| Association và class | [`runner.py`](../src/tracking/runner.py#L306), [`voting`](../src/tracking/runner.py#L510) | Tùy chọn tracker chung + class theo tổng confidence toàn track để chịu class flicker; xử lý offline. |
| Cấu hình và kiểm tra | [`common.py`](../src/tracking/common.py#L85) | Kiểm tra các tùy chọn mới; mặc định giữ four-class separate/upstream noise. |
| Chẩn đoán | [`tracking_cases.py`](../src/analysis/tracking_cases.py#L23), [`detection_audit.py`](../src/analysis/detection_audit.py) | Phân tích matching, coverage và lỗi từ output đã có; đối chiếu count với evaluator. |
| Thực nghiệm val | [`revalidate_tracking.py`](../scripts/revalidate_tracking.py), [`refine_tracking.py`](../scripts/refine_tracking.py#L26) | Chấm lại cùng protocol, tái lập, thử có đối chứng và lưu cả các cấu hình kém hơn. |
| Difficulty | [`subsets.py`](../src/tracking/subsets.py#L14) | Lộ rõ duplicate/missing trong danh sách gốc; tổng hợp metric internals bằng TrackEval thay vì trung bình điểm video. |
| Calibration test | [`test_camera_drafts.json`](../configs/tracking/test_camera_drafts.json), [`prepare_test_cameras.py`](../scripts/prepare_test_cameras.py#L21) | Bổ sung proposal cho 30 test sequences chưa có ma trận; giữ trạng thái draft, không giả mạo review hoặc sửa registry đã duyệt. |
| Khóa test | [`run_tracking_test.py`](../scripts/run_tracking_test.py#L26) | Ghi selection/input/protocol lock trước tracking; chạy một cấu hình val-selected cho đủ 40 test sequences. |
| Export dùng chung | [`export_tracking_bundle.py`](../scripts/export_tracking_bundle.py) | Giữ CSV class-aware/timeline gốc và cung cấp bản MOT tách class, compact frame, đã lọc ignore cho loader dùng chung. |

## Các kiểm tra và giới hạn cần đọc cùng report

- Đối chứng v3 khớp prediction v2 từng byte ở cả 12 val sequences.
- Val v3: count chẩn đoán khớp evaluator cho 24 cặp sequence/run; 129.199 output
  observations của nhánh chọn có bbox/confidence trong detection nguồn; 2.249
  observations đổi class. Đối chiếu tọa độ có làm tròn 6 chữ số.
- Checksum val kiểm tra 30 input/reference files không đổi. Raw/canonical không bị
  chỉnh để làm tăng score; không khẳng định đã hash lại toàn bộ ảnh raw.
- Các regression mới bao phủ covariance theo trục, class flicker/ID, timeline
  rỗng, resume, subset combine, lỗi difficulty và export MOT/class 0.
- Camera val có review của người dùng nhưng kích thước mặt đường vẫn là prior.
  Chỉ 10/40 test cameras đã có review; 30 mới là proposal. Kết quả test phải ghi
  là provisional về hình học camera, dù đã chạy đủ 40 sequences.
- Chia difficulty Detection và Tracking trong toolkit khác nhau; Tracking có
  `MVI_40771` lặp và thiếu `MVI_40711`. Không được tự thay duplicate bằng sequence
  thiếu rồi gọi đó là danh sách chính thức đã sửa.
- Val là development result; test chỉ đánh giá sau khóa. Offline voting không
  được mô tả là realtime. Protocol v2 của đồ án không phải PR-MOTA nguyên bản.

Các bảng, ảnh, mã nguồn, config và receipt phát hành là thành phần bổ sung cho
report; một đoạn Markdown riêng trên Drive có thể không mở được ảnh tương đối.
Gói archive giữ nguyên cấu trúc thư mục để xem đầy đủ các bằng chứng đó.

Ảnh đang được dùng trong các report được sao chép vào `docs/assets/tracking_reports`
để hiển thị trên GitHub; bản artifact gốc vẫn được giữ. Notebook case analysis có
64.214.117 byte ở bản đã thực thi được lưu nguyên bản tại
`outputs/tracking/validation_v2/notebook_executed_v2.ipynb`, checksum trong
`delivery_v3/notebook_preservation.json`. Bản notebook publish chỉ xóa output và
execution count/widget state; source/Markdown của các cell giữ nguyên.

Ở mốc v3 đầu tiên, chưa có review đủ 100 sequences. Người dùng sau đó đã review
toàn bộ homography; kiểm tra ngày 28/11/2026 xác nhận đủ **100/100 reviewed
records hợp lệ**, review hash và kích thước ảnh khớp, A/H và bốn góc nhất quán.
Snapshot mới là `registry_reviewed_h100_7f16c297.json`, tách khỏi snapshot cũ.
Xem [báo cáo cuối với homography mới](TRACKING_FINAL_REVIEWED_H100_REPORT.md) và
[đối chứng CMC ở calibration cũ](TRACKING_CMC_V3_REPORT.md). Các con số 30 camera
test draft phía trên mô tả mốc lịch sử, không phải trạng thái snapshot mới.

Lượt reviewed mới đã hoàn tất ba nhánh trên 12 val và hai nhánh trên 40 test:
V3 + CMC được chọn bằng val, test HOTA 73,1506 so với upstream 73,1033;
IDF1/MOTA thấp hơn upstream và IDS tăng so với camera cũ. Báo cáo cuối giữ đủ
các hồi quy này, phân nhóm difficulty hai hệ, ví dụ IDS và FN audit khớp 40/40
sequence. Gói evaluator dùng chung khớp 210/210 phép đối chiếu; 59 source files
không đổi trong đợt chạy và 33 regression tests phù hợp passed.

Các hạng mục còn **chưa được nghiệm thu đầy đủ**: chưa chạy đối chứng H so với
KRT vì chưa có calibration KRT đã duyệt; chưa kiểm tra frontend Colab thực trong
đợt này. Review homography không tự chứng minh kích thước prior là số đo thực
địa. Những giới hạn này không được suy thành hoàn thành chỉ vì có metric.
