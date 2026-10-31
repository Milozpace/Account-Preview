import importlib.util
import json
import sys
import tempfile
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
if importlib.util.find_spec('dashboard_data'):
    import dashboard_data as data
else:
    data = None


def row(identity='tx_a', amount=10, pool='运行账户', scope='PERSONAL', **extra):
    return {'记录ID': identity, '交易时间': datetime(2026, 10, 1, 12),
            '计入消费金额': amount, '统计口径': scope, '资金池': pool,
            '主分类': '餐饮与日常食品', '交易对方': '合成商户', **extra}


def view(personal=1000, structural=0, unknown=0, used=1000):
    pools = []
    for ident, name in zip(['operation', 'free', 'travel', 'growth'],
                           ['运行账户', '随心消费', '旅行/体验', '成长/项目/耐用品']):
        spending = used if ident == 'operation' else 0
        pools.append(dict(id=ident, name=name, budgetCents=10000, carryCents=0,
                          openingCents=10000, spentCents=spending, endingCents=10000-spending,
                          availableCents=max(0,10000-spending), overrunCents=max(0,spending-10000),
                          usageRatio=spending/10000))
    return {'mechanismActive':True, 'summary':{'personalCents':personal, 'structuralCents':structural,
        'totalCents':personal+structural, 'unknownCents':unknown,'safetyCents':1500},
        'pools':pools, 'daily':{'2026-10-01':{'amountCents':personal,
        'operationCumulativeCents':used,'referenceCents':323}},
        'categories':{'餐饮与日常食品':personal}}


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(data, 'Dashboard exporter must exist')

    def test_net_allocation_structural_and_unknown_are_not_counted_twice(self):
        rows=[row('tx_parent',0), row('alloc_food',8), row('alloc_free',2,pool='待归类'),
              row('tx_school',100,scope='STRUCTURAL'),row('tx_transfer',0,scope='EXCLUDED')]
        month=data.build_month(rows,'2026-10',view(1000,10000,200,800))
        self.assertEqual(month['summary']['personalCents'],1000)
        self.assertEqual(month['summary']['structuralCents'],10000)
        self.assertEqual(sum(d['amountCents'] for d in month['daily']),1000)
        self.assertEqual(len(month['records']),2)
        self.assertEqual(len(month['structuralRecords']),1)
        self.assertNotIn('tx_parent',month['topIds'])

    def test_negative_adjustment_retained_in_daily_and_category_net(self):
        month=data.build_month([row(amount=10),row('tx_adjust',-2)],'2026-10',view(800,used=800))
        self.assertEqual(sum(r['amountCents'] for r in month['records']),800)
        self.assertEqual(month['categories'][0]['amountCents'],800)
        self.assertEqual(month['topIds'],['tx_a'])

    def test_formula_disagreement_refuses_publication(self):
        with self.assertRaisesRegex(ValueError,'对账'):
            data.build_month([row(amount=11)],'2026-10',view())
        broken=view();broken['daily']['2026-10-01']['amountCents']=900
        with self.assertRaisesRegex(ValueError,'对账'):
            data.build_month([row()],'2026-10',broken)

    def test_duplicate_identifiers_refused(self):
        with self.assertRaisesRegex(ValueError,'重复'):
            data.build_month([row(),row()],'2026-10',view(2000,used=2000))

    def test_privacy_title_and_fields_are_whitelisted(self):
        record=row(**{'商品/说明':'商品 订单 AB123456789012 手机13800138000',
                      '交易订单号':'AB123456789012','支付方式':'账户1234567890','语义说明':'私密依据'})
        month=data.build_month([record],'2026-10',view())
        payload=json.dumps(month,ensure_ascii=False)
        for secret in ['AB123456789012','13800138000','1234567890','私密依据','支付方式','交易订单号']:
            self.assertNotIn(secret,payload)
        self.assertEqual(set(month['records'][0]),{'id','time','date','title','category','pool','scope','amountCents'})

    def test_empty_month_and_zero_budget_do_not_invent_ratio(self):
        native=view(0,used=0)
        for pool in native['pools']:
            for key in ['budgetCents','openingCents','endingCents','availableCents']:pool[key]=0
            pool['usageRatio']=None
        month=data.build_month([],'2026-10',native)
        self.assertEqual(len(month['daily']),31)
        self.assertEqual(month['records'],[])
        self.assertIsNone(month['pools'][0]['usageRatio'])

    def test_blank_formula_result_is_not_zero(self):
        with self.assertRaisesRegex(ValueError,'缺失'):
            data.required_cents(None,'Q2')

    def test_non_finite_usage_ratio_is_rejected(self):
        for value in [float('nan'),float('inf'),float('-inf')]:
            native=view();native['pools'][0]['usageRatio']=value
            with self.assertRaisesRegex(ValueError,'有限'):
                data.build_month([row()],'2026-10',native)

    def test_excel_timeout_is_reported_as_recoverable_sync_failure(self):
        with tempfile.TemporaryDirectory(prefix='account-dashboard-') as temp:
            copy=Path(temp)/'source.xlsx';copy.write_bytes(b'copy')
            with patch.object(data.subprocess,'run',side_effect=subprocess.TimeoutExpired('powershell',240)):
                with self.assertRaisesRegex(RuntimeError,'超时'):
                    data.run_excel(copy,['2026-10','2026-09'])

    def test_unknown_category_remains_visible_and_matches_excel_month_total(self):
        native=view();native['categories']={'餐饮与日常食品':0}
        month=data.build_month([row(**{'主分类':'Unknown'})],'2026-10',native)
        self.assertEqual(month['categories'],[{'name':'Unknown','amountCents':1000,'count':1}])
        self.assertEqual(sum(c['amountCents'] for c in month['categories']),native['summary']['personalCents'])

    def test_timeout_cleans_only_the_marked_export_instance(self):
        with tempfile.TemporaryDirectory(prefix='account-dashboard-') as temp:
            copy=Path(temp)/'source.xlsx';copy.write_bytes(b'copy')
            (Path(temp)/'excel-process.json').write_text('{"processId":123,"startedTicks":"1"}')
            cleaned=subprocess.CompletedProcess([],0,stdout='',stderr='')
            with patch.object(data.subprocess,'run',side_effect=[subprocess.TimeoutExpired('powershell',240),cleaned]) as run:
                with self.assertRaisesRegex(RuntimeError,'超时'):
                    data.run_excel(copy,['2026-10','2026-09'])
            cleanup_args=run.call_args_list[1].args[0]
            self.assertIn('-Cleanup',cleanup_args)
            self.assertEqual(cleanup_args[cleanup_args.index('-WorkbookPath')+1],str(copy))

    def test_pool_available_sum_does_not_offset_one_pool_overspending(self):
        native=view(10001,used=10001)
        month=data.build_month([row(amount='100.01')],'2026-10',native)
        self.assertEqual(month['summary']['availableCents'],30000)
        self.assertEqual(month['summary']['overrunCents'],1)

    def test_failed_excel_keeps_existing_snapshot_and_source(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'book.xlsx';source.write_bytes(b'original')
            runtime=Path(temp)/'.runtime';runtime.mkdir()
            snapshot=runtime/'dashboard.json';snapshot.write_text('{"valid":"previous"}')
            with patch.object(data,'run_excel',side_effect=RuntimeError('Excel 不可用')):
                with self.assertRaises(RuntimeError):data.sync_snapshot(source)
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(snapshot.read_text(),'{"valid":"previous"}')
            self.assertFalse(list(runtime.glob('account-dashboard-*')))

    def test_file_hash_and_calendar_rollover_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'book.xlsx';source.write_bytes(b'original')
            snapshot={'schemaVersion':1,'sourceHash':data.account.sha(source),'calendarMonth':'2026-10'}
            self.assertTrue(data.snapshot_current(source,snapshot,'2026-10'))
            self.assertFalse(data.snapshot_current(source,snapshot,'2026-11'))
            source.write_bytes(b'changed')
            self.assertFalse(data.snapshot_current(source,snapshot,'2026-10'))

    def test_source_change_during_export_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp)/'book.xlsx';source.write_bytes(b'original')
            runtime=Path(temp)/'.runtime';runtime.mkdir()
            snapshot=runtime/'dashboard.json';snapshot.write_text('{"valid":"previous"}')
            def changed(*args):
                source.write_bytes(b'user changed')
                return {'months':[]}
            with patch.object(data,'run_excel',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'变化'):data.sync_snapshot(source)
            self.assertEqual(source.read_bytes(),b'user changed')
            self.assertEqual(snapshot.read_text(),'{"valid":"previous"}')


if __name__=='__main__':unittest.main()
