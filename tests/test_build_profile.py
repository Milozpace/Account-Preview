import importlib.util
import inspect
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BuildProfileTests(unittest.TestCase):
    def test_android_display_name_survives_profile_preparation(self):
        module=self.module()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.android_fixture(root)
            strings=root/'app/src-tauri/gen/android/app/src/main/res/values/strings.xml'
            strings.parent.mkdir(parents=True,exist_ok=True)
            strings.write_text('<resources><string name="app_name">Account Preview</string><string name="main_activity_title">old</string><string name="owner">retained</string></resources>',encoding='utf-8')
            module.prepare(root,'preview','android-arm64',apply=True,isolated_build=True)
            import xml.etree.ElementTree as ET
            values={item.attrib['name']:item.text for item in ET.parse(strings).getroot()}
            self.assertEqual(values['app_name'],'Account')
            self.assertEqual(values['main_activity_title'],'Account')
            self.assertEqual(values['owner'],'retained')
            self.assertEqual(json.loads((root/'app/src-tauri/tauri.conf.json').read_text())['identifier'],'com.account.preview')
    def android_fixture(self, root):
        for name in ['app/package.json','app/package-lock.json','app/src-tauri/Cargo.toml','app/src-tauri/Cargo.lock',
                     'dashboard/package.json','dashboard/package-lock.json','pyproject.toml','app/src/delivery-profile.ts']:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
        (root/'local').mkdir(exist_ok=True)
        (root/'local/personal-version.json').write_text('{"version":"0.3.16","androidVersionCode":3016}')
        names = ['release.json', 'app/src-tauri/tauri.conf.json', 'app/src-tauri/src/desktop_bridge.rs',
                 'app/src-tauri/gen/android/app/build.gradle.kts',
                 'app/src-tauri/gen/android/app/src/main/java/com/account/preview/MainActivity.kt']
        for name in names:
            path = root/name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, path)
        build_sources = root/'app/src-tauri/gen/android/buildSrc/src/main/java'
        for profile in ['preview', 'probe']:
            for name in ['BuildTask.kt', 'RustPlugin.kt']:
                path = build_sources/'com/account'/profile/'kotlin'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                source = ROOT/'app/src-tauri/gen/android/buildSrc/src/main/java/com/account/preview/kotlin'/name
                shutil.copyfile(source, path)
        return build_sources

    def test_android_init_keeps_only_active_generated_global_build_classes(self):
        module = self.module()
        self.assertIn('isolated_build', inspect.signature(module.prepare).parameters,
                      'isolated Android generated-source cleanup is missing')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); sources = self.android_fixture(root)
            custom = sources/'com/account/preview/kotlin/OwnerNote.kt'
            custom.write_text('// Owner maintained', encoding='utf-8')
            module.prepare(root, 'personal', 'android-arm64')
            self.assertEqual(len(list(sources.rglob('BuildTask.kt'))), 2)
            module.prepare(root, 'personal', 'android-arm64', apply=True, isolated_build=True)
            self.assertEqual([p.relative_to(sources).as_posix() for p in sources.rglob('BuildTask.kt')],
                             ['com/account/probe/kotlin/BuildTask.kt'])
            self.assertEqual([p.relative_to(sources).as_posix() for p in sources.rglob('RustPlugin.kt')],
                             ['com/account/probe/kotlin/RustPlugin.kt'])
            self.assertTrue(custom.is_file())

    def test_generated_build_cleanup_requires_isolated_checkout_without_ledger(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); sources = self.android_fixture(root)
            before = (root/'app/src-tauri/tauri.conf.json').read_bytes()
            with self.assertRaisesRegex(ValueError, '隔离'):
                module.prepare(root, 'personal', 'android-arm64', apply=True)
            self.assertEqual((root/'app/src-tauri/tauri.conf.json').read_bytes(), before)
            (root/'data').mkdir(); (root/'data/account.sqlite3').write_bytes(b'protected ledger sentinel')
            with self.assertRaisesRegex(ValueError, '账本'):
                module.prepare(root, 'personal', 'android-arm64', apply=True, isolated_build=True)
            self.assertEqual(len(list(sources.rglob('BuildTask.kt'))), 2)
            self.assertEqual((root/'app/src-tauri/tauri.conf.json').read_bytes(), before)

    def test_android_profile_relocates_custom_activity_without_changing_behavior(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            names = ['release.json', 'app/src-tauri/tauri.conf.json', 'app/src-tauri/src/desktop_bridge.rs',
                     'app/src-tauri/gen/android/app/build.gradle.kts',
                     'app/src-tauri/gen/android/app/src/main/java/com/account/preview/MainActivity.kt']
            for name in names:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, path)
            original = (root/names[-1]).read_text(encoding='utf-8')
            manifest = json.loads((root/'release.json').read_text(encoding='utf-8'))
            manifest['androidVersionCode'] += 1
            (root/'release.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.android_fixture(root)
            module.prepare(root, 'personal', 'android-arm64', apply=True, isolated_build=True)
            target = root/names[-1].replace('/preview/', '/probe/')
            self.assertTrue(target.is_file(), 'personal activity package missing')
            self.assertEqual(target.read_text(encoding='utf-8'), original.replace('package com.account.preview', 'package com.account.probe'))
            self.assertFalse((root/names[-1]).exists())
            gradle = (root/names[-2]).read_text(encoding='utf-8')
            self.assertIn('namespace = "com.account.probe"', gradle)
            self.assertIn('applicationId = "com.account.probe"', gradle)
            self.assertIn('targetSdk = 37', gradle)
            release=json.loads((root/'release.json').read_text())
            self.assertIn('versionCode = 3016', gradle)
            self.assertIn('versionName = "0.3.16"', gradle)

    def module(self):
        path = ROOT/'scripts/build_profile.py'
        self.assertTrue(path.exists(), 'build profile module missing')
        spec = importlib.util.spec_from_file_location('build_profile', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_personal_requires_explicit_apply_and_retains_config_identity(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ['release.json', 'app/src-tauri/tauri.conf.json', 'app/src-tauri/src/desktop_bridge.rs']:
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, path)
            self.android_fixture(root)
            public_manifest=(root/'release.json').read_bytes()
            before = (root/'app/src-tauri/tauri.conf.json').read_bytes()
            module.prepare(root, 'personal', 'windows-x64')
            self.assertEqual((root/'app/src-tauri/tauri.conf.json').read_bytes(), before)
            module.prepare(root, 'personal', 'windows-x64', apply=True, isolated_build=True)
            conf = json.loads((root/'app/src-tauri/tauri.conf.json').read_text())
            self.assertEqual(conf['identifier'], 'com.account.probe')
            source=json.loads(public_manifest)
            self.assertEqual(conf['version'],'0.3.16')
            self.assertIn('showIncome = false',(root/'app/src/delivery-profile.ts').read_text())
            self.assertEqual(json.loads((root/'app/package.json').read_text())['version'],'0.3.16')
            self.assertIn('version = "0.3.16"',(root/'app/src-tauri/Cargo.toml').read_text())
            self.assertEqual((root/'release.json').read_bytes(),public_manifest)
            self.assertIn('AccountDesktop/project.json', (root/'app/src-tauri/src/desktop_bridge.rs').read_text(encoding='utf-8'))
            module.prepare(root, 'preview', 'macos-arm64', apply=True)
            self.assertIn('AccountPreview/project.json', (root/'app/src-tauri/src/desktop_bridge.rs').read_text(encoding='utf-8'))
            self.assertEqual(json.loads((root/'app/package.json').read_text())['version'],
                             json.loads(public_manifest)['version'])
            self.assertEqual(json.loads((root/'app/src-tauri/tauri.conf.json').read_text())['version'],
                             json.loads(public_manifest)['nativeVersion'])
            with self.assertRaises(ValueError): module.prepare(root, 'personal', 'macos-arm64', apply=True)


if __name__ == '__main__': unittest.main()
