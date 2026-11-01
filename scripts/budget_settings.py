"""Reviewed desktop budget editing. Save exports locally and never publishes."""
import json

import account
import app_export
import dashboard_data
import ledger_source
import ledger_store

MAX_PAYLOAD_BYTES = 16 * 1024
PAYLOAD_KEYS = {'effective', 'amounts', 'expectedSourceHash', 'reason'}


def _paths(root):
    # Lazy import avoids a cycle when app_desktop dispatches a budget command.
    from app_desktop import project_paths
    paths = project_paths(root)
    if not ledger_source.is_database(paths[1]):
        raise ValueError('预算设置需要当前SQLite账本')
    return paths


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('预算JSON不能包含重复字段')
        result[key] = value
    return result


def read_payload(stream):
    raw = stream.read(MAX_PAYLOAD_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError('预算请求超过大小上限')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)


def _payload(value):
    if not isinstance(value, dict) or set(value) != PAYLOAD_KEYS:
        raise ValueError('预算请求字段无效')
    # APIs called directly receive the same size bound as stdin requests.
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as error:
        raise ValueError('预算请求必须为有效JSON') from error
    if len(encoded) > MAX_PAYLOAD_BYTES:
        raise ValueError('预算请求超过大小上限')
    return value


def status(root):
    _, database, _ = _paths(root)
    state = ledger_store.read_state(database)
    rates = [dict(effective=r['生效月份'][:7], amounts={p: r[p] for p in account.POOLS}) for r in state['rates']]
    return dict(ok=True, sourceHash=state['sourceHash'], revision=state['revision'], rates=rates,
                suggestedMonth=min((r['交易时间'][:7] for r in state['rows']), default=rates[0]['effective']))


def _operation(database, payload, apply):
    return ledger_store.set_budget(database, payload['effective'], payload['amounts'], payload['reason'],
                                   expected_source_hash=payload['expectedSourceHash'],
                                   reviewer='account-desktop-budget',
                                   confirmation_source='本人通过预算预检后明确保存', apply=apply)


def preview(root, payload, apply=False):
    if apply:
        raise ValueError('预算预检只读；请使用明确的保存操作')
    payload = _payload(payload)
    _, database, _ = _paths(root)
    return _operation(database, payload, False)


def save(root, payload, apply=True):
    if not apply:
        return preview(root, payload)
    payload = _payload(payload)
    root, database, _ = _paths(root)
    # Lock order matches refresh: bridge -> ledger/dashboard locks. Preview and
    # status never create lock files or update display/cache artifacts.
    with dashboard_data.sync_lock(root / '.runtime' / 'desktop-bridge'):
        result = _operation(database, payload, True)
        try:
            app_export.export_snapshot(database)
            from app_desktop import load_snapshot
            loaded = load_snapshot(root)
            if loaded['snapshot'] is None or loaded['snapshot']['sourceHash'] != result['sourceHash']:
                raise RuntimeError('snapshot freshness changed')
            result['snapshotUpdated'] = True
        except Exception:
            result['snapshotUpdated'] = False
            result['warning'] = '预算已保存在本地账本；展示更新未确认，请重新同步展示。'
        return result
