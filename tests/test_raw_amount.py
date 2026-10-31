"""Raw-money boundaries: synthetic CSV/XLSX through reviewed SQLite import."""
from contextlib import redirect_stdout
import csv
from decimal import Decimal
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import account
import ledger_store
import onboarding

class RawAmountTests(unittest.TestCase):
    def record(self, value):
        return {'平台':'TEST','交易时间':'2026-10-01 12:00:00','交易订单号':'synthetic-raw','原始金额':value}

    def test_normalization_rejects_absent_blank_placeholders_nonfinite_and_booleans(self):
        for value in (None,'',' ','\t','/','--','NaN',float('nan'),float('inf'),True,False):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):account.normalize_record(self.record(value))

    def test_normalization_preserves_explicit_zero_and_two_decimal_amounts(self):
        for value, expected in [(0,'0.00'),('0.00','0.00'),('2.34','2.34'),(Decimal('2.34'),'2.34'),('￥1,234.56','1234.56')]:
            with self.subTest(value=value):
                self.assertEqual(account.normalize_record(self.record(value))['原始金额'],expected)

    def source(self, root, suffix, value):
        source=root/('bill'+suffix)
        rows=[['交易时间','交易订单号','金额','交易对方'],['2026-10-01 12:00:00','synthetic-source',value,'合成商户']]
        if suffix=='.csv':
            with source.open('w',encoding='utf-8-sig',newline='') as stream:csv.writer(stream).writerows(rows)
        else:
            wb=account.openpyxl.Workbook()
            for values in rows:wb.active.append(values)
            wb.save(source);wb.close()
        return source

    def prepare(self,root,source,reviewed):
        with redirect_stdout(io.StringIO()):
            return account.main(['--root',str(root),'prepare','--source',str(source),'--output',str(reviewed)])

    def test_invalid_csv_and_xlsx_never_create_review_output_or_change_ledger(self):
        for suffix in ('.csv','.xlsx'):
            for value in (None,'',' ','/','--','NaN',True,False):
                with self.subTest(suffix=suffix,value=repr(value)),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory)
                    onboarding.initialize(root,'2026-10',dict.fromkeys(account.POOLS,0),apply=True)
                    database=root/'data/account.sqlite3';before=ledger_store.read_state(database)
                    source=self.source(root,suffix,value);reviewed=root/'reviewed.json'
                    with self.assertRaises(ValueError):self.prepare(root,source,reviewed)
                    self.assertFalse(reviewed.exists())
                    self.assertEqual(ledger_store.read_state(database),before)
                    # Even bypassing prepare cannot convert an absent raw amount into zero.
                    raw=self.record(value)
                    raw.update({'最终财务事件':'purchase','统计口径':'PERSONAL','计入消费金额':'0.00','主分类':'合成','资金池':'待归类','语义说明':'合成测试'})
                    approved=dict(records=[raw],updates=[],reviewed_by='synthetic',reviewed_at='2026-10-01')
                    with self.assertRaises(ValueError):ledger_store.import_reviewed(database,approved,apply=True)
                    self.assertEqual(ledger_store.read_state(database),before)

    def test_valid_csv_and_xlsx_prepare_then_review_import_preserves_zero_and_cents(self):
        for suffix in ('.csv','.xlsx'):
            for value,expected in [(0,'0.00'),('2.34','2.34')]:
                with self.subTest(suffix=suffix,value=value),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory)
                    onboarding.initialize(root,'2026-10',dict.fromkeys(account.POOLS,0),apply=True)
                    source=self.source(root,suffix,value);reviewed=root/'reviewed.json'
                    self.prepare(root,source,reviewed)
                    approved=json.loads(reviewed.read_text(encoding='utf-8'))
                    record=approved['records'][0]
                    self.assertEqual(record['原始金额'],expected)
                    record.update({'最终财务事件':'purchase','统计口径':'PERSONAL','计入消费金额':expected,'主分类':'合成','资金池':'待归类','语义说明':'合成审定'})
                    approved.update(reviewed_by='synthetic',reviewed_at='2026-10-01')
                    database=root/'data/account.sqlite3'
                    self.assertTrue(ledger_store.import_reviewed(database,approved,apply=True)['applied'])
                    self.assertEqual(ledger_store.read_state(database)['rows'][0]['原始金额'],expected)
                    self.assertTrue(ledger_store.verify(database)['ok'])

if __name__=='__main__':unittest.main()
