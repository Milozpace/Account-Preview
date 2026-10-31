import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_release', ROOT / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
if spec.loader and spec.origin and Path(spec.origin).exists(): spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_source_allowlist_excludes_private_and_generated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('README.md', 'scripts/account.py', 'local/个人规则.md', 'data/account.sqlite3',
                         'sra/bill.csv', '.runtime/key.json', 'PROJECT_MEMORY.md', 'app/src/main.ts',
                         'app/src-tauri/target/a.rs', 'app/plugins/platform/android/build/test.xml',
                         'app/src-tauri/gen/android/tauri.settings.gradle', 'app/private.key'):
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('safe')
            files = {p.relative_to(root).as_posix() for p in release.source_files(root)}
            self.assertEqual(files, {'README.md', 'scripts/account.py', 'app/src/main.ts'})

    def test_audit_rejects_developer_path_and_private_key(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'README.md'
            path.write_text('C:\\Users\\' + str(26000 + 400) + '\\private')
            self.assertFalse(release.audit_sources(root)['ok'])
            path.write_text('-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5)
            self.assertFalse(release.audit_sources(root)['ok'])
            path.write_text('A clean personal ledger.')
            self.assertTrue(release.audit_sources(root)['ok'])

    def test_source_zip_shell_is_executable_and_data_is_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'bootstrap.sh').write_text('#!/bin/sh\nexit 0\n')
            (root/'local').mkdir(); (root/'local/private.md').write_text('private')
            output = root/'source.zip'
            release.source_archive(root, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), ['bootstrap.sh'])
                self.assertTrue((archive.getinfo('bootstrap.sh').external_attr >> 16) & 0o111)


if __name__ == '__main__': unittest.main()
