import ast
import json
import unittest
from pathlib import Path


class NotebookTests(unittest.TestCase):
    def test_notebook_cells_parse_with_saved_outputs(self):
        folder = Path(__file__).resolve().parents[2] / 'notebooks/data'
        for name in ['00b_run_pipeline.ipynb', '01_dataset_analysis.ipynb', '02_split_analysis.ipynb', '03_visual_validation.ipynb']:
            nb = json.loads((folder / name).read_text(encoding='utf-8'))
            self.assertEqual(nb['nbformat'], 4)
            for i, cell in enumerate(nb['cells']):
                if cell['cell_type'] == 'code':
                    ast.parse(''.join(cell['source']), filename=f'{name}:cell{i}')
                    self.assertIsInstance(cell['outputs'], list)
