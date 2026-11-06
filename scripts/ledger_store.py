"""SQLite ledger authority with reviewed, atomic and fully audited writes.

Excel is accepted only by the one-time migration entry point. Monetary columns
are integer cents; the 36 source fields retain distinct null and empty values.
No operational API recalculates or rewrites the source workbook.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

import account
import ledger_calculation as calculation

SCHEMA_VERSION = 1
FIELDS = account.HEADERS[:36]
MONEY_COLUMNS = {'原始金额': 'original_cents', '退款调整': 'refund_cents',
                 '净支出候选': 'candidate_cents', '计入消费金额': 'amount_cents'}
ALLOCATIONS = ('分摊', '派生', '派生分摊', 'allocation', 'DERIVED')


def is_database(path):
    return Path(path).suffix.lower() in ('.sqlite3', '.sqlite', '.db')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _require_explicit_money(value, field):
    if _blank(value) or str(value).strip() in ('/', '--'):
        raise ValueError(f'{field}必须明确提供金额；缺失或占位值不得转换为0')
    account.money(value)


def _source_manifest(value):
    """Validate declared provenance without guessing or re-reading old sources."""
    if not isinstance(value, list):
        raise ValueError('source_files必须为来源文件及SHA256列表')
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'}:
            raise ValueError('来源项必须仅包含path和sha256')
        path, digest = item['path'], item['sha256']
        if not isinstance(path, str) or not path.strip() or '\x00' in path:
            raise ValueError('来源path必须为非空路径文本')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdefABCDEF' for c in digest):
            raise ValueError('来源sha256必须为64位十六进制文本')
        identity = os.path.normcase(os.path.normpath(path))
        if identity in seen:
            raise ValueError('来源文件路径重复或哈希冲突')
        seen.add(identity)
    return value


def _canonical_row(row):
    if set(row) - set(FIELDS):
        raise ValueError('流水只允许原始36字段；公式辅助字段不进入数据库')
    result = {key: row.get(key) for key in FIELDS}
    account.raw_money(result['原始金额'])
    result['交易时间'] = account.timestamp(result['交易时间'])
    for key in MONEY_COLUMNS:
        if not _blank(result[key]):
            if str(result[key]).strip() in ('/', '--'):
                raise ValueError('金额占位符不能作为0')
            result[key] = str(account.money(result[key]))
    for key in ('记录ID', '原记录ID', '关联记录ID', '交易订单号', '商家订单号'):
        if result[key] is not None and not isinstance(result[key], str):
            raise ValueError('订单号和关联ID必须为文本')
    return result


def _logical_payload(state):
    # A batch is provenance, not a business-value change; audit has its own trail.
    rows = [{k: r.get(k) for k in FIELDS if k != '导入批次'} for r in state['rows']]
    payload = dict(calculationVersion=calculation.calculation_version(state),
                rows=sorted(rows, key=lambda r: r['记录ID']),
                rates=sorted(state['rates'], key=lambda r: r['生效月份']), initial=state['initial'])
    if state.get('budgetMode') is not None:
        payload['budgetMode'] = state['budgetMode']
    return payload


def _hash(state):
    return hashlib.sha256(_json(_logical_payload(state)).encode('utf-8')).hexdigest()


def _sqlite_uri(path):
    # The Windows native host canonicalizes local paths to \\?\C:\... .
    # pathlib encodes that prefix as a URI authority (%3F), which SQLite
    # rejects. Keep the filesystem path intact; normalize only its URI form.
    uri_path = path
    if os.name == 'nt' and str(path).startswith('\\\\?\\') and not str(path).startswith('\\\\?\\UNC\\'):
        uri_path = Path(str(path)[4:])
    return uri_path.as_uri()


@contextmanager
def _connect(path, writable=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(_sqlite_uri(path) + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=5,
                           isolation_level=None)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN IMMEDIATE' if writable else 'BEGIN')
        yield conn
        if writable:
            conn.commit()
        else:
            conn.rollback()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _writer_lock(path):
    lock = Path(str(Path(path).resolve()) + '.write.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    # OS advisory locks survive an existing marker and are released on crashes.
    # The marker remains reusable; removing it would race another waiting writer.
    stream = open(lock, 'a+b')
    acquired = False
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise RuntimeError('账本正在写入或备份，请稍后重试') from None
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def _schema(conn):
    conn.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
    CREATE TABLE ledger(
        record_id TEXT PRIMARY KEY NOT NULL,
        sequence INTEGER NOT NULL UNIQUE,
        original_key TEXT UNIQUE,
        parent_id TEXT REFERENCES ledger(record_id) DEFERRABLE INITIALLY DEFERRED,
        linked_id TEXT REFERENCES ledger(record_id) DEFERRABLE INITIALLY DEFERRED,
        original_cents INTEGER,
        refund_cents INTEGER,
        candidate_cents INTEGER,
        amount_cents INTEGER,
        row_json TEXT NOT NULL CHECK(json_valid(row_json))
    ) STRICT;
    CREATE TABLE budget_rates(
        effective_month TEXT PRIMARY KEY,
        operation_cents INTEGER NOT NULL CHECK(operation_cents>=0),
        free_cents INTEGER NOT NULL CHECK(free_cents>=0),
        travel_cents INTEGER NOT NULL CHECK(travel_cents>=0),
        growth_cents INTEGER NOT NULL CHECK(growth_cents>=0)
    ) STRICT;
    CREATE TABLE initial_carry(pool TEXT PRIMARY KEY, amount_cents INTEGER NOT NULL) STRICT;
    CREATE TABLE import_batches(
        batch_id TEXT PRIMARY KEY NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('migration','import')),
        source_files_json TEXT CHECK(source_files_json IS NULL OR json_valid(source_files_json)),
        reviewed_by TEXT NOT NULL CHECK(length(trim(reviewed_by))>0),
        reviewed_at TEXT NOT NULL CHECK(length(trim(reviewed_at))>0),
        records_added INTEGER NOT NULL CHECK(records_added>=0),
        records_updated INTEGER NOT NULL CHECK(records_updated>=0),
        duplicates INTEGER NOT NULL CHECK(duplicates>=0),
        revision INTEGER NOT NULL UNIQUE CHECK(revision>0),
        before_source_hash TEXT CHECK(before_source_hash IS NULL OR length(before_source_hash)=64),
        after_source_hash TEXT NOT NULL CHECK(length(after_source_hash)=64)
    ) STRICT;
    CREATE TABLE audit_log(
        id INTEGER PRIMARY KEY,
        revision INTEGER NOT NULL,
        operation TEXT NOT NULL,
        record_id TEXT,
        before_json TEXT,
        after_json TEXT,
        reason TEXT NOT NULL,
        confirmation_source TEXT NOT NULL,
        reviewer TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        written_at TEXT NOT NULL
    ) STRICT;
    CREATE TRIGGER budget_no_update BEFORE UPDATE ON budget_rates
        BEGIN SELECT RAISE(ABORT,'预算历史不可覆盖'); END;
    CREATE TRIGGER budget_no_delete BEFORE DELETE ON budget_rates
        BEGIN SELECT RAISE(ABORT,'预算历史不可删除'); END;
    CREATE TRIGGER initial_no_update BEFORE UPDATE ON initial_carry
        BEGIN SELECT RAISE(ABORT,'初始滚存不可覆盖'); END;
    CREATE TRIGGER initial_no_delete BEFORE DELETE ON initial_carry
        BEGIN SELECT RAISE(ABORT,'初始滚存不可删除'); END;
    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT,'审计记录不可覆盖'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT,'审计记录不可删除'); END;
    CREATE TRIGGER batch_no_update BEFORE UPDATE ON import_batches
        BEGIN SELECT RAISE(ABORT,'导入批次不可覆盖'); END;
    CREATE TRIGGER batch_no_delete BEFORE DELETE ON import_batches
        BEGIN SELECT RAISE(ABORT,'导入批次不可删除'); END;
    CREATE TRIGGER ledger_identity_immutable BEFORE UPDATE OF record_id,original_key,sequence ON ledger
        BEGIN SELECT RAISE(ABORT,'原始身份不可修改'); END;
    CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger
        BEGIN SELECT RAISE(ABORT,'原始流水不可删除'); END;
    ''')
    expressions = [f"json_extract(NEW.row_json,'$.\"{key}\"') IS NOT json_extract(OLD.row_json,'$.\"{key}\"')" for key in account.RAW]
    conn.execute('CREATE TRIGGER ledger_raw_immutable BEFORE UPDATE OF row_json ON ledger WHEN ' +
                 ' OR '.join(expressions) + " BEGIN SELECT RAISE(ABORT,'原始字段不可修改'); END")
    conn.executemany('INSERT INTO metadata VALUES(?,?)', [('schema_version', str(SCHEMA_VERSION)),
                     ('calculation_version', calculation.CALCULATION_VERSION), ('revision', '0')])


def _read(conn):
    metadata = dict(conn.execute('SELECT key,value FROM metadata'))
    if metadata.get('schema_version') != str(SCHEMA_VERSION):
        raise ValueError('不支持的SQLite账本版本')
    expected_version = (calculation.OBSERVATION_CALCULATION_VERSION
                        if metadata.get('budget_mode') == calculation.OBSERVATION_MODE else calculation.CALCULATION_VERSION)
    if metadata.get('calculation_version') != expected_version:
        raise ValueError('账本计算规则版本不匹配')
    rows = [json.loads(value) for (value,) in conn.execute('SELECT row_json FROM ledger ORDER BY sequence')]
    rates = [{'生效月份': month + '-01', **{pool: str(account.money(Decimal(value) / 100)) for pool, value in zip(account.POOLS, values)}}
             for month, *values in conn.execute('SELECT * FROM budget_rates ORDER BY effective_month')]
    initial = {pool: str(account.money(Decimal(value) / 100)) for pool, value in conn.execute('SELECT pool,amount_cents FROM initial_carry')}
    state = dict(rows=rows, rates=rates, initial=initial, revision=int(metadata['revision']))
    if 'budget_mode' in metadata:
        if metadata['budget_mode'] != calculation.OBSERVATION_MODE:
            raise ValueError('不支持的预算计算模式')
        state['budgetMode'] = metadata['budget_mode']
    state['sourceHash'] = _hash(state)
    return state


def read_state(path):
    with _connect(path) as conn:
        return _read(conn)


def source_hash(path):
    return read_state(path)['sourceHash']


def _validate_state(state):
    if state.get('budgetMode') not in (None, calculation.OBSERVATION_MODE):
        raise ValueError('不支持的预算计算模式')
    rows = state['rows']
    ids, originals = {}, set()
    children = {}
    for row in rows:
        if _canonical_row(row) != row:
            raise ValueError('流水字段未规范化')
        identity = account.text(row.get('记录ID'))
        if not identity or identity in ids:
            raise ValueError('流水记录ID为空或重复')
        if not account.text(row.get('平台')):
            raise ValueError('流水缺少平台')
        if account.text(row.get('资金池')) not in ('', *account.POOLS, '待归类'):
            raise ValueError('资金池值不在四池或待归类范围')
        if account.text(row.get('统计口径')).upper() == 'INCOME':
            # Income stays outside consumption, pools and refund netting;
            # reversals are separate negative rows linked to the original.
            if calculation.cents(row.get('计入消费金额')) != 0:
                raise ValueError('收入记录不得携带非零计入消费金额')
            if account.text(row.get('资金池')):
                raise ValueError('收入记录不分配资金池')
            if calculation.cents(row.get('退款调整')) != 0:
                raise ValueError('收入冲回应使用关联的负向收入记录，不使用退款调整字段')
            if not account.text(row.get('主分类')):
                raise ValueError('收入记录缺少主分类')
        derived = account.text(row.get('记录类型')) in ALLOCATIONS
        if derived != identity.startswith('alloc_'):
            raise ValueError('流水类型和ID前缀不一致')
        if not derived:
            key = account.original_key(row)[0]
            if key in originals:
                raise ValueError('平台订单身份重复')
            originals.add(key)
            if account.normalize_record(row)['记录ID'] != identity:
                raise ValueError('原交易身份与记录ID不一致')
        ids[identity] = row
    for row in rows:
        identity = row['记录ID']
        parent, linked = account.text(row.get('原记录ID')), account.text(row.get('关联记录ID'))
        if parent:
            if parent not in ids or parent == identity or not identity.startswith('alloc_') or ids[parent]['记录ID'].startswith('alloc_'):
                raise ValueError('分摊必须指向存在的原始父记录')
            children.setdefault(parent, []).append(row)
        elif identity.startswith('alloc_'):
            raise ValueError('分摊缺少父记录')
        if linked and (linked not in ids or linked == identity):
            raise ValueError('关联记录不存在或自关联')
        if account.text(row.get('统计口径')).upper() == 'INCOME':
            amount = calculation.cents(row['原始金额'])
            reversal = account.text(row.get('最终财务事件')) == 'income_reversal'
            if amount < 0 and not reversal:
                raise ValueError('负向收入必须标明 income_reversal 并关联原收入')
            if reversal:
                original = ids.get(linked)
                if (amount >= 0 or original is None
                        or account.text(original.get('统计口径')).upper() != 'INCOME'
                        or calculation.cents(original['原始金额']) <= 0
                        or account.text(original.get('最终财务事件')) == 'income_reversal'
                        or original['记录ID'].startswith('alloc_')):
                    raise ValueError('收入冲回必须是负向金额，并关联原始正向收入记录')
    for parent, allocated in children.items():
        source = ids[parent]
        expected = calculation.cents(source['原始金额']) - calculation.cents(source.get('退款调整'))
        actual = calculation.cents(source.get('计入消费金额')) + sum(calculation.cents(r.get('计入消费金额')) for r in allocated)
        if actual != expected:
            raise ValueError('父记录剩余消费与分摊合计不等于原交易净额')
    rates = state['rates']
    months = [calculation.month_key(rate['生效月份']) for rate in rates]
    if not rates or len(months) != len(set(months)):
        raise ValueError('预算生效月份缺失或重复')
    for rate in rates:
        if set(rate) != {'生效月份', *account.POOLS}:
            raise ValueError('预算必须明确包含完整四池')
        if any(_blank(rate[p]) or calculation.cents(rate[p]) < 0 for p in account.POOLS):
            raise ValueError('四池预算必须为非负明确金额')
    if set(state['initial']) != set(account.POOLS) or any(_blank(v) for v in state['initial'].values()):
        raise ValueError('初始滚存必须包含完整四池明确金额')
    if any(calculation.cents(state['initial'][p]) != 0 for p in account.POOLS[:2]):
        raise ValueError('运行及随心不允许初始滚存')
    calculation.aggregate(rows)
    return True


def _check_sql(conn):
    if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite完整性检查失败')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('SQLite外键检查失败')
    for record_id, parent, linked, original_key, *values, raw in conn.execute(
            'SELECT record_id,parent_id,linked_id,original_key,original_cents,refund_cents,candidate_cents,amount_cents,row_json FROM ledger'):
        row = json.loads(raw)
        expected_money = [None if _blank(row.get(k)) else calculation.cents(row[k]) for k in MONEY_COLUMNS]
        expected_key = None if record_id.startswith('alloc_') else account.original_key(row)[0]
        if (record_id != row['记录ID'] or parent != (account.text(row['原记录ID']) or None)
                or linked != (account.text(row['关联记录ID']) or None) or original_key != expected_key or values != expected_money):
            raise ValueError('SQLite类型列与权威字段不一致')
    for kind, manifest, added, updated, revision in conn.execute(
            'SELECT kind,source_files_json,records_added,records_updated,revision FROM import_batches'):
        if manifest is not None:
            _source_manifest(json.loads(manifest))
        if kind == 'import':
            audited = dict(conn.execute('SELECT operation,count(*) FROM audit_log WHERE revision=? GROUP BY operation', (revision,)))
            if audited.get('insert', 0) != added or audited.get('update', 0) != updated:
                raise ValueError('导入批次笔数与逐笔审计不一致')
        else:
            audited = conn.execute("SELECT after_json FROM audit_log WHERE revision=? AND operation='migration'", (revision,)).fetchall()
            if len(audited) != 1 or len(json.loads(audited[0][0])['rows']) != added or updated != 0:
                raise ValueError('迁移批次笔数与迁移审计不一致')


def _write_row(conn, row, sequence=None):
    identity = row['记录ID']
    values = [None if _blank(row[k]) else calculation.cents(row[k]) for k in MONEY_COLUMNS]
    parent, linked = account.text(row.get('原记录ID')) or None, account.text(row.get('关联记录ID')) or None
    if sequence is not None:
        key = None if identity.startswith('alloc_') else account.original_key(row)[0]
        conn.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (identity, sequence, key, parent, linked, *values, _json(row)))
    else:
        conn.execute('UPDATE ledger SET parent_id=?,linked_id=?,original_cents=?,refund_cents=?,candidate_cents=?,amount_cents=?,row_json=? WHERE record_id=?',
                     (parent, linked, *values, _json(row), identity))


def _audit(conn, revision, operation, before, after, reason, confirmation, reviewer, reviewed_at, identity=None):
    conn.execute('''INSERT INTO audit_log(revision,operation,record_id,before_json,after_json,reason,
                    confirmation_source,reviewer,reviewed_at,written_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                 (revision, operation, identity, _json(before) if before is not None else None,
                  _json(after) if after is not None else None, reason, confirmation, reviewer,
                  reviewed_at, datetime.now(account.TZ).isoformat()))


def _record_batch(conn, batch_id, kind, sources, reviewer, reviewed_at, added, updated, duplicates,
                  revision, before_hash, after_hash):
    conn.execute('INSERT INTO import_batches VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                 (batch_id, kind, _json(sources) if sources is not None else None, reviewer, reviewed_at,
                  added, updated, duplicates, revision, before_hash, after_hash))


def _excel_state(path):
    import openpyxl
    rows, _ = account.ledger(Path(path))
    rows = [_canonical_row({k: row.get(k) for k in FIELDS}) for row in rows]
    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        sheet = wb['02_资金池']
        if 'BudgetRates' not in sheet.tables:
            raise ValueError('缺少BudgetRates参数表')
        left, top, right, bottom = openpyxl.utils.cell.range_boundaries(sheet.tables['BudgetRates'].ref)
        headers = [sheet.cell(top, col).value for col in range(left, right + 1)]
        if headers != ['生效月份', *account.POOLS]:
            raise ValueError('BudgetRates字段不符合契约')
        rates = []
        for values in sheet.iter_rows(min_row=top + 1, max_row=bottom, min_col=left, max_col=right, values_only=True):
            rate = dict(zip(headers, values))
            if any(_blank(rate[p]) for p in account.POOLS):
                raise ValueError('预算金额缓存缺失')
            rates.append({'生效月份': calculation.month_key(rate['生效月份']) + '-01',
                          **{p: str(account.money(rate[p])) for p in account.POOLS}})
        initial = {p: sheet.cell(i, 3).value for i, p in enumerate(account.POOLS, 7)}
        if any(_blank(v) for v in initial.values()):
            raise ValueError('初始滚存缓存缺失')
        initial = {p: str(account.money(v)) for p, v in initial.items()}
        return dict(rows=rows, rates=rates, initial=initial)
    finally:
        wb.close()


def migrate_excel(workbook, database):
    """Create a separate candidate; refuse existing targets and compare all fields."""
    workbook, database = Path(workbook).resolve(), Path(database).resolve()
    if database.exists():
        raise FileExistsError(database)
    if not is_database(database):
        raise ValueError('SQLite账本扩展名必须为.sqlite3/.sqlite/.db')
    original_hash = account.sha(workbook)
    state = _excel_state(workbook)
    _validate_state(state)
    database.parent.mkdir(parents=True, exist_ok=True)
    with _writer_lock(database):
        if database.exists():
            raise FileExistsError(database)
        fd, tempname = tempfile.mkstemp(prefix='.ledger-migrate-', suffix='.sqlite3', dir=database.parent)
        os.close(fd)
        temporary = Path(tempname)
        try:
            with closing(sqlite3.connect(temporary)) as conn, conn:
                _schema(conn)
                conn.commit()
                conn.execute('BEGIN IMMEDIATE')
                for sequence, row in enumerate(state['rows'], 1):
                    _write_row(conn, row, sequence)
                for rate in state['rates']:
                    conn.execute('INSERT INTO budget_rates VALUES(?,?,?,?,?)',
                                 (calculation.month_key(rate['生效月份']), *(calculation.cents(rate[p]) for p in account.POOLS)))
                conn.executemany('INSERT INTO initial_carry VALUES(?,?)', [(p, calculation.cents(state['initial'][p])) for p in account.POOLS])
                conn.execute("UPDATE metadata SET value='1' WHERE key='revision'")
                conn.execute('INSERT INTO metadata VALUES(?,?)', ('migration_source_sha256', original_hash))
                reviewed_at = datetime.now(account.TZ).isoformat()
                _record_batch(conn, 'migration-' + original_hash[:20], 'migration',
                              [{'path': str(workbook), 'sha256': original_hash}], 'account-migrate',
                              reviewed_at, len(state['rows']), 0, 0, 1, None, _hash(state))
                _audit(conn, 1, 'migration', None, _logical_payload(state), '用户授权SQLite候选迁移',
                       '本地源工作簿逐字段核对', 'account-migrate', reviewed_at)
                _check_sql(conn)
                migrated = _read(conn)
                _validate_state(migrated)
                if migrated['rows'] != state['rows'] or migrated['rates'] != state['rates'] or migrated['initial'] != state['initial']:
                    raise ValueError('迁移36字段或预算参数逐字段不一致')
            if account.sha(workbook) != original_hash:
                raise RuntimeError('迁移期间源工作簿发生变化')
            # Hard-link creation is atomic and never replaces a target created by another writer.
            os.link(temporary, database)
        finally:
            temporary.unlink(missing_ok=True)
    result = verify(database)
    result.update(migrated=True, sourceWorkbookHash=original_hash, fieldsChecked=36)
    return result


def _describe(path, state):
    dates = [r['交易时间'] for r in state['rows']]
    amounts = calculation.aggregate(state['rows'])
    incomes = calculation.income_aggregate(state['rows'])
    def as_money(value):
        return str(account.money(Decimal(value) / 100))
    aggregates = {m: {k: {p: as_money(v) for p, v in val.items()} if isinstance(val, dict) else as_money(val)
                       for k, val in item.items()} for m, item in sorted(amounts.items())}
    for month, entry in aggregates.items():
        income = incomes.get(month, {'incomeCents': 0, 'count': 0})
        entry['incomeCents'] = income['incomeCents']
        entry['incomeCount'] = income['count']
    return dict(database=str(Path(path).resolve()), sourceHash=state['sourceHash'], sha256=state['sourceHash'],
                revision=state['revision'], rows=len(state['rows']), date_min=min(dates, default=None),
                date_max=max(dates, default=None), duplicate_original_keys=0, aggregates=aggregates,
                calculationVersion=calculation.calculation_version(state))


def inspect(path):
    return _describe(path, read_state(path))


def verify(path):
    errors = []
    try:
        with _connect(path) as conn:
            _check_sql(conn)
            state = _read(conn)
            _validate_state(state)
        result = _describe(path, state)
    except (ValueError, sqlite3.DatabaseError, KeyError, TypeError) as exc:
        errors.append(str(exc))
        result = dict(database=str(Path(path).resolve()))
    return {**result, 'ok': not errors, 'errors': errors, 'integrity_checked': not errors,
            'foreign_keys_checked': not errors, 'formula_errors': [], 'calculation_engine': 'sqlite-python'}


def _copy_backup(path, output, replace=False):
    path, output = Path(path).resolve(), Path(output).resolve()
    if path == output:
        raise ValueError('备份目标不能覆盖源账本')
    if output.exists() and not replace:
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tempname = tempfile.mkstemp(prefix='.ledger-backup-', suffix='.sqlite3', dir=output.parent)
    os.close(fd)
    temporary = Path(tempname)
    try:
        with closing(sqlite3.connect(_sqlite_uri(path) + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination, destination:
                source.backup(destination)
        report = verify(temporary)
        if not report['ok']:
            raise ValueError('备份验证失败')
        if replace:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(ok=True, backup=str(output), sourceHash=source_hash(output))


def backup(path, output):
    with _writer_lock(path):
        with _connect(path, writable=True):
            return _copy_backup(path, output)


def restore(backup, output):
    if Path(output).exists():
        raise FileExistsError(output)
    with _writer_lock(output):
        with _writer_lock(backup):
            with _connect(backup, writable=True):
                return _copy_backup(backup, output)


def _prewrite_backup(path):
    path = Path(path)
    return _copy_backup(path, path.with_name(path.stem + '.previous.sqlite3'), replace=True)


def classification_review(rows):
    consumption = [r for r in rows if account.text(r.get('统计口径')).upper() in ('PERSONAL', 'PROJECT')
                   and calculation.cents(r.get('计入消费金额')) != 0]
    pending = [r for r in consumption if account.text(r.get('资金池')) not in account.POOLS]
    unclassified = [r for r in consumption if account.text(r.get('主分类')).casefold()
                    in ('', 'unknown', '待归类', '未填写分类')]
    warnings = []
    if pending:
        warnings.append('本批消费全部待分池；请按用途审核，真实不确定的说明依据后留待确认。' if len(pending) == len(consumption)
                        else '本批仍有消费未分池；需说明待确认范围，不将剩余额度称为完整结果。')
    if unclassified:
        warnings.append('本批仍有消费缺少明确主分类；不能把未审核称为分类完成。')
    return dict(scope='changed-records', reviewComplete=not (pending or unclassified), consumptionRecords=len(consumption),
                assignedPoolRecords=len(consumption)-len(pending), unassignedPoolRecords=len(pending),
                unassignedPoolCents=sum(calculation.cents(r['计入消费金额']) for r in pending),
                unclassifiedRecords=len(unclassified), warnings=warnings)


def find_records(path, *, month=None, query='', limit=20):
    """Bounded read-only lookup for a user's correction; never expose order IDs."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError('查询 limit 必须为 1 到 100')
    if month is not None:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('查询月份必须为 YYYY-MM')
        calculation.month_key(month)  # Reject invalid calendar months.
    query = account.text(query).casefold()
    if not month and not query:
        raise ValueError('定位记录需要 --month 或 --query，不能无条件输出账本')
    with _connect(path) as conn:
        rows = _read(conn)['rows']
    matches = [row for row in rows if (not month or row['交易时间'].startswith(month))
               and (not query or any(query in account.text(row.get(key)).casefold()
                    for key in ('交易对方', '商品/说明', '主分类', '语义说明')))]
    matches.sort(key=lambda row: (row['交易时间'], row['记录ID']), reverse=True)
    fields = ('记录ID', '交易时间', '交易对方', '商品/说明', '主分类', '资金池', '统计口径', '计入消费金额')
    return dict(ok=True, dryRun=True, matchedRecords=len(matches), truncated=len(matches) > limit,
                records=[{key: row.get(key) for key in fields} for row in matches[:limit]])


