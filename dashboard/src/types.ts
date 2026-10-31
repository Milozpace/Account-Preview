export type PoolId = 'operation' | 'free' | 'travel' | 'growth';
export interface LedgerRecord {
  id: string; time: string; date: string; title: string; category: string;
  pool: PoolId | 'unknown'; scope: string; amountCents: number;
}
export interface Daily {
  date: string; amountCents: number; count: number;
  operationCumulativeCents: number | null; referenceCents: number | null;
}
export interface Pool {
  id: PoolId; name: string; budgetCents: number | null; carryCents: number | null;
  openingCents: number | null; spentCents: number; endingCents: number | null;
  availableCents: number | null; overrunCents: number | null; usageRatio: number | null;
}
export interface DashboardMonth {
  month: string; lastRecordedAt: string | null; mechanismActive: boolean;
  summary: {
    personalCents: number; structuralCents: number; totalCents: number; unknownCents: number;
    monthlyBudgetCents: number | null; openingBudgetCents: number | null;
    availableCents: number | null; overrunCents: number | null; safetyCents: number | null;
  };
  pools: Pool[]; daily: Daily[]; categories: { name: string; amountCents: number; count: number }[];
  records: LedgerRecord[]; structuralRecords: LedgerRecord[]; topIds: string[];
}
export interface DashboardSnapshot {
  schemaVersion: number; sourceHash: string; calendarMonth: string; generatedAt: string;
  sourceLastRecordedAt: string | null; ledgerCount: number;
  months: DashboardMonth[];
}
export interface DashboardStatus {
  generatedAt: string | null; sourceChanged: boolean; monthChanged: boolean;
  syncing: boolean; error: string | null; today: string;
  /** Optional native source/cloud detail; shown in the existing sync tooltip. */
  syncDetail?: string;
  /** Windows-only read state; absent for the web API. */
  localState?: 'loading' | 'ready' | 'empty' | 'error';
}
