# Hướng dẫn sử dụng UA-DETRAC cho nhóm

Tài liệu này hướng dẫn Data, Detection và Tracking member dùng chung một bản
dataset, cùng split và cùng quy ước frame/coordinate. Pipeline tạo dataset
`v1.1`; không chỉnh sửa thư mục raw sau khi đã inspect.

## 1. Chuẩn bị dataset (Data member)

### Chạy trên máy local

Mở terminal tại thư mục gốc repository. Mặc định raw nằm ở `data/raw`, output
nằm ở `outputs/ua_detrac`.

```powershell
python -m pip install -r requirements.txt
python scripts/inspect_dataset.py `
  --raw data/raw `
  --report outputs/ua_detrac/raw_inventory.json
```

Đọc báo cáo inventory trước khi export. `ready` nghĩa là file và timeline ảnh
hợp lệ; `annotation_coverage` cho biết tỷ lệ frame ảnh được XML khai báo. Frame
ảnh không có entry XML không được xem là negative sample.

Mở lần lượt các notebook trong `notebooks/data`:

1. `00_data_preparation.ipynb`: trỏ tới raw local và xem inventory. Không bật
   tải Kaggle nếu raw đã có sẵn.
2. `00b_run_pipeline.ipynb`: chạy smoke trước. Smoke giữ nguyên từng sequence
   liên tục và chọn một sequence hợp lệ trong mỗi split.
3. `01_dataset_analysis.ipynb` và `02_split_analysis.ipynb`: xem coverage,
   phân phối class/weather và xác nhận split.
4. `03_visual_validation.ipynb`: xem các trang ảnh đã gán nhãn và các frame bị
   thiếu annotation. Sau khi review xong mới đặt
   `VISUAL_REVIEW_PASSED = True` và chạy cell cuối.
5. `04_data_and_model_analysis.ipynb`: tạo biểu đồ phân tích class, mật độ,
   bbox, coverage, track, speed, điều kiện cảnh quay và annotation. Các biểu đồ
   model được vẽ khi cung cấp CSV metric theo schema trong notebook.
6. Quay lại `00b_run_pipeline.ipynb`, chạy full pipeline, tạo subset Detection
   và package artifact.

Notebook tìm project từ thư mục hiện tại hoặc thư mục cha. Có thể override
đường dẫn trước khi chạy setup cell:

```python
import os
os.environ["UA_DETRAC_PROJECT"] = r"D:\path\to\CS331-Vehicle-Tracking"
os.environ["UA_DETRAC_RAW"] = r"D:\datasets\ua_detrac\raw"
os.environ["UA_DETRAC_OUTPUT"] = r"D:\datasets\ua_detrac\dataset_v1.1"
```

Trên Colab, notebook mount Drive để đọc project; raw ưu tiên
`/content/ua_detrac/raw` nếu thư mục đó tồn tại. Nên để output trên VM Colab
thay vì Drive để giảm I/O file nhỏ. Sau khi reset runtime, cần tải/stage raw và
chạy lại pipeline nếu filesystem VM đã bị xóa.

CLI tương ứng để tạo smoke/full:

```bash
python scripts/prepare_dataset.py --raw data/raw \
  --output outputs/ua_detrac/smoke_v1.1 --smoke
python scripts/prepare_dataset.py --raw data/raw \
  --output outputs/ua_detrac/dataset_v1.1
```

Pipeline không ghi đè output. Chọn tên/version output mới nếu thư mục đã tồn
tại. Để package, phải có validation `PASSED` và visual review `PASSED`:

```bash
python scripts/package_dataset.py outputs/ua_detrac/dataset_v1.1 \
  --artifacts outputs/ua_detrac/artifacts
```

