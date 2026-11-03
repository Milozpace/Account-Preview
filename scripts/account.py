"""Account SQLite-first maintenance and local owner setup."""
from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET
import zipfile

try:
    import openpyxl
except ImportError:
    openpyxl = None  # doctor must remain usable before dependency setup
import ledger_source

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / '个人财物工作簿.xlsx'
SHEETS = ['00_Dashboard', '01_账单流水', '02_资金池', '03_统计数据']
HEADERS = ['平台','交易时间','交易对方','商品/说明','原始类型','原始分类','收支方向','原始金额','支付方式','状态','交易订单号','商家订单号','原始财务事件','退款调整','净支出候选','最终财务事件','统计口径','计入消费金额','主分类','细分类','生活领域','服务对象/事件','标签','语义置信度','语义说明','用户修正规则','复核状态','来源行','资金池','资金池判定','记录ID','原记录ID','关联记录ID','来源文件','导入批次','记录类型','月份','月内支出排名']
RAW = HEADERS[:12]
UPDATE_FIELDS = set(HEADERS[12:36]) - {'记录ID', '来源文件', '来源行'}
MONEY_FIELDS = {'原始金额', '退款调整', '净支出候选', '计入消费金额'}
POOLS = ['运行账户','随心消费','旅行/体验','成长/项目/耐用品']
TZ = timezone(timedelta(hours=8))


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def money(value):
    if value is None or str(value).strip() in ('', '/', '--'): return Decimal('0.00')
    try:
        result = Decimal(str(value).replace('¥','').replace('￥','').replace(',','').strip()).quantize(Decimal('.01'))
        if not result.is_finite(): raise ValueError('non-finite')
        return result
    except (InvalidOperation, ValueError): raise ValueError('金额字段无效') from None


def raw_money(value):
    """Original bill amounts must be explicit before tolerant derived math runs."""
    if value is None or isinstance(value, bool) or str(value).strip() in ('', '/', '--'):
        raise ValueError('原始金额必须明确提供有限金额；零金额请明确提供0')
    return money(value)


def text(value):
    return '' if value is None else str(value).strip().lstrip("'\t")


def timestamp(value):
    if isinstance(value, (datetime, date)): return value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (int, float)): return openpyxl.utils.datetime.from_excel(value).isoformat(sep=' ')
    value = text(value).replace('/', '-').replace('T', ' ')
    try: return datetime.fromisoformat(value).isoformat(sep=' ')
    except ValueError: raise ValueError('交易时间无效') from None


def original_key(record):
    platform = text(record.get('平台'))
    order = next((value for value in (text(record.get('交易订单号')), text(record.get('商家订单号'))) if value and value not in ('/', '--')), '')
    if order: return platform + '|' + order, False
    parts = [platform, timestamp(record.get('交易时间')), str(money(record.get('原始金额'))), text(record.get('交易对方')), text(record.get('商品/说明'))]
    return '|'.join(parts), True


def normalize_record(record):
    if set(record) - set(HEADERS): raise ValueError('记录包含未知字段')
    r = dict(record)
    if text(r.get('资金池')) not in ('', *POOLS, '待归类'):
        raise ValueError('资金池仅允许四个消费池或待归类；安全积累不是消费预算')
    if not text(r.get('平台')): raise ValueError('缺少平台')
    r['交易时间'] = timestamp(r.get('交易时间'))
    if '原始金额' not in r: raise ValueError('缺少原始金额；零金额请明确提供0')
    raw_money(r['原始金额'])  # validate before money() can map an absent value to zero
    if any(key in r for key in HEADERS[36:]): raise ValueError('公式辅助列由Excel维护')
    for key in MONEY_FIELDS:
        if key in r: r[key] = str(money(r[key]))
    for key in ('交易订单号', '商家订单号'):
        if key in r: r[key] = text(r[key])
    identity, fallback = original_key(r)
    derived = text(r.get('记录类型')) in ('分摊', '派生', '派生分摊', 'allocation', 'DERIVED')
    if derived:
        if not text(r.get('原记录ID')): raise ValueError('派生记录必须关联原记录ID')
        if not text(r.get('记录ID')):
            seed = '|'.join([r['原记录ID'], text(r.get('来源行')), text(r.get('细分类')), text(r.get('资金池')), str(money(r.get('计入消费金额')))])
            r['记录ID'] = 'alloc_' + hashlib.sha256(seed.encode()).hexdigest()[:20]
        elif not r['记录ID'].startswith('alloc_'): raise ValueError('派生记录ID前缀必须为alloc_')
    else:
        expected = 'tx_' + hashlib.sha256(identity.encode()).hexdigest()[:20]
        if r.get('记录ID') and r['记录ID'] != expected: raise ValueError('记录ID与原交易身份不一致')
        r['记录ID'] = expected
        r['记录类型'] = r.get('记录类型') or '原始交易'
    if fallback: r['复核状态'] = '需复核：缺少订单号'
    return r


def raw_signature(r):
    values = []
    for key in RAW:
        val = r.get(key)
        values.append(timestamp(val) if key == '交易时间' else str(money(val)) if key == '原始金额' else text(val))
    return tuple(values)


