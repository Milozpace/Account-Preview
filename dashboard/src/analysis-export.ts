import type { DashboardMonth, DashboardSnapshot, LedgerRecord, PoolId } from './types';

const DASH = '—';
const POOLS: PoolId[] = ['operation', 'free', 'travel', 'growth'];

function cents(value: unknown, nullable = false): asserts value is number | null {
  if (nullable && value === null) return;
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new Error('快照金额缺失或不是安全整数分，无法导出');
  }
}

function money(value: number | null): string {
  if (value === null) return DASH;
  const integer = BigInt(value);
  const absolute = integer < 0n ? -integer : integer;
  return `${integer < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')}`;
}

function percent(ratio: number | null): string {
  if (ratio === null) return DASH;
  if (!Number.isFinite(ratio * 100)) throw new Error('快照比例无效，无法导出');
  return `${(ratio * 100).toFixed(2)}%`;
}

/** Same display-only privacy principles as Python display_title; never exports raw fields. */
function text(value: string): string {
  if (typeof value !== 'string') throw new Error('快照展示文本缺失，无法导出');
  return value
    .replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/g, ' ')
    .replace(/(?:[a-z][a-z0-9+.-]*:\/\/|www\.)[^\s<>()[\]]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/gi, '')
    .replace(/(?:(?:交易|商家)?订单(?:号|编号)?|账号|账户号|银行卡号)\s*[:：#]?\s*[A-Za-z0-9_-]+|账户\s*[:：#]\s*[A-Za-z0-9_.@+-]+/g, '')
    .replace(/[A-Za-z]:[\\/][^\s<>|]+|\\\\[^\s<>|]+|(?:^|[\s:："'（(])\/[^\s<>|]+/g, ' ')
    .replace(/[A-Za-z0-9_-]*\d{7,}[A-Za-z0-9_-]*/g, '')
    .replace(/(?<![A-Za-z])[A-Za-z0-9_-]{16,}(?![A-Za-z])/g, '')
    .replace(/\s+/g, ' ').trim()
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/[|`\[\]()*_~#!\\]/g, character => `&#${character.charCodeAt(0)};`);
}

function timestamp(value: string | null): string {
  if (value === null) return DASH;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})$/.test(value)
    || !Number.isFinite(Date.parse(value))) throw new Error('快照时间无效，无法导出');
  return value;
}

function validate(selected: DashboardMonth, month: string): void {
  if (typeof selected.mechanismActive !== 'boolean') throw new Error('快照预算状态缺失');
  const { summary } = selected;
  for (const key of ['personalCents', 'structuralCents', 'totalCents', 'unknownCents'] as const) cents(summary[key]);
  for (const key of ['monthlyBudgetCents', 'openingBudgetCents', 'availableCents', 'overrunCents', 'safetyCents'] as const) cents(summary[key], !selected.mechanismActive);
  if (selected.pools.length !== POOLS.length || POOLS.some(id => selected.pools.filter(pool => pool.id === id).length !== 1)) {
    throw new Error('快照资金池缺失或重复');
  }
  for (const pool of selected.pools) {
    cents(pool.spentCents);
    for (const key of ['budgetCents', 'carryCents', 'openingCents', 'endingCents', 'availableCents', 'overrunCents'] as const) cents(pool[key], !selected.mechanismActive);
    if (pool.usageRatio !== null && (typeof pool.usageRatio !== 'number' || !Number.isFinite(pool.usageRatio))) {
      throw new Error('快照比例无效');
    }
  }
  for (const category of selected.categories) {
    cents(category.amountCents);
    if (!Number.isSafeInteger(category.count) || category.count < 0) throw new Error('快照分类笔数无效');
  }
  for (const [records, structural] of [[selected.records, false], [selected.structuralRecords, true]] as const) {
    for (const record of records) {
      cents(record.amountCents);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(record.date) || !record.date.startsWith(`${month}-`)
        || new Date(`${record.date}T00:00:00Z`).toISOString().slice(0, 10) !== record.date) {
        throw new Error('快照明细日期无效或不属于所选月份');
      }
      if ((structural ? record.scope !== 'STRUCTURAL' : !['PERSONAL', 'PROJECT'].includes(record.scope))
        || ![...POOLS, 'unknown'].includes(record.pool)) throw new Error('快照明细口径无效');
    }
  }
}

function table(headers: string[], rows: string[][]): string[] {
  return [headers, headers.map(() => '---'), ...rows].map(row => `| ${row.join(' | ')} |`);
}

export function buildAnalysisExport(
  snapshot: Pick<DashboardSnapshot, 'months' | 'generatedAt' | 'sourceLastRecordedAt'>,
  month: string,
): { fileName: string; content: string } {
  if (!/^\d{4}-(?:0[1-9]|1[0-2])$/.test(month)) throw new Error('导出月份无效');
  const matches = snapshot.months.filter(item => item.month === month);
  if (matches.length !== 1) throw new Error('所选月份快照不可用');
  const selected = matches[0];
  validate(selected, month);
  const { summary, mechanismActive } = selected;
  const monthEnd = new Date(`${month}-01T00:00:00Z`);
  monthEnd.setUTCMonth(monthEnd.getUTCMonth() + 1);
  monthEnd.setUTCDate(0);
  const dates = [...selected.records, ...selected.structuralRecords].map(record => record.date).sort();
  const share = (amount: number) => percent(summary.personalCents > 0 ? amount / summary.personalCents : null);
  const budgetMoney = (amount: number | null) => money(mechanismActive ? amount : null);
  const poolNames = new Map(selected.pools.map(pool => [pool.id, text(pool.name)]));
  const details = (records: LedgerRecord[], structural: boolean): string[] => records.length === 0 ? ['暂无记录。']
    : table(['序号', '日期', '事项', '主分类', '资金池', '净额（元）'], records.map((record, index) => [
      String(index + 1), record.date, text(record.title) || '消费记录', text(record.category) || '未填写分类',
      structural ? '不适用' : record.pool === 'unknown' ? '待归类' : poolNames.get(record.pool)!, money(record.amountCents),
    ]));
  const lines = [
    `# ${month} 消费复盘`, '',
    `所属月份：${month}-01 至 ${monthEnd.toISOString().slice(0, 10)}`,
    `本月记录日期范围：${dates.length ? `${dates[0]} 至 ${dates[dates.length - 1]}` : DASH}`,
    `快照生成时间：${timestamp(snapshot.generatedAt)}`,
    `来源最新记录时间：${timestamp(snapshot.sourceLastRecordedAt)}`,
    `本月最新记录时间：${timestamp(selected.lastRecordedAt)}`, '',
    '## 统计口径', '',
    '- 金额单位为元，沿用账本已计算净额；退款及其他负调整保留负号。个人净消费包含 PERSONAL 与 PROJECT。',
    '- 结构性净额及明细单列，不计入个人消费分类、四池消费与预算。待归类单列，不补判分类或分池。',
    '- 消费占比以个人净消费为分母，仅在分母大于零时展示；四池占比不强制合计为 100%。预算使用率沿用快照，仅在期初预算大于零且预算机制启用时展示。',
    '- 期初预算包含滚存。预算、可用额度、安全积累均不代表实际账户现金；预算机制未启用时相关数值显示“—”。',
    '- 本文只反映所选月份的已缓存记录，时间信息不代表来源完整性；不作消费价值判断。', '',
    '## 消费概览', '',
    ...table(['指标', '金额（元）'], [
      ['个人净消费（含项目）', money(summary.personalCents)],
      ['结构性净额', money(summary.structuralCents)], ['合计净额', money(summary.totalCents)],
      ['待归类净消费', money(summary.unknownCents)], ['月度新增预算', budgetMoney(summary.monthlyBudgetCents)],
      ['期初预算（含滚存）', budgetMoney(summary.openingBudgetCents)], ['可用额度', budgetMoney(summary.availableCents)],
      ['超支', budgetMoney(summary.overrunCents)], ['安全积累', budgetMoney(summary.safetyCents)],
    ]), '',
    '## 分类汇总', '',
    ...table(['主分类', '净消费（元）', '笔数', '个人消费占比'], selected.categories.map(category => [
      text(category.name) || '未填写分类', money(category.amountCents), String(category.count), share(category.amountCents),
    ])), '',
    '## 四池与待归类', '',
    ...table(['资金池', '月度预算（元）', '滚存（元）', '期初预算（元）', '净消费（元）', '个人消费占比', '预算使用率', '可用额度（元）', '超支（元）'], [
      ...POOLS.map(id => {
        const pool = selected.pools.find(item => item.id === id)!;
        return [poolNames.get(id)!, budgetMoney(pool.budgetCents), budgetMoney(pool.carryCents), budgetMoney(pool.openingCents),
          money(pool.spentCents), share(pool.spentCents),
          percent(mechanismActive && pool.openingCents !== null && pool.openingCents > 0 ? pool.usageRatio : null),
          budgetMoney(pool.availableCents), budgetMoney(pool.overrunCents)];
      }),
      ['待归类', DASH, DASH, DASH, money(summary.unknownCents), share(summary.unknownCents), DASH, DASH, DASH],
    ]), '',
    '## 个人消费明细（含项目）', '', ...details(selected.records, false), '',
    '## 结构性明细', '', ...details(selected.structuralRecords, true), '',
  ];
  return { fileName: `消费复盘_${month}.md`, content: lines.join('\n') };
}
