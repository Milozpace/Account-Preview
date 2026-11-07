import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PersonalVersionTests(unittest.TestCase):
    def test_independent_personal_version_is_distinct_from_source(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); shutil.copyfile(ROOT/'release.json',root/'release.json')
            path=root/'personal.json'; path.write_text('{"version":"0.3.19","androidVersionCode":3019}')
            value=module.build_data(root,path)
            self.assertEqual((value['version'],value['nativeVersion'],value['androidVersionCode']),('0.3.19','0.3.19',3019))
            self.assertEqual(value['sourceVersion'],'0.4.0-preview.5')
            self.assertEqual(value['versionPolicy'],'independent-personal')
    def module(self):
        path=ROOT/'scripts/personal_version.py'
        self.assertTrue(path.is_file(),'independent local personal version support missing')
        spec=importlib.util.spec_from_file_location('personal_version',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_legacy_personal_versions_remain_readable_without_overriding_product(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copyfile(ROOT/'release.json',root/'release.json')
            before=(root/'release.json').read_bytes()
            old={'version':'0.3.15','androidVersionCode':3015}
            self.assertEqual(module.validate(old),old)
            fresh={'version':'0.3.16','androidVersionCode':3016}
            self.assertEqual(module.validate(fresh,old),fresh)
            for bad in ({'version':'0.3.14','androidVersionCode':3017},
                        {'version':'0.3.16','androidVersionCode':3015},
                        {'version':'0.3.15','androidVersionCode':3016},
                        {'version':'0.4.0-preview.4','androidVersionCode':4004}):
                with self.assertRaises(ValueError):module.validate(bad,old)
            path=root/'local/personal-version.json';path.parent.mkdir();path.write_text(json.dumps(fresh))
            data=module.build_data(root)
            source=json.loads(before)
            self.assertEqual((data['version'],data['nativeVersion'],data['androidVersionCode']),('0.3.16','0.3.16',3016))
            self.assertEqual(data['versionPolicy'],'independent-personal')
            self.assertEqual(data['sourceVersion'],json.loads(before)['version'])
            self.assertEqual((root/'release.json').read_bytes(),before)

    def test_new_personal_build_defaults_to_shared_product_version(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copyfile(ROOT/'release.json',root/'release.json')
            source=json.loads((root/'release.json').read_text())
            with self.assertRaises(ValueError): module.build_data(root)

    def test_shared_product_must_exceed_legacy_personal_installation_numbers(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copyfile(ROOT/'release.json',root/'release.json')
            source=json.loads((root/'release.json').read_text())
            path=root/'legacy.json'
            for value in ({'version':'9.0.0','androidVersionCode':1},
                          {'version':'0.3.18','androidVersionCode':source['androidVersionCode']+1}):
                path.write_text(json.dumps(value))
                with self.subTest(value=value):
                    self.assertEqual(module.build_data(root,path)['version'],value['version'])

    def test_explicit_missing_legacy_baseline_is_rejected(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copyfile(ROOT/'release.json',root/'release.json')
            with self.assertRaises(ValueError):module.build_data(root,root/'missing.json')

if __name__=='__main__':unittest.main()
