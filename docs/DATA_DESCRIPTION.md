# Mô tả dữ liệu UA-DETRAC `dataset_v1.1`

Tài liệu mô tả cấu trúc, ý nghĩa thuộc tính và quy mô của dataset sau xử lý,
dùng chung cho hai giai đoạn: Detection (YOLO) và Tracking (UCMCTrack, OC-SORT,
DeepSORT). Cách chạy pipeline, train và chạy tracker xem
[TEAM_USAGE_GUIDE.md](TEAM_USAGE_GUIDE.md).

Mọi số liệu dưới đây lấy từ `metadata/` của bản full. Nguồn annotation là XML
gốc của UA-DETRAC; không có thuộc tính nào được suy ra hay ước lượng thêm.

## 1. Tổng quan

| Thuộc tính | Giá trị |
|---|---|
| Nguồn | UA-DETRAC (100 sequence: 60 train chính thức, 40 test chính thức) |
| Độ phân giải | 960 × 540, giữ nguyên (không resize) |
| FPS | 25 (giá trị mặc định cấu hình, XML không ghi FPS) |
| Class | `0 car`, `1 bus`, `2 van`, `3 others` |
| Split | Theo sequence (không trộn frame của một sequence vào hai split) |
| Seed split | 42; train/val = 80/20 trên tập train chính thức; test giữ nguyên test chính thức |

Danh sách sequence của từng split: `metadata/{train,val,test}_sequences.txt`
(cùng nội dung với `splits/` trong Git).

## 2. Quy mô

### 2.1 Theo split

| Split | Sequence | Frame (timeline) | Frame có nhãn | Frame chưa gán nhãn | Object (bbox) | Track | Object/frame (mean / median) |
|---|---:|---:|---:|---:|---:|---:|---:|
| train | 48 | 67,790 | 66,811 | 979 | 473,719 | 5,268 | 6.99 / 6 |
| val | 12 | 16,001 | 15,274 | 727 | 124,562 | 684 | 7.78 / 6 |
| test | 40 | 56,340 | 56,167 | 173 | 675,774 | 2,337 | 11.99 / 10 |
| **Tổng** | **100** | **140,131** | **138,252** | **1,879** | **1,274,055** | **8,289** | |

- Dung lượng: `detection/` 5.95 GB (84,817 ảnh), `tracking/` 9.47 GB
  (140,131 ảnh). Hai thư mục chứa bản sao ảnh riêng, tổng ~15.4 GB.
- Độ dài sequence: 380 – 2,635 frame (median ~1,370).
- Độ dài track (số frame có nhãn): min 1, median 91, mean 154, max 2,635.
  Track trong XML liên tục: `frame_span` luôn bằng số frame có nhãn.

### 2.2 Phân phối class (toàn bộ object)

| Class | Số bbox | Tỷ lệ |
|---|---:|---:|
| car | 1,052,408 | 82.6% |
| bus | 105,436 | 8.3% |
| van | 95,570 | 7.5% |
| others | 20,641 | 1.6% |

Dữ liệu mất cân bằng mạnh về `car`; `others` rất ít, nên báo cáo thêm AP theo
từng class thay vì chỉ mAP.

### 2.3 Điều kiện quay (thuộc tính sequence)

| `sence_weather` | train | val | test | Tổng |
|---|---:|---:|---:|---:|
| cloudy | 19 | 0 | 11 | 30 |
| night | 11 | 5 | 12 | 28 |
| rainy | 7 | 3 | 9 | 19 |
| sunny | 11 | 4 | 8 | 23 |

| `camera_state` | train | val | test | Tổng |
|---|---:|---:|---:|---:|
| stable | 18 | 4 | 31 | 53 |
| unstable | 30 | 8 | 9 | 47 |

Val không có sequence `cloudy`; kết quả val trên điều kiện mây cần đối chiếu
thêm ở test.

### 2.4 Bbox

