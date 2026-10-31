"""Phone inbox safety tests; only synthetic temporary sources and ledgers."""
from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
import zipfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import ledger_store
try:
    import bill_inbox
except ImportError:
    bill_inbox = None
from test_ledger_store import excel_fixture


CSV = '交易时间,交易订单号,金额,交易对方\n2026-10-18 12:00:00,synthetic,1.23,合成商户\n'.encode('utf-8-sig')
UPLOAD_ID = hashlib.sha256(CSV).hexdigest()


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bill_inbox, 'bill inbox implementation required')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inbox = bill_inbox.Inbox(self.root)
        self.calls = []

    def remote(self, content=CSV, name='账单.csv', identity=None, digest=None, outside=False):
        def run(*args):
            self.calls.append(args)
            if args[0] == 'receive':
                target = self.root / 'outside.csv' if outside else Path(args[1]) / 'cipher-download.csv'
                target.write_bytes(content)
                sha256 = digest or account.sha(target)
                return {'files': [dict(id=identity or sha256, fileName=name, path=str(target), byteCount=len(content), sha256=sha256)]}
            return {'ok': True}
        return run

    def fetch(self, **kwargs):
        with patch.object(self.inbox, '_run', side_effect=self.remote(**kwargs)):
            return self.inbox.fetch(apply=True)

    def test_read_only_default_has_no_filesystem_or_cloud_changes(self):
        with patch.object(self.inbox, '_run', side_effect=AssertionError('no network')):
            self.assertTrue(self.inbox.fetch()['dry_run'])
        self.assertFalse((self.root / '.runtime').exists())

    def test_archive_verified_original_and_only_received_receipt(self):
        result = self.fetch()
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'sra' / '账单.csv').read_bytes(), CSV)
        self.assertEqual(self.calls[-1], ('acknowledge', UPLOAD_ID, 'received'))
        entry = self.inbox.status()['files'][0]
        self.assertEqual(entry['state'], 'received')
        self.assertFalse(entry['receiptPending'])

    def test_native_hash_identity_different_names_reuses_archive_and_manifest_entry(self):
        self.fetch()
        self.fetch(name='改名.csv')
        self.assertEqual(len(list((self.root / 'sra').iterdir())), 1)
        entries = self.inbox.status()['files']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['id'], UPLOAD_ID)

    def test_collision_never_overwrites_and_crash_recovery_reuses_hash(self):
        (self.root / 'sra').mkdir()
        existing = self.root / 'sra' / '账单.csv'
        existing.write_bytes(b'original')
        with patch.object(self.inbox, '_save', side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                self.fetch()
        self.assertEqual(existing.read_bytes(), b'original')
        self.assertTrue(self.fetch()['ok'])
        self.assertEqual(len(list((self.root / 'sra').iterdir())), 2)

    def test_unsafe_names_paths_hash_format_empty_and_oversize_are_rejected(self):
        cases = [dict(name='../escape.csv'), dict(name='C:\\escape.csv'), dict(name='CON.csv'),
                 dict(name=' bill.csv'), dict(name='bill..csv'), dict(name='bill\x7f.csv'),
                 dict(identity='upload-a'), dict(identity='a' * 64), dict(name='测' * 67 + '.csv'),
                 dict(outside=True), dict(digest='0' * 64), dict(name='bill.exe'),
                 dict(content=b'not a bill'), dict(content='交易时间,交易订单号,金额\n'.encode()),
                 dict(content=b'x' * (4 * 1024 * 1024 + 1))]
        for case in cases:
            with self.subTest(case=list(case)):
                result = self.fetch(**case)
                self.assertFalse(result['ok'])
        self.assertFalse((self.root / 'sra').exists())
        self.assertFalse(any(c[0] == 'acknowledge' for c in self.calls))

    def database(self):
        workbook = self.root / 'fixture.xlsx'
        excel_fixture(workbook)
        database = self.root / 'data' / 'account.sqlite3'
        ledger_store.migrate_excel(workbook, database)
        return database

    def import_bill(self, database, include_manifest=True):
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        rows, _ = account.parse_sources([source])
        for row in rows:
            row.update({'最终财务事件': 'purchase', '统计口径': 'PERSONAL', '计入消费金额': '1.23',
                        '主分类': '合成分类', '资金池': account.POOLS[0], '语义说明': '测试审定'})
        approved = dict(records=rows, updates=[], reviewed_by='synthetic-agent', reviewed_at='2026-10-29T12:00:00+08:00')
        if include_manifest:
            approved['source_files'] = [{'path': str(source), 'sha256': account.sha(source)}]
        return ledger_store.import_reviewed(database, approved, apply=True)

    def test_received_failure_retries_but_fetch_never_records(self):
        def failing(*args):
            if args[0] == 'receive':
                return self.remote()(*args)
            raise RuntimeError('secret must be sanitized')
        with patch.object(self.inbox, '_run', side_effect=failing):
            self.assertFalse(self.inbox.fetch(apply=True)['ok'])
        self.assertTrue(self.inbox.status()['files'][0]['receiptPending'])
        with patch.object(self.inbox, '_run', return_value={'ok': True}) as run:
            self.assertTrue(self.inbox.receipts(self.database(), apply=True)['ok'])
        self.assertEqual(run.call_args.args, ('acknowledge', UPLOAD_ID, 'received'))

    def test_recorded_requires_verified_actual_committed_batch_and_current_facts(self):
        self.fetch()
        database = self.database()
        with patch.object(self.inbox, '_run', side_effect=AssertionError('not yet recorded')):
            self.assertTrue(self.inbox.receipts(database, apply=True)['ok'])
        self.import_bill(database)
        with patch.object(self.inbox, '_run', side_effect=RuntimeError('credential text')):
            result = self.inbox.receipts(database, apply=True)
        self.assertFalse(result['ok'])
        self.assertNotIn('credential', json.dumps(result))
        self.assertEqual(self.inbox.status()['files'][0]['state'], 'recorded')
        with patch.object(self.inbox, '_run', return_value={'ok': True}) as run:
            self.assertTrue(self.inbox.receipts(database, apply=True)['ok'])
        self.assertEqual(run.call_args.args, ('acknowledge', UPLOAD_ID, 'recorded'))

    def test_user_declared_hash_and_local_state_alone_cannot_mark_recorded(self):
        self.fetch()
        database = self.database()
        self.import_bill(database, include_manifest=False)
        with patch.object(self.inbox, '_run', side_effect=AssertionError('no committed provenance')):
            self.inbox.receipts(database, apply=True)
        self.assertEqual(self.inbox.status()['files'][0]['state'], 'received')
        with patch.object(ledger_store, 'verify', return_value={'ok': False}), patch.object(self.inbox, '_run', side_effect=AssertionError('invalid ledger')):
            self.assertFalse(self.inbox.receipts(database, apply=True)['ok'])

    def test_cli_status_and_dry_fetch_are_read_only(self):
        database = self.database()
        for command in ('inbox-status', 'inbox-fetch', 'inbox-receipts'):
            output = io.StringIO()
            with redirect_stdout(output), patch.object(account, 'ROOT', self.root), patch.object(bill_inbox.Inbox, '_run', side_effect=AssertionError('read only')):
                self.assertEqual(account.main(['--database', str(database), command]), 0)
            self.assertTrue(json.loads(output.getvalue())['ok'])
        self.assertFalse(self.inbox.runtime.exists())

    def test_import_sends_recorded_after_commit_even_when_snapshot_raises(self):
        self.fetch()
        database = self.database()
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        rows, _ = account.parse_sources([source])
        rows[0].update({'最终财务事件': 'purchase', '统计口径': 'PERSONAL', '计入消费金额': '1.23',
                        '主分类': '合成分类', '资金池': account.POOLS[0], '语义说明': '测试审定'})
        approved = dict(records=rows, updates=[], reviewed_by='agent', reviewed_at='2026-10-29',
                        source_files=[{'path': str(source), 'sha256': account.sha(source)}])
        review = self.root / 'review.json'
        review.write_text(json.dumps(approved), encoding='utf-8')
        output = io.StringIO()
        with patch.object(account, 'ROOT', self.root), redirect_stdout(output), patch.object(account, 'update_dashboard', side_effect=RuntimeError('snapshot failed')), patch.object(bill_inbox.Inbox, '_run', return_value={'ok': True}) as run:
            self.assertEqual(account.main(['--database', str(database), 'import', '--input', str(review), '--apply']), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['applied'])
        self.assertTrue(result['inboxReceipts']['ok'])
        self.assertEqual(run.call_args.args, ('acknowledge', UPLOAD_ID, 'recorded'))

    def test_duplicate_no_batch_import_receipt_failure_can_retry_with_fact_proof(self):
        self.fetch()
        database = self.database()
        self.import_bill(database, include_manifest=False)
        digest = self.inbox.status()['files'][0]['sha256']
        with patch.object(self.inbox, '_run', side_effect=RuntimeError('offline')):
            result = self.inbox.receipts(database, apply=True, allow_noop={digest})
        self.assertFalse(result['ok'])
        with patch.object(self.inbox, '_run', return_value={'ok': True}) as run:
            self.assertTrue(self.inbox.receipts(database, apply=True)['ok'])
        self.assertEqual(run.call_args.args, ('acknowledge', UPLOAD_ID, 'recorded'))

    def test_fixed_companion_timeout_and_errors_do_not_echo_transport_output(self):
        from runtime_tools import helper_path
        executable = helper_path(self.root, 'bill_inbox_cli')
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'synthetic')
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret', 90)) as run:
            with self.assertRaisesRegex(RuntimeError, '账单收取或回执失败') as error:
                self.inbox._run('receive', str(self.root))
        self.assertNotIn('secret', str(error.exception))
        self.assertEqual(run.call_args.args[0][0], str(executable))
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.kwargs['timeout'], 120)

    def test_conflicting_raw_source_cannot_receive_recorded_even_with_matching_batch_hash(self):
        self.fetch()
        database = self.database()
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        rows, _ = account.parse_sources([source])
        rows[0].update({'原始金额': '9.99', '最终财务事件': 'purchase', '统计口径': 'PERSONAL', '计入消费金额': '9.99',
                        '主分类': '合成分类', '资金池': account.POOLS[0], '语义说明': '测试审定'})
        approved = dict(records=rows, updates=[], reviewed_by='agent', reviewed_at='2026-10-29',
                        source_files=[{'path': str(source), 'sha256': account.sha(source)}])
        ledger_store.import_reviewed(database, approved, apply=True)
        with patch.object(self.inbox, '_run', side_effect=AssertionError('conflict not recorded')):
            self.assertTrue(self.inbox.receipts(database, apply=True)['ok'])
        self.assertEqual(self.inbox.status()['files'][0]['state'], 'received')

    def test_stale_manifest_archive_prevents_acknowledgement(self):
        self.fetch()
        database = self.database()
        self.import_bill(database)
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        source.write_bytes(b'changed')
        with patch.object(self.inbox, '_run', side_effect=AssertionError('archive changed')):
            self.inbox.receipts(database, apply=True)
        self.assertEqual(self.inbox.status()['files'][0]['state'], 'received')

    def test_inbox_lock_excludes_parallel_fetch_and_receipts(self):
        with self.inbox.lock():
            with self.assertRaises(RuntimeError):
                self.fetch()

    def test_import_receipt_failure_is_nonfatal_and_dry_import_never_receipts(self):
        self.fetch()
        database = self.database()
        self.import_bill(database)
        source = Path(self.inbox.status()['files'][0]['sourcePath'])
        approved = dict(records=[], updates=[], source_files=[{'path': str(source), 'sha256': account.sha(source)}])
        review = self.root / 'duplicate.json'
        review.write_text(json.dumps(approved), encoding='utf-8')
        for apply in (False, True):
            output = io.StringIO()
            with patch.object(account, 'ROOT', self.root), redirect_stdout(output), patch.object(bill_inbox.Inbox, '_run', side_effect=RuntimeError('offline')) as run:
                command = ['--database', str(database), 'import', '--input', str(review)] + (['--apply'] if apply else [])
                self.assertEqual(account.main(command), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result['ok'])
            if apply:
                self.assertFalse(result['inboxReceipts']['ok'])
            else:
                run.assert_not_called()

    def test_source_change_during_receipt_parse_cannot_acknowledge(self):
        self.fetch()
        database = self.database()
        self.import_bill(database)
        parse = account.parse_sources
        def changing(paths):
            result = parse(paths)
            Path(paths[0]).write_bytes(b'changed during parse')
            return result
        with patch.object(account, 'parse_sources', side_effect=changing), patch.object(self.inbox, '_run') as run:
            self.inbox.receipts(database, apply=True)
        run.assert_not_called()

    def test_valid_receive_cleans_only_its_temporary_decrypted_files(self):
        self.fetch()
        self.assertEqual(list((self.inbox.runtime / 'downloads').iterdir()), [])
        self.assertEqual((self.root / 'sra' / '账单.csv').read_bytes(), CSV)

    def test_invalid_receive_retains_explicit_quarantine_for_diagnosis(self):
        result = self.fetch(content=b'not a bill')
        quarantine = Path(result['retainedDownloads'])
        self.assertTrue(quarantine.is_dir())
        self.assertTrue(quarantine.is_relative_to(self.inbox.runtime / 'downloads'))

    def test_invalid_local_manifest_has_a_sanitized_error(self):
        self.inbox.runtime.mkdir(parents=True)
        self.inbox.manifest.write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '本机账单收取清单无效'):
            self.inbox.status()

    def page(self, args, content=CSV, next_cursor=None, empty=False):
        if empty:
            return dict(files=[], nextCursor=next_cursor)
        path = Path(args[1]) / 'native-download.csv'
        path.write_bytes(content)
        digest = account.sha(path)
        return dict(files=[dict(id=digest, fileName='账单.csv', byteCount=len(content), sha256=digest, path=str(path))], nextCursor=next_cursor or digest)

    def test_paged_interrupt_resumes_durable_cursor_then_rescans_lower_hashes(self):
        lower = CSV + b'\n'
        higher = CSV
        if hashlib.sha256(lower).hexdigest() > UPLOAD_ID:
            lower, higher = higher, lower
        high_id = hashlib.sha256(higher).hexdigest()
        low_id = hashlib.sha256(lower).hexdigest()
        receives = []
        def interrupted(*args):
            if args[0] == 'acknowledge': return {'ok': True}
            receives.append(args)
            if len(receives) == 1: return self.page(args, higher)
            raise RuntimeError('secret timeout details')
        with patch.object(self.inbox, '_run', side_effect=interrupted):
            result = self.inbox.fetch(apply=True)
        self.assertFalse(result['ok'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['received'], 1)
        self.assertEqual(self.inbox.status()['receiveCursor'], high_id)
        self.assertNotIn('secret', json.dumps(result))
        with patch.object(self.inbox, '_run', return_value=dict(files=[], nextCursor=None)) as run:
            self.assertTrue(self.inbox.fetch(apply=True)['ok'])
        self.assertEqual(run.call_args.args[-1], high_id)
        self.assertIsNone(self.inbox.status()['receiveCursor'])
        calls = []
        def new_scan(*args):
            if args[0] == 'acknowledge': return {'ok': True}
            calls.append(args)
            return self.page(args, lower) if len(calls) == 1 else dict(files=[], nextCursor=None)
        with patch.object(self.inbox, '_run', side_effect=new_scan):
            self.assertTrue(self.inbox.fetch(apply=True)['ok'])
        self.assertEqual(len(calls[0]), 2)
        self.assertIn(low_id, [e['id'] for e in self.inbox.status()['files']])

    def test_recorded_empty_page_advances_and_nonincreasing_cursor_stops(self):
        cursors = ['1' * 64, '1' * 64]
        calls = []
        def remote(*args):
            calls.append(args)
            return dict(files=[], nextCursor=cursors.pop(0))
        with patch.object(self.inbox, '_run', side_effect=remote):
            result = self.inbox.fetch(apply=True)
        self.assertFalse(result['ok'])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][-1], '1' * 64)
        self.assertEqual(self.inbox.status()['receiveCursor'], '1' * 64)
        for malformed in ('0' * 64, 'not-a-hash', 17):
            with patch.object(self.inbox, '_run', return_value=dict(files=[], nextCursor=malformed)):
                self.assertFalse(self.inbox.fetch(apply=True)['ok'])
            self.assertEqual(self.inbox.status()['receiveCursor'], '1' * 64)

    def test_page_limit_preserves_cursor_without_claiming_scan_completed(self):
        cursors = iter(('1' * 64, '2' * 64))
        with patch.object(bill_inbox, 'MAX_RECEIVE_PAGES', 2), patch.object(self.inbox, '_run', side_effect=lambda *args: dict(files=[], nextCursor=next(cursors))):
            result = self.inbox.fetch(apply=True)
        self.assertFalse(result['ok'])
        self.assertFalse(result['scanComplete'])
        self.assertEqual(result['pages'], 2)
        self.assertEqual(self.inbox.status()['receiveCursor'], '2' * 64)

    def test_compressed_oversize_xlsx_rejected_before_parser_on_receive_and_proof(self):
        compressed = io.BytesIO()
        with zipfile.ZipFile(compressed, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('xl/worksheets/sheet1.xml', b'x' * (16 * 1024 * 1024 + 1))
        content = compressed.getvalue()
        self.assertLess(len(content), 4 * 1024 * 1024)
        with patch.object(account, 'parse_sources') as parser:
            result = self.fetch(content=content, name='oversize.xlsx')
        self.assertFalse(result['ok'])
        parser.assert_not_called()
        self.assertFalse((self.root / 'sra').exists())
        self.assertTrue(Path(result['retainedDownloads']).is_dir())
        self.inbox.archive.mkdir()
        source = self.inbox.archive / 'oversize.xlsx'
        source.write_bytes(content)
        entry = dict(sourcePath=str(source), sha256=account.sha(source), state='received')
        with patch.object(account, 'parse_sources') as parser:
            self.assertIsNone(self.inbox._proof(entry, self.root / 'unused.sqlite3', [], set()))
        parser.assert_not_called()

    def test_normal_wechat_workbook_still_archives_original_bytes(self):
        workbook = account.openpyxl.Workbook()
        workbook.active.append(['交易时间', '交易单号', '金额(元)', '交易对方'])
        workbook.active.append(['2026-10-18 12:00:00', 'synthetic-wechat', '1.23', '合成商户'])
        stream = io.BytesIO()
        workbook.save(stream)
        workbook.close()
        content = stream.getvalue()
        self.assertTrue(self.fetch(content=content, name='微信账单.xlsx')['ok'])
        self.assertEqual((self.root / 'sra' / '微信账单.xlsx').read_bytes(), content)

    def test_xlsx_total_count_and_encrypted_members_are_rejected_before_parser(self):
        for mode in ('total', 'count', 'encrypted'):
            with self.subTest(mode=mode):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    if mode == 'total':
                        for index in range(3): archive.writestr(str(index), b'x' * (12 * 1024 * 1024))
                    elif mode == 'count':
                        for index in range(4097): archive.writestr(str(index), b'')
                    else: archive.writestr('member', b'content')
                content = bytearray(buffer.getvalue())
                if mode == 'encrypted':
                    central = content.index(b'PK\x01\x02')
                    content[central + 8] |= 1
                path = self.root / (mode + '.xlsx')
                path.write_bytes(content)
                with patch.object(account, 'parse_sources') as parser:
                    with self.assertRaises(ValueError): self.inbox._parse_source(path)
                parser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
