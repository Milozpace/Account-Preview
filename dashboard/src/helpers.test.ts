import { describe, expect, it } from 'vitest';
import { calendarOffset, dateState, escapeHtml, money, monthProgress, percent, ratio } from './helpers';

describe('integer cents presentation', () => {
  it('keeps cents exact including negative adjustments', () => {
    expect(money(123456)).toBe('1,234.56');
    expect(money(-1)).toBe('−0.01');
    expect(money(0)).toBe('0.00');
  });
  it('never presents missing money as zero', () => {
    expect(money(null)).toBe('—');
    expect(money(undefined)).toBe('—');
  });
  it('requires integer cents', () => { expect(() => money(1.5)).toThrow(); });
});

describe('budget reference progress', () => {
  it('marks zero or negative denominators unavailable', () => {
    expect(ratio(100, 0)).toBeNull();
    expect(ratio(100, -50)).toBeNull();
    expect(percent(null)).toBe('—');
  });
  it('preserves overspend above 100% and negative net consumption', () => {
    expect(ratio(12500, 10000)).toBe(1.25);
    expect(percent(1.25)).toBe('125%');
    expect(ratio(-1000, 10000)).toBe(-0.1);
    expect(percent(-0.1)).toBe('−10%');
  });
});

describe('calendar boundaries use date-only Asia/Shanghai values', () => {
  it('counts leap month days', () => {
    expect(monthProgress('2024-02', '2024-02-29')).toEqual({ elapsed: 29, total: 29, ratio: 1 });
  });
  it('shows current day inclusive and clamps past/future months', () => {
    expect(monthProgress('2026-10', '2026-10-03')).toEqual({ elapsed: 3, total: 31, ratio: 3 / 31 });
    expect(monthProgress('2026-09', '2026-10-03').ratio).toBe(1);
    expect(monthProgress('2026-11', '2026-10-03').ratio).toBe(0);
  });
  it('aligns the real month calendar with Monday first', () => {
    expect(calendarOffset('2026-10')).toBe(3);
    expect(calendarOffset('2026-02')).toBe(6);
  });
  it('distinguishes future, recorded zero net, and unrecorded dates', () => {
    expect(dateState('2026-10-04', 0, '2026-10-03')).toBe('future');
    expect(dateState('2026-10-03', 0, '2026-10-03')).toBe('unrecorded');
    expect(dateState('2026-10-02', 2, '2026-10-03')).toBe('recorded');
  });
});

describe('safe tooltip and detail text', () => {
  it('escapes markup and attribute delimiters', () => {
    expect(escapeHtml('<img src="x" onerror=\'bad\'> &')).toBe('&lt;img src=&quot;x&quot; onerror=&#39;bad&#39;&gt; &amp;');
  });
});
