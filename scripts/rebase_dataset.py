"""Run once after extracting detection.tar at a new location."""
import argparse
from pathlib import Path
import yaml


def rebase(detection):
    detection = Path(detection).resolve()
    for path in [detection / "data.yaml", *detection.glob("train_n*_seed*.yaml")]:
        config = yaml.safe_load(path.read_text())
        config["path"] = str(detection)
        path.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detection", type=Path)
    rebase(parser.parse_args().detection)
