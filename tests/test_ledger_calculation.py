import calendar
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account
import dashboard_data
try:
    import ledger_calculation as calculation
except ImportError:
    calculation = None


def fixture(rows=None):
    return {'rows': rows or [], 'rates': [{'生效月份': '2024-01-01', **dict(zip(account.POOLS, ['100.00', '50.00', '20.00', '30.00']))}],
            'initial': dict.fromkeys(account.POOLS, '0.00')}


def record(day, amount, pool='运行账户', scope='PERSONAL', identity=None):
    return {'记录ID': identity or day+amount+pool+scope, '交易时间': day+' 12:00:00',
            '计入消费金额': amount, '资金池': pool, '统计口径': scope, '主分类': '合成分类'}


class CalculationTests(unittest.TestCase):
    def test_new_observation_mode_keeps_classified_spending_without_budget_debt(self):
        state=fixture([record('2024-01-01','25','旅行/体验'),record('2024-01-02','12','成长/项目/耐用品'),record('2024-01-03','-4')])
        state['budgetMode']='zero-observation-v1'
        state['rates']=[{'生效月份':'2024-01-01',**dict.fromkeys(account.POOLS,'0.00')},fixture()['rates'][0]|{'生效月份':'2024-02-01'}]
        observation=calculation.calculate_month(state,'2024-01','2024-03')
        self.assertFalse(observation['mechanismActive'])
        self.assertEqual(observation['summary']['personalCents'],3300)
        self.assertEqual([p['spentCents'] for p in observation['pools']],[-400,0,2500,1200])
        self.assertEqual(observation['summary']['overrunCents'],0)
        self.assertEqual(observation['summary']['safetyCents'],0)
        active=calculation.calculate_month(state,'2024-02','2024-03')
        self.assertTrue(active['mechanismActive'])
        self.assertEqual([p['carryCents'] for p in active['pools']],[0,0,0,0])
        self.assertEqual(active['summary']['safetyCents'],10000)
        # Unmarked existing ledgers retain their established computation.
        del state['budgetMode']
        legacy=calculation.calculate_month(state,'2024-02','2024-03')
        self.assertEqual([p['carryCents'] for p in legacy['pools']],[0,0,-2500,-1200])
        self.assertEqual(legacy['summary']['safetyCents'],10400)

    def test_observation_mode_respects_explicit_initial_carry_and_later_zero_rates(self):
        state=fixture([record('2024-02-01','3','旅行/体验')]);state['budgetMode']='zero-observation-v1'
        state['rates'].append({'生效月份':'2024-02-01',**dict.fromkeys(account.POOLS,'0.00')})
        result=calculation.calculate_month(state,'2024-02','2024-03')
        self.assertTrue(result['mechanismActive'])
        self.assertEqual(result['pools'][2]['endingCents'],1700)
        state['rates']=[{'生效月份':'2024-01-01',**dict.fromkeys(account.POOLS,'0.00')}]
        state['initial']['旅行/体验']='10.00'
        self.assertTrue(calculation.calculate_month(state,'2024-01','2024-01')['mechanismActive'])

    def setUp(self):
        self.assertIsNotNone(calculation, 'SQLite ledger calculation module is required')

    def test_reset_carry_and_safety_history(self):
        state = fixture([record('2024-01-01','120'), record('2024-01-02','60','随心消费'),
                         record('2024-01-03','25','旅行/体验'), record('2024-01-04','10','成长/项目/耐用品'),
                         record('2024-02-01','80')])
        month = calculation.calculate_month(state, '2024-02', '2024-03')
        self.assertEqual([p['carryCents'] for p in month['pools']], [0,0,-500,2000])
        self.assertEqual([p['openingCents'] for p in month['pools']], [10000,5000,1500,5000])
        self.assertEqual(month['summary']['safetyCents'],2000)
        self.assertEqual(calculation.calculate_month(state,'2024-02','2024-02')['summary']['safetyCents'],0)

    def test_negative_unknown_project_structural_reconcile_display(self):
        state = fixture([record('2024-02-01','10'), record('2024-02-02','-3'),
                         record('2024-02-03','5','待归类'), record('2024-02-04','7','成长/项目/耐用品','PROJECT'),
                         record('2024-02-05','100','运行账户','STRUCTURAL')])
        native = calculation.calculate_month(state,'2024-02','2024-02')
        view = dashboard_data.build_month(state['rows'],'2024-02',native)
        self.assertEqual(view['summary']['personalCents'],1900)
        self.assertEqual(view['summary']['totalCents'],11900)
        self.assertEqual(view['summary']['unknownCents'],500)
        self.assertEqual(view['pools'][0]['spentCents'],700)

    def test_actual_days_float_reference_rounding_and_year_boundary(self):
        for month, expected in [('2024-02',29),('2025-02',28),('2024-12',31),('2025-01',31)]:
            result = calculation.calculate_month(fixture(),'2024-02' if month=='2024-02' else month,month)
            self.assertEqual(len(result['daily']),expected)
            for day in range(1, expected+1):
                self.assertEqual(result['daily'][f'{month}-{day:02d}']['referenceCents'],int(account.money(100.0*day/expected)*100))
        self.assertEqual(calculation.calculate_month(fixture(),'2025-01','2025-01')['pools'][2]['carryCents'],24000)

    def test_before_mechanism_keeps_spent_but_zero_budget_reference(self):
        state = fixture([record('2023-12-01','10')])
        result = calculation.calculate_month(state,'2023-12','2024-02')
        self.assertFalse(result['mechanismActive'])
        self.assertEqual(result['pools'][0]['spentCents'],1000)
        for key in ('budgetCents','carryCents','openingCents','endingCents','availableCents','overrunCents'):
            self.assertEqual(result['pools'][0][key],0)
        self.assertIsNone(result['pools'][0]['usageRatio'])
        self.assertEqual(result['daily']['2023-12-31']['referenceCents'],0)

    def test_budget_effective_month_and_initial_carry(self):
        state = fixture()
        state['initial']['旅行/体验'] = '-10.00'
        state['rates'].append({'生效月份':'2024-03-01',**dict.fromkeys(account.POOLS,'40.00')})
        result = calculation.calculate_month(state,'2024-03','2024-03')
        self.assertEqual(result['pools'][2]['openingCents'],7000)
        self.assertEqual(result['pools'][0]['budgetCents'],4000)
        self.assertEqual(result['summary']['safetyCents'],20000)

    def test_nonzero_unsupported_scope_and_bad_month_rejected(self):
        with self.assertRaises(ValueError):
            calculation.calculate_month(fixture([record('2024-02-01','1',scope='UNRESOLVED')]),'2024-02','2024-02')
        with self.assertRaises(ValueError):
            calculation.calculate_month(fixture(),'2024-13','2024-02')


if __name__ == '__main__':
    unittest.main()
