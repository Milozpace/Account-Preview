import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class LedgerSourceTests(unittest.TestCase):
    def test_explicit_source_kind_cannot_silently_select_another_backend(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                ledger_source.resolve(root, database=root / '账本.xlsx')
            with self.assertRaises(ValueError):
                ledger_source.resolve(root, workbook=root / 'account.sqlite3')

    def test_activated_source_prevents_old_excel_exporter_claiming_current(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'data').mkdir()
            database=root/'data'/'account.sqlite3'
            database.touch()
            (root/'data'/'source.json').write_text(json.dumps({'kind':'sqlite','path':'account.sqlite3'}))
            with self.assertRaises(ValueError):
                ledger_source.assert_active(root/'个人财物工作簿.xlsx')
            ledger_source.assert_active(database)

    def test_no_activation_stops_even_when_candidate_database_exists(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data' / 'account.sqlite3').touch()
            with self.assertRaisesRegex(ValueError, 'init'):
                ledger_source.resolve(root)

    def test_active_database_never_falls_back_to_excel_if_missing(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / '个人财物工作簿.xlsx').touch()
            (root / 'data' / 'source.json').write_text(json.dumps({'kind':'sqlite', 'path':'account.sqlite3'}))
            with self.assertRaisesRegex(ValueError, '数据库'):
                ledger_source.resolve(root)

    def test_explicit_database_and_runtime_paths(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            database = root / 'data' / 'account.sqlite3'
            self.assertEqual(ledger_source.resolve(root, database=database), database)
            self.assertEqual(ledger_source.runtime(database), root / '.runtime')

    def test_source_config_cannot_escape_data_directory(self):
        import ledger_source
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            (root / 'data' / 'source.json').write_text(json.dumps({'kind':'sqlite', 'path':'../elsewhere.sqlite3'}))
            with self.assertRaises(ValueError):
                ledger_source.resolve(root)

    def test_database_only_commands_fail_clearly_before_excel_operations(self):
        import account
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError,'SQLite'):
                account.main(['--workbook',str(Path(folder)/'baseline.xlsx'),
                              'backup','--output',str(Path(folder)/'backup.sqlite3')])


if __name__ == '__main__':
    unittest.main()
