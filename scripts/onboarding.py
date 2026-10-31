"""SQLite-first, local-only first-run setup. No cloud/network operations."""
from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import sys
import tempfile

import account
import ledger_calculation as calculation
import ledger_source
import ledger_store as store


def project_root(value):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('--root必须是完整绝对路径')
    if not path.is_dir():
        raise ValueError('--root目录不存在')
    return path.resolve()


def _month(value):
    if not isinstance(value, str) or len(value) != 7:
        raise ValueError('生效月份必须为YYYY-MM')
    return calculation.month_key(value)


def _amount(value):
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('金额必须明确提供数值')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
            raise ValueError('金额必须有限且最多两位小数')
        if abs(amount) * 100 > 9223372036854775807:
            raise ValueError('金额超出SQLite整数分范围')
        return str(amount.quantize(Decimal('.01')))
    except (InvalidOperation, OverflowError):
        raise ValueError('金额必须为有限数值') from None


def parameters(effective, amounts, initial=None):
    effective = _month(effective)
    if not isinstance(amounts, dict) or set(amounts) != set(account.POOLS):
        raise ValueError('预算必须完整包含四池：' + '、'.join(account.POOLS))
    amounts = {p: _amount(amounts[p]) for p in account.POOLS}
    if any(Decimal(v) < 0 for v in amounts.values()):
        raise ValueError('四池预算必须为非负金额')
    initial = {} if initial is None else initial
    if not isinstance(initial, dict) or set(initial) - set(account.POOLS):
        raise ValueError('初始滚存仅允许四池名称')
    initial = {p: _amount(initial.get(p, 0)) for p in account.POOLS}
    if any(Decimal(initial[p]) != 0 for p in account.POOLS[:2]):
        raise ValueError('运行及随心不允许初始滚存')
    return dict(effective=effective, amounts=amounts, initial=initial)


def _fingerprint(plan):
    return hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _flush_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _new_ledger(database, plan):
    fd, name = tempfile.mkstemp(prefix='.init-', suffix='.sqlite3', dir=database.parent)
    os.close(fd)
    draft = Path(name)
    try:
        with closing(sqlite3.connect(draft)) as conn:
            store._schema(conn)
            conn.commit()
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT INTO budget_rates VALUES(?,?,?,?,?)',
                         (plan['effective'], *(calculation.cents(plan['amounts'][p]) for p in account.POOLS)))
            conn.executemany('INSERT INTO initial_carry VALUES(?,?)',
                             [(p, calculation.cents(plan['initial'][p])) for p in account.POOLS])
            conn.execute('INSERT INTO metadata VALUES(?,?)', ('setup_fingerprint', _fingerprint(plan)))
            store._check_sql(conn)
            store._validate_state(store._read(conn))
            conn.commit()
        if not store.verify(draft)['ok']:
            raise ValueError('新建账本核验失败')
        with draft.open('r+b') as stream: os.fsync(stream.fileno())
        os.link(draft, database)  # atomic no-clobber; only fully verified DB appears
        _flush_directory(database.parent)
    finally:
        draft.unlink(missing_ok=True)


def _recoverable(database, plan):
    try:
        with store._connect(database) as conn:
            store._check_sql(conn)
            state = store._read(conn)
            store._validate_state(state)
            marker = conn.execute("SELECT value FROM metadata WHERE key='setup_fingerprint'").fetchone()
            expected_rates = [{'生效月份':plan['effective']+'-01', **plan['amounts']}]
            return (state['revision'] == 0 and not state['rows'] and marker == (_fingerprint(plan),)
                    and state['rates'] == expected_rates and state['initial'] == plan['initial'])
    except (OSError, ValueError, sqlite3.Error):
        return False


def _owner_templates(root):
    templates = {
        'local/档案.md': '# 我的记账档案\n\n称呼：待填写\n记账起始月份：待填写\n个人背景与账户用途：待本人补充\n\n请勿填写密码、令牌或完整账号。\n',
        'local/个人规则.md': '# 我的个人记账规则\n\n以下仅记录本人确认的规则；未确认分类保留待归类。\n\n- 用户确认与证据：待补充\n- 四池预算以SQLite中的budget_rates为准。\n- 初始滚存不是实际现金余额。\n',
        'PROJECT_MEMORY.md': '# Account 本机工作记忆\n\n此文件属于当前使用者，请由使用者和自己的Agent持续维护。\n\n先读 docs/工作说明.md、docs/记账规则.md、local/档案.md、local/个人规则.md。\ndata/account.sqlite3 是唯一当前账本；不得回退到Excel。\n记账、云端发布、安装与Git提交分别按使用者授权执行。\n具体额度及初始滚存只从数据库读取，不复制到此文件。\n'
    }
    for name, contents in templates.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open('x', encoding='utf-8') as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            pass