def plan_import(existing, approved):
    if not isinstance(approved, dict) or set(approved) - {'records','updates','reviewed_by','reviewed_at','rules_sha256','reason','confirmation_source','duplicates','skipped','date_min','date_max','review_required','conflicts'}:
        raise ValueError('审核JSON结构无效')
    ids, originals = {}, {}
    for row in existing:
        entry_id = text(row.get('记录ID'))
        if entry_id in ids: raise ValueError('现有总账记录ID重复')
        ids[entry_id] = row
        if not entry_id.startswith('alloc_'): originals[original_key(row)[0]] = row
    records, duplicates = [], 0
    for row in approved.get('records', []):
        r = normalize_record(row)
        current = ids.get(r['记录ID']) or (originals.get(original_key(r)[0]) if not r['记录ID'].startswith('alloc_') else None)
        if current:
            if raw_signature(current) != raw_signature(r): raise ValueError('重复交易原始字段冲突；需人工核对')
            # Derived changes must use explicit audited updates, never silently overwrite.
            for key in UPDATE_FIELDS - {'导入批次','来源文件','来源行','复核状态','记录类型'}:
                if key in r and text(r[key]) != text(current.get(key)):
                    if key in MONEY_FIELDS and money(r[key]) == money(current.get(key)): continue
                    raise ValueError('重复交易派生字段冲突；请使用updates')
            duplicates += 1
            continue
        if r['记录ID'].startswith('alloc_') and r['原记录ID'] not in ids: raise ValueError('派生记录的原记录不存在')
        records.append(r); ids[r['记录ID']] = r
        if not r['记录ID'].startswith('alloc_'): originals[original_key(r)[0]] = r
    updates, updated = [], set()
    for update in approved.get('updates', []):
        entry_id = text(update.get('记录ID'))
        changes = update.get('changes', {})
        if entry_id not in ids or entry_id in updated: raise ValueError('更新记录不存在或同批重复更新')
        if not isinstance(changes, dict) or not changes or set(changes) - UPDATE_FIELDS: raise ValueError('更新仅允许派生字段；禁止原始字段、记录ID或公式列')
        if not text(update.get('reason')) or not text(update.get('confirmation_source')): raise ValueError('修改必须附reason与confirmation_source')
        if any(isinstance(value, str) and value.startswith('=') for value in changes.values()): raise ValueError('禁止审核JSON注入公式')
        if '资金池' in changes and text(changes['资金池']) not in ('', *POOLS, '待归类'):
            raise ValueError('安全积累不是消费预算；更新资金池值无效')
        clean = {k: str(money(v)) if k in MONEY_FIELDS else v for k,v in changes.items()}
        audit = f"{text(update['reason'])}；依据：{text(update['confirmation_source'])}"
        prior = text(ids[entry_id].get('用户修正规则'))
        clean['用户修正规则'] = (prior + '\n' if prior else '') + audit
        if all((money(ids[entry_id].get(k)) == money(v)) if k in MONEY_FIELDS else (text(ids[entry_id].get(k)) == text(v)) for k,v in changes.items()):
            duplicates += 1; continue
        updates.append({'记录ID': entry_id, 'changes': clean, 'reason': update['reason'], 'confirmation_source': update['confirmation_source']})
        updated.add(entry_id)
    return {'records': records, 'updates': updates, 'duplicates': duplicates}


def ledger(path):
    if ledger_source.is_database(path):
        import ledger_store
        return ledger_store.read_state(path)['rows'], ['SQLite']
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if '01_账单流水' not in wb.sheetnames: raise ValueError('缺少当前权威流水01_账单流水；不能使用旧结构或archive')
        rows = wb['01_账单流水'].iter_rows(values_only=True)
        headers = list(next(rows))[:38]
        if headers != HEADERS: raise ValueError('流水表头与38字段契约不一致')
        result = [dict(zip(HEADERS, row[:38])) for row in rows if row[0] is not None]
        return result, wb.sheetnames
    finally: wb.close()


def aggregate(rows):
    months = {}
    for row in rows:
        month = timestamp(row['交易时间'])[:7]
        entry = months.setdefault(month, {'total': Decimal(0), 'personal': Decimal(0), 'structural': Decimal(0), 'pools': {p: Decimal(0) for p in POOLS}, 'unknown': Decimal(0), 'other': Decimal(0)})
        amount = money(row.get('计入消费金额'))
        entry['total'] += amount
        scope = text(row.get('统计口径')).upper()
        event = text(row.get('最终财务事件')).upper()
        if 'STRUCT' in scope or 'STRUCT' in event or '结构' in scope:
            entry['structural'] += amount
        elif scope in ('PERSONAL','PROJECT','UNKNOWN','待归类','个人消费','个人') or event in ('PERSONAL','PROJECT'):
            entry['personal'] += amount
            pool = text(row.get('资金池'))
            if pool in entry['pools']: entry['pools'][pool] += amount
            else: entry['unknown'] += amount
        else: entry['other'] += amount
    return {month: {key: {p: str(v) for p,v in value.items()} if isinstance(value,dict) else str(value) for key,value in entry.items()} for month,entry in sorted(months.items())}


