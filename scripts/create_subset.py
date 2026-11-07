"""Tạo subset train detection có seed, không đổi validation/test."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from src.data.utils import sha256, write_json


def create_subset(detection, count, seed):
    detection = Path(detection).resolve()
    paths = (detection / "train.txt").read_text().splitlines()
    if not 0 < count <= len(paths):
        raise ValueError(f"count must be in [1, {len(paths)}]")
    # Xáo trộn ổn định trong từng sequence rồi lấy luân phiên để một sequence dài không chiếm hết subset.
    groups = {}
    for path in paths:
        groups.setdefault(Path(path).parent.name, []).append(path)
    key = lambda value: hashlib.sha256(f"{seed}:{value}".encode()).hexdigest()
    for values in groups.values():
        values.sort(key=key)
    names = sorted(groups, key=key)
    order = [groups[s][i] for i in range(max(map(len, groups.values()))) for s in names if i < len(groups[s])]
    prefix = f"train_n{count}_seed{seed}"
    targets = [detection / f"{prefix}{ext}" for ext in (".txt", ".yaml", ".json")]
    if any(p.exists() for p in targets):
        raise FileExistsError(f"Subset already exists: {prefix}")
    # Chỉ tạo train list riêng; val/test gốc không bị thay đổi.
    targets[0].write_text("\n".join(order[:count]) + "\n")
    config = yaml.safe_load((detection / "data.yaml").read_text())
    config.update(path=str(detection), train=targets[0].name)
    targets[1].write_text(yaml.safe_dump(config, sort_keys=False))
    write_json(targets[2], dict(count=count, seed=seed, strategy="sequence_round_robin_hash_order",
        parent_train_sha256=sha256(detection / "train.txt"),
        val_sha256=sha256(detection / "val.txt"), test_sha256=sha256(detection / "test.txt"),
        selected_sha256=sha256(targets[0]), selected=order[:count]))
    return targets[1]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detection", type=Path)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(create_subset(args.detection, args.count, args.seed))
