import importlib.util
import os
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
import shutil

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_release', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
if spec.loader and spec.origin and Path(spec.origin).exists(): spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def repository(self, root, skill=True):
        root.mkdir(exist_ok=True)
        shutil.copyfile(ROOT/'release.json', root/'release.json')
        (root/'README.md').write_text('Source at fixed commit.\n', encoding='utf-8')
        (root/'templates').mkdir()
        (root/'templates/notes.md').write_text('Release heading with empty sections.', encoding='utf-8')
        if skill:
            entry = root/'.agents/skills/account-maintenance/SKILL.md'
            entry.parent.mkdir(parents=True)
            entry.write_text('# Bookkeeping\n' + ('[Contract](references/missing.md)\n' if skill == 'missing-ref' else ''), encoding='utf-8')
        for args in [('init',), ('config', 'user.email', 'fixture@example.invalid'),
                     ('config', 'user.name', 'Fixture'), ('add', '.'), ('commit', '-m', 'fixture')]:
            subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
        return subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()

    def assets(self, root, directory, commit):
        directory.mkdir()
        manifest = json.loads((root/'release.json').read_text(encoding='utf-8'))
        for profile, config in manifest['profiles'].items():
            if profile != 'preview': continue
            for platform in config['platforms']:
                binary = directory/'binaries'; binary.mkdir(exist_ok=True)
                ext = '.exe' if platform.startswith('windows') else ''
                for name in ['ledger_cli', 'bill_inbox_cli', 'sync_diagnose']:
                    (binary/(name+ext)).write_bytes(b'fixture-helper')
                installer = directory/('fixture.apk' if platform.startswith('android') else 'fixture'+('.exe' if ext else '.dmg'))
                installer.write_bytes(b'fixture-installer')
                output = directory/f"Account-{profile.title()}-{manifest['version']}-{platform}.zip"
                release.bundle(platform, binary, installer, output, profile=profile, root=root, commit=commit)
        shutil.rmtree(directory/'binaries')
        for path in directory.glob('fixture*'): path.unlink()

    def test_prepare_requires_notes_clean_commit_and_complete_matching_assets(self):
        self.assertTrue(hasattr(release, 'prepare_release'), 'release preparation missing')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            assets = parent/'assets'; self.assets(root, assets, commit)
            notes = parent/'notes.md'; notes.write_text('User supplied release notes.', encoding='utf-8')
            result = release.prepare_release(root, assets, commit, notes)
            manifest = json.loads((assets/'release-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['commit'], commit)
            self.assertEqual(set(manifest['profiles']), {'preview'})
            self.assertEqual(len(result['assets']), 7)
            self.assertEqual(manifest['source']['sha256'], release.sha(assets/manifest['source']['asset']))
            self.assertTrue((assets/'SHA256SUMS').exists())
            self.assertTrue(result['ok'])
            (root/'README.md').write_text('dirty')
            with self.assertRaisesRegex(ValueError, 'clean|干净|改动'):
                release.prepare_release(root, assets, commit, notes)

    def test_new_release_requires_core_bookkeeping_skill_and_reference_closure(self):
        for skill in (False, 'missing-ref'):
            with self.subTest(skill=skill), tempfile.TemporaryDirectory() as folder:
                parent = Path(folder); root = parent/'repo'; commit = self.repository(root, skill=skill)
                assets = parent/'assets'; self.assets(root, assets, commit)
                notes = parent/'notes.md'; notes.write_text('User authored notes', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Skill|skill|引用'):
                    release.prepare_release(root, assets, commit, notes)

    def test_prepare_rejects_missing_notes_platform_or_mismatched_bundle(self):
        self.assertTrue(hasattr(release, 'prepare_release'), 'release preparation missing')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            assets = parent/'assets'; self.assets(root, assets, commit)
            notes = parent/'notes.md'; notes.write_text('  ', encoding='utf-8')
            with self.assertRaises(ValueError): release.prepare_release(root, assets, commit, notes)
            notes.write_text('User notes', encoding='utf-8')
            apk = next(assets.glob('*Preview*android*.zip')); saved = apk.read_bytes(); apk.unlink()
            with self.assertRaises(ValueError): release.prepare_release(root, assets, commit, notes)
            apk.write_bytes(saved)
            with zipfile.ZipFile(apk, 'w') as archive:
                archive.writestr('manifest.json', json.dumps({'format': 1, 'version': '0.1.0', 'profile': 'preview'}))
            with self.assertRaises(ValueError): release.prepare_release(root, assets, commit, notes)

    def test_source_archive_uses_clean_fixed_git_commit(self):
        self.assertIn('commit', __import__('inspect').signature(release.source_archive).parameters,
                      'source export does not bind a Git commit')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            first = parent/'first.zip'; second = parent/'second.zip'
            release.source_archive(root, first, commit=commit)
            release.source_archive(root, second, commit=commit)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            (root/'untracked.py').write_text('dirty')
            with self.assertRaises(ValueError): release.source_archive(root, parent/'third.zip', commit=commit)

    def test_public_preparation_rejects_personal_packages_and_notes_attachments(self):
        for name in ('Account-Personal-local.zip', '0.4.0-preview.3.md'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                parent=Path(folder); root=parent/'repo'; commit=self.repository(root)
                assets=parent/'assets'; self.assets(root,assets,commit)
                notes=parent/'notes.md'; notes.write_text('Author notes')
                (assets/name).write_bytes(b'not for public upload')
                with self.assertRaisesRegex(ValueError,'公开|附件|个人'):
                    release.prepare_release(root,assets,commit,notes)

    def test_personal_local_package_has_independent_version_and_verifiable_public_source(self):
        from scripts import product_update
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder);root=parent/'repo';commit=self.repository(root)
            version_file=parent/'personal-version.json'
            version_file.write_text('{"version":"0.3.16","androidVersionCode":3016}')
            assets=parent/'personal-assets';assets.mkdir()
            binary=parent/'bin';binary.mkdir()
            for name in ['ledger_cli','bill_inbox_cli','sync_diagnose']:(binary/(name+'.exe')).write_bytes(b'fixture')
            for platform,suffix in [('windows-x64','.exe'),('android-arm64','.apk')]:
                installer=parent/('fixture'+suffix);installer.write_bytes(b'fixture')
                release.bundle(platform,binary,installer,assets/f'Account-Personal-0.3.16-{platform}.zip',
                               profile='personal',root=root,commit=commit,personal_version_file=version_file)
            notes=parent/'notes.md';notes.write_text('User local update notes')
            result=release.prepare_release(root,assets,commit,notes,'personal-local',version_file)
            manifest=json.loads((assets/'release-manifest.json').read_text())
            self.assertEqual(manifest['version'],'0.3.16');self.assertEqual(manifest['nativeVersion'],'0.3.16')
            self.assertEqual(manifest['sourceVersion'],json.loads((root/'release.json').read_text())['version'])
            self.assertEqual(set(manifest['profiles']),{'personal'});self.assertEqual(len(result['assets']),5)
            self.assertIsNone(manifest['tag'])
            validated=product_update.validate_release(assets,'personal','windows-x64')
            self.assertEqual(validated[0]['version'],'0.3.16')
            with self.assertRaises(ValueError):product_update.validate_release(assets,'preview','windows-x64')
            with self.assertRaises(ValueError):release.public_assets(root,assets,commit,notes)

    def test_release_bundle_detects_payload_tampering_and_cross_profile(self):
        self.assertTrue(hasattr(release, 'verify_bundle'), 'bundle verification missing')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            assets = parent/'assets'; self.assets(root, assets, commit)
            data = json.loads((root/'release.json').read_text(encoding='utf-8'))
            path = assets/f"Account-Preview-{data['version']}-windows-x64.zip"
            with self.assertRaises(ValueError): release.verify_bundle(path, data, 'personal', 'windows-x64', commit)
            with zipfile.ZipFile(path) as archive: members = {name: archive.read(name) for name in archive.namelist()}
            members['bin/ledger_cli.exe'] = b'corrupted'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, content in members.items(): archive.writestr(name, content)
            with self.assertRaisesRegex(ValueError, '哈希'): release.verify_bundle(path, data, 'preview', 'windows-x64', commit)

    def test_prepare_rejects_source_archive_from_another_commit(self):
        self.assertTrue(hasattr(release, 'prepare_release'), 'release preparation missing')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            assets = parent/'assets'; self.assets(root, assets, commit)
            notes = parent/'notes.md'; notes.write_text('User notes', encoding='utf-8')
            data = json.loads((root/'release.json').read_text(encoding='utf-8'))
            (assets/f"Account-Preview-{data['version']}-source.zip").write_bytes(b'stale tree')
            with self.assertRaisesRegex(ValueError, '源码包'): release.prepare_release(root, assets, commit, notes)

    def test_prepare_requires_authored_notes_instead_of_template_or_placeholders(self):
        self.assertTrue(hasattr(release, 'prepare_release'), 'release preparation missing')
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder); root = parent/'repo'; commit = self.repository(root)
            assets = parent/'assets'; self.assets(root, assets, commit)
            with self.assertRaisesRegex(ValueError, '模板'):
                release.prepare_release(root, assets, commit, root/'templates/notes.md')
            notes = parent/'notes.md'
            for placeholder in ['请本人填写', '待填写', 'TBD', 'TODO']:
                notes.write_text('Release notes\n'+placeholder, encoding='utf-8')
                with self.subTest(placeholder=placeholder), self.assertRaisesRegex(ValueError, '占位'):
                    release.prepare_release(root, assets, commit, notes)

    def test_cli_audit_json_is_utf8_with_legacy_console_encoding(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/release.py'), 'audit'],
                                env={**os.environ, 'PYTHONIOENCODING':'cp1252'}, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertTrue(json.loads(result.stdout.decode('utf-8'))['ok'])

    def test_source_allowlist_excludes_private_and_generated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            for name in ('README.md', 'scripts/account.py', 'local/个人规则.md', 'data/account.sqlite3',
                         'sra/bill.csv', '.runtime/key.json', 'PROJECT_MEMORY.md', 'app/src/main.ts',
                         'app/src-tauri/target/a.rs', 'app/plugins/platform/android/build/test.xml',
                         'app/src-tauri/gen/android/tauri.settings.gradle', 'app/private.key'):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('safe')
            files = {p.relative_to(root).as_posix() for p in release.source_files(root)}
            self.assertEqual(files, {'README.md', 'scripts/account.py', 'app/src/main.ts'})

    def test_audit_rejects_developer_path_and_private_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            path = root / 'README.md'
            path.write_text('C:\\Users\\' + str(26000 + 400) + '\\private')
            self.assertFalse(release.audit_sources(root)['ok'])
            path.write_text('-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5)
            self.assertFalse(release.audit_sources(root)['ok'])
            path.write_text('A clean personal ledger.')
            self.assertTrue(release.audit_sources(root)['ok'])

    def test_source_zip_shell_is_executable_and_data_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root/'bootstrap.sh').write_text('#!/bin/sh\nexit 0\n')
            (root/'local').mkdir(); (root/'local/private.md').write_text('private')
            output = root/'source.zip'
            release.source_archive(root, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ['bootstrap.sh'])
                self.assertTrue((archive.getinfo('bootstrap.sh').external_attr >> 16) & 0o111)


if __name__ == '__main__': unittest.main()