def import_reviewed(path, approved, apply=False):
    """Plan with the legacy reviewed JSON contract, then validate before commit."""
    if not isinstance(approved, dict):
        raise ValueError('审核JSON结构无效')
    sources = _source_manifest(approved['source_files']) if 'source_files' in approved else None
    import review_provenance
    review_provenance.validate(approved, sources)
    legacy_approved = {k: v for k, v in approved.items() if k != 'source_files'}
    def operation(conn):
        before = _read(conn)
        # Legacy normalization maps blanks to zero; preserve their source meaning.
        existing_ids = {r['记录ID'] for r in before['rows']}
        existing_originals = {account.original_key(r)[0] for r in before['rows'] if not r['记录ID'].startswith('alloc_')}
        for row in approved.get('records', []):
            _require_explicit_money(row.get('原始金额'), '原始金额')
            derived = account.text(row.get('记录类型')) in ALLOCATIONS
            existing = row.get('记录ID') in existing_ids or (not derived and account.original_key(row)[0] in existing_originals)
            if not existing or '计入消费金额' in row:
                _require_explicit_money(row.get('计入消费金额'), '计入消费金额')
            if any(isinstance(v, str) and v.startswith('=') for v in row.values()):
                raise ValueError('禁止审核JSON注入公式')
        for update in approved.get('updates', []):
            changes = update.get('changes', {})
            if isinstance(changes, dict):
                for field in account.MONEY_FIELDS & changes.keys():
                    _require_explicit_money(changes[field], field)
        plan = account.plan_import(before['rows'], legacy_approved)
        for clean in plan['records']:
            original = next((r for r in approved.get('records', []) if account.normalize_record(r)['记录ID'] == clean['记录ID']), None)
            if original is not None:
                for key in account.RAW:
                    if key in original and key not in ('原始金额', '交易时间'):
                        clean[key] = original[key]
            for key in MONEY_COLUMNS:
                if original is not None and key in original and _blank(original[key]):
                    clean[key] = original[key]
        records, updates = plan['records'], plan['updates']
        changed = bool(records or updates)
        if changed and (not account.text(approved.get('reviewed_by')) or not account.text(approved.get('reviewed_at'))):
            raise ValueError('导入需提供reviewed_by及reviewed_at审定信息')
        batch_payload = {k: v for k, v in approved.items() if k not in ('reviewed_at',)}
        batch_seed = {'reviewed': batch_payload, 'before_source_hash': before['sourceHash'], 'before_revision': before['revision']}
        batch = 'sqlite-' + hashlib.sha256(_json(batch_seed).encode()).hexdigest()[:20]
        for row in records:
            required = ('最终财务事件', '统计口径', '计入消费金额', '主分类', '资金池', '语义说明')
            if any(key not in row or row[key] is None for key in required):
                raise ValueError('新增记录缺少必要审定字段')
            row['导入批次'] = batch
        next_rows = [dict(row) for row in before['rows']]
        by_id = {r['记录ID']: r for r in next_rows}
        for row in records:
            clean = _canonical_row(row)
            next_rows.append(clean)
            by_id[clean['记录ID']] = clean
        for update in updates:
            by_id[update['记录ID']].update(update['changes'])
        next_rows = [_canonical_row(r) for r in next_rows]
        candidate = {**before, 'rows': next_rows}
        _validate_state(candidate)
        result = {'duplicates': plan['duplicates'], 'ok': True, 'applied': False, 'added': len(records), 'updated': len(updates),
                  'sourceHash': before['sourceHash'], 'revision': before['revision']}
        affected_ids = {r['记录ID'] for r in records} | {u['记录ID'] for u in updates}
        result['classificationReview'] = classification_review([r for r in next_rows if r['记录ID'] in affected_ids])
        if not apply or not changed:
            return result
        if sources is not None:
            review_provenance.assert_hashes(sources)
        _prewrite_backup(path)
        revision = before['revision'] + 1
        _record_batch(conn, batch, 'import', sources, approved['reviewed_by'], approved['reviewed_at'],
                      len(records), len(updates), plan['duplicates'], revision, before['sourceHash'], _hash(candidate))
        before_ids = {r['记录ID']: r for r in before['rows']}
        after_ids = {r['记录ID']: r for r in next_rows}
        for index, row in enumerate(records, len(before['rows']) + 1):
            clean = after_ids[row['记录ID']]
            _write_row(conn, clean, index)
            _audit(conn, revision, 'insert', None, clean, account.text(approved.get('reason')) or clean['语义说明'],
                   account.text(approved.get('confirmation_source')) or clean['语义说明'],
                   approved['reviewed_by'], approved['reviewed_at'], clean['记录ID'])
        for update in updates:
            identity = update['记录ID']
            _write_row(conn, after_ids[identity])
            _audit(conn, revision, 'update', before_ids.get(identity), after_ids[identity], update['reason'],
                   update['confirmation_source'], approved['reviewed_by'], approved['reviewed_at'], identity)
        conn.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(revision),))
        _check_sql(conn)
        after = _read(conn)
        _validate_state(after)
        if after['rows'] != next_rows:
            raise ValueError('事务写入后逐字段核对失败')
        return {**result, 'applied': True, 'sourceHash': after['sourceHash'], 'revision': revision}
    if apply:
        with _writer_lock(path):
            with _connect(path, writable=True) as conn:
                return operation(conn)
    with _connect(path) as conn:
        return operation(conn)


