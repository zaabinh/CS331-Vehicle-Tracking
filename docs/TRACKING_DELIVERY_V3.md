# Sử dụng bộ kết quả tracking v3 đã phát hành

Thư mục đích: [Drive / outputs / ucmc_tracking](https://drive.google.com/drive/folders/1mKdivVYoxMlIaLvE9ljPvOmMmak5eIOl).
Các gói v3 dùng tên `ua_detrac_ucmc_v3_<role>_<commit>.zip`; commit, SHA256,
kích thước và URL từng file được ghi trong delivery receipt/SHA256SUMS trên Drive.
Các file v1 đã có vẫn được giữ, không bị ghi đè.

| Role | Nội dung và mục đích |
| --- | --- |
| `trackeval_val_test` | CSV class-aware, MOT, GT/metadata cần cho scoring và metric đã lưu của 12 val + 40 test; bản MOT tách class, compact timeline và mapping frame. |
| `analysis` | Báo cáo, hình/GIF, bảng so sánh val, config/metric từng lượt, các summary/lock của test; prediction control và winner để mở notebook val. |
| `test_error_details` | Events, frame/track diagnostics, FN candidate và detector coverage của đủ 40 test sequences. |
| `camera_proposals` | Registry đã duyệt ban đầu, registry test mới, nguồn proposal và preview đầu/giữa/cuối; 30 camera test vẫn là draft. |
| `code` | Mã nguồn repo và source UCMCTrack cần thiết, đã có compatibility patch, không có `.git`, raw dataset hay detections demo MOT của upstream. |
| `executed_notebook_v2` | Bản notebook v2 đầy đủ output trước khi làm gọn bản publish; dùng xem lịch sử, không phải notebook đã chạy test v3. |

Giải nén các gói vào **cùng project root** để giữ cấu trúc `docs/`, `src/`,
`outputs/tracking/...` và các đường dẫn tương đối trong báo cáo. Kiểm tra SHA256
sau download. Source trên GitHub không chứa các CSV/GT nằm trong `outputs`;
muốn mở các link artifact trong report cần tải gói tương ứng. Ảnh report đã được
đưa vào `docs/assets` để vẫn hiển thị trên GitHub.

## Chấm lại output đã có

Không cần tải lại ảnh, YOLO weights hay chạy detector/tracker để scoring:

```powershell
python -m pip install -r requirements-tracking.txt
python scripts/setup_ucmc.py
python scripts/tracking_pipeline.py evaluate --dataset outputs/tracking/delivery_v3/trackeval_bundle/evaluation_input --output outputs/tracking/delivery_v3/trackeval_bundle/runs/test
```

Đổi `runs/test` thành `runs/val` để chấm val. Để kiểm tra cả CSV và dữ liệu MOT
tách class, chạy `python scripts/verify_tracking_bundle.py`. Export gói mới bằng
`export_tracking_bundle.py` cần thư mục output mới, tránh ghi đè artifact.

Theo README của `trackeval_bundle` nếu đưa dữ liệu sang TrackEval dùng chung:
class gốc car=0 trong CSV; class=1 trong GT MOT chỉ là placeholder bên trong thư
mục riêng car/bus/van/others. Dùng DO_PREPROC=False cho bản đã lọc ignore,
CLEAR/Identity IoU 0,7 và HOTA dải chuẩn. Mọi tracker so sánh phải dùng cùng
protocol và CLEAR patch được ghi trong [UCMC_LOCAL_PATCHES.md](UCMC_LOCAL_PATCHES.md).
Không lấy trung bình bốn HOTA để thay cho aggregate class-aware.

## Đọc kết quả và các giới hạn

- [Val v3](TRACKING_REFINEMENT_V3_REPORT.md): HOTA 79,3543→79,9241, IDS 134→46;
  có cả regressions và vị trí code/lý do thay đổi.
- [Test v3](TRACKING_TEST_V3_REPORT.md): HOTA 72,8970, IDF1 80,5637, đầy đủ
  difficulty, per-class/per-sequence, FN/FP/IDS và ca trực quan.
- [Audit report](TRACKING_REPORT_AUDIT.md): phạm vi của các báo cáo cũ và các hạng
  mục vẫn chưa nghiệm thu, gồm KRT và review camera đầy đủ.

Test dùng một cấu hình chọn trên val, không tuning theo test. Kết quả vẫn
**provisional về calibration** do 30/40 camera test chưa được người dùng review;
kích thước mặt đường là prior. Voting dùng toàn track nên là offline. Đây là
protocol v2 của đồ án, không phải benchmark PR-MOTA nguyên bản.

Không resume tracking trực tiếp trên gói portable chỉ phục vụ evaluation.
Khi chạy lại tracking trên máy khác hoặc code/input đã đổi, dùng output mới;
fingerprint lưu path/code/environment để ngăn tái sử dụng nhầm kết quả.
