"""Owner-specific coverage regressions; use synthetic state and temporary ledgers only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import app_export
import dashboard_data as data
import ledger_store
import onboarding

FIXTURE = Path(__file__).parent / 'fixtures/app-v2-history-snapshot.json'


class MonthHistoryTests(unittest.TestCase):
    def make_database(self, root, start):
        root = root.resolve()
        root.mkdir()
        onboarding.initialize(root, start, dict.fromkeys(account.POOLS, 0), apply=True)
        return root / 'data/account.sqlite3'

    def display_start(self, database, value):
        path = database.parent.parent/'local/display.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'trackingStartMonth':value}), encoding='utf-8')
        return path

    def make_migrated_database(self, root):
        from test_ledger_store import excel_fixture, row
        root = root.resolve()
        root.mkdir()
        workbook = root/'synthetic-migration.xlsx'
        excel_fixture(workbook, rows=[row(**{'交易时间':'2025-09-02 12:00:00'})])
        database = root/'data/account.sqlite3'
        ledger_store.migrate_excel(workbook, database)
        return database

    def test_confirmed_display_start_overrides_older_migrated_transactions(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            database = self.make_migrated_database(Path(temp)/'owner')
            self.display_start(database, '2026-09')
            before = ledger_store.source_hash(database)
            data.sync_snapshot(database)
            exported = app_export.export_snapshot(database)
            snapshot = json.loads(Path(exported['output']).read_text(encoding='utf-8'))
            self.assertEqual(snapshot['coverage']['months'], ['2026-11','2026-10','2026-09'])
            self.assertEqual(snapshot['ledgerCount'], 1)
            self.assertEqual(ledger_store.source_hash(database), before)

    def test_config_change_invalidates_cache_without_business_hash_change(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            database = self.make_migrated_database(Path(temp)/'owner')
            self.display_start(database, '2026-09')
            data.sync_snapshot(database)
            cached = data.read_snapshot(database)
            self.assertTrue(data.snapshot_current(database,cached))
            self.display_start(database, '2026-10')
            self.assertFalse(data.snapshot_current(database,cached))
            exported = app_export.export_snapshot(database)
            snapshot = json.loads(Path(exported['output']).read_text(encoding='utf-8'))
            self.assertEqual(snapshot['sourceHash'], cached['sourceHash'])
            self.assertEqual(snapshot['coverage']['months'], ['2026-11','2026-10'])

    def test_display_preferences_do_not_leak_between_owners_or_become_defaults(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            first = self.make_migrated_database(Path(temp)/'first')
            second = self.make_migrated_database(Path(temp)/'second')
            self.display_start(first, '2026-10')
            self.assertEqual(data.sync_snapshot(first)['months'], ['2026-11','2026-10'])
            second_months = data.sync_snapshot(second)['months']
            self.assertEqual(second_months[-1], '2025-09')
            self.assertEqual(len(second_months), 15)
            self.display_start(second, '2026-11')
            self.assertEqual(data.sync_snapshot(second)['months'], ['2026-11'])
            self.assertEqual(data.sync_snapshot(first)['months'], ['2026-11','2026-10'])

    def test_future_confirmed_display_start_keeps_current_month(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            database = self.make_database(Path(temp)/'new', '2026-12')
            self.display_start(database, '2027-01')
            self.assertEqual(data.sync_snapshot(database)['months'], ['2026-11'])

    def test_invalid_local_display_config_fails_closed_and_preserves_cache(self):
        invalid = ['{', '{}', '[]', 'null', '{"trackingStartMonth":"2026-09","extra":true}',
                   '{"trackingStartMonth":null}', '{"trackingStartMonth":202609}',
                   '{"trackingStartMonth":"2026-13"}', '{"trackingStartMonth":"2026-9"}',
                   '{"trackingStartMonth":"0000-09"}', '{"trackingStartMonth":"2026-09-01"}',
                   '{"trackingStartMonth":"2026-09","trackingStartMonth":"2026-10"}']
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            database = self.make_database(Path(temp)/'owner', '2026-09')
            data.sync_snapshot(database)
            cached = data.read_snapshot(database)
            path = self.display_start(database, '2026-09')
            cache_path = database.parent.parent/'.runtime/dashboard.json'
            baseline = cache_path.read_bytes()
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(value,encoding='utf-8')
                    self.assertFalse(data.snapshot_current(database,cached))
                    with self.assertRaisesRegex(ValueError,'display|展示'):
                        data.sync_snapshot(database)
                    self.assertEqual(cache_path.read_bytes(),baseline)

    def test_redirected_local_config_fails_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self.make_database(Path(temp)/'owner', '2026-09')
            config = self.display_start(database, '2026-09')
            original = Path.is_symlink
            for redirected in (config.parent, config):
                with self.subTest(path=redirected), \
                     patch.object(Path,'is_symlink',lambda path:path==redirected or original(path)), \
                     patch.object(Path,'read_text',side_effect=AssertionError('must not follow config redirect')):
                    with self.assertRaisesRegex(ValueError,'display|展示'):
                        data.source_tracking_months(database, ledger_store.read_state(database), '2026-11')

    def test_local_directory_cannot_be_replaced_by_an_ordinary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'local').write_text('not a directory',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'display|展示'):
                data.source_tracking_months(root/'data/account.sqlite3', {'rows':[],'rates':[]}, '2026-11')

    def test_junction_local_config_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temp:
            database = self.make_database(Path(temp)/'owner', '2026-09')
            config = self.display_start(database, '2026-09')
            original = Path.is_junction
            for redirected in (config.parent, config):
                with self.subTest(path=redirected), \
                     patch.object(Path,'is_junction',lambda path:path==redirected or original(path)), \
                     patch.object(Path,'read_text',side_effect=AssertionError('must not follow config junction')):
                    with self.assertRaisesRegex(ValueError,'display|展示'):
                        data.source_tracking_months(database, ledger_store.read_state(database), '2026-11')

    def test_config_change_during_generation_preserves_previous_snapshot(self):
        import ledger_calculation
        with tempfile.TemporaryDirectory() as temp, patch.object(data,'current_month',return_value='2026-11'):
            database = self.make_database(Path(temp)/'owner', '2026-09')
            config = self.display_start(database, '2026-09')
            data.sync_snapshot(database)
            cache_path = database.parent.parent/'.runtime/dashboard.json'
            baseline = cache_path.read_bytes()
            original = ledger_calculation.calculate_month
            def changed(*args):
                config.write_text('{"trackingStartMonth":"2026-10"}',encoding='utf-8')
                return original(*args)
            with patch.object(ledger_calculation,'calculate_month',side_effect=changed):
                with self.assertRaisesRegex(RuntimeError,'展示|配置'):
                    data.sync_snapshot(database)
            self.assertEqual(cache_path.read_bytes(),baseline)

    def test_each_owner_exports_own_start_and_empty_months(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data, 'current_month', return_value='2027-02'):
            for owner, start, expected in (
                ('first', '2026-11', ['2027-02','2027-01','2026-12','2026-11']),
                ('second', '2027-01', ['2027-02','2027-01']),
                ('new', '2027-02', ['2027-02']),
                ('future', '2027-03', ['2027-02']),
            ):
                with self.subTest(owner=owner):
                    database = self.make_database(Path(temp)/owner, start)
                    before = ledger_store.source_hash(database)
                    report = data.sync_snapshot(database)
                    self.assertEqual(report['months'], expected)
                    exported = app_export.export_snapshot(database)
                    snapshot = json.loads(Path(exported['output']).read_text(encoding='utf-8'))
                    self.assertEqual(snapshot['coverage'], {'kind':'tracked-months','months':expected})
                    self.assertTrue(all(view['records'] == [] for view in snapshot['months']))
                    self.assertEqual(before, ledger_store.source_hash(database))

    def test_transaction_before_budget_is_own_history_start(self):
        state = {'rows':[{'交易时间':'2025-12-01 10:00:00'}], 'rates':[{'生效月份':'2026-02-01'}]}
        self.assertEqual(data.tracked_months(state, '2026-03'), ['2026-03','2026-02','2026-01','2025-12'])

    def test_year_rollover_retains_the_oldest_month(self):
        self.assertEqual(data.display_months('2027-01', '2026-11'), ['2027-01','2026-12','2026-11'])

    def test_history_fixture_and_legacy_cache_both_validate(self):
        for path in (FIXTURE, FIXTURE.with_name('app-v2-snapshot.json')):
            app_export.validate_app_snapshot(json.loads(path.read_text(encoding='utf-8')))

    def test_tracked_range_rejects_gaps_reversal_duplicate_and_future(self):
        base = json.loads(FIXTURE.read_text(encoding='utf-8'))
        variants = [base['months'][1:], base['months'][::2], list(reversed(base['months'])),
                    base['months'] + [base['months'][-1]]]
        future = copy.deepcopy(base['months'][0]); future['month'] = '2026-12'
        invalid = copy.deepcopy(base['months'][0]); invalid['month'] = '2026-13'
        variants += [[future]+base['months'], [invalid]+base['months']]
        for months in variants:
            with self.subTest(months=[m['month'] for m in months]):
                value = copy.deepcopy(base)
                value['months'] = months
                value['coverage']['months'] = [m['month'] for m in months]
                with self.assertRaises(ValueError): app_export.validate_app_snapshot(value)

    def test_tracked_oldest_is_supplied_by_export_not_a_personal_constant(self):
        value = json.loads(FIXTURE.read_text(encoding='utf-8'))
        value['months'].pop()
        value['coverage']['months'].pop()
        app_export.validate_app_snapshot(value)

    def test_invalid_calendar_and_start_month_rejected(self):
        for calendar, start in [('2026-13','2026-11'), ('2026-11','2026-13'), ('2026-11','2026-12')]:
            with self.subTest(calendar=calendar,start=start), self.assertRaises(ValueError):
                data.display_months(calendar, start)

    def test_current_two_month_cache_is_refreshed_to_own_full_range(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(data, 'current_month', return_value='2027-02'):
            database = self.make_database(Path(temp)/'owner', '2026-11')
            cached = {'schemaVersion':1,'sourceHash':ledger_store.source_hash(database),'calendarMonth':'2027-02',
                      'months':[{'month':'2027-02'},{'month':'2027-01'}]}
            runtime = database.parent.parent/'.runtime'
            runtime.mkdir(exist_ok=True)
            (runtime/'dashboard.json').write_text(json.dumps(cached), encoding='utf-8')
            self.assertFalse(data.snapshot_current(database,cached,'2027-02'))
            exported = app_export.export_snapshot(database)
            snapshot = json.loads(Path(exported['output']).read_text(encoding='utf-8'))
            self.assertEqual(snapshot['coverage']['months'], ['2027-02','2027-01','2026-12','2026-11'])
            self.assertTrue(data.snapshot_current(database,data.read_snapshot(database),'2027-02'))


if __name__ == '__main__': unittest.main()
