"""Transition synchronization never copies owner data or overwrites both-side edits."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import source_sync
except ImportError:
    source_sync = None


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(source_sync, 'source synchronization tool must exist')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Path(self.tmp.name) / 'source'
        self.b = Path(self.tmp.name) / 'target'
        self.a.mkdir(); self.b.mkdir()

    def write(self, root, name, content):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding='utf-8')

    def sync(self, *names, apply=False):
        return source_sync.synchronize(self.a, self.b, list(names), apply=apply)

    def test_new_file_dry_run_then_copy_repeat_and_source_change(self):
        self.write(self.a, 'scripts/shared.py', 'one')
        dry = self.sync('scripts/shared.py')
        self.assertEqual(dry['changes'][0]['action'], 'copy')
        self.assertFalse((self.b / '.runtime').exists())
        self.sync('scripts/shared.py', apply=True)
        self.assertEqual(self.sync('scripts/shared.py')['changes'][0]['action'], 'unchanged')
        self.write(self.a, 'scripts/shared.py', 'two')
        self.sync('scripts/shared.py', apply=True)
        self.assertEqual((self.b / 'scripts/shared.py').read_text(), 'two')

    def test_existing_different_files_require_manual_port(self):
        self.write(self.a, 'scripts/shared.py', 'Windows')
        self.write(self.b, 'scripts/shared.py', 'Mac and Windows')
        report = self.sync('scripts/shared.py', apply=True)
        self.assertFalse(report['ok'])
        self.assertEqual((self.b / 'scripts/shared.py').read_text(), 'Mac and Windows')
        self.assertFalse((self.b / '.runtime').exists())

    def test_target_only_and_both_side_changes_are_never_lost(self):
        self.write(self.a, 'scripts/shared.py', 'base')
        self.sync('scripts/shared.py', apply=True)
        self.write(self.b, 'scripts/shared.py', 'local adaptation')
        report = self.sync('scripts/shared.py', apply=True)
        self.assertEqual(report['changes'][0]['action'], 'target-only')
        self.write(self.a, 'scripts/shared.py', 'upstream change')
        self.assertFalse(self.sync('scripts/shared.py', apply=True)['ok'])
        self.assertEqual((self.b / 'scripts/shared.py').read_text(), 'local adaptation')

    def test_conflict_prevents_entire_batch_and_private_files_rejected(self):
        self.write(self.a, 'scripts/a.py', 'a')
        self.write(self.a, 'scripts/b.py', 'b')
        self.write(self.b, 'scripts/b.py', 'conflict')
        self.assertFalse(self.sync('scripts/a.py', 'scripts/b.py', apply=True)['ok'])
        self.assertFalse((self.b / 'scripts/a.py').exists())
        for name in ['data/account.sqlite3', 'local/rules.md', 'sra/bill.csv',
                     '../scripts/x.py', '/scripts/x.py', 'docs/记账规则.md',
                     'app/src-tauri/tauri.conf.json', 'scripts/__pycache__/x.py',
                     'scripts/private.key', 'scripts/x.py:stream', 'scripts/../x.py']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.sync(name, apply=True)

    def test_symlink_or_nested_roots_refused(self):
        with self.assertRaises(ValueError):
            source_sync.synchronize(self.a, self.a, ['scripts/a.py'])
        with self.assertRaises(ValueError):
            source_sync.synchronize(self.a, self.a/'nested', ['scripts/a.py'])
        self.write(self.a, 'scripts/a.py', 'safe')
        (self.b/'scripts').mkdir()
        try:
            (self.b/'scripts/a.py').symlink_to(self.a/'scripts/a.py')
        except OSError:
            self.skipTest('symlink privilege unavailable')
        with self.assertRaises(ValueError): self.sync('scripts/a.py', apply=True)

    def test_managed_delete_preserves_unmanaged_file(self):
        self.write(self.a, 'scripts/shared.py', 'base')
        self.sync('scripts/shared.py', apply=True)
        (self.a/'scripts/shared.py').unlink()
        self.write(self.b, 'scripts/private.py', 'owner')
        self.sync('scripts/shared.py', apply=True)
        self.assertFalse((self.b/'scripts/shared.py').exists())
        self.assertTrue((self.b/'scripts/private.py').exists())

    def test_rejects_case_alias_and_sensitive_source(self):
        with self.assertRaises(ValueError):
            self.sync('scripts/a.py', 'scripts/A.py')
        self.write(self.a, 'scripts/a.py', '-' * 5 + 'BEGIN PRIVATE KEY' + '-' * 5)
        with self.assertRaises(ValueError): self.sync('scripts/a.py', apply=True)

    def test_interrupted_transfer_blocks_retry_until_recovered(self):
        self.write(self.a, 'scripts/shared.py', 'base')
        self.sync('scripts/shared.py', apply=True)
        state = next((self.b/'.runtime/product/source-sync').glob('*.json'))
        state.with_suffix('.pending.json').write_text('{}')
        self.write(self.a, 'scripts/shared.py', 'changed')
        with self.assertRaisesRegex(ValueError, '中断'):
            self.sync('scripts/shared.py', apply=True)
        self.assertEqual((self.b/'scripts/shared.py').read_text(), 'base')

    def test_cleanup_failure_restores_baseline_as_well_as_program(self):
        self.write(self.a, 'scripts/shared.py', 'one')
        self.sync('scripts/shared.py', apply=True)
        self.write(self.a, 'scripts/shared.py', 'two')
        unlink = Path.unlink
        failed = False
        def fail_once(path, *args, **kwargs):
            nonlocal failed
            if path.name.endswith('.pending.json') and not failed:
                failed = True
                raise OSError('synthetic cleanup failure')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_once):
            with self.assertRaises(OSError): self.sync('scripts/shared.py', apply=True)
        self.assertEqual((self.b/'scripts/shared.py').read_text(), 'one')
        self.assertEqual(self.sync('scripts/shared.py')['changes'][0]['action'], 'copy')


if __name__ == '__main__': unittest.main()
