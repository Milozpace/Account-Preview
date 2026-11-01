import type {DashboardMonth, Pool} from './types';

// Zero amounts alone do not tell us whether the user intended a spending limit.
// Only show comparisons when an active month has a positive allowance.
export function hasComparableAllowance(month: DashboardMonth): boolean {
  return month.mechanismActive && ((month.summary.openingBudgetCents ?? 0) > 0 || (month.summary.monthlyBudgetCents ?? 0) > 0);
}

export function hasPoolAllowance(pool: Pool, active: boolean): boolean {
  return active && ((pool.openingCents ?? 0) > 0 || (pool.budgetCents ?? 0) > 0);
}