- Kích thước trung bình: ~98 × 64 px (train 91 × 62, val 97 × 69, test 103 × 64).
- 130,181 bbox (≈10%) vượt ra ngoài khung ảnh vì xe bị cắt ở mép.
  Canonical và MOT giữ nguyên tọa độ gốc; chỉ nhãn YOLO được clip vào ảnh.

## 3. Cấu trúc thư mục

```text
dataset_v1.1/
├── canonical/          # Nguồn annotation chung, đầy đủ thuộc tính
│   ├── annotations.csv
│   ├── occlusion_regions.csv
│   └── ignored_regions.csv
├── detection/          # Định dạng YOLO
│   ├── images/<sequence>/img00001.jpg
│   ├── labels/<sequence>/img00001.txt
│   ├── train.txt  val.txt  test.txt
│   ├── train_n{500,1000,5000}_seed42.{txt,yaml,json}   # subset train
│   └── data.yaml
├── tracking/           # Định dạng MOT
│   └── <sequence>/
│       ├── img1/000001.jpg      # toàn bộ frame liên tục
│       ├── gt/gt.txt
│       └── seqinfo.ini
└── metadata/           # Thống kê, split, config, manifest
```

Quy ước frame: `frame_id` bắt đầu từ 1 và trùng với số trong tên ảnh
(`img00001.jpg` của YOLO, `000001.jpg` của MOT, `<frame num="1">` trong XML).

## 4. Thuộc tính annotation

### 4.1 `canonical/annotations.csv` — một dòng cho mỗi bbox

| Cột | Nguồn XML | Ý nghĩa |
|---|---|---|
| `sequence` | `sequence@name` | Tên sequence, ví dụ `MVI_20011` |
| `frame_id` | `frame@num` | Số thứ tự frame, bắt đầu từ 1 |
| `track_id` | `target@id` | ID của xe, duy nhất trong một sequence (không duy nhất giữa các sequence) |
| `class_name`, `class_id` | `attribute@vehicle_type` | Loại xe và ID theo `classes.yaml` |
| `x`, `y`, `w`, `h` | `box@left/top/width/height` | Bbox dạng góc trái trên + kích thước, đơn vị pixel, chưa clip |
| `truncation` | `attribute@truncation_ratio` | Tỷ lệ phần xe nằm ngoài khung ảnh, 0–1. 12.9% bbox có giá trị > 0 |
| `orientation` | `attribute@orientation` | Hướng xe, 0–360 (độ) |
| `speed` | `attribute@speed` | Tốc độ xe (m/s). Median 1.63, p99 27.6, max 104; 0.16% giá trị > 40 m/s (144 km/h) nên coi là nhiễu khi dùng |
| `trajectory_length` | `attribute@trajectory_length` | Độ dài cả trajectory của track (số frame), lặp lại trên mọi dòng của track |
| `occlusion`, `occlusion_ratio` | `attribute@occlusion_ratio` | **Luôn rỗng**: XML UA-DETRAC không có thuộc tính này. Thông tin che khuất nằm ở `occlusion_regions` |
| `occlusion_region_count` | `occlusion/region_overlap` | Số vùng bị che của bbox |
| `occlusion_regions_json` | `occlusion/region_overlap` | Các vùng bị che dạng JSON (xem 4.2) |
| `occlusion_xml` | `occlusion` | Node `<occlusion>` gốc, giữ để đối chiếu |
| `attributes_json` | `attribute` | Toàn bộ thuộc tính gốc của target dạng JSON |

### 4.2 `canonical/occlusion_regions.csv` — một dòng cho mỗi vùng bị che

Cột: `sequence, frame_id, track_id, left, top, width, height, occlusion_id,
occlusion_status, attributes_json`. `(sequence, frame_id, track_id)` trỏ về bbox
bị che trong `annotations.csv`; `left/top/width/height` là vùng bị che (pixel).

Tổng cộng 464,902 vùng. Theo paper UA-DETRAC, tỷ lệ che khuất là phần bbox bị
che: partial 1–50%, heavy > 50%.

