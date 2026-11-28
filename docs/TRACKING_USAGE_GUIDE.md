# Hướng dẫn sử dụng UCMCTrack cho UA-DETRAC

Tài liệu dành cho thành viên Tracking và người phụ trách calibration camera.
Pipeline dùng CSV detection đã xuất từ YOLO, chạy tracker và đánh giá trên
validation/test. Có chế độ bốn tracker theo class hoặc association chung kèm
class voting offline, tùy cấu hình được chọn trên val.

## Lượt cuối sau khi review đủ 100 homography

Registry live `outputs/tracking/calibration/registry.json` đã có 100 homography
reviewed. Lượt cuối dùng snapshot cố định, không dùng lại registry draft hoặc
snapshot v1 trong các lệnh lịch sử bên dưới:

```powershell
python scripts/finalize_reviewed_tracking.py --calibration outputs/tracking/calibration/registry_reviewed_h100_7f16c297.json
```

Lệnh chạy ba đối chứng trên val với geometry mới, chọn cấu hình bằng val, khóa
lựa chọn rồi chạy test. Output ở `outputs/tracking/final_reviewed_h100`;
`val_selection.json` và `best_config.yaml` ghi cấu hình chọn, `completion.json`
chỉ tới test run tương ứng sau khi toàn bộ pipeline hoàn tất. Không mặc định
v3 hoặc CMC phải thắng upstream. Đọc
[`TRACKING_FINAL_REVIEWED_H100_REPORT.md`](TRACKING_FINAL_REVIEWED_H100_REPORT.md).

Các lệnh `run_tracking_test.py` và `compare_tracking_cmc.py` phía dưới phục vụ
tái lập các mốc cũ, có snapshot calibration riêng; chúng không tự nhận review
mới trong registry live. Muốn dùng một snapshot review mới hơn, đóng băng file
đó và truyền `--calibration` cùng một `--output` mới cho lệnh final phía trên.

