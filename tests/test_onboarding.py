"""First-run setup safety, with synthetic files only."""
from contextlib import redirect_stdout, closing
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import ledger_source
import ledger_store
import app_export

class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.amounts = self.root / 'amounts.json'
        self.amounts.write_text(json.dumps(dict.fromkeys(account.POOLS, 100)), encoding='utf-8')

    def cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = account.main(list(args))
        return code, json.loads(out.getvalue())

    def init(self, *args):
        return self.cli('init', '--root', str(self.root), '--effective', '2026-10', '--amounts', str(self.amounts), *args)

    def test_doctor_is_read_only_and_never_assumes_cloud_connected(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        code, status = self.cli('--root', str(self.root), 'doctor')
        self.assertEqual(code, 0)
        self.assertEqual(status['setup']['state'], 'uninitialized')
        self.assertEqual(status['cloud']['state'], 'unverified')
        self.assertTrue(status['nextSteps'])
        self.assertIn('bootstrap.py --bundle', ' '.join(status['nextSteps']))
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))

    def test_init_dry_run_then_revision_zero_empty_ledger_and_idempotency(self):
        _, dry = self.init()
        self.assertTrue(dry['dry_run'])
        self.assertFalse((self.root / 'data').exists())
        _, report = self.init('--apply')
        db = ledger_source.resolve(self.root)
        self.assertEqual(ledger_store.read_state(db)['revision'], 0)
        self.assertEqual(ledger_store.read_state(db)['rows'], [])
        self.assertTrue(ledger_store.verify(db)['ok'])
        for name in ['local/档案.md', 'local/个人规则.md', 'PROJECT_MEMORY.md']:
            self.assertTrue((self.root / name).is_file())
        before = {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        _, repeated = self.init('--apply')
        self.assertEqual(repeated['state'], 'already_initialized')
        self.assertFalse(repeated['applied'])
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        _, status = self.cli('doctor', '--root', str(self.root))
        self.assertEqual(status['setup']['state'], 'initialized')
        self.assertEqual(status['ledger']['rows'], 0)
        self.assertTrue(report['applied'])

    def test_validate_nonnegative_complete_amounts_and_initial_carry(self):
        for bad in [{}, dict.fromkeys(account.POOLS, -1), dict.fromkeys(account.POOLS, None), dict.fromkeys(account.POOLS, True), dict.fromkeys(account.POOLS, 'NaN')]:
            self.amounts.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(ValueError): self.init('--apply')
            self.assertFalse((self.root / 'data').exists())
        self.amounts.write_text(json.dumps(dict.fromkeys(account.POOLS, 0)), encoding='utf-8')
        initial = self.root / 'initial.json'
        initial.write_text(json.dumps({account.POOLS[2]: -10, account.POOLS[3]: 50}), encoding='utf-8')
        self.init('--initial', str(initial), '--apply')
        state = ledger_store.read_state(ledger_source.resolve(self.root))
        self.assertEqual(state['initial'][account.POOLS[2]], '-10.00')
        self.assertEqual(state['initial'][account.POOLS[0]], '0.00')

    def test_reject_invalid_month_relative_root_and_existing_unactivated_database(self):
        with self.assertRaises(ValueError):
            self.cli('init', '--root', '.', '--effective', '2026-10', '--amounts', str(self.amounts))
        with self.assertRaises(ValueError):
            self.cli('init', '--root', str(self.root), '--effective', '2026-13', '--amounts', str(self.amounts), '--apply')
        (self.root / 'data').mkdir()
        db = self.root / 'data/account.sqlite3'
        db.write_bytes(b'preserve')
        with self.assertRaises(ValueError): self.init('--apply')
        self.assertEqual(db.read_bytes(), b'preserve')

    def test_failed_activation_never_leaves_active_source_or_overwrites_owner_notes(self):
        (self.root / 'local').mkdir()
        note = self.root / 'local/档案.md'
        note.write_text('owner edited', encoding='utf-8')
        import onboarding
        original = onboarding.os.replace
        def fail_source(src, dst):
            if Path(dst).name == 'source.json': raise OSError('synthetic activation failure')
            return original(src, dst)
        with patch.object(onboarding.os, 'replace', side_effect=fail_source):
            with self.assertRaises(OSError): self.init('--apply')
        self.assertFalse((self.root / 'data/source.json').exists())
        self.assertEqual(note.read_text(encoding='utf-8'), 'owner edited')
        # A second attempt recovers an unactivated setup-created empty ledger.
        self.init('--apply')
        self.assertTrue(ledger_store.verify(ledger_source.resolve(self.root))['ok'])

    def test_crash_recovery_refuses_modified_unactivated_budget_state(self):
        import onboarding
        import sqlite3
        original = onboarding.os.replace
        def fail_source(src, dst):
            if Path(dst).name == 'source.json': raise OSError('synthetic')
            return original(src, dst)
        with patch.object(onboarding.os, 'replace', side_effect=fail_source):
            with self.assertRaises(OSError): self.init('--apply')
        database=self.root/'data/account.sqlite3'
        with closing(sqlite3.connect(database)) as conn, conn:
            conn.execute('DROP TRIGGER initial_no_update')
            conn.execute('UPDATE initial_carry SET amount_cents=999 WHERE pool=?',(account.POOLS[2],))
        with self.assertRaises(ValueError): self.init('--apply')
        self.assertFalse((self.root/'data/source.json').exists())

    def test_empty_ledger_exports_valid_snapshots_without_excel(self):
        self.init('--apply')
        import dashboard_data
        db = ledger_source.resolve(self.root)
        with patch.object(dashboard_data, 'run_excel', side_effect=AssertionError('Excel forbidden')):
            report = app_export.export_snapshot(db)
        snapshot = json.loads(Path(report['output']).read_text(encoding='utf-8'))
        app_export.validate_app_snapshot(snapshot)
        self.assertEqual(snapshot['ledgerCount'], 0)
        self.assertIsNone(snapshot['sourceLastRecordedAt'])

    def test_doctor_reports_preview_profile_presence_without_reading_secrets(self):
        import onboarding
        profile = self.root / 'profile/AccountPreview'
        profile.mkdir(parents=True)
        (profile / 'project.json').write_text('secret and even invalid JSON', encoding='utf-8')
        with patch.dict('os.environ', {'LOCALAPPDATA':str(self.root/'profile')}), patch.object(onboarding.platform, 'system', return_value='Windows'):
            _, status = self.cli('doctor', '--root', str(self.root))
        self.assertTrue(status['desktop']['profilePresent'])
        self.assertNotIn('secret', json.dumps(status))
        self.assertEqual(status['desktop']['installation'], 'unverified')

    def test_doctor_works_when_openpyxl_dependency_is_missing(self):
        import subprocess
        code = "import builtins,sys; real=builtins.__import__; builtins.__import__=lambda name,*args,**kw: (_ for _ in ()).throw(ImportError('missing')) if name=='openpyxl' else real(name,*args,**kw); sys.path.insert(0,sys.argv[1]); import account; account.main(['doctor','--root',sys.argv[2]])"
        result = subprocess.run([sys.executable,'-X','utf8','-B','-c',code,str(Path(account.__file__).parent),str(self.root)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse(json.loads(result.stdout)['dependencies']['openpyxlAvailable'])

    def test_doctor_requires_managed_python_312_and_supported_sqlite(self):
        import onboarding
        for version,expected in [((3,10,0),False),((3,11,9),False),((3,12,0),True),((3,13,0),False)]:
            with self.subTest(version=version),patch.object(onboarding.sys,'version_info',version):
                status=onboarding.doctor(self.root)
                self.assertEqual(status['dependencies']['pythonSupported'],expected)
                if not expected:self.assertIn('3.12',' '.join(status['nextSteps']))
        with patch.object(onboarding.sqlite3,'sqlite_version_info',(3,36,0)):
            self.assertFalse(onboarding.doctor(self.root)['dependencies']['sqliteStrictSupported'])

    def test_missing_source_has_no_implicit_excel_fallback(self):
        with self.assertRaisesRegex(ValueError, 'init'):
            ledger_source.resolve(self.root)

if __name__ == '__main__': unittest.main()