def inspect(path):
    if ledger_source.is_database(path):
        import ledger_store
        return ledger_store.inspect(path)
    rows, sheets = ledger(path)
    dates = [timestamp(r['交易时间']) for r in rows]
    keys = [original_key(r)[0] for r in rows if not text(r['记录ID']).startswith('alloc_')]
    return {'workbook': str(path.resolve()), 'sha256': sha(path), 'sheets': sheets, 'rows': len(rows), 'date_min': min(dates, default=None), 'date_max': max(dates, default=None), 'duplicate_original_keys': len(keys)-len(set(keys)), 'aggregates': aggregate(rows)}


def pool_reconciliation(rows, rates, initial, selected, calendar):
    """Independent audit only. Never writes balances or substitutes Excel formulas."""
    selected, calendar = str(selected)[:7], str(calendar)[:7]
    entries = sorted((timestamp(r['生效月份'])[:7], {p:money(r.get(p)) for p in POOLS}) for r in rates)
    if not entries or len({m for m,_ in entries}) != len(entries):
        raise ValueError('预算生效月份缺失或重复')
    amounts, monthly = aggregate(rows), {}
    opening = {p:money(initial.get(p)) if p in POOLS[2:] else money(0) for p in POOLS}
    safety, month = money(0), entries[0][0]
    if selected < month:
        zero = {p:money(0) for p in POOLS}
        return {'opening':zero,'available':zero,'ending':zero,'safety':safety,'monthly':monthly}
    while month <= selected:
        budget = next(values for effective,values in reversed(entries) if effective <= month)
        used = {p:money(amounts.get(month,{}).get('pools',{}).get(p)) for p in POOLS}
        available = {p:opening[p]+budget[p] for p in POOLS}
        ending = {p:available[p]-used[p] for p in POOLS}
        added = max(money(0),budget[POOLS[0]]-used[POOLS[0]])
        added = added if month < calendar else money(0)
        safety += added
        monthly[month] = {'opening':dict(opening),'budget':budget,'available':available,'used':used,
                          'ending':ending,'safety_added':added,'safety_cumulative':safety}
        if month == selected:
            return {**monthly[month],'safety':safety,'monthly':monthly}
        opening = {p:ending[p] if p in POOLS[2:] else money(0) for p in POOLS}
        year, number = map(int, month.split('-'))
        month = f'{year+number//12:04d}-{number%12+1:02d}'