def append_budget(path, effective, amounts, reason, apply=False):
    month = calculation.month_key(effective)
    if set(amounts) != set(account.POOLS) or any(_blank(v) or calculation.cents(v) < 0 for v in amounts.values()):
        raise ValueError('预算必须明确给出完整四池非负金额')
    if not account.text(reason):
        raise ValueError('预算变更必须记录理由与确认依据')
    rate = {'生效月份': month + '-01', **{p: str(account.money(amounts[p])) for p in account.POOLS}}
    def operation(conn):
        before = _read(conn)
        if month <= max(calculation.month_key(r['生效月份']) for r in before['rates']):
            raise ValueError('预算只允许追加最新生效月份，不覆盖或插入历史')
        _validate_state({**before, 'rates': [*before['rates'], rate]})
        result = dict(ok=True, applied=False, rate=rate, sourceHash=before['sourceHash'], revision=before['revision'])
        if not apply:
            return result
        _prewrite_backup(path)
        revision = before['revision'] + 1
        conn.execute('INSERT INTO budget_rates VALUES(?,?,?,?,?)', (month, *(calculation.cents(rate[p]) for p in account.POOLS)))
        _audit(conn, revision, 'budget-append', before['rates'][-1], rate, reason, reason,
               'account-reviewed-budget', datetime.now(account.TZ).isoformat())
        conn.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(revision),))
        _check_sql(conn)
        after = _read(conn)
        _validate_state(after)
        return {**result, 'applied': True, 'revision': revision, 'sourceHash': after['sourceHash']}
    if apply:
        with _writer_lock(path):
            with _connect(path, writable=True) as conn:
                return operation(conn)
    with _connect(path) as conn:
        return operation(conn)