Chia sẻ ba archive `ua_detrac_detection_v1.1.tar`,
`ua_detrac_tracking_gt_v1.1.tar`, `ua_detrac_metadata_v1.1.tar` cùng
`manifest_v1.1.json`. Kiểm tra SHA256/kích thước trong manifest sau khi chuyển
file. Các member dùng cùng một version; không trộn Detection v1.1 với Tracking
hoặc metadata từ version khác.

## 2. Detection member

### Dữ liệu nhận

Giải nén `ua_detrac_detection_v1.1.tar`. Thư mục sau giải nén là
`detection/`, gồm:

```text
detection/
  data.yaml
  train.txt
  val.txt
  test.txt
  images/<sequence>/imgNNNNN.jpg
  labels/<sequence>/imgNNNNN.txt
  train_n500_seed42.yaml       # nếu Data member đã tạo subset
  train_n1000_seed42.yaml
  train_n5000_seed42.yaml
```

Detection member cũng cần `ua_detrac_metadata_v1.1.tar` để đọc manifest,
class mapping, thống kê, `annotated_frames.csv` và `unannotated_frames.csv` khi
đánh giá. `annotated_frames.csv` giữ cả frame XML khai báo nhưng không có object;
`unannotated_frames.csv` liệt kê frame ảnh không có entry XML.

`metadata/sequences.csv` lưu đúng key nguồn `sence_weather` và `camera_state`,
cùng `sequence_attributes_json` để không mất thuộc tính sequence chưa chuẩn hóa.
`metadata/annotated_frames.csv` giữ `density` và toàn bộ frame attributes.
`canonical/annotations.csv` có `speed` và `trajectory_length` theo từng target
frame. Trong `metadata/track_lengths.csv`, `annotated_frame_count` là số record
target thực tế đếm từ canonical; các cột `trajectory_length_xml_min/max` giữ
giá trị được khai báo trong XML để so sánh. `speed` thay đổi theo frame nên
canonical lưu từng giá trị; bảng track chỉ tổng hợp min/max để tiện xem nhanh.

`data.yaml` lưu đường dẫn tuyệt đối tại máy đã build artifact. Sau khi giải nén
ở vị trí mới, chạy rebase để cập nhật đường dẫn trong YAML gốc và các YAML
subset:

```bash
python /path/to/CS331-Vehicle-Tracking/scripts/rebase_dataset.py \
  /path/to/detection
```

Nếu không có repository, cập nhật trường `path` trong YAML thành đường dẫn
tuyệt đối tới thư mục `detection/`.

Mỗi file list dùng đường dẫn tương đối từ `detection/`. `data.yaml` là cấu hình
full dataset; YAML `train_nN_seed42.yaml` chỉ thay train list, vẫn dùng chung
validation/test. Dùng đúng file YAML trong mỗi thực nghiệm, ví dụ:

```python
# Cài ultralytics trong môi trường train của nhóm nếu chưa có.
from ultralytics import YOLO

model = YOLO("<model-config-or-checkpoint>")
model.train(
    data="/path/to/detection/train_n1000_seed42.yaml",
    # Cố định và ghi lại các tham số này để so sánh công bằng.
    epochs=50,
    seed=42,
)
```

Ultralytics không nằm trong `requirements.txt` của data pipeline; chốt phiên bản
riêng cho nhóm Detection và ghi phiên bản đó cùng kết quả.

Nếu chạy trên toàn bộ train, truyền `/path/to/detection/data.yaml`. Ghi lại
checkpoint khởi tạo, phiên bản framework/model, image size, batch, augmentation,
seed, epochs và số optimizer step. Khi so sánh N=500/1000/5000, giữ nguyên
validation/test và cấu hình còn lại. N được đếm sau `train_stride=5`; muốn thử
pool ảnh khác phải tạo dataset version mới, không đổi stride giữa các N.

Mapping class cố định trong `data.yaml`/`classes.yaml`: `0=car`, `1=bus`,
`2=van`, `3=others`. Ảnh giữ độ phân giải gốc 960×540. YOLO có thể resize trong
lúc train; khi xuất prediction cho Tracking, chuyển bbox về tọa độ pixel của
ảnh gốc.

