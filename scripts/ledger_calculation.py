"""Deterministic ledger calculations, in cents, independent of Excel at runtime.

The approved workbook's formulas define version 1: operation/free reset monthly,
travel/growth retain signed carries, and only closed months add operation savings.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import calendar
from decimal import Decimal

import account

CALCULATION_VERSION = 'sqlite-ledger-calculation-v1'
OBSERVATION_MODE = 'zero-observation-v1'
OBSERVATION_CALCULATION_VERSION = CALCULATION_VERSION + '-zero-observation-v1'


def calculation_version(state):
    return OBSERVATION_CALCULATION_VERSION if state.get('budgetMode') == OBSERVATION_MODE else CALCULATION_VERSION
POOL_IDS = dict(zip(account.POOLS, ['operation', 'free', 'travel', 'growth']))


def month_key(value):
    """Accept a month or an actual date, and reject invalid calendar months."""
    raw = str(value) if isinstance(value, str) else account.timestamp(value)
    key = raw[:7]
    if len(key) != 7 or key[4] != '-' or not key[:4].isdigit() or not key[5:].isdigit():
        raise ValueError('月份必须为YYYY-MM')
    date.fromisoformat(key + '-01')
    return key


def cents(value):
    return int(account.money(value) * 100)


def _next_month(month):
    year, number = map(int, month.split('-'))
    return f'{year + number // 12:04d}-{number % 12 + 1:02d}'


def aggregate(rows):
    """Use exactly the display contract's approved PERSONAL/PROJECT scopes."""
    months = {}
    for row in rows:
        month = month_key(account.timestamp(row['交易时间']))
        item = months.setdefault(month, {'personal': 0, 'structural': 0, 'total': 0,
                                         'unknown': 0, 'pools': dict.fromkeys(account.POOLS, 0)})
        amount = cents(row.get('计入消费金额'))
        scope = account.text(row.get('统计口径')).upper()
        if amount and scope not in ('PERSONAL', 'PROJECT', 'STRUCTURAL'):
            raise ValueError('非零消费仅支持PERSONAL、PROJECT或STRUCTURAL口径')
        if scope in ('PERSONAL', 'PROJECT'):
            item['personal'] += amount
            pool = account.text(row.get('资金池'))
            if pool in account.POOLS:
                item['pools'][pool] += amount
            else:
                item['unknown'] += amount
        elif scope == 'STRUCTURAL':
            item['structural'] += amount
        item['total'] = item['personal'] + item['structural']
    return months


def calculate_month(state, month, calendar_month):
    """Return the native-shaped calculation contract consumed by build_month."""
    month, calendar_month = month_key(month), month_key(calendar_month)
    rates = sorted((month_key(r['生效月份']), {p: cents(r[p]) for p in account.POOLS}) for r in state['rates'])
    if not rates or len({m for m, _ in rates}) != len(rates):
        raise ValueError('预算生效月份缺失或重复')
    if any(v < 0 for _, values in rates for v in values.values()):
        raise ValueError('月度预算不可为负数')
    amounts = aggregate(state['rows'])
    selected = amounts.get(month, {'personal': 0, 'structural': 0, 'total': 0,
                                  'unknown': 0, 'pools': dict.fromkeys(account.POOLS, 0)})
    start = rates[0][0]
    if state.get('budgetMode') == OBSERVATION_MODE and not any(cents(v) for v in state['initial'].values()):
        # New owners may classify expenses before setting any allowance. Only
        # the first positive rate starts budget accounting; earlier spending
        # must not manufacture debt or savings. Existing unmarked ledgers keep
        # the legacy calculation, including deliberately zero rates.
        start = next((effective for effective, values in rates if any(values.values())), None)
    active = start is not None and month >= start
    carry = {p: cents(state['initial'].get(p)) if p in account.POOLS[2:] else 0 for p in account.POOLS}
    safety = 0
    budget = dict.fromkeys(account.POOLS, 0)
    opening = dict.fromkeys(account.POOLS, 0)
    ending = dict.fromkeys(account.POOLS, 0)
    cursor = start
    while cursor is not None and cursor <= month:
        budget = next(values for effective, values in reversed(rates) if effective <= cursor)
        used = amounts.get(cursor, {}).get('pools', {})
        opening = {p: carry[p] + budget[p] for p in account.POOLS}
        ending = {p: opening[p] - used.get(p, 0) for p in account.POOLS}
        if cursor < calendar_month:
            safety += max(0, budget[account.POOLS[0]] - used.get(account.POOLS[0], 0))
        if cursor == month:
            break
        carry = {p: ending[p] if p in account.POOLS[2:] else 0 for p in account.POOLS}
        cursor = _next_month(cursor)
    if not active:
        carry = dict.fromkeys(account.POOLS, 0)
    pools = []
    for pool in account.POOLS:
        pools.append(dict(id=POOL_IDS[pool], name=pool, budgetCents=budget[pool],
                          carryCents=carry[pool], openingCents=opening[pool],
                          spentCents=selected['pools'][pool], endingCents=ending[pool],
                          availableCents=max(0, ending[pool]), overrunCents=max(0, -ending[pool]),
                          usageRatio=selected['pools'][pool] / opening[pool] if opening[pool] > 0 else None))
    daily_amount = defaultdict(int)
    operation_amount = defaultdict(int)
    categories = defaultdict(int)
    for row in state['rows']:
        instant = account.timestamp(row['交易时间'])
        if instant[:7] != month or account.text(row.get('统计口径')).upper() not in ('PERSONAL', 'PROJECT'):
            continue
        amount = cents(row.get('计入消费金额'))
        daily_amount[instant[:10]] += amount
        if account.text(row.get('资金池')) == account.POOLS[0]:
            operation_amount[instant[:10]] += amount
        categories[account.text(row.get('主分类')) or '未填写分类'] += amount
    days = calendar.monthrange(*map(int, month.split('-')))[1]
    daily = {}
    cumulative = 0
    for number in range(1, days + 1):
        label = f'{month}-{number:02d}'
        cumulative += operation_amount[label]
        # Excel uses binary floating point before the existing exporter rounds.
        reference = cents(float(Decimal(budget[account.POOLS[0]]) / 100) * number / days)
        daily[label] = dict(amountCents=daily_amount[label], operationCumulativeCents=cumulative,
                            referenceCents=reference)
    summary = {key + 'Cents': selected[key] for key in ('personal', 'structural', 'total', 'unknown')}
    summary.update(safetyCents=safety, monthlyBudgetCents=sum(budget.values()),
                   openingBudgetCents=sum(opening.values()), availableCents=sum(p['availableCents'] for p in pools),
                   overrunCents=sum(p['overrunCents'] for p in pools))
    return dict(summary=summary, pools=pools, daily=daily, categories=dict(categories), mechanismActive=active)
