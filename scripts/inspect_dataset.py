"""Kiểm kê XML và frame ảnh trước khi chuẩn bị dataset."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.parse_detrac import inspect_raw
from src.data.utils import read_yaml, write_json


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    # Đường dẫn tới data raw
    parser.add_argument('--raw', type=Path, required=True)
    # Trỏ tới config (danh sách lớp, sequences, processing config, split config)
    parser.add_argument('--configs', type=Path, default=ROOT / 'configs/data')
    # Vị trí tạo file inspect report raw_inventory.json
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    raw, report_path = args.raw.resolve(), args.report.resolve()
    # Report và raw không trùng nhau, không cùng 1 thư mục
    if report_path == raw or raw in report_path.parents:
        raise ValueError('Inventory report must be outside immutable raw')
    # Đọc các sequences của data
    official = read_yaml(args.configs / 'official_sequences.yaml')
    # Inventory chỉ đọc raw; report phải được lưu bên ngoài raw.
    # Inspect train và test sequences
    report, _, _ = inspect_raw(raw, official['train'] + official['test'])
    # Tạo báo cáo
    write_json(report_path, report)
    # In log
    print(json.dumps({k: v for k, v in report.items() if k != 'sequences'}, indent=2))
    print(f'Detailed inventory saved to {report_path}')
