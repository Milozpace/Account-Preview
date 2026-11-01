"""App V2 ledger-view snapshot export.

Reuses the web V1 product of one native Excel recalculation; adds the
versioned `account-ledger-view` envelope with a tracked-months coverage
declaration. Contract: docs/App真实快照契约V2.md. This module never talks
to the network; publishing belongs to the native App publisher.
"""
from __future__ import annotations

import calendar
import json
import math
import os
import re
import subprocess
import uuid
from collections import defaultdict
from pathlib import Path

import account
import dashboard_data
import ledger_source

SCHEMA_VERSION = 2
SNAPSHOT_KIND = 'account-ledger-view'
COVERAGE_KIND = 'tracked-months'
LEGACY_COVERAGE_KIND = 'recent-months'
POOL_IDS = set(dashboard_data.POOL_IDS.values()) | {'unknown'}
FIXED_POOL_IDS = set(dashboard_data.POOL_IDS.values())
MONTH_RE = re.compile(r'^\d{4}-\d{2}$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
DEFAULT_OUTPUT = Path('.runtime') / 'ledger' / 'snapshot-v2.json'

# Every cents value is consumed by JavaScript display code, so the whole
# aggregate chain must stay inside the JS safe-integer range.
SAFE_CENTS_LIMIT = 2 ** 53 - 1

TOP_FIELDS = {'schemaVersion', 'snapshotKind', 'sourceHash', 'calendarMonth',
              'generatedAt', 'sourceLastRecordedAt', 'ledgerCount', 'coverage', 'months'}
MONTH_FIELDS = {'month', 'lastRecordedAt', 'mechanismActive', 'summary', 'pools',
                'daily', 'categories', 'records', 'structuralRecords', 'topIds'}
SUMMARY_FIELDS = {'personalCents', 'structuralCents', 'totalCents', 'unknownCents',
                  'monthlyBudgetCents', 'openingBudgetCents', 'availableCents',
                  'overrunCents', 'safetyCents'}
POOL_FIELDS = {'id', 'name', 'budgetCents', 'carryCents', 'openingCents', 'spentCents',
               'endingCents', 'availableCents', 'overrunCents', 'usageRatio'}
DAILY_FIELDS = {'date', 'amountCents', 'operationCumulativeCents', 'referenceCents', 'count'}
CATEGORY_FIELDS = {'name', 'amountCents', 'count'}


def content_signature(snapshot):
    """Normalized bytes without generatedAt: retries and re-exports of the
    same business content must never manufacture a new publish version."""
    value = {key: item for key, item in snapshot.items() if key != 'generatedAt'}
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _previous_month(month):
    year, number = map(int, month.split('-'))
    return f'{year - (number == 1)}-{(number - 1) or 12:02d}'


def _cents(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{label}必须是安全整数分')
    if abs(value) > SAFE_CENTS_LIMIT:
        raise ValueError(f'{label}超出安全整数范围')
    return value


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 \
            or value > SAFE_CENTS_LIMIT:
        raise ValueError(f'{label}必须是非负安全整数')
    return value


def _timestamp(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) < 20 or not value.endswith('+08:00'):
        raise ValueError(f'{label}必须是+08:00时区时间')
    return value


def _check_record(item, month, seen, label):
    if not isinstance(item, dict):
        raise ValueError(f'{label}记录必须是对象')
    identity = item.get('id')
    if not isinstance(identity, str) or not identity or identity in seen:
        raise ValueError(f'{month}记录ID缺失或重复')
    seen.add(identity)
    if set(item) != {'id', 'time', 'date', 'title', 'category', 'pool', 'scope', 'amountCents'}:
        raise ValueError(f'{month}记录字段不符合白名单')
    if not DATE_RE.match(str(item['date'])) or not str(item['date']).startswith(month + '-'):
        raise ValueError(f'{month}记录日期越界')
    if not str(item['time']).startswith(str(item['date'])) \
            or not str(item['time']).endswith('+08:00'):
        raise ValueError(f'{month}记录时间与日期不一致或缺少时区')
    if not isinstance(item['title'], str) or not item['title'] or len(item['title']) > 72:
        raise ValueError(f'{month}记录标题缺失或超长')
    if not isinstance(item['category'], str) or not item['category']:
        raise ValueError(f'{month}记录分类缺失')
    if item['pool'] not in POOL_IDS:
        raise ValueError(f'{month}记录资金池越界')
    if item['scope'] not in ('PERSONAL', 'PROJECT', 'STRUCTURAL'):
        raise ValueError(f'{month}记录口径越界')
    return _cents(item['amountCents'], f'{month}记录金额')


def _check_pools(view, month, pool_used):
    pools = view.get('pools')
    if not isinstance(pools, list) or len(pools) != 4:
        raise ValueError(f'{month}资金池必须为四池结构')
    seen_ids = set()
    for pool in pools:
        if not isinstance(pool, dict) or set(pool) != POOL_FIELDS:
            raise ValueError(f'{month}资金池字段不符合白名单')
        identity = pool['id']
        if identity not in FIXED_POOL_IDS or identity in seen_ids:
            raise ValueError(f'{month}资金池ID无效或重复')
        seen_ids.add(identity)
        if not isinstance(pool['name'], str) or not pool['name']:
            raise ValueError(f'{month}资金池名称缺失')
        for field in ('budgetCents', 'carryCents', 'openingCents', 'spentCents',
                      'endingCents', 'availableCents', 'overrunCents'):
            _cents(pool[field], f'{month}资金池{field}')
        ratio = pool['usageRatio']
        if pool['openingCents'] > 0:
            # Refund months legitimately produce negative ratios; only
            # non-finite values are corrupted.
            if not isinstance(ratio, float) or not math.isfinite(ratio):
                raise ValueError(f'{month}资金池使用率无效')
        elif ratio is not None:
            raise ValueError(f'{month}资金池无期初预算时使用率必须为null')
        if pool['spentCents'] != pool_used[identity]:
            raise ValueError(f'{month}资金池已用与流水对账失败')
    if seen_ids != FIXED_POOL_IDS:
        raise ValueError(f'{month}四池结果不完整')


def _check_daily(view, month, by_date, by_count):
    daily = view.get('daily')
    if not isinstance(daily, list):
        raise ValueError(f'{month}日消费缺失')
    year, number = map(int, month.split('-'))
    expected_days = {f'{month}-{day:02d}'
                     for day in range(1, calendar.monthrange(year, number)[1] + 1)}
    if len(daily) != len(expected_days):
        raise ValueError(f'{month}日消费必须覆盖完整月历')
    seen_days = set()
    for entry in daily:
        if not isinstance(entry, dict) or set(entry) != DAILY_FIELDS:
            raise ValueError(f'{month}日消费字段不符合白名单')
        label = entry['date']
        if not isinstance(label, str) or label not in expected_days or label in seen_days:
            raise ValueError(f'{month}日消费日期越界或重复')
        seen_days.add(label)
        _cents(entry['amountCents'], f'{month}日消费金额')
        _cents(entry['operationCumulativeCents'], f'{month}日累计金额')
        _cents(entry['referenceCents'], f'{month}日参考金额')
        _count(entry['count'], f'{month}日消费笔数')
        if entry['amountCents'] != by_date.get(label, 0) \
                or entry['count'] != by_count.get(label, 0):
            raise ValueError(f'{month}日消费与明细对账失败')


def _check_categories(view, month, personal, by_category, by_category_count):
    categories = view.get('categories')
    if not isinstance(categories, list):
        raise ValueError(f'{month}分类消费缺失')
    total = 0
    seen_names = set()
    for item in categories:
        if not isinstance(item, dict) or set(item) != CATEGORY_FIELDS:
            raise ValueError(f'{month}分类字段不符合白名单')
        name = item['name']
        if not isinstance(name, str) or not name or name in seen_names:
            raise ValueError(f'{month}分类名称缺失或重复')
        seen_names.add(name)
        amount = _cents(item['amountCents'], f'{month}分类金额')
        _count(item['count'], f'{month}分类笔数')
        if amount != by_category.get(name, 0) or item['count'] != by_category_count.get(name, 0):
            raise ValueError(f'{month}分类消费与明细对账失败')
        total += amount
    if total != personal:
        raise ValueError(f'{month}分类合计与个人净额对账失败')


def validate_app_snapshot(snapshot):
    """Contract/reconciliation gate before any cache write. This validates
    the exported view; it never re-derives budget or classification."""
    if not isinstance(snapshot, dict):
        raise ValueError('快照必须是对象')
    if set(snapshot) != TOP_FIELDS:
        raise ValueError('快照字段不符合白名单')
    if snapshot.get('schemaVersion') != SCHEMA_VERSION:
        raise ValueError('快照不是App V2契约；已拒绝')
    if snapshot.get('snapshotKind') != SNAPSHOT_KIND:
        raise ValueError('快照类型不是account-ledger-view；已拒绝')
    if not isinstance(snapshot['sourceHash'], str) or not snapshot['sourceHash']:
        raise ValueError('来源哈希缺失')
    _timestamp(snapshot['generatedAt'], '生成时间')
    _timestamp(snapshot['sourceLastRecordedAt'], '来源最新记录时间')
    calendar_month = snapshot['calendarMonth']
    if not isinstance(calendar_month, str) or not MONTH_RE.match(calendar_month):
        raise ValueError('日历月份格式无效')
    year, number = map(int, calendar_month.split('-'))
    if not 1 <= year <= 9999 or not 1 <= number <= 12:
        raise ValueError('日历月份格式无效')
    ledger_count = snapshot['ledgerCount']
    if isinstance(ledger_count, bool) or not isinstance(ledger_count, int) \
            or ledger_count < 0 or ledger_count > SAFE_CENTS_LIMIT:
        raise ValueError('ledgerCount必须是来源总笔数整数')
    months = snapshot['months']
    if not isinstance(months, list) or not months:
        raise ValueError('快照必须包含至少一个月份')
    coverage = snapshot['coverage']
    if not isinstance(coverage, dict) or coverage.get('kind') not in (COVERAGE_KIND, LEGACY_COVERAGE_KIND):
        raise ValueError('coverage必须是tracked-months或兼容的recent-months范围声明')
    if coverage.get('months') != [item.get('month') for item in months]:
        raise ValueError('coverage月份与实际months不一致')
    month_list = [view.get('month') for view in months]
    if coverage['kind'] == LEGACY_COVERAGE_KIND:
        # Installed clients may still hold the old authenticated two-month cache.
        if month_list != [calendar_month, _previous_month(calendar_month)]:
            raise ValueError('旧版快照必须正好覆盖当月与上月')
    elif month_list != dashboard_data.display_months(calendar_month, month_list[-1]):
        # The source exporter determines the owner's oldest month; this envelope
        # provides no independent start metadata, so only continuity is provable.
        raise ValueError('快照必须从当月连续覆盖至声明的最早月份')
    seen = set()
    exported = 0
    for view in months:
        if not isinstance(view, dict) or set(view) != MONTH_FIELDS:
            raise ValueError('月份视图字段不符合白名单')
        month = view['month']
        if not isinstance(month, str) or not MONTH_RE.match(month):
            raise ValueError('月份格式无效')
        _timestamp(view['lastRecordedAt'], f'{month}最新记录时间')
        if not isinstance(view['mechanismActive'], bool):
            raise ValueError(f'{month}机制标记必须是布尔值')
        summary = view.get('summary')
        if not isinstance(summary, dict) or set(summary) != SUMMARY_FIELDS:
            raise ValueError(f'{month}汇总字段不符合白名单')
        records = view.get('records')
        structural = view.get('structuralRecords')
        if not isinstance(records, list) or not isinstance(structural, list):
            raise ValueError(f'{month}明细缺失')
        personal = sum(_check_record(item, month, seen, '个人') for item in records)
        structural_total = sum(_check_record(item, month, seen, '结构性') for item in structural)
        exported += len(records) + len(structural)
        if _cents(summary['personalCents'], f'{month}个人净额') != personal:
            raise ValueError(f'{month}个人净额与明细对账失败')
        if _cents(summary['structuralCents'], f'{month}结构性净额') != structural_total:
            raise ValueError(f'{month}结构性净额与明细对账失败')
        if _cents(summary['totalCents'], f'{month}总净额') != personal + structural_total:
            raise ValueError(f'{month}总净额与分项对账失败')
        for field in ('monthlyBudgetCents', 'openingBudgetCents', 'availableCents',
                      'overrunCents', 'safetyCents'):
            _cents(summary[field], f'{month}汇总{field}')
        by_date, by_count = {}, {}
        by_category, by_category_count = defaultdict(int), defaultdict(int)
        pool_used = defaultdict(int)
        for item in records:
            by_date[item['date']] = by_date.get(item['date'], 0) + item['amountCents']
            by_count[item['date']] = by_count.get(item['date'], 0) + 1
            by_category[item['category']] += item['amountCents']
            by_category_count[item['category']] += 1
            pool_used[item['pool']] += item['amountCents']
        if _cents(summary['unknownCents'], f'{month}待归类净额') != pool_used['unknown']:
            raise ValueError(f'{month}待归类净额与明细对账失败')
        _check_pools(view, month, pool_used)
        if _cents(summary['monthlyBudgetCents'], f'{month}月预算') \
                != sum(p['budgetCents'] for p in view['pools']):
            raise ValueError(f'{month}月预算与四池对账失败')
        if _cents(summary['openingBudgetCents'], f'{month}期初预算') \
                != sum(p['openingCents'] for p in view['pools']):
            raise ValueError(f'{month}期初预算与四池对账失败')
        if _cents(summary['availableCents'], f'{month}可用额度') \
                != sum(p['availableCents'] for p in view['pools']):
            raise ValueError(f'{month}可用额度与四池对账失败')
        if _cents(summary['overrunCents'], f'{month}超支额') \
                != sum(p['overrunCents'] for p in view['pools']):
            raise ValueError(f'{month}超支额与四池对账失败')
        _check_daily(view, month, by_date, by_count)
        _check_categories(view, month, personal, by_category, by_category_count)
        top = view.get('topIds')
        if not isinstance(top, list) or len(top) > 10:
            raise ValueError(f'{month}重点记录数量无效')
        if len(set(top)) != len(top) or any(item not in {r['id'] for r in records} for item in top):
            raise ValueError(f'{month}重点记录引用无效')
    if ledger_count < exported:
        raise ValueError('ledgerCount不能小于已导出明细笔数')
    if len(seen) != exported:
        raise ValueError('记录ID跨月重复')
    return snapshot


def ensure_web_snapshot(workbook):
    """Current V1 from disk, or one fresh native recalculation whose on-disk
    product is re-read afterwards. The sync status summary is never mistaken
    for the snapshot itself; one Excel pass serves both presentations."""
    web = dashboard_data.read_snapshot(workbook)
    if dashboard_data.snapshot_current(workbook, web):
        return web, {'reused': True}
    dashboard_data.sync_snapshot(workbook)
    web = dashboard_data.read_snapshot(workbook)
    if not dashboard_data.snapshot_current(workbook, web):
        raise RuntimeError('同步完成后未读到新鲜的V1快照；已保留现场，请检查dashboard.json')
    return web, {'reused': False}


def build_app_snapshot(workbook):
    web, reuse = ensure_web_snapshot(workbook)
    snapshot = {key: web[key] for key in ('sourceHash', 'calendarMonth', 'generatedAt',
                                          'sourceLastRecordedAt', 'ledgerCount', 'months')}
    snapshot['schemaVersion'] = SCHEMA_VERSION
    snapshot['snapshotKind'] = SNAPSHOT_KIND
    snapshot['coverage'] = {'kind': COVERAGE_KIND,
                            'months': [view['month'] for view in snapshot['months']]}
    validate_app_snapshot(snapshot)
    return snapshot, reuse


def _reject_output_overwriting_source(workbook, output):
    """Refuse any export whose target resolves to the source workbook, before
    opening any writable output flow."""
    source = Path(workbook).resolve()
    target = Path(output).resolve()
    root=ledger_source.project_root(workbook)
    protected=[source,root/'个人财物工作簿.xlsx',root/'data'/'source.json',
               root/'.runtime'/'ledger'/'publish.json']
    for path in protected:
        if os.path.normcase(str(target))==os.path.normcase(str(path.resolve())):
            raise ValueError('输出不能覆盖源工作簿、数据库或账本配置')
        if target.exists() and path.exists() and os.path.samefile(target,path):
            raise ValueError('输出不能覆盖源工作簿、数据库或账本配置的别名')
    if target.suffix.lower()!='.json':
        raise ValueError('App快照输出必须为.json，不能覆盖源工作簿或数据库')
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        raise ValueError('输出路径与源工作簿相同，已拒绝导出以保护来源。')
    try:
        if target.exists() and source.exists() and os.path.samefile(target, source):
            raise ValueError('输出路径解析后指向源工作簿，已拒绝导出以保护来源。')
    except OSError:
        pass


def _verify_current_source(workbook, snapshot):
    ledger_source.assert_active(workbook)
    state = None
    if ledger_source.is_database(workbook):
        import ledger_store
        state = ledger_store.read_state(workbook)
        source_hash = state['sourceHash']
    else:
        source_hash = ledger_source.source_hash(workbook)
    if source_hash != snapshot['sourceHash']:
        raise RuntimeError('导出期间工作簿已变化，已拒绝写入并保留旧App快照。')
    if dashboard_data.current_month() != snapshot['calendarMonth']:
        raise RuntimeError('导出期间已跨月，已拒绝写入并保留旧App快照。')
    if state is not None:
        months = dashboard_data.source_tracking_months(workbook, state, snapshot['calendarMonth'])
        if [view['month'] for view in snapshot['months']] != months:
            raise RuntimeError('导出期间展示配置已变化，已拒绝写入并保留旧App快照。')


def export_snapshot(workbook, output=None, write=True):
    """Validated App V2 snapshot; atomic write unless dry-run. Failures and
    stale sources never touch the previous snapshot file."""
    snapshot, reuse = build_app_snapshot(workbook)
    path = Path(output) if output else ledger_source.project_root(workbook) / DEFAULT_OUTPUT
    if write:
        _reject_output_overwriting_source(workbook, path)
    _verify_current_source(workbook, snapshot)
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique per-attempt draft: concurrent exports must never share (and
        # thereby corrupt) one fixed draft file.
        draft = path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:12]}.draft')
        try:
            draft.write_text(payload, encoding='utf-8')
            # Writing/serializing the draft may overlap an Excel save or a
            # month boundary. Revalidate at the final replacement boundary.
            _verify_current_source(workbook, snapshot)
            os.replace(draft, path)
        finally:
            if draft.exists():
                draft.unlink()
    summary = {'ok': True, 'output': str(path), 'dryRun': not write, 'schemaVersion': SCHEMA_VERSION,
               'snapshotKind': SNAPSHOT_KIND, 'months': snapshot['coverage']['months'],
               'calendarMonth': snapshot['calendarMonth'], 'ledgerCount': snapshot['ledgerCount'],
               'exportedRecords': sum(len(v['records']) + len(v['structuralRecords'])
                                      for v in snapshot['months']),
               'bytes': len(payload.encode('utf-8')), 'webSnapshotReused': reuse['reused'],
               'sourceHash': snapshot['sourceHash']}
    return summary


def publish_gate(runtime_dir):
    """Auto-publish trigger file. This is the SECOND gate: the native
    publisher independently enforces its own DPAPI gate for every upload.
    Enabling auto-publish therefore requires BOTH: `app.exe --enable-publish`
    (native master switch) and this file. With only this file present the
    post-processing calls `--publish`, which is refused by the native gate
    and reported as a failure — never silently treated as published."""
    path = Path(runtime_dir) / 'ledger' / 'publish.json'
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'enabled': False, 'reason': '未配置发布开关，已跳过App发布。'}
    if not isinstance(config, dict) or config.get('enabled') is not True:
        return {'enabled': False, 'reason': 'App发布未启用，已跳过。'}
    from runtime_tools import helper_path
    publisher = config.get('publisher')
    if publisher is None:
        try: publisher = str(helper_path(Path(runtime_dir).parent, 'ledger_cli'))
        except ValueError: return {'enabled':False, 'reason':'原生组件清单无效，请检查。'}
    if not isinstance(publisher, str) or not publisher.strip():
        return {'enabled': False, 'reason': '发布开关缺少publisher命令，已跳过。'}
    return {'enabled': True, 'publisher': publisher.strip(), 'snapshot': str(path.parent / 'snapshot-v2.json')}


def prepare_after_dashboard(workbook, result):
    """Accounting-success post-processing: prepare the App snapshot after the
    web snapshot succeeded, then (only when the auto-publish trigger file is
    enabled) hand it to the native publisher. A failure here is reported
    separately and never relabels a successful ledger import. `appPublish.ok`
    is true only when the cloud readback confirmed the upload; staging alone
    or a refusal by the native gate is reported as not published."""
    try:
        result['appSnapshot'] = export_snapshot(workbook)
        gate = publish_gate(ledger_source.runtime(workbook))
        if gate['enabled']:
            try:
                completed = subprocess.run([gate['publisher'], '--publish', gate['snapshot']],
                                           capture_output=True, encoding='utf-8', errors='replace',
                                           timeout=300, cwd=str(ledger_source.project_root(workbook)),
                                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                published = completed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                published = False
            result['appPublish'] = {'ok': published, 'published': published,
                                    'error': None if published else '原生发布未确认，请检查发布状态后重试。'}
        else:
            result['appPublish'] = {'ok': None, 'published': False, 'skipped': True, 'reason': gate['reason']}
    except Exception as error:
        result['appSnapshot'] = {'ok': False, 'error': str(error), 'previousSnapshotPreserved': True}
    return result
