"""Explicit budget editing uses only temporary, synthetic ledgers."""
from contextlib import closing
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import app_desktop
import app_export
import dashboard_data
import ledger_calculation
import ledger_store as store
import onboarding
from test_ledger_store import row
try:
    import budget_settings
except ImportError:
    budget_settings = None


class BudgetSettingsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        onboarding.initialize(self.root, '2026-09', dict.fromkeys(account.POOLS, 0), apply=True)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in ('account.py', 'app_export.py', 'dashboard_data.py', 'app_desktop.py'):
            (scripts / name).write_text('# synthetic tool marker', encoding='utf-8')
        self.db = self.root / 'data/account.sqlite3'
        self.amounts = dict(zip(account.POOLS, ['100.00', '50.00', '20.00', '30.00']))
        self.clock = patch.object(dashboard_data, 'current_month', return_value='2026-11')
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def payload(self, **changes):
        value = dict(effective='2026-09', amounts=self.amounts.copy(),
                     expectedSourceHash=store.source_hash(self.db), reason='本人确认首月预算')
        value.update(changes)
        return value

    def test_classified_observation_exports_then_budget_starts_without_prior_debt(self):
        purchase=row('observation-growth','12.00',**{'资金池':'成长/项目/耐用品','主分类':'学习教育'})
        store.import_reviewed(self.db,{'records':[purchase],'updates':[],
            'reviewed_by':'synthetic-agent','reviewed_at':'2026-09-03T10:00:00+08:00',
            'reason':'虚构的明确学习用途','confirmation_source':'合成测试授权'},apply=True)
        app_export.export_snapshot(self.db)
        snapshot=json.loads((self.root/'.runtime/ledger/snapshot-v2.json').read_text(encoding='utf-8'))
        month=next(m for m in snapshot['months'] if m['month']=='2026-09')
        self.assertFalse(month['mechanismActive'])
        self.assertEqual(month['summary']['personalCents'],1200)
        self.assertEqual(month['summary']['unknownCents'],0)
        self.assertEqual(month['pools'][3]['spentCents'],1200)
        self.assertEqual(month['summary']['overrunCents'],0)
        self.assertEqual(month['summary']['safetyCents'],0)
        saved=budget_settings.save(self.root,self.payload(effective='2026-10'))
        self.assertTrue(saved['snapshotUpdated'])
        snapshot=json.loads((self.root/'.runtime/ledger/snapshot-v2.json').read_text(encoding='utf-8'))
        october=next(m for m in snapshot['months'] if m['month']=='2026-10')
        self.assertTrue(october['mechanismActive'])
        self.assertEqual(october['pools'][3]['carryCents'],0)
        self.assertEqual(october['pools'][3]['openingCents'],3000)

    def set_budget(self, **changes):
        payload = self.payload(**changes)
        return store.set_budget(self.db, payload['effective'], payload['amounts'], payload['reason'],
                                expected_source_hash=payload['expectedSourceHash'],
                                reviewer='synthetic-reviewer', confirmation_source='合成用户明确确认', apply=True)

    def test_same_month_zero_placeholder_is_revised_with_backup_and_audit(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        before = store.read_state(self.db)
        result = self.set_budget()
        self.assertTrue(result['ok'])
        self.assertTrue(result['applied'])
        self.assertTrue(result['changed'])
        after = store.read_state(self.db)
        self.assertEqual(after['rows'], before['rows'])
        self.assertEqual(after['rates'][0], {'生效月份': '2026-09-01', **self.amounts})
        self.assertEqual(after['revision'], before['revision'] + 1)
        self.assertNotEqual(after['sourceHash'], before['sourceHash'])
        previous = self.db.with_name('account.previous.sqlite3')
        self.assertEqual(store.read_state(previous), before)
        with closing(sqlite3.connect(self.db)) as conn:
            audit = conn.execute("SELECT operation,before_json,after_json,reason,confirmation_source,reviewer FROM audit_log WHERE operation='budget-revise'").fetchone()
            self.assertEqual(audit[3:], ('本人确认首月预算', '合成用户明确确认', 'synthetic-reviewer'))
            self.assertEqual(json.loads(audit[1])[account.POOLS[0]], '0.00')
            self.assertEqual(json.loads(audit[2])[account.POOLS[0]], '100.00')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE budget_rates SET operation_cents=1')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM budget_rates')
            self.assertEqual(dict(conn.execute('SELECT key,value FROM metadata'))['schema_version'], '1')
        self.assertTrue(store.verify(self.db)['ok'])

    def test_insert_history_uses_existing_next_rate_and_noop_has_no_side_effect(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        store.append_budget(self.db, '2026-12', dict.fromkeys(account.POOLS, '9.00'), '合成后续额度', apply=True)
        result = self.set_budget(effective='2026-10')
        self.assertEqual(result['affectedThrough'], '2026-11')
        self.assertEqual(result['previousAmounts'], dict.fromkeys(account.POOLS, '0.00'))
        state = store.read_state(self.db)
        self.assertEqual(ledger_calculation.calculate_month(state, '2026-10', '2026-11')['pools'][0]['budgetCents'], 10000)
        self.assertEqual(ledger_calculation.calculate_month(state, '2026-12', '2026-12')['pools'][0]['budgetCents'], 900)
        with patch.object(store, '_prewrite_backup', side_effect=AssertionError('noop backup')):
            again = self.set_budget(effective='2026-10')
        self.assertFalse(again['changed'])
        self.assertFalse(again['applied'])
        self.assertEqual(store.read_state(self.db), state)
        with self.assertRaises(ValueError):
            store.append_budget(self.db, '2026-10', self.amounts, 'legacy append remains strict', apply=True)

    def test_stale_hash_rejected_before_backup_even_for_noop(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        before = store.read_state(self.db)
        with patch.object(store, '_prewrite_backup', side_effect=AssertionError('stale backup')):
            with self.assertRaisesRegex(ValueError, '变化|过期|刷新'):
                self.set_budget(expectedSourceHash='0' * 64)
        self.assertEqual(store.read_state(self.db), before)
        self.assertFalse(self.db.with_name('account.previous.sqlite3').exists())

    def test_failed_audit_rolls_back_budget_and_preserves_protection(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        before = store.read_state(self.db)
        with patch.object(store, '_audit', side_effect=RuntimeError('synthetic write failure')):
            with self.assertRaises(RuntimeError):
                self.set_budget()
        self.assertEqual(store.read_state(self.db), before)
        with closing(sqlite3.connect(self.db)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE budget_rates SET operation_cents=1')

    def test_failure_while_guard_suspended_rolls_back_the_trigger_itself(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("CREATE TRIGGER synthetic_budget_failure BEFORE UPDATE ON budget_rates BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        before = store.read_state(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            self.set_budget()
        self.assertEqual(store.read_state(self.db), before)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('DROP TRIGGER synthetic_budget_failure')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE budget_rates SET operation_cents=1')

    def test_budget_changes_preserve_unknown_pool_and_every_transaction_field(self):
        approved = dict(records=[row('synthetic-unknown', **{'资金池': '待归类'})],
                        reviewed_by='synthetic', reviewed_at='2026-11-01T12:00:00+08:00')
        store.import_reviewed(self.db, approved, apply=True)
        before = store.read_state(self.db)
        self.set_budget()
        after = store.read_state(self.db)
        self.assertEqual(after['rows'], before['rows'])
        totals = ledger_calculation.calculate_month(after, '2026-09', '2026-11')
        self.assertEqual(totals['summary']['personalCents'], 1000)
        self.assertEqual(totals['summary']['unknownCents'], 1000)
        self.assertTrue(all(p['spentCents'] == 0 for p in totals['pools']))

    def test_setter_obeys_ledger_writer_lock_and_requires_all_review_evidence(self):
        before = store.read_state(self.db)
        with store._writer_lock(self.db):
            with self.assertRaises(RuntimeError):
                self.set_budget()
        for evidence in [dict(reviewer=''), dict(confirmation_source=' '), dict(reason='')]:
            args = dict(reason='本人确认', reviewer='synthetic', confirmation_source='本人明确授权')
            args.update(evidence)
            with self.subTest(evidence=evidence):
                with self.assertRaises(ValueError):
                    store.set_budget(self.db, '2026-09', self.amounts,
                                     expected_source_hash=before['sourceHash'], apply=True, **args)
        self.assertEqual(store.read_state(self.db), before)

    def test_amount_validation_rejects_rounding_nonfinite_and_unsafe_sum(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        before = store.read_state(self.db)
        for bad in ['1.001', 'NaN', 'Infinity', '-0.01', '1e2', '', ' 1.00', '1', True, 1.0, None]:
            with self.subTest(bad=bad):
                amounts = {**self.amounts, account.POOLS[0]: bad}
                with self.assertRaises(ValueError):
                    self.set_budget(amounts=amounts)
        with self.assertRaises(ValueError):
            self.set_budget(amounts=dict.fromkeys(account.POOLS, '90071992547409.91'))
        self.assertEqual(store.read_state(self.db), before)

    def test_carry_aggregate_is_checked_before_write(self):
        self.assertTrue(hasattr(store, 'set_budget'), 'explicit reviewed budget setter is required')
        # A individually safe monthly growth rate would overflow after carry.
        values = dict.fromkeys(account.POOLS, '0.00')
        values[account.POOLS[3]] = '45035996273705.00'
        before = store.read_state(self.db)
        with self.assertRaisesRegex(ValueError, '安全|范围'):
            self.set_budget(amounts=values)
        self.assertEqual(store.read_state(self.db), before)

    def test_status_and_preview_are_read_only_and_suggest_imported_month(self):
        self.assertIsNotNone(budget_settings, 'budget settings bridge is required')
        reviewed = dict(records=[row('synthetic-first', **{'交易时间': '2026-08-05 10:00:00'})],
                        reviewed_by='synthetic', reviewed_at='2026-11-01T12:00:00+08:00')
        store.import_reviewed(self.db, reviewed, apply=True)
        before = store.read_state(self.db)
        files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        status = budget_settings.status(self.root)
        self.assertEqual(status['suggestedMonth'], '2026-08')
        self.assertEqual(status['rates'], [dict(effective='2026-09', amounts=dict.fromkeys(account.POOLS, '0.00'))])
        result = budget_settings.preview(self.root, self.payload(effective='2026-08'))
        self.assertFalse(result['applied'])
        self.assertTrue(result['changed'])
        self.assertIsNone(result['previousAmounts'])
        self.assertEqual(result['affectedThrough'], '2026-08')
        self.assertEqual(store.read_state(self.db), before)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, files)

    def test_preview_can_never_be_promoted_to_a_write(self):
        before = store.read_state(self.db)
        with self.assertRaises(ValueError):
            budget_settings.preview(self.root, self.payload(), apply=True)
        self.assertEqual(store.read_state(self.db), before)
        self.assertFalse(self.db.with_name('account.previous.sqlite3').exists())

    def test_save_exports_local_snapshot_and_never_publishes(self):
        self.assertIsNotNone(budget_settings, 'budget settings bridge is required')
        with patch.object(app_desktop, '_publish', side_effect=AssertionError('publishing forbidden')):
            saved = budget_settings.save(self.root, self.payload())
        self.assertTrue(saved['applied'])
        self.assertTrue(saved['snapshotUpdated'])
        snapshot = app_desktop.load_snapshot(self.root)['snapshot']
        self.assertEqual(snapshot['sourceHash'], saved['sourceHash'])
        self.assertEqual(snapshot['schemaVersion'], 2)

    def test_export_failure_is_reported_separately_from_committed_ledger(self):
        self.assertIsNotNone(budget_settings, 'budget settings bridge is required')
        with patch.object(app_export, 'export_snapshot', side_effect=RuntimeError('private diagnostic')):
            saved = budget_settings.save(self.root, self.payload())
        self.assertTrue(saved['ok'])
        self.assertTrue(saved['applied'])
        self.assertFalse(saved['snapshotUpdated'])
        self.assertIn('warning', saved)
        self.assertNotIn('private diagnostic', saved['warning'])
        self.assertEqual(store.source_hash(self.db), saved['sourceHash'])

    def test_save_obeys_desktop_bridge_lock_and_preserves_inputs(self):
        self.assertIsNotNone(budget_settings, 'budget settings bridge is required')
        before = store.read_state(self.db)
        with dashboard_data.sync_lock(self.root / '.runtime/desktop-bridge'):
            with self.assertRaises(RuntimeError):
                budget_settings.save(self.root, self.payload())
        self.assertEqual(store.read_state(self.db), before)

    def test_payload_keys_and_evidence_are_exact(self):
        self.assertIsNotNone(budget_settings, 'budget settings bridge is required')
        invalid = [self.payload(extra='no'), self.payload(reason=' '), self.payload(expectedSourceHash=None),
                   self.payload(effective='2026-09-01'), self.payload(amounts={**self.amounts, 'unknown': '0.00'})]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    budget_settings.preview(self.root, payload)

    def test_desktop_commands_use_bounded_stdin_and_budget_status(self):
        script = Path(app_desktop.__file__)
        status = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'budget-status', '--project-root', str(self.root)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)['suggestedMonth'], '2026-09')
        preview = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'budget-preview', '--project-root', str(self.root)],
                                 input=json.dumps(self.payload()), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(json.loads(preview.stdout)['applied'])
        oversized = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'budget-save', '--project-root', str(self.root)],
                                   input=' ' * 16385, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(oversized.returncode, 0)
        self.assertFalse(self.db.with_name('account.previous.sqlite3').exists())

    def test_duplicate_json_keys_rejected_and_exact_size_limit_is_bounded(self):
        with self.assertRaisesRegex(ValueError, '重复'):
            budget_settings.read_payload(io.BytesIO(b'{"effective":"2026-09","effective":"2026-10"}'))
        exact = b'{}' + b' ' * (16384 - 2)
        self.assertEqual(budget_settings.read_payload(io.BytesIO(exact)), {})
        with self.assertRaises(ValueError):
            budget_settings.read_payload(io.BytesIO(exact + b' '))

    def test_stale_preview_after_another_writer_does_not_replace_backup(self):
        original = self.payload()
        budget_settings.preview(self.root, original)
        self.set_budget(amounts=dict.fromkeys(account.POOLS, '1.00'))
        before = store.read_state(self.db)
        previous = self.db.with_name('account.previous.sqlite3').read_bytes()
        with self.assertRaisesRegex(ValueError, '变化|过期|刷新'):
            budget_settings.save(self.root, original)
        self.assertEqual(store.read_state(self.db), before)
        self.assertEqual(self.db.with_name('account.previous.sqlite3').read_bytes(), previous)

    def test_desktop_budget_commands_can_apply_and_refuse_redirected_roots(self):
        script = Path(app_desktop.__file__)
        saved = subprocess.run([sys.executable, '-X', 'utf8', str(script), 'budget-save', '--project-root', str(self.root)],
                               input=json.dumps(self.payload()), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertTrue(json.loads(saved.stdout)['applied'])
        (self.root / '.git').write_text('gitdir: synthetic', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '工作树'):
            budget_settings.status(self.root)

    def test_cli_budget_set_previews_and_applies_without_publication(self):
        amounts = self.root / 'amounts.json'
        amounts.write_text(json.dumps(self.amounts), encoding='utf-8')
        argv = ['--root', str(self.root), 'budget-set', '--effective', '2026-09', '--amounts', str(amounts),
                '--expected-hash', store.source_hash(self.db), '--reason', '本人明确确认预算']
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(account.main(argv), 0)
        self.assertFalse(json.loads(output.getvalue())['applied'])
        with patch.object(app_desktop, '_publish', side_effect=AssertionError('publishing forbidden')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(account.main([*argv, '--apply']), 0)
        saved = json.loads(output.getvalue())
        self.assertTrue(saved['applied'])
        self.assertTrue(saved['snapshotUpdated'])


if __name__ == '__main__':
    unittest.main()
