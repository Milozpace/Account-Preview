"""Validated disposable display snapshots; Excel remains the calculation engine."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, date, timedelta
import calendar
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import openpyxl
import account
import ledger_source

SCHEMA_VERSION = 2
POOL_IDS = dict(zip(account.POOLS, ['operation', 'free', 'travel', 'growth']))


def current_month():
    return datetime.now(account.TZ).strftime('%Y-%m')


def display_months(month, start_month=None):
    """Descending inclusive range; old Excel callers retain their two-month window."""
    first = date.fromisoformat(month + '-01')
    if start_month is None:
        start_month = (first-timedelta(days=1)).isoformat()[:7]
    date.fromisoformat(start_month + '-01')
    if start_month > month:
        raise ValueError('展示起始月份不能晚于日历月份')
    months = []
    while True:
        selected = first.isoformat()[:7]
        months.append(selected)
        if selected == start_month:
            return months
        first = (first-timedelta(days=1)).replace(day=1)


def _tracking_start_month(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', value):
        raise ValueError('local/display.json 展示起点必须是有效的YYYY-MM月份')
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        raise ValueError('local/display.json 展示起点必须是有效的YYYY-MM月份') from None
    return value


def local_tracking_start(source):
    """Read only this installation's optional, non-redirected display preference."""
    root = ledger_source.project_root(source)
    local = root / 'local'
    path = local / 'display.json'
    try:
        for component in (local, path):
            if (component.is_symlink()
                    or (hasattr(component, 'is_junction') and component.is_junction())
                    or component.resolve() != component):
                raise ValueError('local/display.json 展示配置路径不能重定向')
            if not component.exists():
                return None
            if component == local and not component.is_dir():
                raise ValueError('local/display.json 展示配置目录必须是本机普通目录')
        if not local.is_dir() or not path.is_file():
            raise ValueError('local/display.json 展示配置必须是本机普通文件')

        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('local/display.json 展示配置不能包含重复字段')
                result[key] = value
            return result

        value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_keys)
    except (OSError, UnicodeError, RuntimeError, json.JSONDecodeError) as error:
        raise ValueError('local/display.json 展示配置无法读取或格式无效') from error
    if not isinstance(value, dict) or set(value) != {'trackingStartMonth'}:
        raise ValueError('local/display.json 展示配置仅允许trackingStartMonth字段')
    return _tracking_start_month(value['trackingStartMonth'])


def tracked_months(state, month, start_month=None):
    """Derive this ledger's history start from its own budget and transaction dates."""
    import ledger_calculation
    if start_month is not None:
        return display_months(month, min(month, _tracking_start_month(start_month)))
    starts = [ledger_calculation.month_key(month)]
    starts.extend(ledger_calculation.month_key(rate['生效月份']) for rate in state['rates'])
    starts.extend(ledger_calculation.month_key(account.timestamp(row['交易时间'])) for row in state['rows'])
    return display_months(month, min(starts))


def source_tracking_months(source, state, month):
    """Use one owner-local policy for both generated snapshots and cache checks."""
    return tracked_months(state, month, local_tracking_start(source))


def required_cents(value, label):
    if value is None or value == '':
        raise ValueError(f'Excel计算结果缺失：{label}')
    return int(account.money(value) * 100)


def display_title(row):
    """Allow only compact human-readable display text, never raw source records."""
    def clean(value):
        result = account.text(value)
        for field in ('交易订单号', '商家订单号'):
            secret = account.text(row.get(field))
            if secret and secret not in ('/', '--'):
                result = result.replace(secret, '')
        result = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '', result)
        result = re.sub(r'[A-Za-z0-9_-]*\d{7,}[A-Za-z0-9_-]*', '', result)
        result = re.sub(r'(?<![A-Za-z])[A-Za-z0-9_-]{16,}(?![A-Za-z])', '', result)
        result = re.sub(r'[\x00-\x1f\x7f]', ' ', result)
        result = re.sub(r'\s+', ' ', result).strip(' ·-_:：,，;；/()（）')
        return result
    primary = clean(row.get('服务对象/事件')) or clean(row.get('交易对方'))
    detail = clean(row.get('商品/说明'))
    if detail and detail not in primary and detail not in ('转账', '商品', '/', '商户消费'):
        primary = f'{primary} · {detail}' if primary else detail
    return (primary or clean(row.get('细分类')) or '消费记录')[:72]