| `occlusion_status` | Ý nghĩa | Số vùng | `occlusion_id` |
|---:|---|---:|---|
| 1 | Heavy occlusion (> 50%) | 231,881 | `track_id` của xe che, cùng frame |
| 0 | Partial occlusion (1–50%) | 139,227 | `track_id` của xe che, cùng frame |
| -1 | Bị nền che (cây, cột, biển báo…) | 93,794 | Luôn bằng 0, không trỏ tới xe nào |

Ý nghĩa của `-1` được suy ra từ dữ liệu (`occlusion_id = 0`) và Fig. 2 của paper
(phân biệt "occluded by other vehicles" và "occluded by background").

Có thể dùng bảng này để phân tích lỗi tracking theo mức che khuất (ví dụ ID
switch khi hai track che nhau).

### 4.3 `canonical/ignored_regions.csv`

Vùng ignore của sequence (`sequence, x, y, w, h`), không gắn với frame: 407 vùng
trên 95 sequence. Đây là vùng có xe nhưng không được gán nhãn (xe ở xa, bãi đỗ…).
Theo `preprocessing.yaml`, bbox GT không bị xoá; khi đánh giá, prediction có
≥ 50% diện tích nằm trong vùng ignore sẽ bị bỏ qua.

### 4.4 Thuộc tính frame và sequence (`metadata/`)

| File | Nội dung |
|---|---|
| `sequences.csv` | Mỗi sequence: `num_frames`, `num_annotated_frames`, `unannotated_frames`, `annotation_coverage`, `sence_weather`, `camera_state`, `sequence_attributes_json`, `width`, `height`, `fps`, `split` |
| `annotated_frames.csv` | Mỗi frame có nhãn: `density` (= số object trong frame, đã kiểm 100% khớp), `attributes_json` |
| `unannotated_frames.csv` | Frame có ảnh nhưng XML không khai báo (1,879 frame thuộc 18 sequence) |
| `objects_per_frame.csv` | Số object mỗi frame |
| `track_lengths.csv` | Mỗi track: số frame có nhãn, frame đầu/cuối, `trajectory_length` XML min/max, `speed` min/max, `frame_span` |
| `class_distribution.csv`, `dataset_stats.csv` | Thống kê tổng hợp ở mục 2 |
| `raw_inventory.json` | Kết quả kiểm tra raw lúc export ảnh |
| `manifest.json` | Phiên bản, split, config và SHA256 của XML nguồn |
| `*.yaml` | Bản sao config đã dùng để tạo dataset |

`sence_weather` giữ đúng chính tả của XML gốc.

## 5. Dùng cho Detection (YOLO)

- **Ảnh**: chỉ frame có nhãn. Train lấy mẫu stride 5 (frame 1, 6, 11, …) để
  giảm các frame gần trùng nhau; val và test dùng mọi frame có nhãn.

  | Split | Số ảnh |
  |---|---:|
  | train | 13,376 |
  | val | 15,274 |
  | test | 56,167 |

- **Nhãn** `labels/<sequence>/imgNNNNN.txt`: mỗi dòng
  `class_id x_center y_center width height`, chuẩn hoá 0–1 theo 960 × 540, bbox
  đã clip vào ảnh. Frame có nhãn nhưng không có xe → file rỗng (negative hợp lệ).
- **Frame chưa gán nhãn** không có trong YOLO: không phải negative, vì xe có thể
  có mặt nhưng không được gán nhãn.
- **Subset** `train_n{500,1000,5000}_seed42`: lấy ngẫu nhiên từ `train.txt`,
  dùng để thử nhanh hoặc khảo sát lượng dữ liệu.
- `data.yaml` chứa đường dẫn tuyệt đối; sau khi chuyển thư mục cần chạy
  `scripts/rebase_dataset.py`.

## 6. Dùng cho Tracking (MOT)

- **Ảnh** `tracking/<sequence>/img1/`: toàn bộ timeline liên tục, kể cả frame
  chưa gán nhãn, để motion model (Kalman, UCMC) không bị đứt quãng.
