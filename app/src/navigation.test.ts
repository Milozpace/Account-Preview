import { describe, it, expect } from 'vitest';
import { Navigation, emptyQuery, selectBills, groupDays } from './navigation';
import fixture from '../../tests/fixtures/app-v2-snapshot.json';
import type { AppMonth } from './bills';

describe('mobile navigation and return context', () => {
  it('returns from a category bill and record to the analysis context with scroll and filters', () => {
    const nav = new Navigation();
    nav.selectRoot('analysis', 120);
    nav.open({ view: 'bills', query: { ...emptyQuery(), category: '餐饮' } }, 430);
    nav.open({ view: 'detail', recordId: 'one' }, 210);
    expect(nav.back()).toBe(true);
    expect(nav.current.view).toBe('bills');
    expect(nav.current.query?.category).toBe('餐饮');
    expect(nav.current.scroll).toBe(210);
    nav.back();
    expect(nav.current.view).toBe('analysis');
    expect(nav.current.scroll).toBe(430);
    expect(nav.back()).toBe(false);
  });
  it('keeps each bottom tab stack and its scroll independently', () => {
    const nav = new Navigation();
    nav.selectRoot('my', 320);
    nav.open({ view: 'bills', query: { ...emptyQuery(), query: '咖啡' } }, 30);
    nav.selectRoot('home', 190);
    expect(nav.current.scroll).toBe(320);
    nav.selectRoot('my', 400);
    expect(nav.current.view).toBe('bills');
    expect(nav.current.scroll).toBe(190);
    expect(nav.current.query?.query).toBe('咖啡');
  });
  it('returns to a tab root when its active bottom item is tapped again', () => {
    const nav=new Navigation();nav.open({view:'bills',query:emptyQuery()},90);
    nav.selectRoot('home',200);
    expect(nav.current.view).toBe('home');
    expect(nav.current.scroll).toBe(90);
  });
  it('restores a browser-history entry across root tabs without losing nested filters',()=>{
    const nav=new Navigation();nav.selectRoot('my',100);
    nav.open({view:'bills',query:{...emptyQuery(),query:'午餐'}},20);nav.save(180);
    const entry=nav.snapshot();
    nav.selectRoot('analysis',180);
    expect(nav.restore(entry)).toBe(true);
    expect(nav.root).toBe('my');expect(nav.current.view).toBe('bills');
    expect(nav.current.query?.query).toBe('午餐');expect(nav.current.scroll).toBe(180);
    expect(nav.restore({root:'invalid'})).toBe(false);
    expect(nav.current.query?.query).toBe('午餐');
  });
  it('sanitizes restored day filters against the shared month while keeping other filters',()=>{
    const nav=new Navigation();nav.open({view:'bills',query:{...emptyQuery(),date:'2026-10-02',category:'餐饮'}});
    const entry=nav.snapshot();nav.resetDates();nav.restore(entry);nav.restrictMonth('2026-09');
    expect(nav.current.query?.date).toBe('');expect(nav.current.query?.category).toBe('餐饮');
  });
  it('clears unavailable category and date in every tab stack, preserving useful search and pool',()=>{
    const nav=new Navigation(),month=fixture.months[0] as unknown as AppMonth;
    nav.open({view:'bills',query:{...emptyQuery(),category:'不存在的分类',date:'1900-01-01',query:'午餐',pool:'operation'}});
    nav.selectRoot('my');nav.open({view:'bills',query:{...emptyQuery(),category:month.records[0].category}});
    nav.reconcile(month,new Set(month.records.map(r=>r.id)));
    expect(nav.current.query?.category).toBe(month.records[0].category);
    nav.selectRoot('home');
    expect(nav.current.query).toEqual({...emptyQuery(),query:'午餐',pool:'operation'});
  });
  it('retains a covered detail across months and prunes it when the record leaves coverage',()=>{
    const nav=new Navigation(),month=fixture.months[0] as unknown as AppMonth;
    nav.open({view:'bills',query:emptyQuery()},20);nav.open({view:'detail',recordId:'covered-record'},180);
    nav.reconcile(month,new Set(['covered-record']));expect(nav.current.view).toBe('detail');
    nav.reconcile(month,new Set());expect(nav.current.view).toBe('bills');expect(nav.current.scroll).toBe(180);
  });
});

describe('bill query uses one consistent result set', () => {
  const month = fixture.months[0] as unknown as AppMonth;
  it('combines date, category, pool and search without mutating the snapshot', () => {
    const record = month.records[0];
    const before = JSON.stringify(month);
    const found = selectBills(month, { ...emptyQuery(), date: record.date, category: record.category, pool: record.pool, query: record.title });
    expect(found.map(r => r.id)).toContain(record.id);
    expect(selectBills(month, { ...emptyQuery(), date: '1900-01-01' })).toEqual([]);
    expect(JSON.stringify(month)).toBe(before);
  });
  it('searches and filters structural records too, without mixing their subtotal into consumption', () => {
    const first = month.structuralRecords[0];
    const query = { ...emptyQuery(), scope: 'structural' as const, query: first.title };
    const result = selectBills(month, query);
    expect(result.length).toBeGreaterThan(0);
    expect(result.every(r => r.scope === 'STRUCTURAL')).toBe(true);
    expect(selectBills(month, { ...query, query: 'certainly-no-such-record' })).toEqual([]);
  });
  it('groups the sorted result by day with exact integer cent subtotals', () => {
    const records = selectBills(month, emptyQuery());
    const groups = groupDays(records);
    expect(groups.flatMap(g => g.records)).toEqual(records);
    expect(groups.reduce((sum,g) => sum + g.cents,0)).toBe(records.reduce((sum,r) => sum + r.amountCents,0));
  });
  it('lists only income records when the income scope is selected', () => {
    const first = month.incomeRecords[0];
    expect(first).toBeDefined();
    const result = selectBills(month, {...emptyQuery(), scope: 'income'});
    expect(result.map(r => r.id)).toEqual(month.incomeRecords.map(r => r.id));
    expect(result.every(r => r.scope === 'INCOME')).toBe(true);
    expect(selectBills(month, {...emptyQuery(), scope: 'income', query: first.title}).map(r => r.id)).toContain(first.id);
  });
  it('drops a pool filter that cannot apply to income or structural lists', () => {
    const nav = new Navigation();
    nav.open({view: 'bills', query: {...emptyQuery(), scope: 'income', pool: 'operation'}});
    nav.reconcile(month, new Set(month.incomeRecords.map(r => r.id)));
    expect(nav.current.query?.pool).toBe('');
  });
});