def build_month(rows, month, native):
    """Group approved net rows for display, reconcile against native Excel outputs."""
    seen = set()
    for row in rows:
        identity = account.text(row.get('记录ID'))
        if not identity or identity in seen:
            raise ValueError('流水记录ID缺失或重复')
        seen.add(identity)
    selected = [r for r in rows if account.timestamp(r['交易时间'])[:7] == month]
    records, structural, income = [], [], []
    for row in selected:
        scope = account.text(row.get('统计口径')).upper()
        instant = account.timestamp(row['交易时间']).replace(' ', 'T')
        if not instant.endswith('+08:00'):
            instant += '+08:00'
        if scope == 'INCOME':
            # Income displays from the audited received amount (原始金额);
            # it never enters consumption, pools or the daily calendar.
            income.append(dict(id=row['记录ID'], time=instant, date=instant[:10],
                               title=display_title(row),
                               category=account.text(row.get('主分类')) or '未填写分类',
                               pool='unknown', scope='INCOME',
                               amountCents=int(account.money(row.get('原始金额')) * 100)))
            continue
        amount = int(account.money(row.get('计入消费金额')) * 100)
        if amount == 0:
            continue
        if scope not in ('PERSONAL', 'PROJECT', 'STRUCTURAL'):
            raise ValueError('未支持的非零消费口径；请先核对权威流水')
        item = dict(id=row['记录ID'], time=instant, date=instant[:10], title=display_title(row),
                    category=account.text(row.get('主分类')) or '未填写分类',
                    pool=POOL_IDS.get(account.text(row.get('资金池')), 'unknown'),
                    scope=scope, amountCents=amount)
        (structural if scope == 'STRUCTURAL' else records).append(item)
    records.sort(key=lambda r:(r['time'],r['id']))
    structural.sort(key=lambda r:(r['time'],r['id']))
    income.sort(key=lambda r:(r['time'],r['id']))
    daily_amount, daily_count = defaultdict(int), defaultdict(int)
    category_amount, category_count = defaultdict(int), defaultdict(int)
    pool_used = defaultdict(int)
    for item in records:
        daily_amount[item['date']] += item['amountCents']; daily_count[item['date']] += 1
        category_amount[item['category']] += item['amountCents']; category_count[item['category']] += 1
        pool_used[item['pool']] += item['amountCents']
    income_amount, income_count = defaultdict(int), defaultdict(int)
    for item in income:
        income_amount[item['category']] += item['amountCents']; income_count[item['category']] += 1
    summary = dict(native['summary'])
    personal = sum(r['amountCents'] for r in records)
    structural_total = sum(r['amountCents'] for r in structural)
    income_total = sum(r['amountCents'] for r in income)
    expected = {'personalCents':personal, 'structuralCents':structural_total,
                'totalCents':personal+structural_total, 'unknownCents':pool_used['unknown']}
    if any(summary.get(k) != v for k,v in expected.items()):
        raise ValueError(f'{month}消费净额与Excel结果对账失败')
    native_income = native.get('income')
    if native_income is not None:
        if native_income.get('incomeCents') != income_total:
            raise ValueError(f'{month}收入合计与计算结果对账失败')
        if native_income.get('count') != len(income):
            raise ValueError(f'{month}收入笔数与计算结果对账失败')
        for name, amount in native_income.get('categories', {}).items():
            if income_amount[name] != amount:
                raise ValueError(f'{month}收入分类与计算结果对账失败')
    summary['incomeCents'] = income_total
    pools = native['pools']
    if len(pools) != 4 or {p['id'] for p in pools} != set(POOL_IDS.values()):
        raise ValueError('四池结果不完整')
    for pool in pools:
        if pool.get('usageRatio') is not None and not math.isfinite(float(pool['usageRatio'])):
            raise ValueError('Excel资金池使用率必须是有限数值')
        if pool['spentCents'] != pool_used[pool['id']]:
            raise ValueError(f'{month}资金池已用与流水对账失败')
    summary.update(monthlyBudgetCents=sum(p['budgetCents'] for p in pools),
                   openingBudgetCents=sum(p['openingCents'] for p in pools),
                   availableCents=sum(p['availableCents'] for p in pools),
                   overrunCents=sum(p['overrunCents'] for p in pools))
    year, number = map(int, month.split('-'))
    daily = []
    for day in range(1, calendar.monthrange(year, number)[1]+1):
        label = f'{month}-{day:02d}'
        calculated = native['daily'].get(label)
        if calculated is None:
            # A synthetic empty-day fixture may omit cells. Real extraction includes every day.
            calculated = {'amountCents':0, 'operationCumulativeCents':0, 'referenceCents':0}
        if calculated['amountCents'] != daily_amount[label]:
            raise ValueError(f'{month}日消费与Excel结果对账失败')
        daily.append({'date':label, **calculated, 'count':daily_count[label]})
    for name, amount in native.get('categories', {}).items():
        if category_amount[name] != amount:
            raise ValueError(f'{month}分类消费与Excel结果对账失败')
    categories = [dict(name=name, amountCents=amount, count=category_count[name])
                  for name,amount in category_amount.items() if category_count[name]]
    categories.sort(key=lambda r:(-r['amountCents'],r['name']))
    income_categories = [dict(name=name, amountCents=amount, count=income_count[name])
                         for name,amount in income_amount.items() if income_count[name]]
    income_categories.sort(key=lambda r:(-r['amountCents'],r['name']))
    # The fixed Excel category list may omit an approved Unknown/new category.
    # Its display grouping is still accountable to the native monthly net amount.
    if sum(c['amountCents'] for c in categories) != summary['personalCents']:
        raise ValueError(f'{month}分类合计与Excel月度消费对账失败')
    top = sorted((r for r in records if r['amountCents']>0),key=lambda r:(-r['amountCents'],r['time'],r['id']))[:10]
    last = max((account.timestamp(r['交易时间']) for r in selected),default=None)
    return dict(month=month, lastRecordedAt=last.replace(' ','T')+'+08:00' if last else None,
                mechanismActive=native['mechanismActive'], summary=summary, pools=pools,
                daily=daily, categories=categories, records=records, structuralRecords=structural,
                incomeRecords=income, incomeCategories=income_categories,
                topIds=[r['id'] for r in top])