- **`gt/gt.txt`**, mỗi dòng:

  ```text
  frame_id, track_id, x, y, w, h, conf, class_id, visibility
  ```

  - `x, y, w, h`: tọa độ gốc, không clip.
  - `conf` luôn bằng 1 (mọi GT đều được đánh giá).
  - `class_id` theo `classes.yaml` (khác MOTChallenge, nơi cột 8 là class của MOT).
  - `visibility` luôn bằng -1: XML không có tỷ lệ hiển thị; dùng
    `occlusion_regions.csv` nếu cần thông tin che khuất.
- **`seqinfo.ini`**: `frameRate=25`, `seqLength`, `imWidth=960`, `imHeight=540`.
- **DeepSORT**: ReID có thể dùng `track_id` của train để cắt crop theo identity;
  `track_id` chỉ duy nhất trong một sequence, nên khoá identity là
  `(sequence, track_id)`.
- **Bàn giao Detection → Tracking**: định dạng prediction (ma trận `(N,6)`
  `[x1,y1,x2,y2,conf,class_id]` theo pixel ảnh gốc, `frame_id` từ 1, không bỏ
  frame rỗng) và adapter cho từng tracker, gồm cả tham số camera của UCMCTrack,
  xem [DETECTION_TRACKING_ADAPTER_GUIDE_EN.md](DETECTION_TRACKING_ADAPTER_GUIDE_EN.md).
  Detector chỉ chạy một lần; ba tracker dùng chung một file detection.

## 7. Giao thức đánh giá

Theo paper UA-DETRAC (Wen et al., arXiv:1511.04136, mục 3).

### 7.1 Detection

- Vẽ đường PR bằng cách thay đổi ngưỡng confidence; xếp hạng bằng **AP** kiểu
  PASCAL VOC (trung bình precision tại 11 mức recall 0, 0.1, …, 1).
- Một detection đúng khi **IoU ≥ 0.7** với GT (chặt hơn mức 0.5 của COCO/VOC).
- Trước khi tính: bỏ frame chưa gán nhãn và bỏ prediction nằm ≥ 50% trong
  ignored region (`preprocessing.yaml`).

### 7.2 Tracking

1. Lấy 10 ngưỡng confidence chia đều từ điểm nhỏ nhất đến lớn nhất của detector.
2. Với mỗi ngưỡng: lọc detection, chạy tracker, tính precision/recall của
   detection và các chỉ số CLEAR (MOTA, MOTP, MT, ML, IDS, FM, FP, FN), ghép
   track với GT theo **IoU ≥ 0.7**.
3. Mỗi chỉ số X được tích phân dọc đường PR:
   `PR-X = ½ ∫_C X(p, r) ds`, xấp xỉ bằng
   `½ Σ X(p_i, r_i) · ‖(p_i, r_i) − (p_{i−1}, r_{i−1})‖`.
4. Báo cáo **PR-MOTA, PR-MOTP, PR-MT, PR-ML, PR-IDS, PR-FM, PR-FP, PR-FN**;
   xếp hạng theo PR-MOTA, hoà thì xét PR-MOTP. Kèm AP của detector.

MT/ML: tỷ lệ trajectory GT được track > 80% / < 20% độ dài.

### 7.3 Lưu ý khi dùng TrackEval

TrackEval tính được các chỉ số CLEAR tại một ngưỡng (ngoài ra có HOTA, IDF1 để
tham khảo), nhưng không có sẵn PR-*. Cần viết thêm:

- Vòng lặp 10 ngưỡng và phép tích phân ở mục 7.2.
- Đặt ngưỡng IoU của CLEAR là 0.7 (mặc định của TrackEval là 0.5).
- Chuyển GT trước khi đánh giá: loader `MotChallenge2DBox` hiểu cột 8 theo
  class của MOT17 (1 = pedestrian, 2/7/8/12 = distractor), nên `class_id` của
  dataset này (0–3) sẽ bị hiểu sai. Đặt cột 8 = 1 (class-agnostic) hoặc viết
  dataset class riêng.
