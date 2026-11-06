import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('product_version', ROOT/'scripts/product_version.py')


class VersionTests(unittest.TestCase):
    def test_generated_dashboard_and_python_versions_share_release_input(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            names=set(module.VERSION_FILES+['dashboard/package.json','dashboard/package-lock.json','pyproject.toml'])
            for name in names:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
            source=module.load(ROOT)
            for version,python in (('0.4.0-preview.5','0.4.0.dev5'),('0.4.0-alpha.0','0.4.0a0'),
                                   ('0.4.0-beta.1','0.4.0b1'),('0.4.0-rc.2','0.4.0rc2'),('0.4.0','0.4.0')):
                with self.subTest(version=version):
                    generated=module.generated(root,{**source,'version':version})
                    for name in ('dashboard/package.json','dashboard/package-lock.json'):
                        self.assertIn(name,set(generated))
                        package=json.loads(generated[name]);self.assertEqual(package['version'],version)
                        if 'packages' in package:self.assertEqual(package['packages']['']['version'],version)
                    python_project=tomllib.loads(generated['pyproject.toml'])
                    self.assertEqual(python_project['project']['version'],python)
                    self.assertFalse(python_project['tool']['uv']['package'])

    def test_product_versions_order_prereleases_before_stable(self):
        module = self.module()
        versions = ['0.4.0-alpha.0', '0.4.0-alpha.1', '0.4.0-beta.1',
                    '0.4.0-preview.4', '0.4.0-rc.1', '0.4.0', '0.4.1-alpha.0']
        self.assertEqual(sorted(versions, key=module.version_key), versions)
        for invalid in ('0.4', '0.4.0-alpha.01', '0.4.0-preview.0', '0.4.0-dev.1', '0.4.0+local'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): module.version_key(invalid)

    def test_stable_version_retains_strict_installer_monotonicity(self):
        module = self.module(); old = copy.deepcopy(module.load(ROOT))
        stable='.'.join(map(str,module.version_key(old['version'])[:3]))
        candidate = {**old, 'version':stable, 'tag':'v'+stable}
        with self.assertRaises(ValueError): module.validate(candidate, previous=old)
        major, minor, patch = module.native_key(old['nativeVersion'])
        candidate.update(nativeVersion=f'{major}.{minor}.{patch+1}', androidVersionCode=old['androidVersionCode']+1)
        self.assertEqual(module.validate(candidate, previous=old), candidate)

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
        major, minor, patch, _, sequence = module.version_key(old['version'])
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
