export function money(cents: number | null | undefined): string {
  if (cents == null) return '—';
  if (!Number.isSafeInteger(cents)) throw new TypeError('金额必须是安全整数分');
  const absolute = Math.abs(cents);
  return `${cents < 0 ? '−' : ''}${Math.floor(absolute / 100).toLocaleString('zh-CN')}.${String(absolute % 100).padStart(2, '0')}`;
}

export function ratio(spent: number, budget: number): number | null {
  return budget > 0 ? spent / budget : null;
}

export function percent(value: number | null): string {
  return value == null ? '—' : `${value < 0 ? '−' : ''}${Math.round(Math.abs(value) * 100)}%`;
}

export function monthProgress(month: string, today: string): { elapsed: number; total: number; ratio: number } {
  const [year, monthNumber] = month.split('-').map(Number);
  const total = new Date(Date.UTC(year, monthNumber, 0)).getUTCDate();
  const todayMonth = today.slice(0, 7);
  const elapsed = todayMonth < month ? 0 : todayMonth > month ? total : Math.min(total, Math.max(0, Number(today.slice(8, 10))));
  return { elapsed, total, ratio: elapsed / total };
}

export function calendarOffset(month: string): number {
  const [year, monthNumber] = month.split('-').map(Number);
  return (new Date(Date.UTC(year, monthNumber - 1, 1)).getUTCDay() + 6) % 7;
}

export function dateState(date: string, count: number, today: string): 'future' | 'recorded' | 'unrecorded' {
  return date > today ? 'future' : count > 0 ? 'recorded' : 'unrecorded';
}

export function escapeHtml(value: unknown): string {
  const entities: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value ?? '').replace(/[&<>"']/g, (character) => entities[character]);
}

export function monthTitle(month: string): string {
  return `${Number(month.slice(0, 4))} 年 ${Number(month.slice(5))} 月`;
}

export function dateTitle(date: string | null, includeTime = false): string {
  if (!date) return '暂无记录';
  const value = date.slice(0, 10).replaceAll('-', '.');
  return includeTime ? `${value} ${date.slice(11, 16)}`.trim() : value;
}

export function shanghaiToday(): string {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

export function boundedWidth(value: number | null): number {
  return value == null ? 0 : Math.min(100, Math.max(0, value * 100));
}