- Lọc frame chưa gán nhãn và ignored regions khỏi cả GT lẫn prediction; TrackEval
  không tự làm việc này.

## 8. Thách thức của dataset

| Thách thức | Số liệu | Ảnh hưởng |
|---|---|---|
| **Che khuất** | 29.4% bbox có ít nhất một vùng bị che. Theo mức nặng nhất: heavy 14.5%, partial 8.4%, chỉ bị nền che 6.5% | Detector bỏ sót xe bị che; tracker dễ ID switch hoặc đứt track (FM) khi hai xe đi sát nhau |
| **Ánh sáng, thời tiết** | `night` 28 sequence (21.5% bbox), `rainy` 19 sequence (25.9% bbox) | Đèn pha chói, phản chiếu mặt đường ướt, nhoè do mưa; đặc trưng ReID của DeepSORT kém ổn định |
| **Biến thiên kích thước** | √diện tích từ 9 đến 590 px; small (< 50 px) 34.7%, medium 56.6%, large (> 150 px) 8.7%; 5% bbox cao < 23 px | Xe nhỏ ở xa khó phát hiện ở IoU 0.7; một track đổi kích thước mạnh khi xe tiến lại gần. Không nên giảm `imgsz` dưới 960 |
| **Mất cân bằng class** | car 82.6%, bus 8.3%, van 7.5%, others 1.6%. Phân phối lệch giữa các split: bus 4.7% ở train và 10.6% ở test; others 0.6% và 2.5% | AP của `others` và `bus` thấp, mAP bị `car` chi phối; kết quả test khác val |
| **Truncation** | 12.9% bbox bị cắt ở mép, 4.7% bị cắt ≥ 50%; ~10% bbox vượt khung ảnh | Bbox không đầy đủ khi xe vào/ra khung; YOLO dùng bbox đã clip, MOT giữ bbox gốc |
| **Mật độ** | Test trung bình 12 object/frame so với 7 ở train | Test khó hơn train về che khuất và số ID cần giữ |
| **Nhãn thiếu** | 1,879 frame chưa gán nhãn (18 sequence); 407 ignored region (95 sequence) | Nếu không lọc, prediction đúng bị tính là FP |
| **Camera** | 47/100 sequence `camera_state=unstable` | Rung camera làm lệch dự đoán chuyển động của Kalman |

### 8.1 Khó khăn khi tạo input cho UCMCTrack

UCMCTrack không ghép bbox trên mặt phẳng ảnh mà chiếu điểm chân bbox xuống mặt
đất bằng tham số camera (intrinsic + extrinsic), rồi ghép theo khoảng cách
Mahalanobis trên mặt đất. Vì vậy:

- **UA-DETRAC không cung cấp tham số camera.** Phải ước lượng thủ công cho từng
  cảnh (ví dụ chỉnh tham số tới khi các vạch kẻ đường song song khi chiếu xuống
  mặt đất). Không được tự bịa tham số; thiếu calibration thì adapter phải báo lỗi.
- **Nhiều cảnh**: 100 sequence quay ở 24 địa điểm. Tiền tố tên sequence tạo 16
  nhóm (`MVI_200xx`, `MVI_407xx`, …) có thể dùng chung calibration, nhưng cần
  kiểm tra bằng mắt trước khi dùng lại.
- **Camera không cố định**: với 47 sequence `unstable`, một bộ tham số cho cả
  sequence sẽ sai dần khi camera rung hoặc lệch.
- **Sai số chiếu**: xe nhỏ ở xa (34.7% bbox dưới 50 px) và bbox bị truncation
  làm điểm chân bbox sai, nên vị trí trên mặt đất sai nhiều hơn so với xe ở gần.
- **Kết quả phụ thuộc chất lượng calibration**: khi so sánh với OC-SORT và
  DeepSORT phải ghi lại nguồn calibration và đánh giá trên cùng tập sequence.