def verify(path, require_native=False, native_result=None):
    if ledger_source.is_database(path):
        import ledger_store
        return ledger_store.verify(path)
    rows, sheets = ledger(path)
    errors = []
    if sheets != SHEETS: errors.append('工作表必须恰好为4个核心页')
    identifiers = [text(r.get('记录ID')) for r in rows]
    if any(not x for x in identifiers) or len(set(identifiers)) != len(identifiers): errors.append('记录ID为空或重复')
    original_keys = [original_key(r)[0] for r in rows if not text(r['记录ID']).startswith('alloc_')]
    if len(original_keys) != len(set(original_keys)): errors.append('平台订单身份重复')
    for row in rows:
        if text(row['记录ID']).startswith('alloc_') and text(row.get('原记录ID')) not in identifiers: errors.append('分摊缺少原记录')
    missing_cache = []
    budget_ref = None
    with zipfile.ZipFile(path) as archive:
        tables = []
        for name in archive.namelist():
            if name.startswith('xl/tables/') and name.endswith('.xml'):
                table = ET.fromstring(archive.read(name))
                if table.get('name') == 'LedgerTable': tables.append(table)
                if table.get('name') == 'BudgetRates': budget_ref = table.get('ref')
        if len(tables) != 1: errors.append('LedgerTable不存在或重名')
        else:
            table = tables[0]; columns = table.find('{*}tableColumns')
            if [col.get('name') for col in columns] != HEADERS: errors.append('LedgerTable必须覆盖38列')
            if table.get('ref') != f'A1:AL{len(rows)+1}': errors.append('LedgerTable未覆盖全部流水')
        for name in archive.namelist():
            if name.startswith('xl/worksheets/sheet') and name.endswith('.xml'):
                root = ET.fromstring(archive.read(name))
                for cell in root.findall('.//{*}c'):
                    if cell.find('{*}f') is not None:
                        cached_value = cell.find('{*}v')
                        # Empty-string formula results are valid native caches (t=str).
                        if cached_value is None or (cached_value.text is None and cell.get('t') != 'str'):
                            missing_cache.append(name+'!'+cell.get('r','?'))
    formula_errors = []
    cached = openpyxl.load_workbook(path, read_only=True, data_only=True)
    formulas = openpyxl.load_workbook(path, read_only=True, data_only=False)
    summary = {}
    try:
        for ws in cached:
            for row in ws.iter_rows():
                for cell in row:
                    if cell.data_type == 'e': formula_errors.append(f'{ws.title}!{cell.coordinate}:{cell.value}')
        for idx, row in enumerate(formulas['01_账单流水'].iter_rows(min_row=2, min_col=37, max_col=38), 2):
            if idx <= len(rows)+1 and any(cell.data_type != 'f' for cell in row): errors.append(f'流水第{idx}行缺少公式辅助列')
        if all(name in cached.sheetnames for name in SHEETS):
            stats = cached['03_统计数据']; pools = cached['02_资金池']; dashboard = cached['00_Dashboard']
            selected = stats['B2'].value
            month = timestamp(selected)[:7] if isinstance(selected,(datetime,date,int,float)) else text(selected)[:7]
            amounts = aggregate(rows).get(month, {'personal':'0','total':'0','structural':'0','pools':{p:'0' for p in POOLS},'unknown':'0'})
            summary = {'selected_month':month,'statistics_checked':True,'independent':amounts}
            def compare(sheet, coordinate, expected):
                value = sheet[coordinate].value
                if value is None or money(value) != money(expected): errors.append(f'{sheet.title}!{coordinate}与独立流水聚合不一致')
            if len(month) != 7 or month[4] != '-': errors.append('统计当前月份无效')
            for coordinate,key in [('Q2','personal'),('Q3','total'),('Q4','structural'),('U5','unknown')]: compare(stats,coordinate,amounts[key])
            for coordinate in ('U2','U3','U4'): compare(stats,coordinate,'0')
            compare(stats,'J2',str(len(rows)))
            pool_sum = Decimal(0)
            for idx,pool in enumerate(POOLS,7):
                if pools[f'A{idx}'].value != pool: errors.append(f'资金池第{idx}行名称不一致')
                compare(pools,f'F{idx}',amounts['pools'][pool]); pool_sum += money(amounts['pools'][pool])
            compare(dashboard,'A7',amounts['personal']); compare(dashboard,'E7',amounts['structural']); compare(dashboard,'I7',str(pool_sum))
            funds_month, mechanism_start = pools['B3'].value, pools['B4'].value
            def month_value(value):
                return timestamp(value)[:7] if isinstance(value,(datetime,date,int,float)) else text(value)[:7]
            before_start = bool(funds_month and mechanism_start and month_value(funds_month) < month_value(mechanism_start))
            if before_start:
                if dashboard['M7'].value != '未启用': errors.append('Dashboard!M7在机制起始前应为未启用')
                if any(money(pools[f'G{i}'].value) != 0 for i in range(7,11)): errors.append('机制起始前资金池期末应为零')
            else:
                compare(dashboard,'M7',str(sum((money(pools[f'G{i}'].value) for i in range(7,11)),Decimal(0))))
            if pools['A24'].value == '安全积累（不属于消费预算）':
                if not budget_ref: errors.append('BudgetRates缺失')
                else:
                    left,top,right,bottom = openpyxl.utils.cell.range_boundaries(budget_ref)
                    rate_headers = [pools.cell(top,col).value for col in range(left,right+1)]
                    rates = [dict(zip(rate_headers,values)) for values in pools.iter_rows(min_row=top+1,max_row=bottom,min_col=left,max_col=right,values_only=True)]
                    calendar = month_value(stats['N2'].value)
                    initial = {p: pools[f'C{i}'].value for i,p in enumerate(POOLS,7)}
                    control = pool_reconciliation(rows,rates,initial,month,calendar)
                    for i,p in enumerate(POOLS,7):
                        for col,key in [('D','opening'),('E','available'),('G','ending')]: compare(pools,f'{col}{i}',control[key][p])
                    compare(pools,'G25',control['safety']);compare(dashboard,'E10',control['safety'])
                    last = max(month,calendar,max(aggregate(rows),default=calendar))
                    history = pool_reconciliation(rows,rates,initial,last,calendar)['monthly']
                    for values in stats.iter_rows(min_row=41,max_col=16,values_only=True):
                        if not values[0]: continue
                        historical = month_value(values[0]); item = history.get(historical)
                        expected_added = item['safety_added'] if item else money(0)
                        expected_total = item['safety_cumulative'] if item else money(0)
                        if money(values[9]) != expected_added or money(values[15]) != expected_total:
                            errors.append(historical+'安全积累与独立核对不一致')
                    summary['pool_rollover_checked'] = True
                    summary['safety_accumulation'] = str(control['safety'])
    finally: cached.close(); formulas.close()
    if formula_errors: errors.append('公式错误: ' + ', '.join(formula_errors[:10]))
    if missing_cache: errors.append('缺少原生计算缓存: ' + ', '.join(missing_cache[:10]))
    report = inspect(path)
    for month, amounts in report['aggregates'].items():
        if money(amounts['personal']) != sum((money(v) for v in amounts['pools'].values()), Decimal(0))+money(amounts['unknown']): errors.append(month+'个人与四池未对齐')
        if money(amounts['total']) != money(amounts['personal']) + money(amounts['structural']): errors.append(month+'消费总额与个人+结构性未对齐')
    status = path.parent / '.runtime' / 'status.json'
    saved = json.loads(status.read_text(encoding='utf-8')) if status.exists() else {}
    proof = native_result or (saved.get('native', {}) if saved.get('sha256') == report['sha256'] else {})
    if require_native and (not proof.get('recalculated') or proof.get('calculation_state') != 0): errors.append('未确认原生Excel重算完成')
    report.update({'ok': not errors, 'errors': errors, 'formula_errors': formula_errors, 'missing_formula_cache': len(missing_cache), 'native_recalculation_verified': bool(proof.get('recalculated') and proof.get('calculation_state') == 0),'summary_reconciliation':summary})
    return report


