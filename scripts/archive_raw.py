"""Đóng gói raw và kiểm tra checksum bản sao trên Drive."""
import argparse
import shutil
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.parse_detrac import inspect_raw
from src.data.utils import read_yaml, sha256, write_json


def archive_raw(raw, archive, destination, expected):
    raw, archive, destination = (Path(p).resolve() for p in (raw, archive, destination))
    if raw == archive or raw in archive.parents or raw == destination or raw in destination.parents:
        raise ValueError('Archive and backup destination must be outside raw')
    # Chỉ đặt tên backup hoàn chỉnh khi inventory xác nhận đủ sequence/frame.
    report, _, _ = inspect_raw(raw, expected)
    if report['ready_sequences'] != report['expected_sequences'] or report['unexpected_sequences']:
        raise ValueError('Raw inventory incomplete; refusing to mark this as a complete raw backup')
    target = destination / archive.name
    local_partial = archive.with_name(archive.name + '.partial')
    drive_partial = target.with_name(target.name + '.partial')
    manifest_path = target.with_name(target.name + '.json')
    if len({raw, archive, local_partial, target, drive_partial, manifest_path}) != 6:
        raise ValueError('Local archive and Drive backup must have distinct paths')
    if any(p.exists() for p in (archive, local_partial, target, drive_partial, manifest_path)):
        raise FileExistsError('Backup output already exists; choose a new name/destination')
    archive.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)
    # .partial giúp lần copy bị ngắt không bị nhầm thành archive hoàn tất.
    with tarfile.open(local_partial, 'x') as stream:
        stream.add(raw, arcname='raw')
    local_partial.rename(archive)
    digest = sha256(archive)
    print(f'Archive created: {archive}; copying one file to Drive', flush=True)
    with archive.open('rb') as source, drive_partial.open('xb') as target_stream:
        shutil.copyfileobj(source, target_stream, length=8 * 1024 * 1024)
    # Đọc lại bản trên Drive để kiểm tra dữ liệu sau khi truyền.
    if sha256(drive_partial) != digest:
        raise ValueError(f'Backup checksum mismatch; unverified copy retained at {drive_partial}')
    drive_partial.rename(target)
    write_json(manifest_path, dict(status='VERIFIED', sha256=digest, bytes=archive.stat().st_size,
        archive=target.name, inventory=report))
    print(f'VERIFIED (copied file read back and hashed): {target}', flush=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--configs', type=Path, default=ROOT / 'configs/data')
    args = parser.parse_args()
    official = read_yaml(args.configs / 'official_sequences.yaml')
    archive_raw(args.raw, args.archive, args.destination, official['train'] + official['test'])