Các artifact trong `outputs` không được đưa vào GitHub. Với máy mới, lấy gói
`code` hoặc `analysis_and_cameras` tại
[Drive của lượt cuối](https://drive.google.com/drive/folders/1_GFS4c8bxnWtCMJBxtj5RCmp5r4fcYZH),
giải nén giữ nguyên đường dẫn tương đối trong repo. Script final cần snapshot
`outputs/tracking/calibration/registry_reviewed_h100_7f16c297.json` và cấu hình
số học gốc `outputs/tracking/cmc_v3/val_selection.json`. Dataset v1.1, ảnh và CSV
detection vẫn là đầu vào riêng, không có trong các gói code/output này.
Nếu chỉ chấm lại kết quả đã lưu, dùng gói `trackeval_selected_val_test`; không
cần ảnh, chạy YOLO hay chạy tracker lại.

`best_config.yaml` ghi `fix_coasting=True` trong comment để lưu bối cảnh;
comment YAML không tự bật tùy chọn này. Script final truyền cờ đó riêng cho
runner; khi tự gọi CLI tracking phải truyền `--fix-coasting` cho cấu hình V3.

## 1. Bắt đầu từ đâu?

Sau khi clone từ GitHub, khởi tạo dependency đã ghim commit và áp dụng patch:

```powershell
git submodule update --init --recursive
python scripts/setup_ucmc.py
```

Lệnh setup có thể chạy lại; nếu patch đã áp dụng thì không ghi đè. Sau khi
patch, Git sẽ báo submodule có thay đổi so với upstream; đây là trạng thái
dự kiến. Các thay đổi được lưu ở `patches/ucmc_compat.patch` trong repo chính.

### Registry của đợt tuning cũ: chỉ để tái lập

Ở đợt đầu, sau khi chủ đồ án xác nhận đã duyệt ảnh, 30 đề xuất homography đã được ghi
nhận duyệt, gồm đủ 12 sequence val. Bản cố định cho thí nghiệm nằm tại
`outputs/tracking/calibration/registry_approved_h_v1.json`; hồ sơ xác nhận và
checksum nằm ở `approval_h_v1.json` cùng thư mục. Đây là snapshot lịch sử;
lượt cuối dùng snapshot 100 reviewed ở đầu tài liệu. Kích thước mặt đường vẫn là prior được ghi
nguồn; việc duyệt ảnh không biến chúng thành số đo thực địa.

```powershell
.\.venv-tracking\Scripts\python.exe -X utf8 scripts/tracking_pipeline.py tune --method homography --calibration outputs/tracking/calibration/registry_approved_h_v1.json --output outputs/tracking/tuning_h_approved_v1 --max-trials 15 --resume
```

Theo dõi `outputs/tracking/verification/local_approved_tuning.log` và
`outputs/tracking/tuning_h_approved_v1/leaderboard.csv`. Không sửa registry,
input hoặc code tracking giữa các lần resume của cùng thí nghiệm. KRT chưa
có đủ ma trận, nên đây là tuning nhánh homography, chưa phải so sánh H/KRT.

Sau khi hoàn tất, xem `LOCAL_TUNING_REPORT.md` và các bảng
`comparison_per_sequence.csv`, `comparison_per_class.csv` trong thư mục
thí nghiệm. File `best_tracker_config.yaml` chứa riêng tham số tracker để
truyền vào `--config`. File `best_config.json` là báo cáo có cả metric và
đường dẫn run, không dùng trực tiếp thay cho YAML cấu hình.

### Chạy thử hiệu năng với camera nháp

Lệnh `run` có tùy chọn `--allow-unreviewed` để đo tốc độ với ma trận nháp
đã có. Tùy chọn này không thay đổi trạng thái duyệt và vẫn kiểm tra ma trận,
độ phân giải, detections và timeline. Manifest và báo cáo đánh giá ghi
`calibration_reviewed=false` nếu camera chưa được duyệt.

```powershell
.\.venv-tracking\Scripts\python.exe -X utf8 scripts/tracking_pipeline.py run --split val --allow-unreviewed --calibration outputs/tracking/local_experiment/draft_registry.json --output outputs/tracking/local_val_draft_baseline --resume
.\.venv-tracking\Scripts\python.exe -X utf8 scripts/tracking_pipeline.py evaluate --output outputs/tracking/local_val_draft_baseline
```

Registry thử nghiệm chứa sáu đề xuất bổ sung cho val, dựa trên điểm mặt đường
chọn thủ công và kích thước giả định chưa đo thực tế. Registry chính không bị
ghi đè. Preview nằm trong `outputs/tracking/local_experiment/camera_previews`.
Kết quả là baseline phát triển tạm thời. Lệnh `smoke` so sánh H/KRT và lệnh
`tune` vẫn yêu cầu calibration được duyệt; không dùng tùy chọn này cho chúng.

| Công việc | Notebook |
| --- | --- |
| Chọn bốn góc mặt đường, tính camera và duyệt bản nháp | [00_camera_calibration.ipynb](../notebooks/tracking/00_camera_calibration.ipynb) |
| Chạy thử và so sánh Homography với KRT | [01_ucmc_smoke_and_evaluation.ipynb](../notebooks/tracking/01_ucmc_smoke_and_evaluation.ipynb) |
| Chạy validation, xem leaderboard và tuning | [02_ucmc_validation_and_tuning.ipynb](../notebooks/tracking/02_ucmc_validation_and_tuning.ipynb) |

Thứ tự thực hiện: **chuẩn bị môi trường → duyệt camera smoke → chạy smoke →
chọn phương pháp → duyệt đủ 12 camera val → chạy baseline và tuning**.

Gói đề xuất 24 view nằm tại
[trang review](../outputs/tracking/calibration/proposals_24_v1/review.html).
Mỗi view có ảnh bốn góc, ma trận và preview đầu/giữa/cuối.
Đây là 24 view đại diện được đề xuất; chưa phải mapping 24 camera chính thức.
Chiều rộng 3,5 m và chiều dài từng vùng là giả định chưa đo thực. Chưa tự áp
chung các ma trận này cho 76 sequences còn lại.

## 2. Cài đặt và đường dẫn

### Máy local Windows

Mở terminal tại thư mục gốc repository:

~~~powershell
python -m venv .venv-tracking
.\.venv-tracking\Scripts\python.exe -m pip install -r requirements-tracking.txt
.\.venv-tracking\Scripts\python.exe -m pip install ipykernel
.\.venv-tracking\Scripts\python.exe -m ipykernel install --user --name ua-detrac-tracking
~~~

Trong Jupyter hoặc VS Code, chọn kernel **ua-detrac-tracking**.
Các lệnh CLI dưới đây dùng `python`; cần dùng Python của môi trường tracking.
Nếu chưa kích hoạt môi trường, thay bằng
`.\.venv-tracking\Scripts\python.exe`.

| Dữ liệu | Đường dẫn mặc định |
| --- | --- |
| Dataset đã chuẩn bị | `outputs/ua_detrac/dataset_v1.1` |
| CSV detection và cấu hình handoff | `outputs/detections_full_yolo26l_E17` |
| Registry camera | `outputs/tracking/calibration/registry.json` |
| Cấu hình baseline | `configs/tracking/baseline.yaml` |
| Code UCMCTrack gốc | `UCMC/UCMCTrack` |
| Các lần chạy và báo cáo | `outputs/tracking` |

Không cần GPU để chạy tracking từ CSV. Dataset có thể dùng trực tiếp, không cần
nén; ảnh tại `tracking/<sequence>/img1` phải đọc được. Nếu chuyển máy làm hỏng
symlink, cần khôi phục đường dẫn ảnh trước khi tạo preview hoặc chạy CMC.

### Google Colab

Đặt các biến sau trước cell thiết lập môi trường nếu đường dẫn khác mặc định:

~~~python
import os

os.environ["UA_DETRAC_PROJECT"] = "/content/drive/MyDrive/CS331/CS331-Vehicle-Tracking"
os.environ["UA_DETRAC_DATASET"] = "/content/ua_detrac/dataset_v1.1"
os.environ["UA_DETRAC_DETECTIONS"] = "/content/drive/MyDrive/CS331/CS331-Vehicle-Tracking/outputs/detections_full_yolo26l_E17"
os.environ["UA_DETRAC_CALIBRATION"] = "/content/drive/MyDrive/CS331/CS331-Vehicle-Tracking/outputs/tracking/calibration/registry.json"
~~~

Notebook tự mount Drive và bật custom widget manager trên Colab. Chạy cell
Dependencies trong đúng kernel đang sử dụng. Đọc ảnh từ ổ local Colab sẽ phù hợp
hơn cho các vòng lặp đọc ảnh liên tục, đặc biệt là CMC.

Nếu click ảnh không phản hồi, kiểm tra đã cài `ipyevents` và `ipywidgets`
trong kernel đó, tải lại trang notebook rồi chạy lại cell tạo widget.
Sau khi cập nhật code, cell widget dùng `importlib.reload(review)` để nạp lại module.
Giao diện cần được kiểm tra trực tiếp trên frontend local/Colab; kiểm thử Python
mô phỏng sự kiện chuột không thay thế bước này.

## 3. Chọn bốn điểm: luồng đơn giản gồm năm bước

Mở notebook `00_camera_calibration.ipynb` và chạy cell tạo giao diện.
Luồng mặc định là Homography. KRT, JSON và chia sẻ camera nằm trong các mục
**Nâng cao** được thu gọn.

### Bước 1 — Chọn video và ảnh

Chọn video, rồi chọn ảnh đầu, giữa hoặc cuối làm ảnh tham chiếu.
Đổi video hoặc ảnh sẽ xóa các điểm đang chọn để tránh dùng tọa độ của ảnh cũ.

Nếu dùng gói 24 bản nháp, chọn đúng video và bấm **Nạp bản đã lưu**.
Các điểm, kích thước và nguồn giả định sẽ được điền sẵn. Nạp bản lưu không tự
đánh dấu camera là đã duyệt.

### Bước 2 — Bấm bốn góc theo đường bao

Chọn một đoạn đường tương đối phẳng, có thể xem như hình chữ nhật ngoài thực tế.
Trên ảnh phối cảnh, vùng đó thường trông như hình thang.

~~~text
        4 -------- 3
        |          |
        |          |  Chiều dài: cạnh 2 → 3
        |          |
        1 -------- 2
           Chiều rộng: cạnh 1 → 2
~~~

Sơ đồ chỉ minh họa thứ tự. Vùng đường trong ảnh có thể xoay theo hướng khác.

1. Bấm **điểm 1** tại một đầu của cạnh ngang làn đường.
2. Bấm **điểm 2** tại đầu còn lại của cùng cạnh rộng.
3. Đi dọc làn đường từ điểm 2 để bấm **điểm 3**.
4. Bấm **điểm 4** để khép vùng hình chữ nhật.

Cạnh **xanh 1→2** tương ứng chiều rộng. Cạnh **cam 2→3** tương ứng chiều dài.
Không bấm chéo qua vùng chọn, vì bốn điểm phải đi theo đường bao.

Đặt điểm trên mặt đường; tránh đặt lên xe, cột, mặt trên lan can hoặc vật thể
cao hơn mặt đường. Có thể chọn một làn hoặc nhiều làn nếu biết hai kích thước.
Vùng tham chiếu dùng để tính phép chiếu, không giới hạn tracking chỉ trong vùng đó.
Độ tin cậy giảm khi chiếu sang phần đường khác độ dốc hoặc khác mặt phẳng.

- **Bỏ điểm cuối**: xóa điểm vừa chọn để bấm lại.
- **Chọn lại từ đầu**: xóa cả bốn điểm.
- Khi đủ bốn điểm, click tiếp không ghi đè lựa chọn.
- Tọa độ lấy theo pixel ảnh gốc, kể cả khi ảnh hiển thị thu nhỏ.
- Có thể mở **Nâng cao: tọa độ JSON và lưới pixel** để sửa số trực tiếp.

### Bước 3 — Nhập hai kích thước

Nhập **Rộng 1→2 (m)** và **Dài 2→3 (m)**, đều theo mét.
Chiều rộng mặc định 3,5 m chỉ là gợi ý. Chiều dài bắt đầu bằng 0 để yêu cầu nhập rõ.

Phải có căn cứ riêng cho cả hai kích thước. Không suy chiều dài chỉ từ chiều rộng
làn; không lấy bề ngang bbox xe làm chiều rộng mặt đất. Nếu chưa đo, ghi rõ
“giả định chưa xác minh” trong **Nguồn kích thước** và lưu nháp.

### Bước 4 — Xem thử phép chiếu

Nút **Xem thử phép chiếu** bật khi đủ bốn điểm và hai kích thước dương.
Điểm trùng, thứ tự tạo vùng tự cắt hoặc hình học suy biến sẽ bị hàm calibration từ chối.

Preview gồm ảnh đầu, giữa và cuối, với lưới chiếu và ảnh mặt đường được nắn.
Nếu cung cấp CSV detection, các chấm đỏ biểu diễn đáy giữa bbox.

Kiểm tra lưới có bám hướng đường, vùng nắn có hợp lý và góc nhìn có thay đổi
giữa ba ảnh không. Fit đúng bốn điểm về mặt toán học không chứng minh kích thước
giả định ngoài thực tế là đúng. Camera rung có thể cần CMC hoặc xử lý riêng.

### Bước 5 — Lưu nháp hoặc duyệt

**Lưu bản nháp** dùng khi chưa chắc chắn. Bản nháp không được đưa vào tracking chính thức.

Để bật **Lưu và xác nhận đã duyệt**, cần:

- Đã tạo preview cho hình học hiện tại.
- Có tên người duyệt và nguồn kích thước.
- Đánh dấu đã kiểm tra điểm, kích thước và ba ảnh preview.

Sửa điểm, kích thước hoặc thông số camera sẽ hủy preview hiện tại và yêu cầu
xem lại. Sửa tên, ghi chú hoặc nguồn kích thước giữ preview, nhưng bỏ dấu xác nhận
để người dùng xác nhận lại. Code cũng kiểm tra điều kiện duyệt ở backend.

JSON dùng `units='m'`. Với KRT, file TXT xuất cho upstream đổi translation
từ mét sang millimetre; không tự nhân 1000 vào ma trận Homography.

### Khi nào dùng phần Nâng cao?

**KRT:** chọn phương pháp KRT, nhập focal giả định hoặc hai cặp đường tương ứng
hai hướng vuông góc để ước lượng vanishing points. VP JSON có cấu trúc
`[2 hướng][2 đoạn][2 đầu mút][x,y]`. Mô hình khởi tạo principal point ở tâm ảnh,
square pixels, zero skew và không méo ống kính. Fit pose yêu cầu độ sâu dương;
slider dùng để chỉnh pose. Các giả định vẫn cần ghi nguồn.

**Chia sẻ camera:** chỉ dùng chung khi góc nhìn, crop, zoom và độ phân giải phù hợp.
Một bản riêng của sequence được ưu tiên hơn bản nhóm. Nhóm có camera tham chiếu
đã duyệt vẫn cần xác nhận từng thành viên. Thay ma trận nhóm làm mất hiệu lực
xác nhận gắn với ma trận cũ.

## 4. Chạy từ CLI

Chạy các lệnh ở thư mục gốc repository. Có thể ghi đè đường dẫn bằng
`--dataset`, `--detections`, `--calibration`, `--upstream` và `--output`.

### Tạo inventory và kiểm tra camera

~~~text
python scripts/tracking_pipeline.py prepare-camera
python scripts/tracking_pipeline.py status --method homography --split val
~~~

Inventory tạo contact sheets đầu/giữa/cuối, gợi ý cảnh tương tự và báo cáo trạng thái.
Gợi ý giống ảnh không tự xác nhận cùng camera. Lệnh `status` trả exit code 2
nếu còn camera chờ duyệt.

Cấu hình chọn điểm của gói 24 view ở
[configs/tracking/camera_proposals_24.json](../configs/tracking/camera_proposals_24.json).
Tạo lại gói output bằng:

~~~text
python scripts/propose_camera_homographies.py
~~~

Thêm `--install` để đưa bản đề xuất vào registry đang dùng. Script bảo vệ bản
calibration đã duyệt hoặc bản được tạo độc lập khỏi việc ghi đè; có backup registry.

### Adapter detection

Thành viên Detection bàn giao mỗi file `<sequence>.csv` với cột:

~~~text
frame_id,class_id,confidence,x1,y1,x2,y2
~~~

Kèm `detection_handoff_config.json`, trong đó mapping là:
`0=car, 1=bus, 2=van, 3=others`.

~~~text
python scripts/tracking_pipeline.py adapt --split all --output outputs/tracking/adapted_yolo26l_E17
~~~

TXT đầu ra có mười cột:

~~~text
frame_id,det_id,x,y,w,h,score,class_id,-1,-1
~~~

Adapter giữ frame 1-based, class, confidence và bbox; không chạy lại NMS.
`det_id` được tạo từ thứ tự dòng gốc, không phải ID quỹ đạo.
Runner cũng chuyển CSV trực tiếp và cache nội bộ, nên không bắt buộc xuất TXT trước.

Ngưỡng xuất detector hiện là 0,05. Không sweep confidence thấp hơn mức này;
detection đã bị loại khi xuất không thể khôi phục ở bước tracking.

### Smoke: so sánh hai phương pháp camera

Duyệt cả Homography và KRT cho `MVI_20012`, `MVI_39811`, `MVI_63521`, rồi chạy:

~~~text
python scripts/tracking_pipeline.py smoke --output outputs/tracking/smoke --resume
~~~

Mỗi nhánh chạy đủ timeline ba sequence. Đọc `camera_comparison.csv`, video
overlay và `selected_method_recommendation.json`.
Chọn HOTA tổng hợp cao hơn, rồi IDF1; hòa hoàn toàn chọn Homography.
`runtime_estimate.json` ngoại suy từ thời gian smoke thực đo, không bảo đảm
thời gian CMC hay evaluation.

Sau đó duyệt và khóa phương pháp được chọn trên **đủ 12 sequences val**.

### Baseline rồi tuning trong cùng một experiment

~~~text
python scripts/tracking_pipeline.py tune --method homography --output outputs/tracking/val_tuning --max-trials 1 --resume
python scripts/tracking_pipeline.py tune --method homography --output outputs/tracking/val_tuning --max-trials 15 --resume
~~~

Đổi `homography` thành `krt` nếu chọn KRT.
Lượt một chạy baseline; lượt hai dùng lại baseline có cùng fingerprint.

| Nhóm | Số slot | Cấu hình |
| --- | ---: | --- |
| Baseline | 1 | conf=0.10, high_score=0.60, a1=a2=10, wx=wy=0.1, vmax=0.5, max_age=30, fps=25 |
| Biến thể độc lập | 9 | conf=0.05/0.20; high_score=0.40; a1=a2=20/40; wx=wy=1/10; vmax=15; max_age=60 |
| Ghép tham số | 1 | Ghép biến thể cải thiện HOTA tốt nhất theo mỗi nhóm tham số |
| Ablation | 4 | HP riêng, interpolation riêng, CMC riêng, rồi ghép các bước có cải thiện |

Tối đa 15 slot, có thể ít hơn 15 cấu hình duy nhất vì cấu hình trùng được dùng cache.
Mỗi cấu hình áp dụng chung cho cả 12 val sequences và cả bốn class.
Xếp hạng theo HOTA, IDF1, ít IDS hơn, rồi runtime thấp hơn.
Runtime cache là thời gian gốc đã đo, không phải thời gian kiểm tra cache.
Không dùng test để tuning.

Để chạy cấu hình riêng ngoài lịch tuning:

~~~text
python scripts/tracking_pipeline.py run --method homography --config configs/tracking/baseline.yaml --output outputs/tracking/custom_val --resume
python scripts/tracking_pipeline.py evaluate --output outputs/tracking/custom_val
~~~

## 5. Giải thích code quan trọng trong src/tracking

Luồng dữ liệu:

~~~text
calibration.py / review.py → camera A, H đã duyệt
detections.py              → detections chuẩn hóa + vị trí/covariance mặt đất
runner.py + UCMC clone     → quỹ đạo theo từng class, đủ 25 FPS
evaluation.py             → metric theo protocol cố định
tuning.py                  → lịch thử nghiệm, cache và leaderboard
~~~

### common.py — quy ước và khả năng chạy lại

[common.py](../src/tracking/common.py) chứa `CLASS_NAMES`, `BASELINE` và các helper.

- `read_sequences()` đọc timeline từ `metadata/sequences.csv`; không đoán
  số frame bằng detection cuối cùng.
- `resolve_config()` ghép mặc định và kiểm tra các tùy chọn vận hành như FPS=25,
  confidence hợp lệ, tham số dương và tên tùy chọn được hỗ trợ.
- `sha256_file()` hash nội dung file; `fingerprint()` hash cấu hình JSON.
- `environment_versions()` ghi phiên bản thư viện. `write_json()` ghi file
  tạm rồi thay thế file báo cáo để giảm nguy cơ đọc JSON đang ghi dở.

### calibration.py — bốn điểm thành phép chiếu mặt đường

[fit_homography() trong calibration.py](../src/tracking/calibration.py)
ghép bốn điểm pixel với bốn góc mặt đất:

~~~python
ground_points = [[0, 0], [W, 0], [W, L], [0, L]]
H = cv2.getPerspectiveTransform(image_points, ground_points)
A = np.linalg.inv(H)
~~~

Đoạn trên lược giản kiểu dữ liệu và bước chuẩn hóa. `H_image_to_ground` đưa
pixel về mặt đất; `A_ground_to_image` chiếu mặt đất lên ảnh.
Phép chiếu dùng tọa độ đồng nhất: phải chia hai thành phần đầu cho thành phần thứ ba.

Các hàm cần chú ý:

| Hàm | Vai trò |
| --- | --- |
| `_rectangle()` | Kiểm tra bốn điểm tạo vùng lồi đúng đường bao và hai kích thước dương |
| `project_points()` | Chiếu điểm và từ chối kết quả không hữu hạn hoặc tại vô cực |
| `fit_krt()` | Fit camera theo focal/VP, chọn pose có độ sâu dương |
| `validate_calibration_record()` | Kiểm tra ma trận, đơn vị, KRT nhất quán và dấu vết review |
| `save_calibration()` | Lưu nháp hoặc ghi review gắn với hash hình học và bằng chứng |
| `resolve_calibration()` | Ưu tiên bản riêng của sequence, rồi nhóm đã xác nhận |
| `create_calibration_previews()` | Xuất lưới, ảnh nắn và đáy bbox trên đầu/giữa/cuối |

Hash review giúp phát hiện JSON bị sửa sau khi duyệt. Chỉ đổi chuỗi trạng thái
sang `reviewed` không tạo được một approval hợp lệ.
Sai số khớp bốn điểm rất nhỏ chỉ chứng minh phép biến đổi khớp dữ liệu nhập,
không chứng minh chiều rộng/chiều dài ngoài thực tế đúng.

### review.py — giao diện, không chứa thuật toán tracker

[calibration_widget() trong review.py](../src/tracking/review.py) dựng năm bước
bằng `ipywidgets`. `ipyevents` chuyển click ảnh thành sự kiện Python.

- `pick_corner()` lấy `dataX/dataY` của ảnh gốc, chặn quá bốn điểm và
  cập nhật JSON dùng chung cho cả click và sửa tay.
- `show_source()` vẽ lại số điểm, cạnh rộng màu xanh và cạnh dài màu cam.
- `load_saved()` nạp bản nháp đã có mà không kế thừa dấu xác nhận.
- `compute()` gọi Homography/KRT trong module calibration.
- `preview_current()` tạo preview rồi cho phép người dùng xác nhận.
- `invalidate()` hủy preview khi hình học thay đổi.
- `update_ready()` bật/tắt nút theo dữ liệu hiện tại.
- `save()` vẫn kiểm tra điều kiện review tại backend trước khi lưu.

Các widget và thuật toán dùng cùng hàm calibration; không có một bản tính
Homography riêng chỉ dành cho notebook.

### detections.py — đổi định dạng và đưa bbox xuống mặt đất

[normalize_detections()](../src/tracking/detections.py) đọc CSV, kiểm tra schema,
số hữu hạn, class, frame thuộc timeline và kích thước bbox dương.
Nó đổi `xyxy` thành `xywh`, giữ dữ liệu nguồn và sắp frame ổn định.
`load_cached_detections()` chỉ dùng lại cache khi source/hash adapter phù hợp.

`GroundMapper.mapto()` lấy điểm đại diện của xe là **đáy giữa bbox**:

~~~python
u = x + w / 2
v = y + h
~~~

`GroundMapper.uv2xy()` tính:

~~~python
b = H @ [u, v, 1]
X, Y = b[0] / b[2], b[1] / b[2]
R_ground = J @ R_pixel @ J.T
~~~

`J` là Jacobian của phép chiếu. Nó truyền độ bất định pixel sang mặt đất:
xe gần chân trời có thể có sai số mặt đất lớn dù chỉ lệch vài pixel.

Công thức measurement noise giữ theo mapper của clone:

~~~python
sigma_u = clip(0.05 * h, 2, 13)
sigma_v = clip(0.05 * h, 2, 10)
R_pixel = diag([sigma_u**2, sigma_v**2])
~~~

Cả hai trục dùng **chiều cao bbox h**. Đây là nhiễu đo, khác `wx/wy` là
tham số nhiễu chuyển động trong Kalman. Không thay công thức clone bằng một
công thức khác chỉ vì bài báo mô tả theo cách khác.

### runner.py — chạy bốn tracker trên timeline đầy đủ

[run_tracking()](../src/tracking/runner.py) kiểm tra camera trước khi tạo run,
đọc metadata/handoff, tạo mapper rồi import tracker/Kalman từ clone.

Phần vòng lặp quan trọng có thể đọc theo dạng lược giản:

~~~python
for sequence in selected_sequences:
    kalman_cls.count = 1
    trackers = {class_id: make_upstream_tracker() for class_id in CLASS_NAMES}
    for frame in range(1, num_frames + 1):
        detections = by_frame.get(frame, [])
        for class_id, tracker in trackers.items():
            tracker.update(detections_of_class(detections, class_id), frame)
~~~

Đây là mã minh họa luồng, không phải hàm thay thế để copy chạy độc lập.

`count` reset **một lần mỗi sequence**, không reset từng class.
Do vậy bốn tracker độc lập vẫn cấp ID không trùng nhau trong cùng video.
Sang video khác có thể bắt đầu lại ID.

Frame không có detection vẫn gọi `update([])` để Kalman predict và cập nhật tuổi
track. Frame thiếu XML cũng được chạy; chỉ evaluator mới quyết định frame nào
được chấm. FPS=25 nghĩa là khoảng thời gian giữa hai bước là 0,04 giây.

Tracker gốc giữ trạng thái `[X, vx, Y, vy]`. Hai lượt association dùng detection
điểm cao rồi điểm thấp; `high_score` tách hai nhóm.
Cost trong clone là Mahalanobis bình phương cộng `log(det(S))`;
`a1/a2` là ngưỡng cost hai lượt, không phải ngưỡng IoU.
`vmax` đi vào phương sai vận tốc ban đầu `vmax²/3`, không phải lệnh cắt
vận tốc ở một giới hạn cứng. `wx/wy` đi vào covariance nhiễu chuyển động Q.

Đầu ra thông thường ghi bbox detection đã được liên kết với track confirmed,
không tự xuất bbox dự đoán cho mọi frame vắng detection.

**HP:** lưu lịch sử detection đầu track; khi track được xác nhận, lấy lại
những quan sát ban đầu đó. Không giữ các track chưa bao giờ confirmed.

**Interpolation:** `interpolate_tracks()` xử lý riêng theo class/track, yêu cầu
hơn ba quan sát và `1 < khoảng cách frame < max_age`.
BBox nội suy có confidence -1. Giữ bản trước xử lý trong `raw_tracks`.

### gmc.py và _Provider — bù chuyển động camera

[gmc.py](../src/tracking/gmc.py) mask bbox xe trên cả ảnh trước và hiện tại,
dùng sparse optical flow lấy điểm nền, rồi RANSAC fit partial affine.

Chiều biến đổi là **previous → current**. Frame đầu dùng identity.
Thiếu điểm hoặc fit thất bại sẽ dùng identity và ghi nguyên nhân.
Cache gắn với nội dung ảnh, detections, code và phiên bản OpenCV.

`_Provider.cmc()` trong runner chiếu vị trí dự đoán từ mặt đất về ảnh,
áp affine rồi đưa lại về mặt đất theo quy ước upstream.
Nhánh này là UCMCTrack+ và được báo cáo riêng khi bật `cmc`.

### evaluation.py — tách timeline tracking khỏi timeline chấm điểm

[evaluate_run()](../src/tracking/evaluation.py) dùng metric HOTA, CLEAR và Identity
của TrackEval trong clone, với adapter dữ liệu UA-DETRAC.

Trình tự chính:

1. Đọc GT canonical và các frame được khai báo trong `annotated_frames.csv`.
2. Bỏ frame chưa annotation khỏi chấm điểm; vẫn giữ ID qua khoảng trống.
3. Lọc prediction theo union ignore regions bằng helper hiện có.
4. Tách từng class, tạo IoU và dữ liệu metric trong `_metric_data()`.
5. Combine sequence và class bằng hàm của TrackEval.

Class 0 là car hợp lệ. Không thêm lọc GT theo kích thước, occlusion hoặc truncation.
Frame XML được khai báo nhưng không có object vẫn được chấm: prediction trên
frame này có thể là FP. Frame không được XML khai báo không tự coi là negative.

Prediction bị loại khi phần diện tích bbox nhìn thấy nằm trong union ignore
ít nhất 50%. Helper raster hóa và tính union nên vùng chồng nhau không bị cộng hai lần.
Lọc này chỉ áp ở evaluation, không sửa CSV nguồn.

**CLEAR/Identity dùng IoU 0,7. HOTA dùng các ngưỡng 0,05–0,95.**
Score chính combine theo detection, không lấy trung bình HOTA từng video.
Macro-average chỉ xét class có GT hoặc prediction.
Các score báo theo phần trăm; MOTA có thể âm. IDS, FP, FN, Frag là số đếm nguyên.

Bản CLEAR có patch reset trạng thái frame trước khi gặp frame rỗng, tránh bỏ sót
Frag và giữ ưu tiên matching cũ. HOTA/Identity không thay thuật toán.
Chi tiết và phạm vi thay đổi ở [UCMC_LOCAL_PATCHES.md](UCMC_LOCAL_PATCHES.md).
Protocol và checksum ghi nhận patch này.

### tuning.py — thử nghiệm có giới hạn và tái lập được

[tuning.py](../src/tracking/tuning.py) chứa:

| Thành phần | Ý nghĩa |
| --- | --- |
| `_val()` | Đòi đúng 12 val sequences và khớp split trong manifest |
| `_preflight()` | Kiểm tra đủ camera được duyệt, không âm thầm bỏ sequence |
| `_trial()` | Chạy tracker rồi evaluator cho một cấu hình |
| `run_smoke()` | So sánh hai phương pháp camera trên cùng ba sequence |
| `OFAT_VARIANTS` | Chín biến thể đổi từng nhóm tham số so với baseline |
| `tune()` | Lịch tối đa 15 slot, leaderboard và best config |

Cấu hình trùng dùng cùng run đã kiểm tra fingerprint.
Kết quả val được dùng để chọn cấu hình nên phải gọi là **kết quả trên tập phát triển**,
không gọi là benchmark PR-MOTA gốc của UA-DETRAC.

## 6. Đọc output và tiếp tục một run

| File/thư mục | Nội dung |
| --- | --- |
| `run_manifest.json` | Cấu hình đã resolve, camera, hash input/code, phiên bản môi trường, trạng thái và runtime |
| `tracks/<sequence>.csv` | Output chính có frame_id, track_id, class_id, xywh và confidence |
| `raw_tracks/<sequence>.csv` | Output confirmed trước HP/interpolation |
| `mot/<sequence>.txt` | Output MOT mười cột; dùng CSV khi cần đánh giá class-aware |
| `previews/<sequence>.mp4` | Video ngắn nếu bật preview_frames |
| `sequence_reports` | Thời gian adapter, mapping, tracker, CMC, hậu xử lý và fallback |
| `evaluation/summary.json` | Score chính, macro, per-class và fingerprint evaluation |
| `per_sequence.csv`, `per_class.csv`, `per_sequence_class.csv` | Chi tiết theo sequence/class trong thư mục evaluation |
| `evaluation/frame_map.csv` | Frame chấm điểm liên hệ với frame gốc |
| `evaluation/protocol.json`, `metrics.json` | Protocol và dữ liệu metric |
| `leaderboard.csv`, `trials.csv`, `best_config.json` | Kết quả experiment tuning |

`--resume` dùng lại sequence hoàn thành sau khi kiểm tra hash output.
Sequence đang dở được chạy lại. Đổi detections, camera, cấu hình, môi trường hoặc
code cần run mới. Hiện fingerprint chứa các file Python của cả package tracking,
nên sửa giao diện cũng có thể làm run cũ không còn khớp fingerprint.

Đổi GT/protocol cần experiment tuning mới. Có thể gọi evaluator để tạo báo cáo
mới từ output tracking có sẵn. Không ghép các leaderboard khác fingerprint.

## 7. Kiểm thử và các lỗi thường gặp

~~~text
python -X utf8 -m unittest discover -s tests/tracking -v
python -X utf8 -m unittest discover -s tests/data -v
~~~

Kiểm thử dùng dữ liệu tổng hợp cho hình học, covariance, sự kiện chuột, review gate,
adapter, timeline rỗng, ID/class, metric, ignore union, CMC, HP/interpolation,
resume và lịch tuning. Cần thêm kiểm tra frontend thực và calibration ngoài thực tế.

| Hiện tượng | Cách xử lý |
| --- | --- |
| Không click được ảnh | Cài dependencies đúng kernel, bật widget manager trên Colab, tải lại trang và tạo lại widget |
| Nút Xem thử chưa bật | Chọn đủ 4 điểm; nhập cả chiều rộng và chiều dài lớn hơn 0 |
| Điểm nối chéo | Bỏ điểm cuối hoặc chọn lại theo đường bao 1→2→3→4 |
| Nút duyệt chưa bật | Xem preview, nhập tên/nguồn kích thước, rồi đánh dấu xác nhận |
| Lưới sai sau khi nạp nháp | Sửa điểm hoặc kích thước; các bản đề xuất không phải số đo đã xác minh |
| Camera preflight failed / exit code 2 | Duyệt đủ các camera được liệt kê; không sửa riêng trường status để vượt kiểm tra |
| Run exists with different inputs/configuration | Chọn output mới hoặc khôi phục đúng input/config của run cũ |
| Preview báo thiếu ảnh | Kiểm tra img1 và symlink sau khi di chuyển dataset |
| KRT không có nghiệm độ sâu dương | Kiểm tra thứ tự điểm, kích thước, focal và các đường VP |
| CMC fallback identity | Xem báo cáo từng frame; kiểm tra độ mờ, lượng điểm nền và vùng mask |

Chỉ coi giai đoạn thực nghiệm hoàn thành khi đã chạy smoke thật, duyệt đủ 12 camera
val và hoàn thành lịch val cần thiết. Gói camera đầy đủ 100 sequences còn cần
review từng sequence hoặc xác nhận chia sẻ phù hợp. Kiểm thử tổng hợp và sai số
khớp bốn điểm không thay thế các bước nghiệm thu này.

## Phân tích trường hợp tốt và xấu sau tracking

Mở `notebooks/tracking/03_tracking_case_analysis.ipynb`, sửa `ROOT`, `DATASET` và
`EXPERIMENT` trong cell môi trường rồi chạy lần lượt từ trên xuống. Notebook đọc
kết quả đã lưu, không chạy lại detector hoặc tracker. Trên Colab, giải nén dataset
và các gói output vào đúng cấu trúc thư mục trước; có thể đặt biến môi trường
`UA_DETRAC_PROJECT`, `UA_DETRAC_DATASET`, `UA_DETRAC_EXPERIMENT` để đổi đường dẫn.

Notebook xếp hạng sequence bằng HOTA đã lưu, so sánh baseline với cấu hình tốt
nhất, chọn đoạn 5 giây tốt/xấu và vẽ overlay đồng bộ. Xanh lá là prediction khớp
GT, đỏ là FP, cam là GT bị bỏ sót, tím là đổi ID; vùng ignore và prediction bị
loại có màu xám. Frame chưa gán nhãn được ghi rõ là không chấm điểm.

Dùng trình xem tương tác để chọn sequence, frame và class; nút Next error và
Next IDS chuyển đến lỗi tiếp theo. Ảnh tĩnh và GIF vẫn xem được nếu frontend
không hỗ trợ widget. GIF, biểu đồ, bảng chẩn đoán và sự kiện từng frame được lưu
vào `EXPERIMENT/case_analysis/`. Cell phân tích kiểm tra FP/FN/IDS/Frag có khớp
với evaluator đã lưu hay không; nếu không khớp thì dừng để tránh diễn giải sai.

Logic nằm trong `src/analysis/tracking_cases.py`: `diagnose` tái hiện matching
CLEAR IoU 0.7; `window_scores` chọn đoạn có đủ GT và annotation;
`comparison_frame` vẽ baseline/best cùng ảnh; `export_clip` tạo GIF. F1 của đoạn
ngắn và tỷ lệ GT được match chỉ là chỉ số chẩn đoán, không phải HOTA/IDF1.

## Chạy lại validation với protocol v2

Protocol v2 lọc cả GT và prediction theo union vùng ignore tại bước đánh giá,
với ngưỡng 50% diện tích nhìn thấy. Canonical và raw không bị sửa. Các quy định
khác về class, timeline, IoU và cách tổng hợp metric giữ nguyên. Đây là protocol
của đồ án, không phải bản sao đầy đủ của PR-MOTA/toolkit UA-DETRAC.

Chấm lại 15 cấu hình cũ, rồi tái lập cấu hình thắng và thử confidence 0,10/0,20:

```powershell
python scripts/revalidate_tracking.py --workers 3 --followups
```

Có thể đổi đường dẫn bằng `--dataset`, `--detections`, `--source`, `--output`,
`--calibration`. Mặc định output mới là `outputs/tracking/validation_v2`, nguồn
cũ là `outputs/tracking/tuning_h_approved_v1`. Không dùng cùng thư mục cho hai
đối số này. Bỏ `--followups` nếu chỉ muốn chấm lại output đã có. Script kiểm tra
fingerprint và checksum trước khi tái sử dụng kết quả; dùng lại cùng lệnh để
resume với cùng code/input. Nếu code tracking hoặc input đổi, chọn output mới.

Khi chạy đủ follow-up, `all_trials.csv` chứa bảng xếp hạng chung, `best_config.json`
chỉ cấu hình được chọn. `runs/<config_id>/tracks` và `mot` của cấu hình thắng và
baseline được đặt sẵn trong output mới. `reproduction_check.json` kiểm tra output
tái lập có khớp từng byte với run gốc trên đủ 12 val sequences hay không.

Để phân tích kết quả mới bằng notebook case analysis, đặt biến môi trường
`UA_DETRAC_EXPERIMENT` tới thư mục `outputs/tracking/validation_v2` trước khi chạy
cell thiết lập. Notebook đọc version từ `evaluation/protocol.json`, nên vẫn có
thể mở lại kết quả v1 mà không áp dụng nhầm bộ lọc GT của v2.

Audit coverage detector theo matching one-to-one, class và kích thước:

```powershell
python scripts/audit_tracking_detections.py
```

CSV được lưu trong `validation_v2/detection_audit`. Coverage class-agnostic chỉ
dùng chẩn đoán nhầm class, không thay thế tiêu chí class-aware để chọn tracker.
Các trace assignment/cost thật của upstream cho các frame mục tiêu được lưu
trong `runs/reproduce_v2_winner/association_trace`; chúng không thay đổi matching.

Đợt follow-up còn có đối chứng HP/high_score 0,4 và nhánh sửa lifecycle. Clone
có thể giữ unmatched Confirmed quá max_age khi không có low-score detections.
`src/tracking/lifecycle.py` sửa việc chuyển Coasted/hết hạn qua wrapper; không
sửa trực tiếp clone. Bật bằng `--fix-coasting` trên lệnh `run`, hoặc
`fix_coasting=True` khi gọi `run_tracking`. Mặc định giữ upstream để tái lập.
`best_config.json` có trường `fix_coasting`; khi dùng YAML của cấu hình được
chọn, cần truyền thêm flag nếu trường này là true. Không truyền flag vào lệnh
`evaluate`: evaluator chỉ đọc output đã tạo, không chạy lại lifecycle.

Cấu hình được chọn tại mốc v2 ngày 28/11/2026 là `best_val_v2.yaml`, HP bật, CMC tắt và
`fix_coasting=true`. Chạy vào thư mục mới:

```powershell
python scripts/tracking_pipeline.py run --config configs/tracking/best_val_v2.yaml --calibration outputs/tracking/calibration/registry_approved_h_v1.json --fix-coasting --output outputs/tracking/run_best_val_v2
python scripts/tracking_pipeline.py evaluate --output outputs/tracking/run_best_val_v2
```

Xem số liệu, các đối chứng và lỗi còn lại trong
[`TRACKING_VAL_V2_REPORT.md`](TRACKING_VAL_V2_REPORT.md). Không dùng score v1
và v2 làm một phép so sánh cải thiện mô hình vì tập GT được chấm đã thay đổi.

## Cải thiện tracking bằng detection đã xuất

Chạy các đối chứng trên đủ 12 val sequences mà không gọi lại YOLO:

```powershell
python scripts/refine_tracking.py
```

Script dùng cấu hình `validation_v2/runs/coasting_fix_hp` làm đối chứng, bật
`fix_coasting=True` cho mọi lượt và giữ protocol v2. Output mặc định nằm trong
`outputs/tracking/refinement_v3`. Có thể đổi `--dataset`, `--detections`,
`--calibration`, `--reference`, `--output`; dùng `--trials control boundary_6`
để chỉ chạy các lượt được chỉ định. Chạy lại cùng lệnh để resume; nếu code hoặc
input đổi, chọn output mới. `run.log` ghi tiến độ, `leaderboard.csv` xếp hạng
những lượt đã hoàn thành, `best_config.json` chỉ tới output thắng hiện tại.

Các tùy chọn mới nằm trong YAML, được kiểm tra tại `src/tracking/common.py`:

| Tùy chọn | Mặc định | Tác dụng |
| --- | --- | --- |
| `boundary_sigma` | `0.0` | Độ lệch chuẩn tối thiểu, đơn vị pixel, trên trục bị cắt ở biên; 0 giữ noise upstream |
| `class_mode` | `separate` | Bốn tracker riêng như trước; `shared_vote` dùng một tracker chung và chọn class bằng tổng confidence của cả track |

`GroundMapper.mapto` trong `src/tracking/detections.py` giữ nguyên điểm đáy giữa
bbox. Khi `boundary_sigma > 0`, bbox cách biên trái/phải không quá 2 pixel được
tăng uncertainty trên trục ảnh x; bbox cách biên dưới không quá 2 pixel được tăng
uncertainty trên trục y. Covariance sau đó đi qua cùng Jacobian homography.
Không thay tọa độ bbox, không tự suy ra phần xe nằm ngoài ảnh. Đây là giả thuyết
measurement cần kiểm chứng trên val, không phải công thức gốc của benchmark.

Với `class_mode: shared_vote`, `src/tracking/runner.py` cho phép association giữa
các detection khác class. Mỗi detection được gán vào track đóng góp confidence
vào class gốc; class có tổng lớn nhất trở thành class của toàn bộ track, hòa thì
chọn class ID nhỏ hơn. Đây là xử lý **offline**, dùng cả các frame tương lai;
không phù hợp để báo cáo như hệ thống realtime không độ trễ. Bbox/confidence gốc
được giữ lại trong `raw_tracks`; `tracks` chứa kết quả sau HP/voting/interpolation.
Vote được lưu trong `sequence_reports/*_class_votes.json` để kiểm tra. Nhầm class
xuyên suốt một track vẫn có thể tồn tại; association chung cũng có thể nối nhầm
hai xe gần nhau nên chỉ chọn nếu metric class-aware cải thiện.

Các lượt còn lại dùng implementation hiện có: `interpolate_tracks` nội suy các
khoảng thiếu ngắn giữa hai observation của cùng track/class; `max_age` thay thời
gian giữ track mất detection; `a1/a2` thay ngưỡng assignment cost upstream, không
phải ngưỡng IoU. Interpolation có thể thêm bbox sai, ngưỡng rộng có thể nối nhầm,
vì vậy đều phải xem cả FP/FN/IDS thay vì chỉ số lượng track.

Sau khi chọn cấu hình, dùng YAML `refinement_v3/best_config.yaml` cùng flag
`--fix-coasting` để chạy lại; evaluator đọc class sau voting và vẫn chấm
class-aware. Chi tiết kết quả trong
[`TRACKING_REFINEMENT_V3_REPORT.md`](TRACKING_REFINEMENT_V3_REPORT.md).

Đợt v3 chọn `configs/tracking/best_val_v3.yaml`: `class_mode=shared_vote`,
`boundary_sigma=10`, HP và lifecycle fix bật, CMC/interpolation tắt. Có thể đặt
`UA_DETRAC_EXPERIMENT` tới `outputs/tracking/refinement_v3` để mở notebook case
analysis; `trials.csv` coi run `control` là baseline của đợt này.

## Chạy test sau khi khóa cấu hình val

```powershell
python scripts/prepare_test_cameras.py
python scripts/run_tracking_test.py
```

Lệnh đầu không sửa registry đã duyệt. Nó tạo registry mới ở
`outputs/tracking/test_calibration_draft`, giữ 10 camera test đã có review và
bổ sung 30 proposal dựa trên ảnh. Các proposal vẫn là `draft`, kích thước là
prior chưa đo thực tế. Xem preview đầu/giữa/cuối để review; không sửa `status`
thành reviewed nếu chưa có review thật. Kết quả test hiện phải được báo cáo cùng
giới hạn hình học này.

Lệnh thứ hai đọc `refinement_v3/best_config.json`, ghi `selection_lock.json`
trước khi tracking, chạy đủ 40 test sequences và không tuning theo điểm test.
Script có thể resume với code/input giống hệt; không sửa lock để thay cấu hình
sau khi đã xem điểm. `completion.json` xác nhận đủ run/evaluation/subset combine.

`difficulty_summary.csv` có hai hệ phân nhóm: Tracking và Detection từ toolkit.
Tracking có một ID lặp và thiếu `MVI_40711`, vì vậy medium chỉ có 19 ID duy nhất
và có nhóm `unassigned`. Detection có đủ 10/20/10 nhưng là cách chia khác. Điểm
nhóm được combine từ metric internals qua `src/tracking/subsets.py`, không phải
trung bình điểm các sequence. Xem [`TRACKING_TEST_V3_REPORT.md`](TRACKING_TEST_V3_REPORT.md).

## Gói output dùng chung với TrackEval

```powershell
python scripts/export_tracking_bundle.py
```

Output mới là `outputs/tracking/delivery_v3/trackeval_bundle`. Gói gồm:

- `runs/val` và `runs/test`: CSV class-aware, MOT theo frame gốc, manifest và metric.
- `evaluation_input`: GT canonical cần cho evaluation, metadata, annotated frames,
  vùng ignore; không cần ảnh để chấm saved predictions.
- `mot_challenge`: bốn benchmark riêng car/bus/van/others cho loader MOT, đã lọc
  ignore và compact timeline; `original_to_evaluation_frames.csv` giữ mapping.

Theo README của gói để chọn đúng loader/protocol. Class `1` trong GT MOT tách
class chỉ là placeholder cho loader; class gốc `car=0` vẫn được giữ trong CSV.
Không lấy MOT TXT classless của timeline gốc rồi chấm như tất cả đều là pedestrian.
Không áp dụng lại preprocessing ignore cho bản đã lọc. Đọc
[`TRACKING_REPORT_AUDIT.md`](TRACKING_REPORT_AUDIT.md) để đối chiếu các mốc report.

## Đối chứng v3 bật CMC và UCMCTrack gốc

```powershell
python scripts/compare_tracking_cmc.py val
# Chỉ chạy test nếu val_selection.json có cmc_selected=true.
python scripts/prepare_tracking_cmc_cache.py --workers 4
python scripts/compare_tracking_cmc.py test
```

Script dùng `configs/tracking/v3_cmc.yaml`, chỉ đổi CMC so với cấu hình v3 đã
chọn. Output riêng ở `outputs/tracking/cmc_v3`; không ghi đè các run v2/v3 cũ.
Nhánh `upstream_hp_cmc` dùng tracker/Kalman nguyên gốc với HP và CMC, cùng các
tham số số học, nhưng tắt sửa lifecycle, class voting và noise bổ sung ở biên.
Đây là đối chứng được adapter cho UA-DETRAC, không phải điểm benchmark MOT17.

Đọc `val_comparison.csv`, `val_selection.json`, `test_comparison.csv` và
`test_difficulty.csv` nếu bước tương ứng đã hoàn tất. Script từ chối chạy CMC
trên test khi CMC không thắng trên val. Chi tiết ở
[`TRACKING_CMC_V3_REPORT.md`](TRACKING_CMC_V3_REPORT.md).

Bước chuẩn bị cache dùng cùng hàm CMC nhưng xử lý các sequence độc lập song song;
có thể bỏ qua bước này để runner tự tính tuần tự. Cache vẫn được kiểm tra checksum
ảnh/code/detection khi tracking. Giảm `--workers` nếu máy đang dùng nhiều RAM;
thời gian chuẩn bị cache phải được cộng khi báo chi phí lần chạy đầu.
