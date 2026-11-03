import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

class PersonalVersionTests(unittest.TestCase):
    def module(self):
        path=ROOT/'scripts/personal_version.py'
        self.assertTrue(path.is_file(),'independent local personal version support missing')
        spec=importlib.util.spec_from_file_location('personal_version',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_personal_versions_are_local_independent_and_monotonic(self):
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
            self.assertEqual(data['version'],'0.3.16');self.assertEqual(data['nativeVersion'],'0.3.16')
            self.assertEqual(data['androidVersionCode'],3016)
            self.assertEqual(data['sourceVersion'],json.loads(before)['version'])
            self.assertEqual((root/'release.json').read_bytes(),before)

    def test_missing_personal_version_does_not_inherit_public_version(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);shutil.copyfile(ROOT/'release.json',root/'release.json')
            with self.assertRaisesRegex(ValueError,'个人|personal'):module.build_data(root)

if __name__=='__main__':unittest.main()