Frame được XML khai báo nhưng không có object có label rỗng và là negative
sample theo annotation nguồn. Frame ảnh không được XML khai báo bị loại khỏi
Detection train/val/test để tránh tạo negative giả. Không tự thêm các frame đó
vào file list.

### Bàn giao prediction cho Tracking

Chạy detector trên **từng frame** của các sequence cần tracking; không dùng
train-stride dataset để đánh giá Tracking. Detection artifact chỉ chứa các
frame được export cho Detection, nên để inference full-rate cần thêm
`tracking/` image sequences từ `ua_detrac_tracking_gt_v1.1.tar` (hoặc nguồn ảnh
đầy đủ cùng version). Data member cần cấp artifact này cho Detection member nếu
họ chịu trách nhiệm tạo predictions. Tracking member cần predictions cho cùng
sequence, frame và image coordinate system với `tracking/`.

Đóng gói một CSV cho mỗi sequence, ví dụ `detections/MVI_20011.csv`, với header:

```csv
frame_id,class_id,confidence,x1,y1,x2,y2
1,0,0.93,214.5,182.1,295.8,274.2
1,2,0.88,403.2,191.7,481.4,281.3
```

Quy ước: `frame_id` bắt đầu từ 1 và là số trong tên ảnh; bbox là pixel XYXY
trên ảnh gốc, với góc phải/dưới độc quyền; confidence thuộc [0,1]; class ID
dùng mapping trên. Một frame không có detection có thể không có dòng CSV. Ghi
rõ threshold/NMS và cách xử lý class khi tạo file. Không đổi frame numbering hoặc
tọa độ sau resize.

Để đánh giá Detection, dùng `val.txt` trong lúc phát triển và chỉ dùng `test.txt`
cho báo cáo cuối. Frame trong `metadata/unannotated_frames.csv` không thuộc
ground truth hợp lệ; loại chúng khỏi đánh giá. Ignore regions nằm ở
`detection/ignored_regions.csv` và `metadata/canonical/ignored_regions.csv`;
nếu áp dụng protocol overlap 50%, lọc prediction khi
đánh giá và ghi policy đó vào báo cáo. Không xóa các annotation canonical để
thực hiện lọc.

## 3. Tracking member

### Dữ liệu nhận

Giải nén đồng thời `ua_detrac_tracking_gt_v1.1.tar` và
`ua_detrac_metadata_v1.1.tar` vào cùng thư mục. Hai artifact có thư mục gốc
khác nhau nên không ghi đè:

```bash
tar -xf ua_detrac_tracking_gt_v1.1.tar
tar -xf ua_detrac_metadata_v1.1.tar
```

Kết quả gồm `tracking/<sequence>/` và `metadata/`. Mỗi sequence tracking có:

```text
tracking/MVI_20011/
  seqinfo.ini
  img1/000001.jpg ...
  gt/gt.txt
```

`img1` giữ toàn bộ timeline liên tục, stride 1. GT MOT-style có chín cột:

```text
frame,id,x,y,w,h,confidence,class_id,visibility
```

`frame` bắt đầu từ 1; `id` chỉ có ý nghĩa trong một sequence; bbox là XYWH pixel
trên ảnh gốc; confidence của GT là 1; class ID theo mapping UA-DETRAC;
visibility là -1 (không biết). Không đưa class ID UA-DETRAC trực tiếp vào
TrackEval MOT17 mặc định như dữ liệu pedestrian. Cần adapter/evaluator hiểu
vehicle classes và policy visibility.

### Chạy tracker

