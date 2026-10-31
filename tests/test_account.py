import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account


class AccountSafetyTests(unittest.TestCase):
    def row(self, order='001', amount='0'):
        return {'平台': '支付宝', '交易时间': '2026-10-01 12:00:00', '交易对方': '商户',
                '商品/说明': '说明', '原始金额': amount, '交易订单号': order}

    def test_zero_amount_and_stable_platform_order_identity(self):
        a = account.normalize_record(self.row())
        self.assertEqual(a['原始金额'], '0.00')
        self.assertEqual(a['记录ID'], account.normalize_record(self.row())['记录ID'])
        other = self.row(); other['平台'] = '微信'
        self.assertNotEqual(a['记录ID'], account.normalize_record(other)['记录ID'])

    def test_merchant_order_fallback_used_when_transaction_order_placeholder(self):
        row = self.row(order='/'); row['商家订单号'] = '001'
        a = account.normalize_record(row)
        expected = account.normalize_record(self.row(order='001'))
        self.assertEqual(a['记录ID'], expected['记录ID'])
        self.assertNotIn('需复核', a.get('复核状态', ''))

    def test_duplicate_skipped_and_raw_conflict_rejected(self):
        a = account.normalize_record(self.row())
        plan = account.plan_import([a], {'records': [self.row()]})
        self.assertEqual(plan['duplicates'], 1)
        with self.assertRaises(ValueError):
            account.plan_import([a], {'records': [self.row(amount='1')]})

    def test_refund_update_requires_reason_and_protects_original(self):
        a = account.normalize_record(self.row())
        valid = {'records': [], 'updates': [{'记录ID': a['记录ID'], 'changes': {'退款调整': '5.00'},
                                            'reason': '退款凭证', 'confirmation_source': '用户确认2026-10-02'}]}
        plan = account.plan_import([a], valid)
        self.assertEqual(plan['updates'][0]['changes']['退款调整'], '5.00')
        valid['updates'][0]['changes']['原始金额'] = '5.00'
        with self.assertRaises(ValueError): account.plan_import([a], valid)
        valid['updates'][0]['changes'] = {'退款调整': '5.00'}
        valid['updates'][0]['reason'] = ''
        with self.assertRaises(ValueError): account.plan_import([a], valid)

    def test_missing_order_marked_and_allocations_require_parent(self):
        a = account.normalize_record(self.row(order=''))
        self.assertEqual(a['复核状态'], '需复核：缺少订单号')
        row = self.row(); row['记录类型'] = '分摊'
        with self.assertRaises(ValueError): account.normalize_record(row)

    def test_unknown_pool_is_part_of_personal_reconciliation(self):
        rows = []
        for pool, amount in [('运行账户', '10'), ('待归类', '5'), ('', '0')]:
            row = self.row(amount=amount)
            row.update({'资金池': pool, '统计口径': 'PERSONAL', '计入消费金额': amount})
            rows.append(row)
        result = account.aggregate(rows)['2026-10']
        self.assertEqual(result['personal'], '15.00')
        self.assertEqual(result['pools']['运行账户'], '10.00')
        self.assertEqual(result['unknown'], '5.00')

    def test_repeat_refund_update_is_idempotent_but_new_amount_not_ignored(self):
        row = account.normalize_record(self.row())
        row.update({'退款调整': 5, '用户修正规则': '退款；依据：凭证'})
        update = {'记录ID': row['记录ID'], 'changes': {'退款调整': '5.00'}, 'reason': '退款', 'confirmation_source': '凭证'}
        self.assertEqual(account.plan_import([row], {'updates': [update]})['duplicates'], 1)
        update['changes']['退款调整'] = '6.00'
        self.assertEqual(len(account.plan_import([row], {'updates': [update]})['updates']), 1)

    def test_prepare_omitted_classification_and_timestamp_conflict_preserve_raw(self):
        existing = account.normalize_record(self.row())
        existing['原始分类'] = '商户消费'
        incoming = account.normalize_record(self.row())
        new, count, conflicts = account.prepare_records([existing], [incoming])
        self.assertEqual((len(new),count,len(conflicts)),(0,1,0))
        incoming['交易时间'] = '2026-10-01 12:00:01'
        new, count, conflicts = account.prepare_records([existing], [incoming])
        self.assertEqual(conflicts[0]['different_raw_fields'],['交易时间'])
        self.assertEqual(existing['交易时间'],'2026-10-01 12:00:00')

    def test_failed_worker_preserves_original_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            live = Path(directory) / '个人财务系统.xlsx'
            live.write_bytes(b'original')
            with patch.object(account, 'native_worker', side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError): account.safe_write(live, 'Recalculate')
            self.assertEqual(live.read_bytes(), b'original')
            self.assertEqual(list(Path(directory).glob('.account-*')), [])

    def test_safety_accumulation_is_not_a_consumption_pool(self):
        row = self.row(); row['资金池'] = '安全积累'
        with self.assertRaises(ValueError): account.normalize_record(row)
        existing=account.normalize_record(self.row())
        with self.assertRaises(ValueError):
            account.plan_import([existing],{'updates':[{'记录ID':existing['记录ID'],'changes':{'资金池':'安全积累'},'reason':'测试','confirmation_source':'测试'}]})

    def test_open_workbook_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            live=Path(temp)/'book.xlsx';live.write_bytes(b'original')
            live.with_name('~$'+live.name).write_bytes(b'Excel lock')
            with self.assertRaises(ValueError):account.safe_write(live,'Recalculate')
            self.assertEqual(live.read_bytes(),b'original')
            self.assertFalse(list(Path(temp).glob('.account-*')))

    def test_month_rollover_resets_and_separates_safety(self):
        rates = [{'生效月份':'2026-09-01','运行账户':100,'随心消费':20,'旅行/体验':30,'成长/项目/耐用品':40}]
        rows = []
        for pool, amount in [('运行账户','80'),('随心消费','25'),('旅行/体验','5'),('成长/项目/耐用品','15'),('待归类','2')]:
            row = self.row(amount=amount)
            row.update({'交易时间':'2026-09-30 23:59:59','资金池':pool,'计入消费金额':amount,'统计口径':'PERSONAL'})
            rows.append(row)
        # Even a mislabeled structural transaction must not affect pool consumption.
        rows.append(dict(rows[0],统计口径='STRUCTURAL',计入消费金额='900'))
        result = account.pool_reconciliation(rows,rates,{},'2026-10','2026-10')
        self.assertEqual(result['opening'], {'运行账户':account.money(0),'随心消费':account.money(0),'旅行/体验':account.money(25),'成长/项目/耐用品':account.money(25)})
        self.assertEqual(result['available']['随心消费'],account.money(20))
        self.assertEqual(result['available']['成长/项目/耐用品'],account.money(65))
        self.assertEqual(result['safety'],account.money(20))
        self.assertEqual(result['monthly']['2026-09']['safety_added'],account.money(20))
        self.assertEqual(result['monthly']['2026-10']['safety_added'],account.money(0))

    def test_operating_overspend_does_not_reduce_next_month_or_safety(self):
        rates = [{'生效月份':'2026-09-01','运行账户':100,'随心消费':20,'旅行/体验':30,'成长/项目/耐用品':40},
                 {'生效月份':'2026-11-01','运行账户':110,'随心消费':25,'旅行/体验':35,'成长/项目/耐用品':45}]
        row = self.row(amount='150');row.update({'交易时间':'2026-09-01','统计口径':'PERSONAL','计入消费金额':'150','资金池':'运行账户'})
        result=account.pool_reconciliation([row],rates,{},'2026-11','2026-11')
        self.assertEqual(result['available']['运行账户'],account.money(110))
        self.assertEqual(result['safety'],account.money(100)) # October has no recorded spend.
        self.assertEqual(result['monthly']['2026-09']['safety_added'],account.money(0))
        early=account.pool_reconciliation([row],rates,{},'2026-08','2026-11')
        self.assertEqual(sum(early['available'].values()),account.money(0))
        self.assertEqual(early['safety'],account.money(0))


if __name__ == '__main__': unittest.main()
