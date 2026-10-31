"""Synthetic SQLite month rollover; never requires an owner workbook or Excel."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import account
import onboarding
import ledger_calculation as calculation
import ledger_store
from test_ledger_store import row

class MonthHandoffTests(unittest.TestCase):
    def test_reset_and_signed_carry_with_structural_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            onboarding.initialize(root,'2026-09',dict.fromkeys(account.POOLS,100),apply=True)
            database=root/'data/account.sqlite3'
            records=[row('operation',amount='120',**{'交易时间':'2026-09-30 12:00:00'}),
                     row('travel',amount='130',**{'交易时间':'2026-09-30 12:00:00','资金池':account.POOLS[2]}),
                     row('structural',amount='5000',**{'交易时间':'2026-09-30 12:00:00','统计口径':'STRUCTURAL'})]
            ledger_store.import_reviewed(database,dict(records=records,updates=[],reviewed_by='synthetic',reviewed_at='2026-10-01'),apply=True)
            month=calculation.calculate_month(ledger_store.read_state(database),'2026-10','2026-10')
            self.assertEqual([p['carryCents'] for p in month['pools']],[0,0,-3000,10000])
            self.assertEqual(month['summary']['safetyCents'],0)
            self.assertEqual(len(month['daily']),31)

    def test_empty_start_has_no_prior_budget_and_actual_leap_calendar(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            onboarding.initialize(root,'2024-02',dict.fromkeys(account.POOLS,100),apply=True)
            state=ledger_store.read_state(root/'data/account.sqlite3')
            prior=calculation.calculate_month(state,'2024-01','2024-02')
            self.assertFalse(prior['mechanismActive'])
            self.assertEqual(prior['summary']['monthlyBudgetCents'],0)
            current=calculation.calculate_month(state,'2024-02','2024-02')
            self.assertEqual(len(current['daily']),29)
            self.assertEqual(current['summary']['safetyCents'],0)
            closed=calculation.calculate_month(state,'2024-02','2024-03')
            self.assertEqual(closed['summary']['safetyCents'],10000)

if __name__=='__main__':unittest.main()
