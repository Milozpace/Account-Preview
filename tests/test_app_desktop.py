"""Read-only desktop freshness tests using temporary synthetic installations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import account
import app_desktop
import app_export
import dashboard_data as data
import onboarding


class DesktopDisplayPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        onboarding.initialize(self.root,'2026-09',dict.fromkeys(account.POOLS,0),apply=True)
        scripts = self.root/'scripts'
        scripts.mkdir()
        for name in ('account.py','app_export.py','dashboard_data.py','app_desktop.py'):
            (scripts/name).write_text('# synthetic tool marker',encoding='utf-8')
        self.database = self.root/'data/account.sqlite3'
        self.config = self.root/'local/display.json'
        clock = patch.object(data,'current_month',return_value='2026-11')
        clock.start();self.addCleanup(clock.stop)

    def start(self, month):
        self.config.write_text(json.dumps({'trackingStartMonth':month}),encoding='utf-8')

    def export(self):
        return Path(app_export.export_snapshot(self.database)['output'])

    def test_old_app_schema_is_preserved_until_local_refresh_without_ledger_changes(self):
        path = self.export()
        value = json.loads(path.read_text(encoding='utf-8'))
        value['schemaVersion'] = 2
        for month in value['months']:
            month.pop('incomeRecords')
            month.pop('incomeCategories')
            month['summary'].pop('incomeCents')
        path.write_text(json.dumps(value), encoding='utf-8')
        old = path.read_bytes()
        before = app_desktop.ledger_store.source_hash(self.database)
        with self.assertRaisesRegex(ValueError, 'App V3'):
            app_desktop.load_snapshot(self.root)
        self.assertEqual(path.read_bytes(), old)
        with patch.object(app_desktop, '_publish', return_value={
                'ok': None, 'published': False, 'skipped': True, 'reason': '合成本地刷新'}) as publish:
            refreshed = app_desktop.refresh_snapshot(self.root)
        self.assertEqual(refreshed['snapshot']['schemaVersion'], 3)
        self.assertTrue(refreshed['appPublish']['skipped'])
        self.assertEqual(app_desktop.ledger_store.source_hash(self.database), before)
        self.assertEqual(publish.call_count, 1)

    def test_restore_local_rebuilds_old_schema_without_publish_or_business_change(self):
        path = self.export()
        value = json.loads(path.read_text(encoding='utf-8'))
        value['schemaVersion'] = 2
        for month in value['months']:
            month.pop('incomeRecords')
            month.pop('incomeCategories')
            month['summary'].pop('incomeCents')
        path.write_text(json.dumps(value), encoding='utf-8')
        before = app_desktop.ledger_store.source_hash(self.database)
        with self.assertRaises(ValueError): app_desktop.load_snapshot(self.root)
        with patch.object(app_desktop, '_publish', side_effect=AssertionError('restoration must not publish')) as publish:
            result = app_desktop.refresh_snapshot(self.root, publish=False)
        publish.assert_not_called()
        self.assertEqual(result['snapshot']['schemaVersion'], 3)
        self.assertEqual(result['appPublish'], {'ok':None, 'published':False, 'skipped':True,
                                              'reason':'仅恢复本地账本目录，未执行App发布。'})
        self.assertFalse(result['sourceChanged'])
        self.assertFalse(result['monthChanged'])
        self.assertEqual(before, app_desktop.ledger_store.source_hash(self.database))

    def test_failed_local_restore_preserves_old_snapshot_and_never_publishes(self):
        path = self.export()
        old = b'{"schemaVersion":2,"syntheticOldCache":true}'
        path.write_bytes(old)
        before = self.database.read_bytes()
        with patch.object(app_export, '_verify_current_source', side_effect=RuntimeError('synthetic source changed')), \
             patch.object(app_desktop, '_publish', side_effect=AssertionError('must not publish')) as publish:
            with self.assertRaisesRegex(RuntimeError, 'synthetic source changed'):
                app_desktop.refresh_snapshot(self.root, publish=False)
        publish.assert_not_called()
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(before, self.database.read_bytes())

    def test_restore_local_cli_dispatches_without_publication(self):
        with patch.object(sys, 'argv', ['app_desktop.py', 'restore-local', '--project-root', str(self.root)]), \
             patch.object(app_desktop, 'refresh_snapshot', return_value={'synthetic': True}) as refresh, \
             patch.object(app_desktop, 'load_snapshot', side_effect=AssertionError('old cache must not be loaded first')), \
             patch.object(sys, 'stdout'):
            self.assertEqual(app_desktop.main(), 0)
        refresh.assert_called_once_with(self.root, publish=False)

    def test_changed_display_range_hides_cached_snapshot_with_same_hash_and_calendar(self):
        self.start('2026-09')
        self.export()
        current = app_desktop.load_snapshot(self.root)
        self.assertIsNotNone(current['snapshot'])
        self.start('2026-10')
        stale = app_desktop.load_snapshot(self.root)
        self.assertTrue(stale['sourceChanged'])
        self.assertFalse(stale['monthChanged'])
        self.assertIsNone(stale['snapshot'])
        self.export()
        self.assertEqual(app_desktop.load_snapshot(self.root)['snapshot']['coverage']['months'],['2026-11','2026-10'])

    def test_legacy_cache_works_for_same_range_but_is_stale_when_range_expands(self):
        self.start('2026-10')
        path = self.export()
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        snapshot['coverage']['kind'] = 'recent-months'
        path.write_text(json.dumps(snapshot),encoding='utf-8')
        self.assertIsNotNone(app_desktop.load_snapshot(self.root)['snapshot'])
        self.start('2026-09')
        stale = app_desktop.load_snapshot(self.root)
        self.assertTrue(stale['sourceChanged'])
        self.assertIsNone(stale['snapshot'])

    def test_display_change_during_payload_read_is_not_accepted_as_current(self):
        self.start('2026-09')
        path = self.export()
        validate = app_export.validate_app_snapshot
        def changed(value):
            self.start('2026-10')
            return validate(value)
        with patch.object(app_export,'validate_app_snapshot',side_effect=changed):
            stale = app_desktop.load_snapshot(self.root)
        self.assertTrue(stale['sourceChanged'])
        self.assertIsNone(stale['snapshot'])
        self.assertTrue(path.is_file())

    def test_legacy_workbook_read_does_not_apply_local_tracking_config(self):
        fixture = Path(__file__).parent/'fixtures/app-v2-snapshot.json'
        snapshot = json.loads(fixture.read_text(encoding='utf-8'))
        book = self.root/'synthetic-backup.xlsx';book.write_bytes(b'synthetic')
        path = self.root/'legacy-snapshot.json'
        path.write_text(json.dumps(snapshot),encoding='utf-8')
        self.config.write_text('{invalid optional config ignored for legacy Excel',encoding='utf-8')
        with patch.object(app_desktop,'project_paths',return_value=(self.root,book,path)), \
             patch.object(app_desktop.ledger_source,'source_hash',return_value=snapshot['sourceHash']), \
             patch.object(data,'current_month',return_value=snapshot['calendarMonth']):
            result = app_desktop.load_snapshot(self.root)
        self.assertIsNotNone(result['snapshot'])
        self.assertFalse(result['sourceChanged'])

    def test_refresh_flags_old_returned_range_when_another_export_updates_during_publish(self):
        self.start('2026-09')
        publication = {'ok':True,'published':True}
        def changed(*args):
            self.start('2026-10')
            self.export()
            return publication
        with patch.object(app_desktop,'_publish',side_effect=changed):
            result = app_desktop.refresh_snapshot(self.root)
        self.assertEqual(result['appPublish'],publication)
        self.assertEqual(result['snapshot']['coverage']['months'],['2026-11','2026-10','2026-09'])
        self.assertTrue(result['sourceChanged'])
        self.assertFalse(result['monthChanged'])


if __name__ == '__main__': unittest.main()