def extract_native(path, month, calendar_month):
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        stats, funds = workbook['03_统计数据'], workbook['02_资金池']
        def cents(sheet, address):
            return required_cents(sheet[address].value, sheet.title+'!'+address)
        if account.timestamp(stats['B2'].value)[:7] != month:
            raise ValueError('Excel查看月份与导出月份不一致')
        if account.timestamp(stats['N2'].value)[:7] != calendar_month:
            raise ValueError('Excel日历月份与北京时间月份不一致')
        summary = {key:cents(stats,coordinate) for key,coordinate in
                   [('personalCents','Q2'),('structuralCents','Q4'),('totalCents','Q3'),('unknownCents','U5')]}
        summary['safetyCents'] = cents(funds,'G25')
        pools = []
        for index, (name, ident) in enumerate(POOL_IDS.items(),7):
            if funds[f'A{index}'].value != name:
                raise ValueError('Excel资金池结构变动，请更新展示适配器')
            pool = dict(id=ident,name=name)
            for key,col in [('budgetCents','B'),('carryCents','D'),('openingCents','E'),
                            ('spentCents','F'),('endingCents','G'),('availableCents','H'),('overrunCents','I')]:
                pool[key] = cents(funds,f'{col}{index}')
            ratio = funds[f'J{index}'].value
            if ratio is None:
                raise ValueError('Excel资金池使用率缺失')
            if not math.isfinite(float(ratio)):
                raise ValueError('Excel资金池使用率必须是有限数值')
            pool['usageRatio'] = float(ratio) if pool['openingCents']>0 else None
            pools.append(pool)
        daily = {}
        for idx in range(5,36):
            value = stats[f'A{idx}'].value
            if value in (None,''):
                continue
            label = account.timestamp(value)[:10]
            daily[label] = dict(amountCents=cents(stats,f'D{idx}'),
                operationCumulativeCents=cents(stats,f'B{idx}'),referenceCents=cents(stats,f'C{idx}'))
        days = calendar.monthrange(*map(int,month.split('-')))[1]
        if len(daily) != days:
            raise ValueError('Excel日计算结果缺失')
        categories = {}
        for idx in range(5,23):
            label=stats[f'G{idx}'].value
            if label:categories[str(label)]=cents(stats,f'H{idx}')
        return dict(summary=summary,pools=pools,daily=daily,categories=categories,
                    mechanismActive=month>=account.timestamp(funds['B4'].value)[:7])
    finally:
        workbook.close()


def cleanup_owned_excel(copy):
    # Only the instance identified by this export's PID + process start time.
    if not (Path(copy).parent/'excel-process.json').exists():
        return
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
          '-File',str(Path(__file__).with_name('dashboard_excel.ps1')),
          '-WorkbookPath',str(copy),'-Cleanup'],capture_output=True,
          encoding='utf-8',errors='replace',timeout=20)
    if result.returncode:
        raise RuntimeError('独立Excel实例清理失败：'+result.stderr.strip()[-300:])