def _reviewed_budget_amounts(amounts):
    """Desktop amounts are exact cents; never silently round user input."""
    if not isinstance(amounts, dict) or set(amounts) != set(account.POOLS):
        raise ValueError('预算必须明确给出完整四池金额')
    limit = 2 ** 53 - 1
    result = {}
    for pool in account.POOLS:
        value = amounts[pool]
        if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{2}', value):
            raise ValueError('预算必须为非负金额文本，精确到两位小数')
        # Bound text before Decimal arithmetic or conversion to an integer.
        if len(value) > 17 or calculation.cents(value) > limit:
            raise ValueError('预算金额超出安全整数范围')
        result[pool] = value
    if sum(calculation.cents(v) for v in result.values()) > limit:
        raise ValueError('预算合计超出安全整数范围')
    return result


def _validate_budget_display_range(state):
    """Check current/historical aggregates, including inherited signed carry.

    Extrema occur at transaction months, rate boundaries and the last month;
    between these points monthly carries and safety only change linearly.
    This avoids calculating every intervening month repeatedly.
    """
    limit = 2 ** 53 - 1
    calendar_month = datetime.now(account.TZ).strftime('%Y-%m')
    points = {calendar_month, *(r['生效月份'][:7] for r in state['rates']),
              *(r['交易时间'][:7] for r in state['rows'])}
    for month in tuple(points):
        year, number = map(int, month.split('-'))
        if month != '0001-01':
            points.add(f'{year - (number == 1):04d}-{(number - 1) or 12:02d}')

    def check(value):
        if isinstance(value, dict):
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, int) and not isinstance(value, bool) and abs(value) > limit:
            raise ValueError('预算及滚存汇总超出安全整数范围')

    for month in sorted(points):
        check(calculation.calculate_month(state, month, calendar_month))


