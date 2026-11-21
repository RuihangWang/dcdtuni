"""tools/gen_pbr_textures.py keeps the texture pool and its manifest reproducible.

Run: python -m pytest tools/test_gen_pbr_textures.py
"""
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('gen_pbr_textures', HERE / 'gen_pbr_textures.py')
gen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gen
spec.loader.exec_module(gen)


class GeneratorTest(unittest.TestCase):
    def test_check_is_byte_identical(self):
        """Every generated file, the measured manifest fields and CREDITS regenerate bit for bit."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gen.main(['--check'])
        self.assertEqual(code, 0, out.getvalue())

    def test_every_generated_set_names_a_known_recipe(self):
        manifest = json.loads((gen.TEXTURES / gen.MANIFEST).read_text())
        for name, entry in manifest['sets'].items():
            if entry['source']['kind'] == 'generated':
                self.assertIn(entry['source']['recipe'], {**gen.SQUARE_RECIPES, **gen.RECIPES}, name)

    def test_a_recipe_is_deterministic_and_tiles(self):
        """Same seed, same bytes; and the colour map's opposite edges meet (periodic layout)."""
        manifest = json.loads((gen.TEXTURES / gen.MANIFEST).read_text())
        entry = manifest['sets']['floor-tile']
        a = gen.build_generated(entry)
        b = gen.build_generated(entry)
        self.assertEqual(a, b)
        from PIL import Image
        import numpy as np
        img = np.asarray(Image.open(io.BytesIO(a[entry['maps']['color']['file']])).convert('L'), dtype=float)
        # The step across the wrap is no larger than an ordinary step inside the tile.
        wrap = np.abs(img[:, 0] - img[:, -1]).mean()
        inner = np.abs(np.diff(img, axis=1)).mean()
        self.assertLess(wrap, 3 * inner + 1.0)

    def test_an_lfs_pointer_is_reported_not_measured(self):
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(io.StringIO()):
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    (Path(tmp) / 'x').mkdir()
                    (Path(tmp) / 'x' / 'color.jpg').write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:00\n')
                    gen.read_pool_file(Path(tmp), 'x/color.jpg')
        self.assertIn('git lfs pull', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