def parse_sources(sources):
    records, skipped = [], 0
    aliases = {'交易类型':'原始类型','交易分类':'原始分类','交易对方':'交易对方','商品':'商品/说明','商品名称':'商品/说明','商品说明':'商品/说明','收/支':'收支方向','金额':'原始金额','金额(元)':'原始金额','金额（元）':'原始金额','收/付款方式':'支付方式','支付方式':'支付方式','当前状态':'状态','交易状态':'状态','交易订单号':'交易订单号','交易单号':'交易订单号','商家订单号':'商家订单号','商户单号':'商家订单号','交易时间':'交易时间'}
    for source in sources:
        path = Path(source).resolve()
        if path.suffix.lower() == '.csv':
            raw = path.read_bytes()
            try: content = raw.decode('utf-8-sig')
            except UnicodeDecodeError: content = raw.decode('gb18030')
            matrix = list(csv.reader(io.StringIO(content))); platform = 'Alipay'
        elif path.suffix.lower() == '.xlsx':
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try: matrix = list(wb.active.iter_rows(values_only=True))
            finally: wb.close()
            platform = 'WeChat'
        else: raise ValueError('仅支持支付宝CSV和微信xlsx原账单')
        header_index = next((i for i,row in enumerate(matrix) if '交易时间' in [text(x) for x in row] and any(text(x) in ('交易订单号','交易单号') for x in row)), None)
        if header_index is None: raise ValueError('未发现原账单表头')
        headers = [text(x) for x in matrix[header_index]]
        for index, values in enumerate(matrix[header_index+1:], header_index+2):
            row = {aliases[h]: value for h,value in zip(headers, values) if h in aliases}
            if not row.get('交易时间') or text(row.get('交易时间')).startswith(('共','-')): skipped += 1; continue
            row.update({'平台': platform,'来源文件': path.name, '来源行': index, '记录类型': '原始交易'})
            records.append(normalize_record(row))
    return records, skipped


def prepare_records(existing, records):
    """Same identity differences become review notes; never overwrite old raw data."""
    seen = {original_key(r)[0]:r for r in existing if not text(r['记录ID']).startswith('alloc_')}
    new, conflicts, duplicates = [], [], 0
    for r in records:
        key = original_key(r)[0]
        if key not in seen:
            new.append(r); seen[key] = r; continue
        duplicates += 1
        current = seen[key]
        changed = []
        for field in RAW:
            if field not in r: continue  # A source that omits a column provides no new evidence.
            old, incoming = current.get(field), r[field]
            equal = timestamp(old) == timestamp(incoming) if field == '交易时间' else money(old) == money(incoming) if field == '原始金额' else text(old) == text(incoming)
            if not equal: changed.append(field)
        if changed:
            conflicts.append({'记录ID':current.get('记录ID'),'来源文件':r.get('来源文件'),'来源行':r.get('来源行'),
                'different_raw_fields':changed,'priority':'high' if set(changed)&{'原始金额','交易对方','平台'} else 'review',
                'action':'保留旧原始字段；核验差异，以带理由及确认来源的updates修改派生列'})
    return new, duplicates, conflicts


def native_worker(path, action, input_path=None):
    args = ['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts'/'excel_worker.ps1'),'-WorkbookPath',str(path),'-Action',action]
    if input_path: args += ['-InputJson',str(input_path)]
    completed = subprocess.run(args, capture_output=True, encoding='utf-8', errors='replace', timeout=240)
    if completed.returncode: raise RuntimeError('Excel原生操作失败；原文件保持不变: '+completed.stderr[-1200:])
    try: result = json.loads(completed.stdout.strip().lstrip('\ufeff'))
    except json.JSONDecodeError: raise RuntimeError('Excel操作未返回可验证结果') from None
    return result


def safe_write(path, action, payload=None):
    path = Path(path).resolve()
    ledger_source.assert_active(path)
    lock = path.with_name('~$'+path.name)
    if lock.exists(): raise ValueError('请先保存并关闭当前Excel工作簿，再执行安全写入')
    original_hash = sha(path)
    workbook_tmp = json_tmp = None
    try:
        handle, name = tempfile.mkstemp(prefix='.account-', suffix='.xlsx', dir=path.parent)
        os.close(handle); workbook_tmp = Path(name)
        shutil.copy2(path, workbook_tmp)
        if os.name == 'nt': ctypes.windll.kernel32.SetFileAttributesW(str(workbook_tmp), 2)
        if payload is not None:
            handle, name = tempfile.mkstemp(prefix='.account-', suffix='.json', dir=path.parent)
            os.close(handle); json_tmp = Path(name)
            json_tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8-sig')
            if os.name == 'nt': ctypes.windll.kernel32.SetFileAttributesW(str(json_tmp), 2)
        native = native_worker(workbook_tmp, action, json_tmp)
        report = verify(workbook_tmp, require_native=True, native_result=native)
        if not report['ok']: raise ValueError('临时工作簿验证失败: '+ '; '.join(report['errors']))
        if sha(path) != original_hash: raise ValueError('原工作簿在操作期间发生变化，拒绝替换')
        if lock.exists(): raise ValueError('工作簿已在Excel重新打开，拒绝替换')
        ledger_source.assert_active(path)
        if os.name == 'nt': ctypes.windll.kernel32.SetFileAttributesW(str(workbook_tmp), 128)
        os.replace(workbook_tmp, path); workbook_tmp = None
        report['workbook'] = str(path); report['sha256'] = sha(path)
        report.update({'native':native,'generated_time':datetime.now(TZ).isoformat(), 'rules_sha256':sha(ROOT/'docs'/'记账规则.md') if (ROOT/'docs'/'记账规则.md').exists() else None})
        runtime = path.parent/'.runtime'; runtime.mkdir(exist_ok=True)
        report_tmp = runtime/'.status.tmp'
        report_tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(report_tmp, runtime/'status.json')
        return report
    finally:
        for temp in (workbook_tmp, json_tmp):
            if temp and temp.exists():
                if os.name == 'nt': ctypes.windll.kernel32.SetFileAttributesW(str(temp), 128)
                temp.unlink()


