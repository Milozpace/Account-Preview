import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_bootstrap', ROOT / 'bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
if spec.loader and spec.origin and Path(spec.origin).exists(): spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def bundle(self, extra=None, wrong_hash=False):
        files = {'bin/ledger_cli.exe': b'publisher', 'bin/bill_inbox_cli.exe': b'inbox',
                 'bin/sync_diagnose.exe': b'diagnose', 'install/Account-Preview.exe': b'installer'}
        if extra: files[extra] = b'unsafe'
        manifest = {'format': 1, 'version': '0.4.0-preview.1', 'platform': 'windows-x64',
                    'files': {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}}
        if wrong_hash: manifest['files']['bin/ledger_cli.exe'] = '0' * 64
        archive = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('manifest.json', json.dumps(manifest))
            for p, v in files.items(): z.writestr(p, v)
        return archive

    def test_platform_selection(self):
        self.assertEqual(bootstrap.platform_key('Darwin', 'arm64'), 'macos-arm64')
        self.assertEqual(bootstrap.platform_key('Darwin', 'x86_64'), 'macos-x64')
        self.assertEqual(bootstrap.platform_key('Windows', 'AMD64'), 'windows-x64')
        with self.assertRaises(ValueError): bootstrap.platform_key('Linux', 'x86_64')

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows PowerShell compatibility')
    def test_stock_windows_powershell_can_parse_launcher(self):
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(ROOT/'bootstrap.ps1')], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_config_dry_run_and_no_project_rebind(self):
        cfg = self.root / 'config/project.json'
        python = self.root / 'python'; python.write_bytes(b'interpreter')
        bootstrap.configure_desktop(self.root, python, config=cfg)
        self.assertFalse(cfg.exists())
        bootstrap.configure_desktop(self.root, python, config=cfg, apply=True)
        before = cfg.read_bytes()
        bootstrap.configure_desktop(self.root, python, config=cfg, apply=True)
        self.assertEqual(before, cfg.read_bytes())
        other = self.root / 'other'; other.mkdir()
        with self.assertRaises(ValueError): bootstrap.configure_desktop(other, python, config=cfg, apply=True)
        self.assertEqual(before, cfg.read_bytes())

    def test_bundle_stages_verified_version_and_switches_manifest(self):
        archive = self.bundle()
        result = bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        manifest = json.loads((self.root / '.runtime/helpers.json').read_text())
        for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose'):
            self.assertTrue((self.root / manifest['helpers'][name]).is_file())
        self.assertTrue(Path(result['installer']).is_file())
        bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        (self.root / 'local').mkdir(); (self.root / 'local/个人规则.md').write_text('keep')
        bootstrap.stage_bundle(self.root, archive, 'windows-x64')
        self.assertEqual((self.root / 'local/个人规则.md').read_text(), 'keep')

    def test_bundle_rejects_tamper_and_paths_before_creating_runtime(self):
        for extra, bad_hash in (('../escape', False), ('bin\\escape.exe', False), ('local/private.txt', False),
                                ('bin/./ledger_cli.exe', False), ('bin/LEDGER_CLI.exe', False), (None, True)):
            with self.subTest(extra=extra, bad_hash=bad_hash):
                archive = self.bundle(extra, bad_hash)
                with self.assertRaises(ValueError): bootstrap.stage_bundle(self.root, archive, 'windows-x64')
                self.assertFalse((self.root / '.runtime').exists())

    def test_native_setup_preserves_existing_identity_and_enables_both_gates(self):
        self.bundle()
        bootstrap.stage_bundle(self.root, self.root/'bundle.zip', 'windows-x64')
        (self.root/'data').mkdir()
        (self.root/'data/source.json').write_text('{"kind":"sqlite","path":"account.sqlite3"}')
        (self.root/'data/account.sqlite3').write_bytes(b'synthetic marker')
        calls = []
        def native(args, **kwargs):
            calls.append([str(x) for x in args])
            return subprocess.CompletedProcess(args, 0, stdout='{"initialized":true,"publishEnabled":false}')
        with patch.object(bootstrap, 'run', side_effect=native):
            bootstrap.setup_native(self.root, initialize=True, enable_publish=True)
        self.assertFalse(any('--init' in c for c in calls))
        self.assertTrue(any('--enable-publish' in c for c in calls))
        self.assertEqual(json.loads((self.root/'.runtime/ledger/publish.json').read_text()), {'enabled': True})

    def test_native_failure_does_not_enable_local_gate(self):
        self.bundle(); bootstrap.stage_bundle(self.root, self.root/'bundle.zip', 'windows-x64')
        (self.root/'data').mkdir()
        (self.root/'data/source.json').write_text('{"kind":"sqlite","path":"account.sqlite3"}')
        (self.root/'data/account.sqlite3').write_bytes(b'synthetic marker')
        with patch.object(bootstrap, 'run', side_effect=subprocess.CalledProcessError(2, ['native'])):
            with self.assertRaises(subprocess.CalledProcessError): bootstrap.setup_native(self.root, enable_publish=True)
        self.assertFalse((self.root/'.runtime/ledger/publish.json').exists())

    def test_direct_bootstrap_reuses_managed_uv_without_pip(self):
        name = 'uv.exe' if sys.platform == 'win32' else ('uv-aarch64-apple-darwin/uv' if bootstrap.platform.machine() == 'arm64' else 'uv-x86_64-apple-darwin/uv')
        uv = self.root/'.runtime/bootstrap'/name
        uv.parent.mkdir(parents=True); uv.write_bytes(b'managed uv')
        python = self.root/('.venv/Scripts/python.exe' if sys.platform == 'win32' else '.venv/bin/python')
        python.parent.mkdir(parents=True); python.write_bytes(b'no pip interpreter')
        with patch.object(bootstrap, 'run') as execute:
            bootstrap.prepare_python(self.root)
        commands = [[str(a) for a in call.args[0]] for call in execute.call_args_list]
        self.assertTrue(any(command[0] == str(uv) and command[1:3] == ['pip','sync'] for command in commands))
        self.assertFalse(any(command[1:3] == ['-m','pip'] for command in commands))


if __name__ == '__main__': unittest.main()