def _existing(root):
    source = root / 'data/source.json'
    if not source.exists(): return None
    database = ledger_source.resolve(root)
    verified = store.verify(database)
    if not verified['ok']: raise ValueError('已有来源或账本无效，请保留现场并检查；拒绝重新初始化')
    return dict(ok=True, applied=False, state='already_initialized', database=str(database),
                revision=verified['revision'], rows=verified['rows'], sourceHash=verified['sourceHash'])


def initialize(root, effective, amounts, initial=None, apply=False):
    root = project_root(root)
    plan = parameters(effective, amounts, initial)
    existing = _existing(root)
    if existing: return {**existing, 'dry_run': not apply}
    database = root / 'data/account.sqlite3'
    if database.exists() and not _recoverable(database, plan):
        raise ValueError('存在未激活账本，拒绝覆盖；请检查doctor并保留原数据')
    if not apply:
        return dict(ok=True, applied=False, dry_run=True, state='ready', rows=0, revision=0,
                    database=str(database), parameters=plan, nextSteps=['核对预算与初始滚存后使用init --apply'])
    for name in ('data', 'local', 'sra', '.runtime'):
        path = root / name
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('本机数据目录不能重定向到项目外部')
        path.mkdir(parents=True, exist_ok=True)
    with store._writer_lock(root / '.runtime/setup'):
        existing = _existing(root)
        if existing: return {**existing, 'dry_run': False}
        if database.exists():
            if not _recoverable(database, plan): raise ValueError('未激活账本与本次参数不匹配，拒绝覆盖')
        else:
            _new_ledger(database, plan)
        _owner_templates(root)
        fd, name = tempfile.mkstemp(prefix='.source-', suffix='.json', dir=database.parent)
        draft = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'kind':'sqlite', 'path':'account.sqlite3'}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(draft, database.parent / 'source.json')  # activate last
            _flush_directory(database.parent)
        finally:
            draft.unlink(missing_ok=True)
    result = store.verify(database)
    return {**result, 'applied':True, 'dry_run':False, 'state':'initialized',
            'nextSteps':['补充local/档案.md与local/个人规则.md', '运行doctor检查本机环境；云端仍需单独配置与回读验证']}


def doctor(root):
    root = project_root(root)
    setup = dict(state='uninitialized')
    ledger = dict(present=(root / 'data/account.sqlite3').is_file())
    next_steps = []
    if (root / 'data/source.json').exists():
        try:
            report = store.verify(ledger_source.resolve(root))
            if not report['ok']: raise ValueError()
            setup['state'] = 'initialized'
            ledger.update(valid=True, rows=report['rows'], revision=report['revision'], sourceHash=report['sourceHash'])
        except (ValueError, OSError, sqlite3.Error):
            setup['state'] = 'invalid'
            ledger['valid'] = False
            next_steps.append('保留data/，核对source.json与SQLite完整性；不要重新初始化或覆盖')
    elif ledger['present']:
        setup['state'] = 'incomplete'
        next_steps.append('发现未激活账本；保留数据，使用原初始化参数重试或联系自己的Agent核对')
    else:
        next_steps.append('准备四池预算JSON，运行init --effective YYYY-MM --amounts JSON_FILE；核对后添加--apply')
    from runtime_tools import helper_status
    helpers = helper_status(root)
    if any(not item['present'] for item in helpers.values()):
        next_steps.append('运行bootstrap.py --bundle 本平台发布包.zip --apply准备原生组件，或按发行说明构建')
    owner = {name:(root/name).is_file() for name in ['local/档案.md','local/个人规则.md','PROJECT_MEMORY.md']}
    if not all(owner.values()): next_steps.append('初始化后补全本机档案、个人规则与工作记忆')
    dependencies = dict(pythonSupported=sys.version_info[:2] == (3,12), openpyxlAvailable=account.openpyxl is not None,
                        sqliteStrictSupported=sqlite3.sqlite_version_info >= (3,37,0))
    if not all(dependencies.values()): next_steps.append('运行bootstrap准备项目专用Python 3.12及锁定依赖；SQLite需3.37+')
    if platform.system() == 'Windows':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))
    elif platform.system() == 'Darwin':
        base = Path.home()/'Library/Application Support'
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
    desktop = dict(profilePresent=(base/'AccountPreview/project.json').is_file(),
                   installation='unverified', reason='仅检查试用版配置文件是否存在，不读取凭据或宣称已安装')
    # Config presence is a filesystem fact, never a credential or connection test.
    return dict(ok=True, root=str(root), platform=platform.system(), python=platform.python_version(),
                dependencies=dependencies, setup=setup, ledger=ledger, helpers=helpers, ownerFiles=owner, desktop=desktop,
                cloud=dict(state='unverified', publishConfigPresent=(root/'.runtime/ledger/publish.json').is_file(),
                           reason='doctor不读取凭据、不联网；只有实际发布回读成功才能确认连接'),
                nextSteps=next_steps)
