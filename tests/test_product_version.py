import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('product_version', ROOT/'scripts/product_version.py')


class VersionTests(unittest.TestCase):
    def module(self):
        self.assertTrue(Path(spec.origin).exists(), 'version lifecycle module missing')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_canonical_manifest_and_sync(self):
        module = self.module()
        manifest = module.load(ROOT)
        self.assertGreaterEqual(module.native_key(manifest['nativeVersion']), module.version_key(manifest['version'])[:3])
        self.assertEqual(manifest['profiles']['personal']['identifier'], 'com.account.probe')
        self.assertEqual(module.check(ROOT), [])

    def test_rejects_invalid_and_nonmonotonic_version(self):
        module = self.module()
        old = module.load(ROOT)
        for changes in ({'version': 'garbage'}, {'tag': 'vwrong'}, {'nativeVersion': '0.3.9'},
                        {'androidVersionCode': True}, {'androidVersionCode': 1},
                        {'profiles': {'personal': {'identifier': 'com.account.preview'}}}):
            candidate = copy.deepcopy(old); candidate.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                module.validate(candidate, previous=old)
        candidate = copy.deepcopy(old)
        candidate.update(version='0.4.0-preview.1', tag='v0.4.0-preview.1', nativeVersion='0.4.0')
        with self.assertRaises(ValueError): module.validate(candidate, previous=old)

    def test_sync_detects_and_repairs_only_generated_versions(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in module.VERSION_FILES + ['release.json']:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT/name, path)
            package = root/'app/package.json'
            obj = json.loads(package.read_text()); obj['version'] = '0.1.0'; package.write_text(json.dumps(obj))
            self.assertIn('app/package.json', module.check(root))
            module.sync(root)
            self.assertEqual(module.check(root), [])

    def test_preview_sequence_cannot_replace_native_installer_version_increment(self):
        module = self.module(); old = module.load(ROOT)
        candidate = copy.deepcopy(old)
        major, minor, patch, sequence = module.version_key(old['version'])
        new_version = f'{major}.{minor}.{patch}-preview.{sequence+1}'
        candidate.update(version=new_version, tag='v'+new_version)
        with self.assertRaises(ValueError): module.validate(candidate, previous=old)
        candidate['androidVersionCode'] += 1
        with self.assertRaises(ValueError): module.validate(candidate, previous=old)
        native_major,native_minor,native_patch=module.native_key(old['nativeVersion'])
        native = f'{native_major}.{native_minor}.{native_patch+1}'
        candidate.update(version=native+'-preview.1', tag='v'+native+'-preview.1', nativeVersion=native)
        candidate['androidVersionCode'] = max(candidate['androidVersionCode'], major*1_000_000+minor*1_000+patch+1)
        self.assertEqual(module.validate(candidate, previous=old), candidate)

    def test_preview_release_can_keep_product_series_with_newer_internal_installer(self):
        module=self.module(); old=copy.deepcopy(module.load(ROOT))
        old.update(version='0.4.0-preview.1',tag='v0.4.0-preview.1',nativeVersion='0.4.0',androidVersionCode=4000)
        candidate=copy.deepcopy(old)
        candidate.update(version='0.4.0-preview.2',tag='v0.4.0-preview.2',nativeVersion='0.4.2',androidVersionCode=4002)
        self.assertEqual(module.validate(candidate,previous=old),candidate)


if __name__ == '__main__': unittest.main()
