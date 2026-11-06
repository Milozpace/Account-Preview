"""SQLite authority safety tests; all writes use synthetic temporary ledgers."""
import copy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
try:
    import ledger_store as store
except ImportError:
    store = None


def row(order='001', amount='10.00', **changes):
    result = dict.fromkeys(account.HEADERS[:36])
    result.update({'平台': 'TEST', '交易时间': '2026-09-02 12:00:00', '交易对方': '合成商户',
                   '商品/说明': '', '交易订单号': order, '商家订单号': None,
                   '原始金额': amount, '退款调整': '0.00', '净支出候选': amount,
                   '计入消费金额': amount, '最终财务事件': 'purchase', '统计口径': 'PERSONAL',
                   '主分类': '合成分类', '资金池': account.POOLS[0], '语义说明': '测试审定',
                   '记录类型': '原始交易'})
    result.update(changes)
    result['记录ID'] = account.normalize_record(result)['记录ID']
    return result


def excel_fixture(path, rows=None):
    import openpyxl
    from openpyxl.worksheet.table import Table
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '01_账单流水'
    ws.append(account.HEADERS)
    for r in rows if rows is not None else [row()]:
        ws.append([r.get(k) for k in account.HEADERS])
    pool = wb.create_sheet('02_资金池')
    for i in range(7, 11):
        pool.cell(i, 3, 0)
    pool.append([])
    for col, value in enumerate(['生效月份', *account.POOLS], 1):
        pool.cell(17, col, value)
    for col, value in enumerate(['2026-09-01', 100, 50, 20, 30], 1):
        pool.cell(18, col, value)
    pool.add_table(Table(displayName='BudgetRates', ref='A17:E18'))
    wb.save(path)
    wb.close()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(store, 'SQLite ledger storage module is required')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.xlsx = self.root / 'baseline.xlsx'
        self.db = self.root / 'ledger.sqlite3'
        excel_fixture(self.xlsx)
        store.migrate_excel(self.xlsx, self.db)

    def approved(self, records=None, updates=None):
        return {'records': records or [], 'updates': updates or [], 'reviewed_by': 'synthetic-agent',
                'reviewed_at': '2026-10-18T10:00:00+08:00', 'reason': '合成验证',
                'confirmation_source': '测试指令'}

    def income(self, order, amount='8000.00', **changes):
        """Audited income: received amount is 原始金额, zero counted amount, no pool."""
        return row(order, amount, **{'收支方向': '收入', '统计口径': 'INCOME',
                                     '计入消费金额': '0.00', '净支出候选': '0.00',
                                     '资金池': '', '主分类': '工资薪酬',
                                     '最终财务事件': 'income', **changes})

    def test_import_preflight_exposes_classification_coverage_without_forcing_a_pool(self):
        pending=row('pool-pending',**{'资金池':'待归类','主分类':'餐饮'})
        known=row('pool-known',**{'资金池':'运行账户','主分类':'餐饮'})
        before=self.db.read_bytes()
        result=store.import_reviewed(self.db,self.approved([pending,known]))
        coverage=result['classificationReview']
        self.assertEqual(coverage['consumptionRecords'],2)
        self.assertEqual(coverage['assignedPoolRecords'],1)
        self.assertEqual(coverage['unassignedPoolRecords'],1)
        self.assertEqual(coverage['unassignedPoolCents'],1000)
        self.assertEqual(self.db.read_bytes(),before)
        all_pending=store.import_reviewed(self.db,self.approved([pending]))
        self.assertIn('全部',' '.join(all_pending['classificationReview']['warnings']))
        # Genuine unresolved evidence remains allowed; a warning is not a guess.
        self.assertTrue(all_pending['ok'])

    @unittest.skipUnless(os.name == 'nt', 'Windows native host canonical paths')
    def test_native_extended_drive_path_reads_and_writes_same_ledger(self):
        extended = Path('\\\\?\\' + str(self.db.resolve()))
        expected = store.source_hash(self.db)
        self.assertEqual(store.source_hash(extended), expected)
        approved = self.approved([row('extended-path')])
        store.import_reviewed(extended, approved, apply=True)
        self.assertEqual(len(store.read_state(self.db)['rows']), 2)
        self.assertEqual(store.source_hash(extended), store.source_hash(self.db))

    def test_migration_exact_rows_null_blank_and_cents(self):
        state = store.read_state(self.db)
        self.assertEqual(len(state['rows']), 1)
        self.assertIsNone(state['rows'][0]['商家订单号'])
        self.assertEqual(state['rows'][0]['计入消费金额'], '10.00')
        self.assertEqual(len(state['sourceHash']), 64)
        self.assertTrue(store.verify(self.db)['ok'])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(conn.execute('SELECT amount_cents, typeof(amount_cents) FROM ledger').fetchone(), (1000, 'integer'))
        with self.assertRaises(FileExistsError):
            store.migrate_excel(self.xlsx, self.db)

    def test_import_dry_run_duplicate_and_audit_hash_noop(self):
        before = store.read_state(self.db)
        approved = self.approved([row('002')])
        plan = store.import_reviewed(self.db, approved)
        self.assertEqual(plan['added'], 1)
        self.assertNotIn('records', plan)
        self.assertNotIn('updates', plan)
        self.assertEqual(store.source_hash(self.db), before['sourceHash'])
        store.import_reviewed(self.db, approved, apply=True)
        after = store.read_state(self.db)
        self.assertEqual(len(after['rows']), 2)
        self.assertNotEqual(after['sourceHash'], before['sourceHash'])
        again = store.import_reviewed(self.db, approved, apply=True)
        self.assertEqual(again['duplicates'], 1)
        self.assertEqual(store.read_state(self.db)['revision'], after['revision'])
        self.assertEqual(store.source_hash(self.db), after['sourceHash'])
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE metadata SET value=value WHERE key='revision'")
        self.assertEqual(store.source_hash(self.db), after['sourceHash'])

    def test_invalid_whole_state_rolls_back_all_updates(self):
        old = store.read_state(self.db)
        bad = self.approved([row('002')], [{'记录ID': old['rows'][0]['记录ID'],
                  'changes': {'统计口径': 'EXCLUDED'}, 'reason': '测试', 'confirmation_source': '测试'}])
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, bad, apply=True)
        self.assertEqual(store.read_state(self.db), old)
        with store._writer_lock(self.db):
            pass

    def test_raw_update_forbidden_and_derived_update_audited(self):
        old = store.read_state(self.db)
        update = {'记录ID': old['rows'][0]['记录ID'], 'changes': {'原始金额': '5'},
                  'reason': '测试', 'confirmation_source': '测试'}
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved(updates=[update]), apply=True)
        update['changes'] = {'资金池': '待归类'}
        store.import_reviewed(self.db, self.approved(updates=[update]), apply=True)
        new = store.read_state(self.db)
        self.assertEqual(account.raw_signature(new['rows'][0]), account.raw_signature(old['rows'][0]))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            audit = conn.execute("SELECT before_json, after_json, reason, confirmation_source, reviewer FROM audit_log WHERE operation='update'").fetchone()
            self.assertEqual(audit[2:], ('测试', '测试', 'synthetic-agent'))
            raw = json.loads(audit[0]); raw['原始金额'] = '999.00'
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE ledger SET row_json=?', (json.dumps(raw, ensure_ascii=False),))

    def test_missing_raw_amount_is_not_zero_and_blank_money_stays_blank(self):
        bad = row('002'); bad['原始金额'] = ''
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved([bad]), apply=True)
        good = row('002'); good['退款调整'] = ''
        store.import_reviewed(self.db, self.approved([good]), apply=True)
        self.assertEqual(store.read_state(self.db)['rows'][1]['退款调整'], '')

    def test_latest_backup_restore_refuses_overwrite(self):
        old_hash = store.source_hash(self.db)
        store.import_reviewed(self.db, self.approved([row('002')]), apply=True)
        previous = self.db.with_name(self.db.stem + '.previous.sqlite3')
        self.assertTrue(previous.exists())
        restored = self.root / 'restored.sqlite3'
        store.restore(previous, restored)
        self.assertEqual(store.source_hash(restored), old_hash)
        with self.assertRaises(FileExistsError):
            store.restore(previous, restored)
        copied = self.root / 'copy.sqlite3'
        store.backup(self.db, copied)
        self.assertEqual(store.source_hash(copied), store.source_hash(self.db))

    def test_budget_append_full_pools_historical_immutable(self):
        before = store.read_state(self.db)
        amounts = dict.fromkeys(account.POOLS, '25.00')
        store.append_budget(self.db, '2026-11', amounts, '用户确认预算', apply=False)
        self.assertEqual(store.read_state(self.db), before)
        store.append_budget(self.db, '2026-11', amounts, '用户确认预算', apply=True)
        self.assertEqual(len(store.read_state(self.db)['rates']), 2)
        with self.assertRaises(ValueError):
            store.append_budget(self.db, '2026-10', amounts, '禁止插历史', apply=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM budget_rates')

    def test_split_parent_remainder_plus_children_matches_original(self):
        parent = store.read_state(self.db)['rows'][0]
        child = row('child', '0', **{'记录类型': '派生分摊', '原记录ID': parent['记录ID'],
                    '计入消费金额': '3.00', '净支出候选': '3.00'})
        update = {'记录ID': parent['记录ID'], 'changes': {'计入消费金额': '7', '净支出候选': '7'},
                  'reason': '分摊', 'confirmation_source': '测试确认'}
        store.import_reviewed(self.db, self.approved([child], [update]), apply=True)
        self.assertTrue(store.verify(self.db)['ok'])
        bad = copy.deepcopy(update); bad['changes'] = {'计入消费金额': '8'}
        before = store.read_state(self.db)
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved(updates=[bad]), apply=True)
        self.assertEqual(store.read_state(self.db), before)

    def test_writing_lock_blocks_parallel_writers(self):
        with store._writer_lock(self.db):
            with self.assertRaises((RuntimeError, FileExistsError)):
                store.import_reviewed(self.db, self.approved([row('002')]), apply=True)

    def test_crashed_writer_releases_os_lock(self):
        code = ('import os,sys;sys.path.insert(0,sys.argv[1]);import ledger_store as s;'
                'lock=s._writer_lock(sys.argv[2]);lock.__enter__();os._exit(0)')
        run = subprocess.run([sys.executable, '-c', code, str(Path(store.__file__).parent), str(self.db)],
                             capture_output=True, timeout=15)
        self.assertEqual(run.returncode, 0, run.stderr)
        store.import_reviewed(self.db, self.approved([row('002')]), apply=True)
        self.assertEqual(len(store.read_state(self.db)['rows']), 2)

    def test_insert_preserves_null_and_empty_raw_fields(self):
        incoming = row('002')
        incoming['商家订单号'] = None
        incoming['支付方式'] = ''
        store.import_reviewed(self.db, self.approved([incoming]), apply=True)
        saved = store.read_state(self.db)['rows'][1]
        self.assertIsNone(saved['商家订单号'])
        self.assertEqual(saved['支付方式'], '')

    def test_failure_after_sql_insert_rolls_back_revision_and_rows(self):
        before = store.read_state(self.db)
        with patch.object(store, '_audit', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):
                store.import_reviewed(self.db, self.approved([row('002')]), apply=True)
        self.assertEqual(store.read_state(self.db), before)
        self.assertTrue(store.verify(self.db)['ok'])

    def test_cents_storage_roundtrip_does_not_use_float(self):
        amounts = dict.fromkeys(account.POOLS, '90071992547409.91')
        store.append_budget(self.db, '2027-01', amounts, 'synthetic precision', apply=True)
        saved = store.read_state(self.db)['rates'][-1]
        self.assertEqual(saved[account.POOLS[0]], amounts[account.POOLS[0]])

    def test_unknown_link_fails_without_guessing_parent(self):
        incoming = row('002', **{'关联记录ID': 'tx_unknown'})
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved([incoming]), apply=True)

    def test_duplicate_order_and_unreviewed_records_rejected(self):
        incoming = row('002')
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, {'records': [incoming]}, apply=True)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('INSERT INTO ledger SELECT record_id,sequence,original_key,parent_id,linked_id,original_cents,refund_cents,candidate_cents,amount_cents,row_json FROM ledger')

    def test_verify_describes_the_same_read_transaction_it_validated(self):
        with patch.object(store, 'inspect', side_effect=RuntimeError('second transaction forbidden')):
            result = store.verify(self.db)
        self.assertTrue(result['ok'])
        self.assertEqual(result['sourceHash'], store.source_hash(self.db))

    def batches(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM import_batches ORDER BY revision')]

    def test_migration_batch_provenance_is_structured_and_immutable(self):
        batches = self.batches()
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch['kind'], 'migration')
        self.assertEqual(json.loads(batch['source_files_json']), [{'path': str(self.xlsx.resolve()), 'sha256': account.sha(self.xlsx)}])
        self.assertEqual((batch['records_added'], batch['records_updated'], batch['duplicates'], batch['revision']), (1, 0, 0, 1))
        self.assertEqual(batch['reviewed_by'], 'account-migrate')
        self.assertIsNone(batch['before_source_hash'])
        self.assertEqual(batch['after_source_hash'], store.source_hash(self.db))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE import_batches SET reviewed_by='changed'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM import_batches')

    def test_reviewed_import_batch_retains_manifest_reviewer_and_counts(self):
        old = store.read_state(self.db)
        update = {'记录ID': old['rows'][0]['记录ID'], 'changes': {'资金池': '待归类'},
                  'reason': '测试', 'confirmation_source': '测试确认'}
        source = self.root / 'synthetic-source.csv'
        source.write_text('交易时间,交易订单号,金额,交易对方\n2026-10-18 12:00:00,002,10.00,合成商户\n', encoding='utf-8-sig')
        parsed, _ = account.parse_sources([source])
        parsed[0].update({k: row()[k] for k in ('最终财务事件','统计口径','计入消费金额','主分类','资金池','语义说明')})
        approved = self.approved(parsed, [update])
        manifest = [{'path': str(source.resolve()), 'sha256': account.sha(source)}]
        approved['source_files'] = manifest
        original = copy.deepcopy(approved)
        planned = store.import_reviewed(self.db, approved)
        self.assertEqual(len(self.batches()), 1)
        self.assertEqual(planned['added'], 1)
        result = store.import_reviewed(self.db, approved, apply=True)
        self.assertEqual(approved, original)
        batch = self.batches()[-1]
        self.assertEqual(batch['kind'], 'import')
        self.assertEqual(json.loads(batch['source_files_json']), manifest)
        self.assertEqual((batch['reviewed_by'], batch['reviewed_at']), (approved['reviewed_by'], approved['reviewed_at']))
        self.assertEqual((batch['records_added'], batch['records_updated'], batch['duplicates'], batch['revision']), (1, 1, 0, 2))
        self.assertEqual(batch['before_source_hash'], old['sourceHash'])
        self.assertEqual(batch['after_source_hash'], result['sourceHash'])
        self.assertEqual(store.read_state(self.db)['rows'][-1]['导入批次'], batch['batch_id'])
        self.assertNotIn('source_files', result)
        self.assertNotIn('source_files_json', result)

    def test_legacy_import_marks_unknown_sources_without_invented_hashes(self):
        result = store.import_reviewed(self.db, self.approved([row(), row('002')]), apply=True)
        batch = self.batches()[-1]
        self.assertIsNone(batch['source_files_json'])
        self.assertEqual((result['added'], batch['records_added'], batch['duplicates']), (1, 1, 1))

    def test_manifest_validation_rejects_malformed_and_conflicting_sources(self):
        before = store.read_state(self.db)
        bad_manifests = [None, {}, 'source.csv', [{'path': 'x', 'sha256': 'not-a-hash'}],
                         [{'path': '', 'sha256': 'ab' * 32}], [{'path': 'x', 'sha256': 'ab' * 32, 'extra': True}],
                         [{'path': 'x', 'sha256': 'ab' * 32}, {'path': 'x', 'sha256': 'cd' * 32}]]
        for manifest in bad_manifests:
            with self.subTest(manifest=manifest):
                approved = self.approved([row('002')]); approved['source_files'] = manifest
                with self.assertRaises(ValueError):
                    store.import_reviewed(self.db, approved, apply=True)
        self.assertEqual(store.read_state(self.db), before)
        self.assertEqual(len(self.batches()), 1)

    def test_batch_and_audit_rollback_together_and_noop_does_not_add_batch(self):
        approved = self.approved([row('002')])
        before = store.read_state(self.db)
        with patch.object(store, '_audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):
                store.import_reviewed(self.db, approved, apply=True)
        self.assertEqual(store.read_state(self.db), before)
        self.assertEqual(len(self.batches()), 1)
        store.import_reviewed(self.db, approved, apply=True)
        batches = self.batches()
        after = store.read_state(self.db)
        approved['reviewed_at'] = '2026-10-19T10:00:00+08:00'
        result = store.import_reviewed(self.db, approved, apply=True)
        self.assertFalse(result['applied'])
        self.assertEqual(self.batches(), batches)
        self.assertEqual(store.read_state(self.db), after)

    def test_new_counted_amount_requires_explicit_number_before_normalization(self):
        before = store.read_state(self.db)
        for missing in (None, '', ' ', '/', '--'):
            with self.subTest(missing=missing):
                incoming = row('002'); incoming['计入消费金额'] = missing
                with self.assertRaises(ValueError):
                    store.import_reviewed(self.db, self.approved([incoming]), apply=True)
                self.assertEqual(store.read_state(self.db), before)
        incoming = row('002'); del incoming['计入消费金额']
        with self.assertRaises(ValueError):
            store.import_reviewed(self.db, self.approved([incoming]), apply=True)
        self.assertEqual(len(self.batches()), 1)

    def test_explicit_money_update_cannot_clear_or_coerce_missing_to_zero(self):
        before = store.read_state(self.db)
        for field in ('退款调整', '净支出候选', '计入消费金额'):
            for missing in (None, '', ' ', '/', '--'):
                with self.subTest(field=field, missing=missing):
                    update = {'记录ID': before['rows'][0]['记录ID'], 'changes': {field: missing},
                              'reason': '测试', 'confirmation_source': '测试确认'}
                    with self.assertRaises(ValueError):
                        store.import_reviewed(self.db, self.approved(updates=[update]), apply=True)
                    self.assertEqual(store.read_state(self.db), before)
        self.assertEqual(len(self.batches()), 1)

    def test_explicit_numeric_zero_counted_amount_and_update_remain_allowed(self):
        incoming = row('002', **{'计入消费金额': '0.00'})
        store.import_reviewed(self.db, self.approved([incoming]), apply=True)
        current = store.read_state(self.db)
        update = {'记录ID': current['rows'][0]['记录ID'], 'changes': {'计入消费金额': 0},
                  'reason': '明确核实为零', 'confirmation_source': '测试确认'}
        store.import_reviewed(self.db, self.approved(updates=[update]), apply=True)
        self.assertEqual(store.read_state(self.db)['rows'][0]['计入消费金额'], '0.00')

    def test_income_import_audit_inspect_and_history_stability(self):
        report = store.inspect(self.db)
        self.assertEqual(report['aggregates']['2026-09']['incomeCents'], 0)
        self.assertEqual(report['aggregates']['2026-09']['incomeCount'], 0)
        before = store.read_state(self.db)
        result = store.import_reviewed(self.db, self.approved([self.income('inc-001')]), apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(result['classificationReview']['consumptionRecords'], 0)
        state = store.read_state(self.db)
        self.assertEqual(len(state['rows']), 2)
        self.assertEqual(state['rows'][:1], before['rows'])
        month = store.inspect(self.db)['aggregates']['2026-09']
        self.assertEqual(month['incomeCents'], 800000)
        self.assertEqual(month['incomeCount'], 1)
        self.assertEqual(month['personal'], '10.00')
        self.assertEqual(month['total'], '10.00')
        self.assertTrue(store.verify(self.db)['ok'])

    def test_income_validation_rejects_consumption_amount_pool_refund_and_blank_category(self):
        before = store.read_state(self.db)
        cases = [{'计入消费金额': '5.00'}, {'资金池': account.POOLS[0]},
                 {'退款调整': '100.00'}, {'主分类': ''}]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    store.import_reviewed(self.db, self.approved([self.income('inc-bad', **changes)]), apply=True)
                self.assertEqual(store.read_state(self.db), before)

    def test_negative_income_reversal_nets_with_linked_original(self):
        store.import_reviewed(self.db, self.approved([self.income('inc-001')]), apply=True)
        original = next(r for r in store.read_state(self.db)['rows'] if r['交易订单号'] == 'inc-001')
        reversal = self.income('inc-002', '-300.00', **{'最终财务事件': 'income_reversal',
                    '关联记录ID': original['记录ID'], '语义说明': '工资更正，关联原收入行'})
        store.import_reviewed(self.db, self.approved([reversal]), apply=True)
        month = store.inspect(self.db)['aggregates']['2026-09']
        self.assertEqual(month['incomeCents'], 770000)
        self.assertEqual(month['incomeCount'], 2)

    def test_income_reversal_requires_negative_amount_and_original_income_link(self):
        store.import_reviewed(self.db, self.approved([self.income('original', '100.00')]), apply=True)
        rows = store.read_state(self.db)['rows']
        original = rows[-1]['记录ID']
        purchase = rows[0]['记录ID']
        cases = [('-20.00', {'最终财务事件': 'income_reversal'}),
                 ('-20.00', {'最终财务事件': 'income_reversal', '关联记录ID': purchase}),
                 ('20.00', {'最终财务事件': 'income_reversal', '关联记录ID': original}),
                 ('0.00', {'最终财务事件': 'income_reversal', '关联记录ID': original}),
                 ('-20.00', {'最终财务事件': 'income', '关联记录ID': original})]
        before = store.source_hash(self.db)
        for index, (amount, changes) in enumerate(cases):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, '收入'):
                store.import_reviewed(self.db, self.approved([
                    self.income('invalid-'+str(index), amount, **changes)]), apply=True)
            self.assertEqual(store.source_hash(self.db), before)
        reversal = self.income('valid-reversal', '-20.00', **{
            '最终财务事件': 'income_reversal', '关联记录ID': original})
        store.import_reviewed(self.db, self.approved([reversal]), apply=True)
        with self.assertRaisesRegex(ValueError, '收入'):
            store.import_reviewed(self.db, self.approved([self.income('chained', '-10.00', **{
                '最终财务事件': 'income_reversal', '关联记录ID': reversal['记录ID']})]), apply=True)

    def test_income_reversal_updates_cannot_remove_required_link(self):
        original = self.income('original-update', '100.00')
        reversal = self.income('reversal-update', '-20.00', **{
            '最终财务事件': 'income_reversal', '关联记录ID': original['记录ID']})
        store.import_reviewed(self.db, self.approved([original, reversal]), apply=True)
        before = store.source_hash(self.db)
        update = {'记录ID': reversal['记录ID'], 'changes': {'关联记录ID': ''},
                  'reason': '合成检查关联约束', 'confirmation_source': '合成测试'}
        with self.assertRaisesRegex(ValueError, '收入'):
            store.import_reviewed(self.db, self.approved(updates=[update]), apply=True)
        self.assertEqual(store.source_hash(self.db), before)
        self.assertTrue(store.verify(self.db)['ok'])
        self.assertTrue(store.verify(self.db)['ok'])

    def test_fresh_reapproval_after_reversal_gets_new_revision_and_batch(self):
        identity = store.read_state(self.db)['rows'][0]['记录ID']
        def approved_pool(pool):
            return self.approved(updates=[{'记录ID': identity, 'changes': {'资金池': pool},
                      'reason': '测试确认用途', 'confirmation_source': '用户逐次确认'}])
        decision_a = approved_pool('待归类')
        store.import_reviewed(self.db, decision_a, apply=True)
        store.import_reviewed(self.db, approved_pool(account.POOLS[0]), apply=True)
        decision_a['reviewed_at'] = '2026-10-19T11:00:00+08:00'
        result = store.import_reviewed(self.db, decision_a, apply=True)
        self.assertTrue(result['applied'])
        self.assertEqual(result['revision'], 4)
        self.assertEqual(store.read_state(self.db)['rows'][0]['资金池'], '待归类')
        batches = self.batches()
        self.assertEqual(len(batches), 4)
        self.assertEqual(len({b['batch_id'] for b in batches}), 4)
        again = store.import_reviewed(self.db, decision_a, apply=True)
        self.assertFalse(again['applied'])
        self.assertEqual(self.batches(), batches)


if __name__ == '__main__':
    unittest.main()