def set_budget(path, effective, amounts, reason, *, expected_source_hash,
               reviewer, confirmation_source, apply=False):
    """Explicitly insert or revise a reviewed effective month, with CAS.

    The legacy append API stays append-only. Revision of this parameter is
    allowed only inside the writer lock and BEGIN IMMEDIATE transaction; raw
    ledger fields and the schema/calculation versions are never modified.
    """
    if not isinstance(effective, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}', effective):
        raise ValueError('预算生效月份必须为YYYY-MM')
    month = calculation.month_key(effective)
    amounts = _reviewed_budget_amounts(amounts)
    if not isinstance(expected_source_hash, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_source_hash):
        raise ValueError('预算变更必须提供当前账本预期哈希，请刷新后重试')
    for value in (reason, reviewer, confirmation_source):
        if not isinstance(value, str) or not value.strip() or len(value) > 2000 or '\x00' in value:
            raise ValueError('预算变更必须记录理由、审定者和明确确认依据')
    rate = {'生效月份': month + '-01', **amounts}

    def operation(conn):
        before = _read(conn)
        if before['sourceHash'] != expected_source_hash:
            raise ValueError('账本已变化，预算预检已过期；请刷新后重试')
        current = next((r for r in before['rates'] if r['生效月份'][:7] == month), None)
        prior = next((r for r in reversed(before['rates']) if r['生效月份'][:7] <= month), None)
        following = next((r['生效月份'][:7] for r in before['rates'] if r['生效月份'][:7] > month), None)
        through = None
        if following:
            year, number = map(int, following.split('-'))
            through = f'{year - (number == 1):04d}-{(number - 1) or 12:02d}'
        changed = current != rate
        result = dict(ok=True, applied=False, changed=changed, effective=month, amounts=amounts,
                      previousAmounts={p: prior[p] for p in account.POOLS} if prior else None,
                      affectedThrough=through, sourceHash=before['sourceHash'], revision=before['revision'])
        candidate_rates = sorted([r for r in before['rates'] if r != current] + [rate], key=lambda r: r['生效月份'])
        candidate = {**before, 'rates': candidate_rates}
        _validate_state(candidate)
        _validate_budget_display_range(candidate)
        if not apply or not changed:
            return result
        _prewrite_backup(path)
        revision = before['revision'] + 1
        values = tuple(calculation.cents(amounts[p]) for p in account.POOLS)
        if current is None:
            conn.execute('INSERT INTO budget_rates VALUES(?,?,?,?,?)', (month, *values))
        else:
            trigger = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='budget_no_update'").fetchone()
            if trigger is None:
                raise ValueError('预算历史保护缺失，拒绝写入')
            # execute preserves the surrounding transaction; executescript would
            # implicitly commit and must never be used to suspend this guard.
            conn.execute('DROP TRIGGER budget_no_update')
            conn.execute('UPDATE budget_rates SET operation_cents=?,free_cents=?,travel_cents=?,growth_cents=? WHERE effective_month=?',
                         (*values, month))
            conn.execute(trigger[0])
        _audit(conn, revision, 'budget-revise' if current else 'budget-insert', current, rate,
               reason.strip(), confirmation_source.strip(), reviewer.strip(), datetime.now(account.TZ).isoformat())
        conn.execute("UPDATE metadata SET value=? WHERE key='revision'", (str(revision),))
        _check_sql(conn)
        after = _read(conn)
        _validate_state(after)
        if after['rows'] != before['rows'] or after['initial'] != before['initial'] or after['rates'] != candidate_rates:
            raise ValueError('预算事务写入后逐字段核对失败')
        return {**result, 'applied': True, 'sourceHash': after['sourceHash'], 'revision': revision}

    if apply:
        with _writer_lock(path):
            with _connect(path, writable=True) as conn:
                return operation(conn)
    with _connect(path) as conn:
        return operation(conn)
