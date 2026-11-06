"""Bounded income transition review; synthetic ledgers only."""
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_ledger_store import account, excel_fixture, row, store


class IncomeReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'ledger.sqlite3'
        workbook = self.root / 'synthetic.xlsx'
        excel_fixture(workbook, [])
        store.migrate_excel(workbook, self.db)

    def approved(self, records=None, updates=None):
        return dict(records=records or [], updates=updates or [], reviewed_by='synthetic-agent',
                    reviewed_at='2026-11-06T12:00:00+08:00', reason='synthetic income review',
                    confirmation_source='synthetic fixture')

    def add(self, order, month='2026-11', scope='EXCLUDED', direction='收入', **changes):
        value = row(order, **{'交易时间': month+'-02 12:00:00', '统计口径': scope,
                             '收支方向': direction, '计入消费金额': '0.00', '净支出候选': '0.00',
                             '资金池': '', '最终财务事件': 'income' if scope == 'INCOME' else 'excluded',
                             **changes})
        store.import_reviewed(self.db, self.approved([value]), apply=True)
        return value

    def query(self, **kwargs):
        with patch.object(store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 6, tzinfo=account.TZ)
            return store.income_review_candidates(self.db, **kwargs)

    def test_query_is_database_filtered_paged_private_and_readonly(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        one = self.add('recent-income', **{'商品/说明': 'salary', '原始类型': 'other'})
        two = self.add('legacy-structural', scope='STRUCTURAL', direction='不计收支')
        self.add('transfer', **{'最终财务事件': 'transfer'})
        self.add('refund', **{'最终财务事件': 'refund'})
        self.add('confirmed', scope='INCOME')
        personal = self.add('personal-raw-income', scope='PERSONAL')
        self.add('spending', scope='PERSONAL', direction='支出')
        self.add('old-income', month='2026-08')
        before = self.db.read_bytes()
        with patch.object(store, '_read', side_effect=AssertionError('must filter within SQLite')):
            first = self.query(limit=2)
            second = self.query(limit=2, offset=2)
        self.assertEqual(first['matchedRecords'], 5)
        self.assertTrue(first['truncated'])
        self.assertEqual(first['nextOffset'], 2)
        self.assertEqual(second['nextOffset'], 4)
        third = self.query(limit=2, offset=4)
        self.assertIsNone(third['nextOffset'])
        ids = [r['记录ID'] for page in (first, second, third) for r in page['records']]
        self.assertEqual(len(set(ids)), 5)
        self.assertIn(personal['记录ID'], ids)
        self.assertIn(one['记录ID'], ids)
        self.assertIn(two['记录ID'], ids)
        self.assertEqual(first['queryStartMonth'], '2026-09')
        self.assertEqual(first['queryEndMonth'], '2026-11')
        self.assertEqual(first['ledgerStartMonth'], '2026-08')
        self.assertTrue(first['historyExceedsWindow'])
        self.assertNotIn('交易订单号', json.dumps(first, ensure_ascii=False))
        self.assertNotIn('商家订单号', json.dumps(first, ensure_ascii=False))
        self.assertEqual(before, self.db.read_bytes())

    def test_short_history_returns_all_existing_rows_and_empty_ledger_is_explicit(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        empty = self.query()
        self.assertIsNone(empty['ledgerStartMonth'])
        self.assertEqual(empty['matchedRecords'], 0)
        self.add('only')
        report = self.query()
        self.assertFalse(report['historyExceedsWindow'])
        self.assertEqual(report['queryStartMonth'], '2026-11')
        self.assertEqual(report['matchedRecords'], 1)

    def test_calendar_year_boundary_and_beijing_clock(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        self.add('nov', month='2025-11')
        self.add('oct', month='2025-10')
        self.add('jan', month='2026-01')
        with patch.object(store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 1, 1, tzinfo=account.TZ)
            report = store.income_review_candidates(self.db)
            clock.now.assert_called_once_with(account.TZ)
        self.assertEqual(report['queryStartMonth'], '2025-11')
        self.assertEqual(report['queryEndMonth'], '2026-01')
        self.assertEqual(report['matchedRecords'], 2)

    def test_all_explicitly_covers_old_history_but_default_stays_current(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        self.add('old', month='2024-01')
        self.add('old-later', month='2024-04')
        self.add('future', month='2027-01')
        self.assertEqual(self.query()['matchedRecords'], 0)
        report = self.query(months='all')
        self.assertEqual(report['queryStartMonth'], '2024-01')
        self.assertEqual(report['queryEndMonth'], '2027-01')
        self.assertEqual(report['matchedRecords'], 3)
        self.assertFalse(report['historyExceedsWindow'])
        self.assertEqual(self.query(months='6')['matchedRecords'], 2)

    def test_old_short_history_is_all_nonfuture_history(self):
        self.add('old-only', month='2024-01')
        self.add('old-third-month', month='2024-03')
        self.add('future', month='2027-01')
        report = self.query()
        self.assertEqual(report['requestedStartMonth'], '2026-09')
        self.assertEqual(report['requestedEndMonth'], '2026-11')
        self.assertEqual(report['queryStartMonth'], '2024-01')
        self.assertEqual(report['queryEndMonth'], '2024-03')
        self.assertEqual(report['historyMonths'], 3)
        self.assertFalse(report['historyExceedsWindow'])
        self.assertEqual(report['matchedRecords'], 2)

    def test_offset_past_end_including_huge_integer_is_empty(self):
        self.add('only')
        report = self.query(offset=10**30)
        self.assertEqual(report['records'], [])
        self.assertIsNone(report['nextOffset'])

    def test_invalid_parameters_rejected_without_changes(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        before = self.db.read_bytes()
        for args in ({'months':0}, {'months':'-1'}, {'months':'1.5'}, {'months':True},
                     {'months':1000000}, {'limit':0}, {'limit':101}, {'limit':True},
                     {'offset':-1}, {'offset':True}, {'offset':0.5}):
            with self.subTest(args=args), self.assertRaises(ValueError): self.query(**args)
        self.assertEqual(before, self.db.read_bytes())

    def test_audited_correction_excludes_income_and_preserves_raw_fields(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        original = self.add('legacy-salary', scope='STRUCTURAL')
        transfer = self.add('keep-transfer', **{'最终财务事件': 'transfer'})
        before = store.read_state(self.db)
        change = dict(记录ID=original['记录ID'], changes={'统计口径':'INCOME', '最终财务事件':'income',
                       '计入消费金额':'0.00', '资金池':'', '主分类':'工资薪酬', '语义说明':'synthetic confirmed income'},
                      reason='synthetic source establishes salary', confirmation_source='synthetic fixture')
        approved = self.approved(updates=[change])
        self.assertEqual(store.import_reviewed(self.db, approved)['updated'], 1)
        self.assertTrue(store.import_reviewed(self.db, approved, apply=True)['applied'])
        after = store.read_state(self.db)
        old = {r['记录ID']:r for r in before['rows']}
        new = {r['记录ID']:r for r in after['rows']}
        for field in account.RAW:
            self.assertEqual(old[original['记录ID']][field], new[original['记录ID']][field])
        self.assertEqual(old[transfer['记录ID']], new[transfer['记录ID']])
        self.assertEqual([r['记录ID'] for r in self.query()['records']], [transfer['记录ID']])
        self.assertTrue(store.verify(self.db)['ok'])
        self.assertEqual(store.import_reviewed(self.db, approved)['updated'], 0)
        self.assertEqual(after['revision'], store.read_state(self.db)['revision'])
        with store._connect(self.db) as connection:
            audit = connection.execute("SELECT before_json, after_json, reason, confirmation_source FROM audit_log WHERE operation='update'").fetchone()
        self.assertEqual(json.loads(audit[0])['统计口径'], 'STRUCTURAL')
        self.assertEqual(json.loads(audit[1])['统计口径'], 'INCOME')
        self.assertEqual(audit[2:], ('synthetic source establishes salary', 'synthetic fixture'))
        forbidden = {**change, 'changes': {'原始金额':'9999.00'}}
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved(updates=[forbidden]), apply=True)


    def test_cli_exposes_query_and_rejects_non_sqlite(self):
        self.assertTrue(hasattr(store, 'income_review_candidates'), 'income review query is missing')
        self.add('cli')
        result = subprocess.run([sys.executable, str(Path(account.__file__)), '--root', str(self.root),
                                 '--database', str(self.db), 'income-review-candidates', '--months', 'all',
                                 '--limit', '1', '--offset', '0'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['matchedRecords'], 1)
        rejected = subprocess.run([sys.executable, str(Path(account.__file__)), '--root', str(self.root),
                                   '--workbook', str(self.root/'synthetic.xlsx'), 'income-review-candidates'],
                                  capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('SQLite', rejected.stderr)



if __name__ == '__main__': unittest.main()
