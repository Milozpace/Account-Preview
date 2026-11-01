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
        root.mkdir()
        onboarding.initialize(root, start, dict.fromkeys(account.POOLS, 0), apply=True)
        return root / 'data/account.sqlite3'

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
