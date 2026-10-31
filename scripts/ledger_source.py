"""Explicit authority selection. A candidate database never activates itself."""
from pathlib import Path
import json


def is_database(path):
    return Path(path).suffix.lower() in {'.sqlite3', '.sqlite', '.db'}


def project_root(source):
    source = Path(source).resolve()
    return source.parent.parent if is_database(source) and source.parent.name == 'data' else source.parent


def runtime(source):
    return project_root(source) / '.runtime'


def resolve(root, *, workbook=None, database=None):
    root = Path(root).resolve()
    if workbook is not None and database is not None:
        raise ValueError('不能同时指定工作簿和数据库来源')
    if database is not None:
        if not is_database(database):
            raise ValueError('--database必须明确指向SQLite数据库，不能选择Excel')
        return Path(database).resolve()
    if workbook is not None:
        if Path(workbook).suffix.lower() != '.xlsx':
            raise ValueError('--workbook必须明确指向.xlsx工作簿')
        return Path(workbook).resolve()
    config = root / 'data' / 'source.json'
    if not config.exists():
        raise ValueError('尚未初始化SQLite账本；请先运行account.py init')
    try:
        value = json.loads(config.read_text(encoding='utf-8'))
        if value != {'kind':'sqlite', 'path':'account.sqlite3'}:
            raise ValueError('账本来源配置无效')
    except (OSError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('账本来源配置无法读取') from error
    database = root / 'data' / 'account.sqlite3'
    if not database.is_file() or database.resolve() != database:
        raise ValueError('已启用的账本数据库不存在或路径无效；拒绝回退到旧Excel')
    return database


def source_hash(source):
    if is_database(source):
        import ledger_store
        return ledger_store.source_hash(source)
    import account
    return account.sha(source)


def assert_active(source):
    """A pre-cutover process must not overwrite shared caches from old Excel."""
    root=project_root(source)
    if (root/'data'/'source.json').exists() and resolve(root)!=Path(source).resolve():
        raise ValueError('账本来源已切换，请关闭所有看板页面后重新打开')
