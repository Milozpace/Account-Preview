"""App V2 snapshot contract tests with synthetic fixtures (no real ledger).

The canonical shared fixture (tests/fixtures/app-v2-snapshot.json) is the
same file the Rust and TypeScript suites load; targeted corruptions of it
prove the validator rejects each reviewed defect class.
"""
import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account  # noqa: E402
import dashboard_data as data  # noqa: E402
import app_export  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'app-v2-snapshot.json'


def load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))


def record(identity='tx_a', month='2026-10', day=1, amount=1000, pool='operation',
           scope='PERSONAL', category='餐饮与日常食品', title='合成商户 · 午餐'):
    return {'id': identity, 'time': f'{month}-{day:02d}T12:00:00+08:00',
            'date': f'{month}-{day:02d}', 'title': title, 'category': category,
            'pool': pool, 'scope': scope, 'amountCents': amount}


def mutate(base=None, change=None):
    value = copy.deepcopy(base if base is not None else load_fixture())
    if change:
        change(value)
    return value


class SharedFixtureContractTests(unittest.TestCase):
    """The three language suites all load this exact file."""

    def test_shared_fixture_passes_validation(self):
        result = app_export.validate_app_snapshot(load_fixture())
        self.assertEqual(result['schemaVersion'], 2)
        self.assertEqual(result['coverage']['months'], ['2026-10', '2026-09'])

    def test_wrong_total_rejected(self):
        broken = mutate(change=lambda v: v['months'][0]['summary'].update(totalCents=999))
        with self.assertRaisesRegex(ValueError, '总净额'):
            app_export.validate_app_snapshot(broken)

    def test_unknown_cents_mismatch_rejected(self):
        broken = mutate(change=lambda v: v['months'][0]['summary'].update(unknownCents=1))
        with self.assertRaisesRegex(ValueError, '待归类'):
            app_export.validate_app_snapshot(broken)

    def test_bad_pool_entry_rejected(self):
        def change(v):
            v['months'][0]['pools'][0]['id'] = 'safety'
        with self.assertRaisesRegex(ValueError, '资金池'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_pool_spent_mismatch_rejected(self):
        def change(v):
            v['months'][0]['pools'][0]['spentCents'] = 1
        with self.assertRaisesRegex(ValueError, '对账'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_missing_pool_rejected(self):
        def change(v):
            del v['months'][0]['pools'][3]
        with self.assertRaisesRegex(ValueError, '四池'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_budget_summary_mismatch_rejected(self):
        def change(v):
            v['months'][0]['summary']['monthlyBudgetCents'] = 1
        with self.assertRaisesRegex(ValueError, '月预算'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_unsafe_integer_rejected(self):
        for field in ('amountCents', 'operationCumulativeCents', 'referenceCents'):
            def change(v, field=field):
                v['months'][0]['daily'][0][field] = 2 ** 60
            with self.assertRaisesRegex(ValueError, '安全整数'):
                app_export.validate_app_snapshot(mutate(change=change))
        with self.assertRaisesRegex(ValueError, '安全整数'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: v['months'][0]['records'][0].update(amountCents=2 ** 60)))

    def test_phantom_day_amount_rejected(self):
        def change(v):
            empty = v['months'][0]['daily'][2]
            empty['amountCents'] = 777
        with self.assertRaisesRegex(ValueError, '对账'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_missing_or_duplicate_daily_rejected(self):
        with self.assertRaisesRegex(ValueError, '完整月历'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: v['months'][0]['daily'].pop()))
        with self.assertRaisesRegex(ValueError, '重复'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: v['months'][0]['daily'].__setitem__(
                    1, dict(v['months'][0]['daily'][0]))))

    def test_null_daily_reference_rejected(self):
        def change(v):
            v['months'][0]['daily'][0]['referenceCents'] = None
        with self.assertRaisesRegex(ValueError, '安全整数分'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_time_date_mismatch_rejected(self):
        def change(v):
            v['months'][0]['records'][0]['time'] = '2026-10-02T12:00:00+08:00'
        with self.assertRaisesRegex(ValueError, '时间与日期不一致'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_months_window_must_be_calendar_and_previous(self):
        def reversed_months(v):
            v['months'].reverse()
            v['coverage']['months'] = [m['month'] for m in v['months']]
        with self.assertRaisesRegex(ValueError, '当月与上月'):
            app_export.validate_app_snapshot(mutate(change=reversed_months))
        with self.assertRaisesRegex(ValueError, '当月与上月'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: (v['months'].pop(),
                                  v['coverage'].update(months=[v['months'][0]['month']]))))
        with self.assertRaisesRegex(ValueError, '当月与上月'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: (v['months'].__setitem__(1, copy.deepcopy(v['months'][0])),
                                  v['coverage'].update(months=['2026-10', '2026-10']))))

    def test_category_mismatch_rejected(self):
        def change(v):
            v['months'][0]['categories'][0]['amountCents'] = 1
        with self.assertRaisesRegex(ValueError, '分类'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_category_total_mismatch_rejected(self):
        def change(v):
            v['months'][0]['categories'].append(
                {'name': '幽灵分类', 'amountCents': 100, 'count': 0})
        with self.assertRaisesRegex(ValueError, '分类'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_top_ids_must_reference_records(self):
        def change(v):
            v['months'][0]['topIds'] = ['tx_not_present']
        with self.assertRaisesRegex(ValueError, '重点记录'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_top_level_and_month_whitelists_rejected(self):
        with self.assertRaisesRegex(ValueError, '白名单'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: v.update(secretField='leak')))
        with self.assertRaisesRegex(ValueError, '白名单'):
            app_export.validate_app_snapshot(mutate(
                change=lambda v: v['months'][0].update(extra='x')))

    def test_null_pool_cents_rejected(self):
        def change(v):
            v['months'][0]['pools'][0]['budgetCents'] = None
        with self.assertRaisesRegex(ValueError, '安全整数分'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_nonfinite_usage_ratio_rejected(self):
        with self.assertRaisesRegex(ValueError, '使用率'):
            app_export.validate_app_snapshot(mutate(change=lambda v: (
                v['months'][0]['pools'][0].update(openingCents=100),
                v['months'][0]['pools'][0].update(usageRatio=float('inf')))))


class ValidatorEdgeTests(unittest.TestCase):
    """Compact synthetic cases complementing the shared fixture."""

    def test_v1_snapshot_is_rejected(self):
        value = load_fixture()
        value['schemaVersion'] = 1
        with self.assertRaisesRegex(ValueError, 'V2'):
            app_export.validate_app_snapshot(value)

    def test_duplicate_record_id_across_months_rejected(self):
        def change(v):
            v['months'][1]['records'][0]['id'] = v['months'][0]['records'][0]['id']
        with self.assertRaisesRegex(ValueError, '重复'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_cross_month_date_rejected(self):
        def change(v):
            v['months'][0]['records'][0]['date'] = '2026-09-15'
            v['months'][0]['records'][0]['time'] = '2026-09-15T12:00:00+08:00'
        with self.assertRaisesRegex(ValueError, '越界'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_summary_mismatch_with_records_rejected(self):
        def change(v):
            v['months'][0]['summary']['personalCents'] = 999
        with self.assertRaisesRegex(ValueError, '对账'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_ledger_count_below_exported_records_rejected(self):
        def change(v):
            v['ledgerCount'] = 3
        with self.assertRaisesRegex(ValueError, 'ledgerCount'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_record_field_whitelist_enforced(self):
        def change(v):
            v['months'][0]['records'][0]['交易订单号'] = 'AB123456789012'
        with self.assertRaisesRegex(ValueError, '白名单'):
            app_export.validate_app_snapshot(mutate(change=change))

    def test_content_signature_ignores_generated_at_only(self):
        first = load_fixture()
        second = mutate(change=lambda v: v.update(generatedAt='2026-10-15T13:00:00+08:00'))
        self.assertEqual(app_export.content_signature(first), app_export.content_signature(second))
        second['months'][0]['records'][0]['amountCents'] += 1
        self.assertNotEqual(app_export.content_signature(first),
                            app_export.content_signature(second))


class AppExportBuildTests(unittest.TestCase):
    def test_invalid_last_day_cannot_replace_a_real_calendar_day(self):
        value = load_fixture()
        value['months'][0]['daily'][-1]['date'] = '2026-10-99'
        with self.assertRaisesRegex(ValueError, '日期|月历'):
            app_export.validate_app_snapshot(value)

    def test_changes_during_draft_write_preserve_previous_snapshot(self):
        real_write_text = Path.write_text
        for change in ['source', 'month']:
            with self.subTest(change=change):
                self.workbook.write_bytes(b'synthetic-before-draft')
                value = load_fixture()
                value['sourceHash'] = account.sha(self.workbook)
                output = Path(self.directory.name) / 'ledger' / 'snapshot-v2.json'
                output.parent.mkdir(exist_ok=True)
                output.write_text('previous-snapshot', encoding='utf-8')
                clock = {'month': value['calendarMonth']}

                def write_then_change(path, *args, **kwargs):
                    result = real_write_text(path, *args, **kwargs)
                    if path.suffix == '.draft':
                        if change == 'source':
                            self.workbook.write_bytes(b'synthetic-saved-during-draft')
                        else:
                            clock['month'] = '2026-11'
                    return result

                with patch.object(data, 'read_snapshot', return_value=value), \
                     patch.object(data, 'snapshot_current', return_value=True), \
                     patch.object(data, 'current_month', side_effect=lambda: clock['month']), \
                     patch.object(Path, 'write_text', new=write_then_change):
                    with self.assertRaisesRegex(RuntimeError, '已变化|跨月'):
                        app_export.export_snapshot(self.workbook, output=output)
                self.assertEqual(output.read_text(encoding='utf-8'), 'previous-snapshot')
                self.assertFalse(list(output.parent.glob('*.draft')))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.workbook = Path(self.directory.name) / '个人财物工作簿.xlsx'
        self.workbook.write_bytes(b'synthetic-workbook')

    def tearDown(self):
        self.directory.cleanup()

    def source_hash(self):
        return load_fixture()['sourceHash']

    def test_export_reuses_current_web_snapshot_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as runtime:
            web_path = Path(runtime) / 'dashboard.json'
            web_path.write_text(json.dumps(load_fixture(), ensure_ascii=False), encoding='utf-8')
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
                 patch.object(data, 'snapshot_current', return_value=True), \
                 patch.object(account, 'sha', return_value=self.source_hash()):
                summary = app_export.export_snapshot(self.workbook, output=output)
                self.assertTrue(summary['webSnapshotReused'])
                self.assertFalse(summary['dryRun'])
                stored = json.loads(output.read_text(encoding='utf-8'))
                app_export.validate_app_snapshot(stored)
                self.assertFalse(list(output.parent.glob('*.draft')))

    def test_cold_export_reads_fresh_v1_from_disk_not_sync_summary(self):
        """Regression for the reviewed cold-cache KeyError: sync_snapshot
        returns a status summary; the exporter must re-read the on-disk V1."""
        status_summary = {'ok': True, 'generatedAt': '2026-10-15T08:30:00+08:00',
                          'months': ['2026-10', '2026-09'], 'ledgerCount': 2681,
                          'sourceHash': self.source_hash(), 'nativeRecalculated': True,
                          'originalUnchanged': True}
        reads = [None, load_fixture()]
        currents = [False, True]
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            with patch.object(data, 'read_snapshot', side_effect=lambda wb: reads.pop(0)), \
                 patch.object(data, 'snapshot_current',
                              side_effect=lambda wb, snap, month=None: currents.pop(0)), \
                 patch.object(data, 'sync_snapshot', return_value=status_summary) as sync, \
                 patch.object(account, 'sha', return_value=self.source_hash()):
                summary = app_export.export_snapshot(self.workbook, output=output)
                self.assertEqual(sync.call_count, 1)
                self.assertFalse(summary['webSnapshotReused'])
                self.assertTrue(output.exists())

    def test_sync_failure_never_writes_output(self):
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            with patch.object(data, 'read_snapshot', return_value=None), \
                 patch.object(data, 'snapshot_current', return_value=False), \
                 patch.object(data, 'sync_snapshot', side_effect=RuntimeError('同步失败')):
                with self.assertRaises(RuntimeError):
                    app_export.export_snapshot(self.workbook, output=output)
            self.assertFalse(output.exists())

    def test_source_change_between_build_and_write_preserves_previous(self):
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            output.parent.mkdir(parents=True)
            output.write_text('previous-snapshot', encoding='utf-8')
            with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
                 patch.object(data, 'snapshot_current', return_value=True), \
                 patch.object(account, 'sha', side_effect=['cd' * 32]):
                with self.assertRaisesRegex(RuntimeError, '已变化'):
                    app_export.export_snapshot(self.workbook, output=output)
            self.assertEqual(output.read_text(encoding='utf-8'), 'previous-snapshot')

    def test_month_rollover_between_build_and_write_preserves_previous(self):
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            output.parent.mkdir(parents=True)
            output.write_text('previous-snapshot', encoding='utf-8')
            with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
                 patch.object(data, 'snapshot_current', return_value=True), \
                 patch.object(account, 'sha', return_value=self.source_hash()), \
                 patch.object(data, 'current_month', return_value='2026-11'):
                with self.assertRaisesRegex(RuntimeError, '跨月'):
                    app_export.export_snapshot(self.workbook, output=output)
            self.assertEqual(output.read_text(encoding='utf-8'), 'previous-snapshot')

    def test_output_must_never_overwrite_source_workbook(self):
        with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
             patch.object(data, 'snapshot_current', return_value=True), \
             patch.object(account, 'sha', return_value=self.source_hash()):
            with self.assertRaisesRegex(ValueError, '源工作簿'):
                app_export.export_snapshot(self.workbook, output=self.workbook)
            # A case-variant path resolves to the same file on Windows.
            alias = self.workbook.with_name('个人财物工作簿.XLSX')
            with self.assertRaisesRegex(ValueError, '源工作簿'):
                app_export.export_snapshot(self.workbook, output=alias)
        # The workbook bytes are untouched.
        self.assertEqual(self.workbook.read_bytes(), b'synthetic-workbook')

    def test_failed_write_cleans_up_its_own_draft(self):
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
                 patch.object(data, 'snapshot_current', return_value=True), \
                 patch.object(account, 'sha', return_value=self.source_hash()), \
                 patch.object(app_export.os, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    app_export.export_snapshot(self.workbook, output=output)
            self.assertFalse(list(Path(runtime).glob('**/*.draft')))

    def test_concurrent_exports_never_share_one_draft(self):
        replaced = []
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as runtime:
            output = Path(runtime) / 'ledger' / 'snapshot-v2.json'
            with patch.object(data, 'read_snapshot', return_value=load_fixture()), \
                 patch.object(data, 'snapshot_current', return_value=True), \
                 patch.object(account, 'sha', return_value=self.source_hash()), \
                 patch.object(app_export.os, 'replace',
                              side_effect=lambda a, b: replaced.append(Path(a).name)
                              or real_replace(a, b)):
                app_export.export_snapshot(self.workbook, output=output)
                app_export.export_snapshot(self.workbook, output=output)
            drafts = [name for name in replaced if '.draft' in name]
            self.assertEqual(len(drafts), 2)
            self.assertEqual(len(set(drafts)), 2)

    def test_publish_gate_defaults_to_disabled(self):
        gate = app_export.publish_gate(Path(self.directory.name))
        self.assertFalse(gate['enabled'])

    def test_publish_gate_requires_enabled_and_defaults_to_portable_publisher(self):
        ledger = Path(self.directory.name) / 'ledger'
        ledger.mkdir(parents=True)
        (ledger / 'publish.json').write_text('{"enabled": true}', encoding='utf-8')
        self.assertTrue(app_export.publish_gate(Path(self.directory.name))['enabled'])
        (ledger / 'publish.json').write_text(
            json.dumps({'enabled': True, 'publisher': 'C:/tools/ledger-publish.exe'}), encoding='utf-8')
        gate = app_export.publish_gate(Path(self.directory.name))
        self.assertTrue(gate['enabled'])
        self.assertEqual(gate['publisher'], 'C:/tools/ledger-publish.exe')

    def test_post_processing_failure_never_raises_and_reports_separately(self):
        result = {}
        with patch.object(app_export, 'export_snapshot', side_effect=ValueError('对账失败')):
            app_export.prepare_after_dashboard(self.workbook, result)
        self.assertFalse(result['appSnapshot']['ok'])
        self.assertIn('对账失败', result['appSnapshot']['error'])

    def test_post_processing_skips_publish_when_disabled(self):
        result = {}
        summary = {'ok': True, 'output': 'x', 'months': ['2026-10']}
        with patch.object(app_export, 'export_snapshot', return_value=summary), \
             patch.object(app_export, 'publish_gate', return_value={'enabled': False, 'reason': '未启用'}):
            app_export.prepare_after_dashboard(self.workbook, result)
        self.assertTrue(result['appSnapshot']['ok'])
        self.assertIsNone(result['appPublish']['ok'])
        self.assertTrue(result['appPublish']['skipped'])
        self.assertFalse(result['appPublish']['published'])

    def test_post_processing_invokes_real_publish_entry(self):
        """Regression: the post-processing must call the native publish entry
        (`--publish`), and only report ok on a confirmed publish (exit 0)."""
        import subprocess as sp
        result = {}
        summary = {'ok': True, 'output': 'x', 'months': ['2026-10']}
        ok_run = sp.CompletedProcess([], 0, stdout='发布成功：版本 1', stderr='')
        with patch.object(app_export, 'export_snapshot', return_value=summary), \
             patch.object(app_export, 'publish_gate',
                          return_value={'enabled': True, 'publisher': 'pub.exe', 'snapshot': 's.json'}), \
             patch.object(app_export.subprocess, 'run', return_value=ok_run) as run:
            app_export.prepare_after_dashboard(self.workbook, result)
        self.assertEqual(run.call_args.args[0][:2], ['pub.exe', '--publish'])
        self.assertTrue(result['appPublish']['ok'])
        self.assertTrue(result['appPublish']['published'])

    def test_publisher_diagnostics_never_leak_into_public_result(self):
        import subprocess as sp
        for code in (0,2):
            result = {}
            private = 'PRIVATE_ORDER_ACCOUNT_MARKER'
            with patch.object(app_export,'export_snapshot',return_value={'ok':True}), \
                 patch.object(app_export,'publish_gate',return_value={
                    'enabled':True,'publisher':'pub.exe','snapshot':'s.json'}), \
                 patch.object(app_export.subprocess,'run',return_value=sp.CompletedProcess(
                    [],code,stdout=private,stderr=private)):
                app_export.prepare_after_dashboard(self.workbook,result)
            self.assertNotIn(private,json.dumps(result))
            self.assertEqual(result['appPublish']['published'],code==0)

    def test_post_processing_publish_failure_is_reported_not_fatal(self):
        import subprocess as sp
        result = {}
        summary = {'ok': True, 'output': 'x', 'months': ['2026-10']}
        failed = sp.CompletedProcess([], 2, stdout='', stderr='认证失败')
        with patch.object(app_export, 'export_snapshot', return_value=summary), \
             patch.object(app_export, 'publish_gate',
                          return_value={'enabled': True, 'publisher': 'pub.exe', 'snapshot': 's.json'}), \
             patch.object(app_export.subprocess, 'run', return_value=failed):
            app_export.prepare_after_dashboard(self.workbook, result)
        self.assertTrue(result['appSnapshot']['ok'])
        self.assertFalse(result['appPublish']['ok'])
        self.assertFalse(result['appPublish']['published'])
        self.assertIn('发布未确认', result['appPublish']['error'])


if __name__ == '__main__':
    unittest.main()
