"""Bookkeeping regressions; only generated bills and temporary ledgers."""
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import ledger_store
import test_bill_inbox as inbox_fixtures
CSV = inbox_fixtures.CSV


class BookkeepingReviewTests(unittest.TestCase):
    setUp = inbox_fixtures.InboxTests.setUp
    remote = inbox_fixtures.InboxTests.remote
    fetch = inbox_fixtures.InboxTests.fetch
    database = inbox_fixtures.InboxTests.database
    def approved_source(self, content=CSV):
        self.fetch(content=content)
        db = self.database()
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        rows, _ = account.parse_sources([source])
        for row in rows:
            row.update({'最终财务事件': 'purchase', '统计口径': 'PERSONAL',
                        '计入消费金额': row['原始金额'], '主分类': '日常餐饮',
                        '资金池': '运行账户', '语义说明': '合成本人日常午餐'})
        approved = dict(records=rows, updates=[], reviewed_by='synthetic-agent',
                        reviewed_at='2026-11-03T16:00:00+08:00',
                        source_files=[{'path': str(source), 'sha256': account.sha(source)}])
        return db, source, approved

    def test_original_omission_or_reconstruction_is_rejected_before_backup_and_commit(self):
        db, source, approved = self.approved_source()
        before = ledger_store.source_hash(db)
        for field in ('交易对方', '来源行', '来源文件'):
            with self.subTest(field=field):
                bad = copy.deepcopy(approved)
                bad['records'][0].pop(field)
                for apply in (False, True):
                    with self.assertRaisesRegex(ValueError, '来源|原始'):
                        ledger_store.import_reviewed(db, bad, apply=apply)
        bad = copy.deepcopy(approved)
        bad['records'][0]['原始金额'] = '9.99'
        with self.assertRaisesRegex(ValueError, '来源|原始'):
            ledger_store.import_reviewed(db, bad, apply=True)
        self.assertEqual(ledger_store.source_hash(db), before)
        self.assertFalse(db.with_name('account.previous.sqlite3').exists())

    def test_changed_source_hash_rejected_even_when_approved_rows_look_valid(self):
        db, source, approved = self.approved_source()
        source.write_bytes(source.read_bytes() + b'\n')
        before = ledger_store.source_hash(db)
        with self.assertRaisesRegex(ValueError, '来源|哈希|SHA'):
            ledger_store.import_reviewed(db, approved, apply=True)
        self.assertEqual(ledger_store.source_hash(db), before)

    def test_partial_import_then_reprepare_and_user_correction_preserve_facts(self):
        content = (CSV.decode('utf-8-sig') +
                   '2026-10-18 13:00:00,synthetic-second,0.80,合成收款方\n').encode('utf-8-sig')
        db, source, approved = self.approved_source(content)
        original = copy.deepcopy(approved['records'][1])
        first = copy.deepcopy(approved); first['records'] = first['records'][:1]
        ledger_store.import_reviewed(db, first, apply=True)
        with patch.object(self.inbox, '_run', side_effect=AssertionError('no recorded receipt yet')):
            pending = self.inbox.receipts(db, apply=True)
        self.assertTrue(pending['ok'])
        self.assertEqual(pending['pending'][0]['reason'], 'unimported-records')
        self.assertEqual(pending['pending'][0]['newRecords'], 1)
        parsed, _ = account.parse_sources([source])
        remaining, _, conflicts = account.prepare_records(ledger_store.read_state(db)['rows'], parsed)
        self.assertFalse(conflicts); self.assertEqual(len(remaining), 1)
        remaining[0].update({'最终财务事件': 'purchase', '统计口径': 'PERSONAL',
                             '计入消费金额': '0.80', '主分类': 'Unknown', '资金池': '待归类',
                             '资金池判定': '小额二维码收款，尚无用途证据；完成后集中说明',
                             '语义说明': '消费金额已知，具体用途待补充'})
        second = {**approved, 'records': remaining}
        result = ledger_store.import_reviewed(db, second, apply=True)
        self.assertFalse(result['classificationReview']['reviewComplete'])
        identity = remaining[0]['记录ID']
        correction = dict(records=[], updates=[{'记录ID': identity,
            'changes': {'主分类': '交通出行', '资金池': '运行账户', '资金池判定': '本人确认日常通勤共享单车'},
            'reason': '补充分类和分池', 'confirmation_source': '合成用户明确回复：这是通勤共享单车，帮我补回去'}],
            reviewed_by='synthetic-agent', reviewed_at='2026-11-03T17:00:00+08:00')
        result = ledger_store.import_reviewed(db, correction, apply=True)
        self.assertTrue(result['classificationReview']['reviewComplete'])
        row = next(r for r in ledger_store.read_state(db)['rows'] if r['记录ID'] == identity)
        for field in account.RAW:
            self.assertEqual(row.get(field), original.get(field), field)
        self.assertEqual(ledger_store.import_reviewed(db, correction)['updated'], 0)
        with patch.object(self.inbox, '_run', return_value={'ok': True}):
            self.assertEqual(self.inbox.receipts(db, apply=True)['recorded'], 1)

    def test_unimported_source_is_pending_not_false_complete(self):
        self.fetch(); db = self.database()
        with patch.object(self.inbox, '_run', side_effect=AssertionError('no transport')):
            result = self.inbox.receipts(db, apply=True)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['pending']), 1)
        self.assertEqual(result['blocked'], [])

    def test_source_conflict_is_diagnosed_without_network_retry(self):
        db, source, approved = self.approved_source()
        # Simulate a legacy defective batch, before source-bound imports existed.
        approved.pop('source_files'); approved['records'][0].pop('交易对方')
        ledger_store.import_reviewed(db, approved, apply=True)
        with patch.object(self.inbox, '_run', side_effect=AssertionError('not a network retry')):
            result = self.inbox.receipts(db, apply=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['blocked'][0]['reason'], 'source-conflict')
        self.assertEqual(result['blocked'][0]['conflictCount'], 1)
        self.assertEqual(result['receiptErrors'], 0)

    def test_missing_or_changed_source_is_diagnosed(self):
        db, source, approved = self.approved_source()
        raw = source.read_bytes()
        for reason in ('source-changed', 'source-missing'):
            if reason == 'source-changed': source.write_bytes(raw + b'\n')
            else: source.unlink()
            with patch.object(self.inbox, '_run', side_effect=AssertionError('no transport')):
                result = self.inbox.receipts(db, apply=True)
            self.assertFalse(result['ok'])
            self.assertEqual(result['blocked'][0]['reason'], reason)

    def test_locator_updates_cannot_bypass_original_source_validation(self):
        db, source, approved = self.approved_source()
        identity = approved['records'][0]['记录ID']
        approved['updates'] = [{'记录ID': identity, 'changes': {'来源文件': 'invented.csv', '来源行': 999},
                                'reason': '合成错误尝试', 'confirmation_source': '测试'}]
        before = ledger_store.source_hash(db)
        with self.assertRaises(ValueError):
            ledger_store.import_reviewed(db, approved, apply=True)
        self.assertEqual(ledger_store.source_hash(db), before)

    def test_blocked_recorded_receipt_retry_is_not_counted_as_network_failure(self):
        db, source, approved = self.approved_source()
        ledger_store.import_reviewed(db, approved, apply=True)
        with patch.object(self.inbox, '_run', side_effect=RuntimeError('synthetic offline')):
            result = self.inbox.receipts(db, apply=True)
        self.assertEqual(result['receiptErrors'], 1)
        source.write_bytes(source.read_bytes() + b'\n')
        with patch.object(self.inbox, '_run', side_effect=AssertionError('must not attempt network')):
            result = self.inbox.receipts(db, apply=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['blocked'][0]['reason'], 'source-changed')
        self.assertEqual(result['receiptErrors'], 0)

    def test_find_existing_ids_is_bounded_readonly_and_does_not_expose_orders(self):
        db, source, approved = self.approved_source()
        ledger_store.import_reviewed(db, approved, apply=True)
        before = db.read_bytes()
        out = io.StringIO()
        with redirect_stdout(out):
            code = account.main(['--database', str(db), 'find-records', '--month', '2026-10', '--query', '合成', '--limit', '1'])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(result['records'][0]['记录ID'], approved['records'][0]['记录ID'])
        self.assertNotIn('交易订单号', result['records'][0])
        self.assertNotIn('商家订单号', result['records'][0])
        self.assertEqual(db.read_bytes(), before)
        for args in ([], ['--limit', '0'], ['--month', '2026-13']):
            with self.assertRaises(ValueError):
                account.main(['--database', str(db), 'find-records', *args])


if __name__ == '__main__':
    unittest.main()