Với mỗi sequence, ghép ảnh `tracking/<sequence>/img1` với CSV prediction cùng
sequence do Detection member bàn giao. Reset tracker khi chuyển sequence. Với
smoke, chạy cả sequence để xác nhận format, detector→tracker wiring, frame
numbering và ID association; smoke có sequence từ cả train/val/test nên không
dùng metric smoke để chọn tham số. Tune bằng validation; test chỉ dùng cho đánh
giá cuối.

Vòng xử lý phải đi theo frame tăng dần: đọc ảnh frame `f`, lấy detection có
`frame_id=f`, đưa detection vào tracker, rồi lưu các track được cập nhật. Nếu
tracker cần frame liên tục, gọi update cả khi frame đó không có detection.
Dùng `seqinfo.ini` để lấy FPS, kích thước và chiều dài sequence; không suy ra
calibration camera từ file này. UCMCTrack cần calibration riêng; xác nhận nguồn,
đơn vị và camera mapping với Data/Tracking trước khi chạy.

Nếu tracker chạy class-aware, giữ class ID qua detection association và output.
Nếu chạy class-agnostic, ghi rõ phép gộp class trong cấu hình/report để kết quả
có thể so sánh lại.

Output tracker nên lưu riêng theo sequence, không ghi đè GT, ví dụ:

```text
predictions/<sequence>.txt
```

Quy ước output cần được thống nhất với evaluator trước khi chạy; MOT-style phổ
biến là `frame,id,x,y,w,h,confidence,class_id,visibility`. Giữ frame ID và bbox
theo ảnh gốc. Không nối ID giữa các sequence/camera.

### Frame chưa gán nhãn và ignore regions

`metadata/unannotated_frames.csv` liệt kê frame ảnh không có frame entry trong
XML. Các ảnh này vẫn có trong tracking timeline để giữ chuyển động liên tục,
nhưng absence của GT **không có nghĩa là không có xe**. Không tính chúng như
negative GT: loại các frame này khỏi metric hoặc mask mọi prediction ở các frame
đó theo evaluator đã chọn. Báo cáo số frame/sequence đã bị mask. `gt.txt` chỉ có
object annotation từ XML, nên không tự thêm GT nội suy.

`canonical/ignored_regions.csv` (metadata artifact) chứa ignore regions. Khi
protocol yêu cầu, loại prediction có ít nhất 50% diện tích nằm trong vùng ignore
trước khi tính metric. Ignore regions khác với frame chưa gán nhãn: region lọc
box trong frame, còn unannotated frame loại cả frame khỏi đánh giá. Không bỏ
annotation chỉ vì truncation/occlusion; nếu thí nghiệm áp dụng ngưỡng kích
thước/occlusion/truncation, mô tả rõ ngưỡng và giữ bản baseline không lọc.

Occlusion của từng object được giữ hai dạng: `annotations.csv` lưu nguyên node
XML trong `occlusion_xml` cùng `occlusion_regions_json`; bảng
`canonical/occlusion_regions.csv` tách từng `region_overlap` theo sequence,
frame và track. Các trường `left/top/width/height`, `occlusion_id`,
`occlusion_status` và toàn bộ thuộc tính XML gốc được bảo toàn. `occlusion`/
`occlusion_ratio` chỉ mang ratio trực tiếp nếu XML cung cấp; không suy ratio từ
diện tích overlap region.

## 4. Quy tắc chung khi báo cáo kết quả

- Dùng split cố định: train để fit, val để chọn cấu hình, test cho báo cáo cuối.
- Lưu dataset version, manifest/checksum, subset JSON (Detection), model/checkpoint,
  seed và cấu hình chạy cạnh kết quả.
- Smoke chỉ kiểm tra pipeline/kết nối; không dùng làm benchmark.
- Không chia ngẫu nhiên frame giữa train/val/test; split của dự án là theo
  sequence để tránh rò rỉ cùng camera/video.
- Nếu evaluator chưa hỗ trợ class UA-DETRAC, unannotated frames và ignored
  regions, ghi rõ giới hạn thay vì báo metric như benchmark chính thức.