def update_dashboard(workbook, result):
    """A display failure must never relabel a successfully saved ledger import."""
    try:
        import dashboard_data
        result['dashboard'] = dashboard_data.sync_snapshot(workbook)
    except Exception as error:
        result['dashboard'] = {'ok': False, 'error': str(error), 'previousSnapshotPreserved': True}
        return result
    import app_export
    app_export.prepare_after_dashboard(workbook, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='完整项目目录；默认当前脚本所属项目')
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument('--workbook', type=Path, default=None)
    source_group.add_argument('--database', type=Path, default=None)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('update-status', help='只读查看本项目采用的产品身份和版本')
    product_adopt = commands.add_parser('adopt-product', help='显式登记更新身份和源码基线，不改账/安装')
    product_adopt.add_argument('--profile', choices=('personal', 'preview'), required=True)
    product_adopt.add_argument('--apply', action='store_true')
    product_update = commands.add_parser('update', help='检查最新已发布试用版；--apply才升级')
    product_update.add_argument('--release-dir', type=Path)
    product_update.add_argument('--prepare-python', action='store_true')
    product_update.add_argument('--apply', action='store_true')
    doctor_cmd = commands.add_parser('doctor', help='只读检查本机环境与初始化进度')
    doctor_cmd.add_argument('--root', type=Path, default=argparse.SUPPRESS)
    init_cmd = commands.add_parser('init', help='初始化空SQLite账本；默认只检查')
    init_cmd.add_argument('--root', type=Path, default=argparse.SUPPRESS)
    init_cmd.add_argument('--effective', required=True)
    init_cmd.add_argument('--amounts', type=Path, help='可选四池额度JSON；省略时四池默认零，先记账观察')
    init_cmd.add_argument('--initial', type=Path)
    init_cmd.add_argument('--apply', action='store_true')
    commands.add_parser('inspect'); commands.add_parser('verify')
    find = commands.add_parser('find-records', help='只读定位待纠正记录，返回有限字段与真实记录ID')
    find.add_argument('--month')
    find.add_argument('--query', default='')
    find.add_argument('--limit', type=int, default=20)
    commands.add_parser('inbox-status', help='只读查看手机账单本机收取状态')
    inbox_fetch = commands.add_parser('inbox-fetch', help='核验并归档手机账单；--apply才收取，不记账')
    inbox_fetch.add_argument('--apply', action='store_true')
    inbox_receipts = commands.add_parser('inbox-receipts', help='核验本机及账本证据并重试回执')
    inbox_receipts.add_argument('--apply', action='store_true')
    prepare = commands.add_parser('prepare'); prepare.add_argument('--source', nargs='+', required=True); prepare.add_argument('--output', type=Path, required=True)
    apply = commands.add_parser('import'); apply.add_argument('--input', type=Path, required=True); apply.add_argument('--apply', action='store_true')
    refresh = commands.add_parser('refresh'); refresh.add_argument('--apply', action='store_true')
    commands.add_parser('dashboard-sync', help='计算临时副本并更新本机展示快照，不改工作簿')
    app_export_cmd = commands.add_parser('app-export', help='从原生重算产物导出App V2账单快照（不发布）')
    app_export_cmd.add_argument('--output', type=Path, default=None)
    app_export_cmd.add_argument('--dry-run', action='store_true')
    dashboard = commands.add_parser('dashboard', help='启动本机私有Dashboard')
    dashboard.add_argument('--port', type=int, default=8765)
    dashboard.add_argument('--no-browser', action='store_true')
    migration = commands.add_parser('migrate-excel', help='迁移到候选数据库；默认只检查，--apply创建且不激活')
    migration.add_argument('--output', type=Path, required=True)
    migration.add_argument('--apply', action='store_true')
    excel_export = commands.add_parser('export-excel', help='导出只供查看的Excel副本')
    excel_export.add_argument('--output', type=Path, required=True)
    backup = commands.add_parser('backup', help='使用SQLite Backup API备份')
    backup.add_argument('--output', type=Path, required=True)
    restore = commands.add_parser('restore', help='恢复到全新的候选数据库，不激活')
    restore.add_argument('--input', type=Path, required=True)
    restore.add_argument('--output', type=Path, required=True)
    restore.add_argument('--apply', action='store_true')
    budget = commands.add_parser('budget', help='追加有依据的四池预算生效月份')
    budget.add_argument('--effective', required=True)
    budget.add_argument('--amounts', type=Path, required=True)
    budget.add_argument('--reason', required=True)
    budget.add_argument('--apply', action='store_true')
    budget_set = commands.add_parser('budget-set', help='明确新增或修订所选预算生效月；只更新本地展示')
    budget_set.add_argument('--effective', required=True)
    budget_set.add_argument('--amounts', type=Path, required=True)
    budget_set.add_argument('--expected-hash', required=True)
    budget_set.add_argument('--reason', required=True)
    budget_set.add_argument('--apply', action='store_true')
    activate = commands.add_parser('activate-sqlite', help='通过独立迁移验收后显式切换主项目来源')
    activate.add_argument('--audit', type=Path, required=True)
    activate.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    import onboarding
    root = onboarding.project_root(args.root)
    if args.command in {'update-status', 'adopt-product', 'update'}:
        import product_update
        if args.command == 'update-status': result = product_update.status(root)
        elif args.command == 'adopt-product': result = product_update.adopt(root, profile=args.profile, apply=args.apply)
        else:
            result = product_update.update(root, release_dir=args.release_dir, apply=args.apply,
                                           prepare_environment=args.prepare_python)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok') else 1
    if args.command in {'doctor','init'}:
        if args.command == 'doctor': result = onboarding.doctor(root)
        else:
            amounts = json.loads(args.amounts.read_text(encoding='utf-8-sig')) if args.amounts else dict.fromkeys(POOLS, '0.00')
            initial = json.loads(args.initial.read_text(encoding='utf-8-sig')) if args.initial else None
            result = onboarding.initialize(root, args.effective, amounts, initial, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok', True) else 1
    if args.command in {'migrate-excel','restore','activate-sqlite'}:
        import ledger_migration
        result = ledger_migration.command(args, root)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result.get('ok', True) else 1
    if args.command == 'budget-set':
        import budget_settings
        _, selected, _ = budget_settings._paths(root)
        requested = ledger_source.resolve(root, workbook=args.workbook, database=args.database)
        if requested != selected:
            raise ValueError('预算设置只能使用本项目已启用的SQLite账本')
        with args.amounts.open('rb') as stream:
            amounts = budget_settings.read_payload(stream)
        payload = dict(effective=args.effective, amounts=amounts, expectedSourceHash=args.expected_hash, reason=args.reason)
        result = budget_settings.save(root, payload, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok') else 1
    args.workbook = ledger_source.resolve(root, workbook=args.workbook, database=args.database)
    if 'archive' in [p.lower() for p in args.workbook.resolve().parts]: raise ValueError('拒绝将archive当作当前工作簿')
    if ledger_source.is_database(args.workbook):
        return database_command(args)
    if args.command in {'backup','budget','export-excel','inbox-status','inbox-fetch','inbox-receipts','find-records'}:
        raise ValueError('该命令需要SQLite账本；请显式传--database或先按验收流程启用SQLite')
    if args.command == 'dashboard':
        from dashboard_server import serve
        serve(args.workbook, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == 'dashboard-sync':
        from dashboard_data import sync_snapshot
        result = sync_snapshot(args.workbook)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    if args.command == 'app-export':
        from app_export import export_snapshot
        result = export_snapshot(args.workbook, output=args.output, write=not args.dry_run)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    if args.command == 'inspect': result = inspect(args.workbook)
    elif args.command == 'verify': result = verify(args.workbook, require_native=True)
    elif args.command == 'prepare':
        existing,_ = ledger(args.workbook); records, skipped = parse_sources(args.source)
        new, duplicates, conflicts = prepare_records(existing,records)
        result = {'records':new,'updates':[],'duplicates':duplicates,'conflicts':conflicts,'skipped':skipped,'date_min':min((r['交易时间'] for r in records),default=None),'date_max':max((r['交易时间'] for r in records),default=None),'review_required':True}
        args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        result = {'output':str(args.output.resolve()),'new_records':len(new),'duplicates':duplicates,'conflicts':len(conflicts),'skipped':skipped,'review_required':True}
    elif args.command == 'import':
        approved = json.loads(args.input.read_text(encoding='utf-8-sig'))
        existing,_ = ledger(args.workbook); plan = plan_import(existing, approved)
        if args.apply:
            if plan['records'] and (not approved.get('reviewed_by') or not approved.get('reviewed_at')): raise ValueError('新增记录必须记录reviewed_by/reviewed_at')
            for r in plan['records']:
                if any(key not in r for key in ('最终财务事件','统计口径','计入消费金额','主分类','资金池','语义说明')): raise ValueError('新增记录缺少审核后的事件/口径/金额/分类/资金池/语义说明')
            batch = sha(args.input)
            for r in plan['records']: r['导入批次'] = batch
            result = safe_write(args.workbook,'Import',plan) if plan['records'] or plan['updates'] else {'applied':False,'reason':'完全重复，未修改工作簿'}
            result.update({'added':len(plan['records']),'updated':len(plan['updates']),'duplicates':plan['duplicates']})
            if result.get('ok'): update_dashboard(args.workbook,result)
        else: result = {'dry_run':True,'added':len(plan['records']),'updated':len(plan['updates']),'duplicates':plan['duplicates']}
    else:
        result = safe_write(args.workbook,'Recalculate') if args.apply else {'dry_run':True,'action':'Recalculate','workbook':str(args.workbook.resolve())}
        if args.apply and result.get('ok'): update_dashboard(args.workbook,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('ok', True) else 1


def database_command(args):
    """Same reviewed CLI and publisher gates, with a transactional SQLite source."""
    import ledger_store
    source = args.workbook
    if args.command in {'inbox-status', 'inbox-fetch', 'inbox-receipts'}:
        from bill_inbox import Inbox
        inbox = Inbox(ledger_source.project_root(source))
        if args.command == 'inbox-status': result = inbox.status()
        elif args.command == 'inbox-fetch': result = inbox.fetch(apply=args.apply)
        else: result = inbox.receipts(source, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok') else 1
    if args.command == 'dashboard':
        from dashboard_server import serve
        serve(source,port=args.port,open_browser=not args.no_browser)
        return 0
    if args.command == 'inspect': result = ledger_store.inspect(source)
    elif args.command == 'find-records':
        result = ledger_store.find_records(source, month=args.month, query=args.query, limit=args.limit)
    elif args.command == 'verify': result = ledger_store.verify(source)
    elif args.command == 'prepare':
        sources=[{'path':str(Path(path).resolve()),'sha256':sha(Path(path).resolve())} for path in args.source]
        records, skipped = parse_sources(args.source)
        if any(sha(Path(item['path']))!=item['sha256'] for item in sources):
            raise ValueError('解析期间原始账单发生变化，请重新prepare')
        new, duplicates, conflicts = prepare_records(ledger_store.read_state(source)['rows'], records)
        payload = dict(records=new, updates=[], duplicates=duplicates, conflicts=conflicts, skipped=skipped,
                       date_min=min((r['交易时间'] for r in records), default=None),
                       date_max=max((r['交易时间'] for r in records), default=None), review_required=True,
                       source_files=sources)
        if args.output.resolve() == source.resolve() or args.output.exists():
            raise ValueError('审核输出不能覆盖来源或已有文件')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        result = dict(output=str(args.output.resolve()),new_records=len(new),duplicates=duplicates,
                      conflicts=len(conflicts),skipped=skipped,review_required=True)
    elif args.command == 'import':
        approved = json.loads(args.input.read_text(encoding='utf-8-sig'))
        if not isinstance(approved, dict):
            raise ValueError('审核JSON结构无效')
        if approved.get('records') and not approved.get('source_files'):
            raise ValueError('新增账单必须保留 prepare 生成的 source_files；历史无来源批次不作新导入模板')
        if not args.apply:
            result = ledger_store.import_reviewed(source, approved, apply=False)
        else:
            from bill_inbox import Inbox
            inbox = Inbox(ledger_source.project_root(source))
            with inbox.lock():
                result = ledger_store.import_reviewed(source, approved, apply=True)
                if result.get('ok'):
                    if result.get('applied'):
                        try: update_dashboard(source, result)
                        except Exception:
                            result['dashboard'] = dict(ok=False, error='账本已提交，展示或发布后处理失败', previousSnapshotPreserved=True)
                    try:
                        hashes = {s['sha256'].lower() for s in approved.get('source_files', [])}
                        result['inboxReceipts'] = inbox.receipts(source, apply=True, allow_noop=hashes, locked=True)
                    except Exception:
                        result['inboxReceipts'] = dict(ok=False, error='账本已提交，本机账单回执待重试')
    elif args.command == 'budget':
        amounts = json.loads(args.amounts.read_text(encoding='utf-8-sig'))
        result = ledger_store.append_budget(source,args.effective,amounts,args.reason,apply=args.apply)
        if result.get('applied') and result.get('ok'): update_dashboard(source,result)
    elif args.command == 'refresh':
        result = ledger_store.verify(source)
        if args.apply and result.get('ok'): update_dashboard(source,result)
        else: result['dry_run'] = True
    elif args.command == 'dashboard-sync':
        from dashboard_data import sync_snapshot
        result = sync_snapshot(source)
    elif args.command == 'app-export':
        from app_export import export_snapshot
        result = export_snapshot(source,output=args.output,write=not args.dry_run)
    elif args.command == 'backup':
        result = ledger_store.backup(source,args.output)
    elif args.command == 'export-excel':
        from ledger_excel import export_excel
        result = export_excel(source,args.output)
    else: raise ValueError('该操作不支持SQLite账本')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('ok',True) else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try: sys.exit(main())
    except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False),file=sys.stderr); sys.exit(1)
    except sqlite3.Error:
        print(json.dumps({'ok':False,'error':'SQLite核验或写入失败；事务已回滚，请检查本地账本。'},ensure_ascii=False),file=sys.stderr)
        sys.exit(1)