def run_excel(copy, months):
    args=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
          '-File',str(Path(__file__).with_name('dashboard_excel.ps1')),
          '-WorkbookPath',str(copy),'-Months',','.join(months)]
    try:
        result=subprocess.run(args,capture_output=True,encoding='utf-8',errors='replace',timeout=240)
    except subprocess.TimeoutExpired:
        cleanup_owned_excel(copy)
        raise RuntimeError('Excel展示计算超时；已清理本次独立实例并保留旧快照') from None
    if result.returncode:
        cleanup_owned_excel(copy)
        raise RuntimeError('Excel展示计算失败；已保留旧快照。'+result.stderr.strip()[-600:])
    try:return json.loads(result.stdout.strip().lstrip('\ufeff'))
    except json.JSONDecodeError:raise RuntimeError('Excel没有返回可验证的展示计算结果') from None


@contextmanager
def sync_lock(runtime):
    """OS advisory lock also covers simultaneous CLI and HTTP exporters, no stale PID lock."""
    runtime.mkdir(parents=True,exist_ok=True)
    stream=open(runtime/'dashboard.lock','a+b')
    try:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('已有展示同步正在进行，请稍后再试') from None
        yield
    finally:
        stream.close()  # Closing releases the lock, including on failure.


def read_snapshot(workbook):
    path=ledger_source.runtime(workbook)/'dashboard.json'
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if value.get('schemaVersion')==SCHEMA_VERSION and isinstance(value.get('months'),list) else None
    except (OSError,ValueError,AttributeError):return None


def snapshot_current(workbook,snapshot,month=None):
    try:
        ledger_source.assert_active(workbook)
        calendar_month = month or current_month()
        if ledger_source.is_database(workbook):
            import ledger_store
            months = source_tracking_months(workbook, ledger_store.read_state(workbook), calendar_month)
        else:
            months = None  # Preserve the legacy Excel cache contract.
    except ValueError:
        return False
    return bool(snapshot and snapshot.get('schemaVersion')==SCHEMA_VERSION
                and snapshot.get('calendarMonth')==calendar_month
                and (months is None or [view.get('month') for view in snapshot.get('months', [])] == months)
                and snapshot.get('sourceHash')==ledger_source.source_hash(workbook))


def sync_snapshot(workbook):
    ledger_source.assert_active(workbook)
    if ledger_source.is_database(workbook):
        from ledger_snapshot import sync_database_snapshot
        return sync_database_snapshot(workbook)
    workbook=Path(workbook).resolve()
    if 'archive' in [p.lower() for p in workbook.parts]:
        raise ValueError('拒绝将历史存档作为Dashboard事实源')
    runtime=workbook.parent/'.runtime'
    with sync_lock(runtime):
        source_hash=account.sha(workbook)
        month=current_month(); months=display_months(month)
        with tempfile.TemporaryDirectory(prefix='account-dashboard-',dir=runtime) as temp:
            directory=Path(temp);copy=directory/'source.xlsx'
            shutil.copyfile(workbook,copy)
            if account.sha(copy)!=source_hash:
                raise ValueError('复制期间原工作簿发生变化，请重新同步')
            native=run_excel(copy,months)
            if account.sha(workbook)!=source_hash or current_month()!=month:
                raise ValueError('同步期间来源或日历月份发生变化，请重新同步')
            if [v.get('month') for v in native.get('months',[])]!=months:
                raise ValueError('Excel未完整返回当月与上月结果')
            rows,_=account.ledger(copy)
            payload=[]
            for item in native['months']:
                view_path=directory/(item['month']+'.xlsx')
                audit=account.verify(view_path,require_native=True,native_result=item['native'])
                if not audit['ok']:
                    raise ValueError('展示副本验证失败：'+'；'.join(audit['errors'][:5]))
                payload.append(build_month(rows,item['month'],extract_native(view_path,item['month'],month)))
            last=max((account.timestamp(r['交易时间']) for r in rows),default=None)
            snapshot=dict(schemaVersion=SCHEMA_VERSION,sourceHash=source_hash,calendarMonth=month,
                          generatedAt=datetime.now(account.TZ).isoformat(),ledgerCount=len(rows),
                          sourceLastRecordedAt=last.replace(' ','T')+'+08:00' if last else None,months=payload)
            if account.sha(workbook)!=source_hash or current_month()!=month:
                raise ValueError('发布前原工作簿或月份发生变化，拒绝替换快照')
            # Same-volume atomic replacement; failed verification never truncates a good snapshot.
            draft=directory/'dashboard.json'
            draft.write_text(json.dumps(snapshot,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
            ledger_source.assert_active(workbook)
            os.replace(draft,runtime/'dashboard.json')
            return {'ok':True,'generatedAt':snapshot['generatedAt'],'months':months,
                    'ledgerCount':len(rows),'sourceHash':source_hash,'nativeRecalculated':True,
                    'originalUnchanged':True}
