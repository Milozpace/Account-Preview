from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import account
import app_export
import dashboard_data


class SQLitePipelineTests(unittest.TestCase):
    def setUp(self):
        import ledger_store
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.database = self.root / 'data' / 'account.sqlite3'
        from test_ledger_store import excel_fixture
        fixture = self.root / 'synthetic.xlsx'
        excel_fixture(fixture)
        ledger_store.migrate_excel(fixture, self.database)

    def test_v1_and_v2_use_one_database_calculation_without_excel(self):
        with patch.object(dashboard_data, 'run_excel', side_effect=AssertionError('must not start Excel')):
            report = dashboard_data.sync_snapshot(self.database)
            exported = app_export.export_snapshot(self.database)
        self.assertTrue(report['ok'])
        self.assertEqual(Path(exported['output']), self.root / '.runtime' / 'ledger' / 'snapshot-v2.json')
        snapshot = json.loads(Path(exported['output']).read_text(encoding='utf-8'))
        app_export.validate_app_snapshot(snapshot)
        self.assertEqual(snapshot['ledgerCount'], 1)
        self.assertEqual(snapshot['sourceHash'], report['sourceHash'])
        self.assertFalse((self.root / 'data' / '.runtime').exists())

    def test_cli_database_inspect_and_verify_without_excel(self):
        for command in ['inspect','verify']:
            buffer = io.StringIO()
            with redirect_stdout(buffer), patch.object(account, 'native_worker', side_effect=AssertionError('must not start Excel')):
                code = account.main(['--database', str(self.database), command])
            report = json.loads(buffer.getvalue())
            self.assertEqual(code, 0)
            if command == 'verify':
                self.assertTrue(report['ok'], report)

    def test_prepare_records_source_hash_manifest_for_reviewed_batch(self):
        source=self.root/'synthetic.csv'
        source.write_text('交易时间,交易订单号,金额,交易对方\n2026-10-18 12:00:00,synthetic-new,1.23,合成商户\n',encoding='utf-8-sig')
        reviewed=self.root/'reviewed.json'
        with redirect_stdout(io.StringIO()):
            account.main(['--database',str(self.database),'prepare','--source',str(source),'--output',str(reviewed)])
        payload=json.loads(reviewed.read_text(encoding='utf-8'))
        self.assertEqual(payload['source_files'],[{'path':str(source.resolve()),'sha256':account.sha(source)}])


    def test_source_hash_is_content_hash_and_export_cannot_overwrite_database(self):
        import ledger_source
        import ledger_store
        dashboard_data.sync_snapshot(self.database)
        before = ledger_source.source_hash(self.database)
        ledger_store.backup(self.database, self.root / 'copy.sqlite3')
        self.assertEqual(before, ledger_source.source_hash(self.root / 'copy.sqlite3'))
        with self.assertRaises(ValueError):
            app_export.export_snapshot(self.database, output=self.database)
        self.assertEqual(before, ledger_source.source_hash(self.database))

    def test_app_export_cannot_destroy_retained_workbook_or_source_config(self):
        retained=self.root/'个人财物工作簿.xlsx'
        retained.write_bytes(b'baseline must be preserved')
        config=self.root/'data'/'source.json'
        config.write_text(json.dumps({'kind':'sqlite','path':'account.sqlite3'}))
        dashboard_data.sync_snapshot(self.database)
        for output in (retained,config):
            with self.assertRaises(ValueError):
                app_export.export_snapshot(self.database,output=output)
        self.assertEqual(retained.read_bytes(),b'baseline must be preserved')
        self.assertEqual(json.loads(config.read_text())['kind'],'sqlite')

    def test_excel_export_is_view_only_and_refuses_source_or_existing_output(self):
        import ledger_excel
        import ledger_source
        before = ledger_source.source_hash(self.database)
        target = self.root / 'exports' / '账单导出.xlsx'
        result = ledger_excel.export_excel(self.database, target)
        self.assertTrue(result['ok'])
        workbook = account.openpyxl.load_workbook(target, read_only=True, data_only=False)
        try:
            self.assertEqual(workbook.sheetnames, ['账单流水','预算参数','月度统计'])
            self.assertEqual(workbook['账单流水'].max_row, result['rows'] + 1)
            for sheet in workbook:
                self.assertFalse(any(cell.data_type == 'f' for row in sheet for cell in row))
        finally:
            workbook.close()
        with self.assertRaises(ValueError):
            ledger_excel.export_excel(self.database, target)
        with self.assertRaises(ValueError):
            ledger_excel.export_excel(self.database, self.database)
        self.assertEqual(before, ledger_source.source_hash(self.database))


if __name__ == '__main__':
    unittest.main()
