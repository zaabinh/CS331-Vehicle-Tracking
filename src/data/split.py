import random
from pathlib import Path


def create_splits(train_sequences, test_sequences, config):
    if config["split_method"] != "sequence" or not config["preserve_official_test"]:
        raise ValueError("Only sequence split preserving official test is supported")
    if abs(config["train_ratio"] + config["val_ratio"] - 1) > 1e-9:
        raise ValueError("Split ratios must sum to one")
    train = sorted(train_sequences)
    # Chỉ chia official train; official test luôn được giữ nguyên.
    random.Random(config["seed"]).shuffle(train)
    cut = round(len(train) * config["train_ratio"])
    if not 0 < cut < len(train):
        raise ValueError("Both train and validation need sequences")
    result = dict(train=sorted(train[:cut]), val=sorted(train[cut:]), test=sorted(test_sequences))
    validate_splits(result)
    return result


def validate_splits(splits):
    flat = [s for names in splits.values() for s in names]
    if len(flat) != len(set(flat)):
        raise ValueError("Duplicate sequence / split leakage")


def load_splits(directory):
    # Đọc split đã commit thay vì tạo split mới theo từng lần chạy.
    result = {part: (Path(directory) / f"{part}_sequences.txt").read_text().split()
              for part in ("train", "val", "test")}
    validate_splits(result)
    if any(not names for names in result.values()):
        raise ValueError("Empty fixed split")
    return result
