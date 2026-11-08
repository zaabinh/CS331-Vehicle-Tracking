"""Small end-to-end fixture: empty frame, occlusion, splits, exports and relocation."""
import json
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import pandas as pd
import yaml
from PIL import Image
from scripts.prepare_dataset import prepare
from scripts.package_dataset import package
from scripts.create_subset import create_subset
from scripts.rebase_dataset import rebase
from src.data.parse_detrac import parse_detrac, inspect_raw
from src.data.split import create_splits, load_splits
from src.data.utils import xywh_to_yolo
from src.data.validate import validate_exports


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw, self.out = self.root / 'raw', self.root / 'dataset'
        self.config = self.root / 'configs'
        self.config.mkdir()
        for path in (ROOT / 'configs/data').glob('*.yaml'):
            shutil.copyfile(path, self.config / path.name)
        self.official = dict(train=['MVI_1', 'MVI_2', 'MVI_3'], test=['MVI_4'])
        (self.config / 'official_sequences.yaml').write_text(yaml.safe_dump(self.official))
        self.splits = create_splits(self.official['train'], self.official['test'],
            yaml.safe_load((self.config / 'split.yaml').read_text()))
        self.split_dir = self.root / 'splits'
        self.split_dir.mkdir()
        for part, names in self.splits.items():
            (self.split_dir / f'{part}_sequences.txt').write_text('\n'.join(names))
        for sequence in sum(self.official.values(), []):
            folder = self.raw / 'DETRAC-Images' / sequence
            folder.mkdir(parents=True)
            frames = []
            for frame in range(1, 7):
                Image.new('RGB', (960, 540)).save(folder / f'img{frame:05d}.jpg')
                target = ''
                if frame != 2:
                    target = (
                        '<target id="7"><box left="10" top="20" width="50" '
                        'height="40"/><attribute vehicle_type="car" '
                        'truncation_ratio="0.1" orientation="90" speed="2.5" '
                        'trajectory_length="5"/><occlusion>'
                        '<region_overlap left="10" top="20" width="5" '
                        'height="5" occlusion_id="5" '
                        'occlusion_status="1"/></occlusion></target>'
                    )
                frames.append(f'<frame num="{frame}" density="1"><target_list>{target}</target_list></frame>')
            annotation = self.raw / ('DETRAC-Test-Annotations-XML' if sequence in self.official['test'] else 'DETRAC-Train-Annotations-XML')
            annotation.mkdir(exist_ok=True)
            (annotation / f'{sequence}.xml').write_text(f'<sequence name="{sequence}"><sequence_attribute camera_state="stable" sence_weather="sunny"/><ignored_region><box left="0" top="0" width="10" height="10"/></ignored_region>{"".join(frames)}</sequence>')

    def run_pipeline(self):
        prepare(self.raw, self.out, self.split_dir, self.config)

    def test_labels_only_rebuilds_annotations_without_raw_images(self):
        self.run_pipeline()
        inventory = (self.out / 'metadata/raw_inventory.json').read_text()
        sequence = self.splits['val'][0]
        image = self.out / 'tracking' / sequence / 'img1/000001.jpg'
        mtime = image.stat().st_mtime_ns
        shutil.rmtree(self.raw / 'DETRAC-Images')
        xml = self.raw / 'DETRAC-Train-Annotations-XML' / f'{sequence}.xml'
        xml.write_text(xml.read_text().replace('left="10" top="20" width="50"', 'left="11" top="20" width="50"'))
        prepare(self.raw, self.out, self.split_dir, self.config, labels_only=True)
        self.assertEqual(json.loads((self.out / 'metadata/validation_report.json').read_text())['status'], 'PASSED')
        self.assertEqual((self.out / 'metadata/raw_inventory.json').read_text(), inventory)
        self.assertNotIn('xml_path', pd.read_csv(self.out / 'metadata/sequences.csv').columns)
        gt = (self.out / 'tracking' / sequence / 'gt/gt.txt').read_text().splitlines()[0]
        self.assertTrue(gt.startswith('1,7,11.0,20.0,'))
        self.assertEqual(image.stat().st_mtime_ns, mtime)
        # Mất cả metadata: suy split từ tracking/, inventory lấy từ thư mục cha nếu có.
        shutil.rmtree(self.out / 'metadata')
        (self.root / 'raw_inventory.json').write_text(inventory)
        prepare(self.raw, self.out, self.split_dir, self.config, labels_only=True)
        manifest = json.loads((self.out / 'metadata/manifest.json').read_text())
        self.assertEqual(manifest['scope'], 'full')
        self.assertEqual(manifest['splits'], self.splits)
        self.assertEqual((self.out / 'metadata/raw_inventory.json').read_text(), inventory)
        with self.assertRaises(FileNotFoundError):
            prepare(self.raw, self.root / 'missing', self.split_dir, self.config, labels_only=True)

    def test_bbox_roundtrip(self):
        xc, yc, w, h = xywh_to_yolo(10, 20, 50, 40, 960, 540)
        for actual, expected in zip(((xc-w/2)*960, (yc-h/2)*540, w*960, h*540), (10,20,50,40)):
            self.assertAlmostEqual(actual, expected)

    def test_split(self):
        self.assertEqual(load_splits(self.split_dir), self.splits)
        self.assertEqual(self.splits['test'], self.official['test'])
        (self.split_dir / 'val_sequences.txt').write_text(self.splits['train'][0])
        with self.assertRaises(ValueError):
            load_splits(self.split_dir)

    def test_export_subset_package_relocation(self):
        self.run_pipeline()
        self.assertEqual(json.loads((self.out / 'metadata/validation_report.json').read_text())['status'], 'PASSED')
        annotated = pd.read_csv(self.out / 'metadata/annotated_frames.csv')
        sequence = self.splits['val'][0]
        self.assertIn(2, annotated.loc[annotated.sequence == sequence, 'frame_id'].tolist())
        self.assertNotIn('annotated_frame_ids', pd.read_csv(
            self.out / 'metadata/sequences.csv'
        ).columns)
        sequence_metadata = pd.read_csv(
            self.out / 'metadata/sequences.csv'
        ).set_index('sequence')
        self.assertEqual(sequence_metadata.loc[sequence, 'sence_weather'], 'sunny')
        self.assertEqual(sequence_metadata.loc[sequence, 'camera_state'], 'stable')
        self.assertNotIn('weather', sequence_metadata.columns)
        frame_metadata = pd.read_csv(self.out / 'metadata/annotated_frames.csv')
        self.assertEqual(frame_metadata.iloc[0].density, 1)
        self.assertEqual((self.out / 'detection/labels' / sequence / 'img00002.txt').read_text(), '')
        self.assertEqual(len(list((self.out / 'tracking' / sequence / 'img1').glob('*.jpg'))), 6)
        first_gt = (self.out / 'tracking' / sequence / 'gt/gt.txt').read_text().splitlines()[0]
        self.assertEqual(first_gt, '1,7,10.0,20.0,50.0,40.0,1,0,-1')
        df = pd.read_csv(self.out / 'canonical/annotations.csv')
        self.assertTrue(df.occlusion.isna().all())
        self.assertTrue(df.occlusion_ratio.isna().all())
        self.assertTrue((df.speed == 2.5).all())
        self.assertTrue((df.trajectory_length == 5).all())
        self.assertTrue(df.occlusion_xml.str.contains('region_overlap').all())
        self.assertEqual(
            df.loc[df.sequence == sequence, 'occlusion_region_count'].sum(), 5
        )
        overlaps = pd.read_csv(self.out / 'canonical/occlusion_regions.csv')
        overlaps = overlaps[overlaps.sequence == sequence]
        self.assertEqual(len(overlaps), 5)
        self.assertEqual(overlaps.iloc[0].occlusion_id, 5)
        self.assertEqual(overlaps.iloc[0].occlusion_status, 1)
        self.assertEqual(overlaps.iloc[0].width, 5)
        track = pd.read_csv(self.out / 'metadata/track_lengths.csv')
        track = track[(track.sequence == sequence) & (track.track_id == 7)].iloc[0]
        self.assertEqual(track.annotated_frame_count, 5)
        self.assertEqual(track.trajectory_length_xml_min, 5)
        detection = self.out / 'detection'
        val_before = (detection / 'val.txt').read_bytes()
        small = create_subset(detection, 2, 42)
        large = create_subset(detection, 3, 42)
        self.assertEqual(small.with_suffix('.txt').read_text().splitlines(), large.with_suffix('.txt').read_text().splitlines()[:2])
        self.assertEqual(val_before, (detection / 'val.txt').read_bytes())
        with self.assertRaises(ValueError):
            create_subset(detection, 999, 42)
        with self.assertRaises(ValueError):
            package(self.out, self.root / 'artifacts')
        manifest_path = self.out / 'metadata/manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['visual_inspection'] = 'PASSED'
        manifest_path.write_text(json.dumps(manifest))
        package(self.out, self.root / 'artifacts')
        self.assertEqual(len(list((self.root / 'artifacts').glob('*.tar'))), 3)
        relocated = self.root / 'relocated'
        shutil.copytree(detection, relocated)
        rebase(relocated)
        self.assertEqual(yaml.safe_load((relocated / 'data.yaml').read_text())['path'], str(relocated.resolve()))
        for relative in (relocated / 'train.txt').read_text().splitlines():
            self.assertTrue((relocated / relative).is_file())
        with self.assertRaises(FileExistsError):
            self.run_pipeline()

    def test_invalid_box_fails(self):
        path = next(self.raw.rglob('*.xml'))
        path.write_text(path.read_text().replace('width="50"', 'width="-50"'))
        with self.assertRaises(ValueError):
            self.run_pipeline()
        self.assertEqual(json.loads((self.out / 'metadata/validation_report.json').read_text())['status'], 'FAILED')

    def test_missing_image_fails(self):
        next(self.raw.rglob('img00002.jpg')).unlink()
        with self.assertRaises(ValueError):
            self.run_pipeline()

    def test_duplicate_track_and_unknown_class_fail(self):
        path = next(self.raw.rglob('*.xml'))
        text = path.read_text()
        start, end = text.index('<target id='), text.index('</target>') + len('</target>')
        path.write_text(text[:end] + text[start:end] + text[end:])
        with self.assertRaises(ValueError):
            self.run_pipeline()
        shutil.rmtree(self.out)
        path.write_text(text.replace('vehicle_type="car"', 'vehicle_type="spaceship"'))
        with self.assertRaises(ValueError):
            self.run_pipeline()

    def test_sparse_annotation_frames_are_allowed_but_invalid_xml_fails(self):
        path = next(self.raw.rglob('*.xml'))
        text = path.read_text()
        text = text.replace(
            '<frame num="2" density="1"><target_list></target_list></frame>', ''
        )
        path.write_text(text)
        annotations, _, meta = parse_detrac(path, num_frames=6)
        self.assertEqual(meta['num_frames'], 6)
        self.assertEqual(meta['num_annotated_frames'], 5)
        self.assertNotIn(2, annotations.frame_id.unique())

        path.write_text('<sequence')
        with self.assertRaises(ET.ParseError):
            parse_detrac(path)

    def test_parse_preserves_direct_ratio_and_region_attributes(self):
        path = next(self.raw.rglob('*.xml'))
        text = path.read_text().replace(
            'truncation_ratio="0.1"',
            'truncation_ratio="0.1" occlusion_ratio="0.25"',
            1,
        )
        path.write_text(text)
        annotations, _, _ = parse_detrac(path)
        first = annotations.iloc[0]
        self.assertEqual(first.occlusion_ratio, 0.25)
        self.assertEqual(first.occlusion, 0.25)
        region = json.loads(first.occlusion_regions_json)[0]
        self.assertEqual(region['occlusion_id'], '5')
        self.assertEqual(region['occlusion_status'], '1')
        self.assertEqual(json.loads(region['attributes_json'])['width'], '5')

    def test_ratio_values_outside_zero_one_are_preserved(self):
        path = next(self.raw.rglob('*.xml'))
        path.write_text(
            path.read_text().replace('truncation_ratio="0.1"',
                                     'truncation_ratio="-3"', 1)
        )

        self.run_pipeline()

        annotations = pd.read_csv(self.out / 'canonical/annotations.csv')
        self.assertTrue((annotations.truncation == -3).any())
        report = json.loads(
            (self.out / 'metadata/validation_report.json').read_text()
        )
        self.assertEqual(report['status'], 'PASSED')

    def test_flattened_raw_and_smoke(self):
        for directory in list(self.raw.iterdir()):
            for child in list(directory.iterdir()):
                shutil.move(str(child), str(self.raw / child.name))
            directory.rmdir()
        prepare(self.raw, self.out, self.split_dir, self.config, smoke=True)
        manifest = json.loads((self.out / 'metadata/manifest.json').read_text())
        self.assertEqual(manifest['scope'], 'smoke')
        self.assertEqual(sum(map(len, manifest['splits'].values())), 3)

    def test_package_detects_tampered_yolo(self):
        self.run_pipeline()
        path = next(p for p in (self.out / 'detection/labels').rglob('*.txt') if p.read_text())
        path.write_text('0 0.5 0.5 0.1 0.1\n')
        with self.assertRaises(ValueError):
            package(self.out, self.root / 'artifacts', revalidate=True)
        self.assertEqual(json.loads((self.out / 'metadata/validation_report.json').read_text())['status'], 'FAILED')

    def test_smoke_skips_missing_sequence_without_changing_fixed_split(self):
        missing = self.splits['train'][0]
        shutil.rmtree(self.raw / 'DETRAC-Images' / missing)
        prepare(self.raw, self.out, self.split_dir, self.config, smoke=True)
        report = json.loads((self.out / 'metadata/raw_inventory.json').read_text())
        self.assertEqual(report['missing_images'], [missing])
        manifest = json.loads((self.out / 'metadata/manifest.json').read_text())
        self.assertNotIn(missing, manifest['splits']['train'])
        self.assertIn(missing, load_splits(self.split_dir)['train'])
        full = self.root / 'full'
        with self.assertRaisesRegex(ValueError, 'Full dataset requires'):
            prepare(self.raw, full, self.split_dir, self.config)
        self.assertTrue((full / 'metadata/raw_inventory.json').is_file())

    def test_smoke_requires_complete_frames_and_each_split(self):
        sequence = self.splits['val'][0]
        (self.raw / 'DETRAC-Images' / sequence / 'img00002.jpg').unlink()
        with self.assertRaisesRegex(ValueError, 'No complete sequence'):
            prepare(self.raw, self.out, self.split_dir, self.config, smoke=True)
        report = json.loads((self.out / 'metadata/raw_inventory.json').read_text())
        row = next(r for r in report['sequences'] if r['sequence'] == sequence)
        self.assertEqual(row['missing_image_frames'], [2])

    def test_inventory_nested_images_case_and_duplicates(self):
        folder = self.raw / 'DETRAC-Images' / self.splits['train'][0]
        nested = folder / 'image'
        nested.mkdir()
        for path in folder.glob('*.jpg'):
            path.rename(nested / (path.stem.upper() + '.JPG'))
        report, _, images = inspect_raw(self.raw, sum(self.official.values(), []))
        self.assertEqual(report['ready_sequences'], 4)
        self.assertEqual(len(images[folder.name]), 6)
        shutil.copyfile(nested / 'IMG00001.JPG', folder / 'img00001.jpg')
        report, _, images = inspect_raw(self.raw, sum(self.official.values(), []))
        self.assertNotIn(folder.name, images)
        row = next(r for r in report['sequences'] if r['sequence'] == folder.name)
        self.assertIn('duplicate_image_frame: 1', row['issues'])


if __name__ == '__main__':
    unittest.main()
