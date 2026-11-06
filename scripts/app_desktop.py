"""Explicit-root local display bridge. No server, bill import or workbook writes.

The native host invokes this script with exact argv. Load and budget preview
are read-only. Refresh uses the established optional publisher gate; explicit
reviewed budget save updates SQLite and exports locally without publishing.
Payloads go only to the caller's pipe.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import account
import app_export
import dashboard_data
import ledger_source
import ledger_store

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def project_paths(project_root):
    supplied = Path(project_root)
    if not supplied.is_absolute():
        raise ValueError('请配置完整的主项目路径。')
    try:
        root = supplied.resolve(strict=True)
    except OSError as error:
        raise ValueError('主项目目录不存在。') from error
    if not root.is_dir():
        raise ValueError('主项目目录无效。')
    # Git worktrees carry a .git file; a regular checkout has a directory.
    # Also reject archive/worktree paths after canonicalization (junctions too).
    if (root / '.git').is_file() or any(p.lower() in {'worktrees', 'archive'} for p in root.parts):
        raise ValueError('拒绝使用开发工作树或历史存档作为正式账本来源。')
    for name in ('account.py', 'app_export.py', 'dashboard_data.py', 'app_desktop.py'):
        path = root / 'scripts' / name
        if not path.is_file() or path.resolve().parent != root / 'scripts':
            raise ValueError('主项目工具不完整，请检查配置。')
    workbook = ledger_source.resolve(root)
    expected_parent = root / 'data' if ledger_source.is_database(workbook) else root
    if not workbook.is_file() or workbook.resolve().parent != expected_parent:
        raise ValueError('主项目SQLite账本不存在或路径无效。')
    # Stable legacy path; the payload's schemaVersion defines the contract (now 3).
    snapshot = root / '.runtime' / 'ledger' / 'snapshot-v2.json'
    if snapshot.resolve() != snapshot:
        raise ValueError('App快照路径不能重定向到其他位置。')
    return root, workbook, snapshot


def load_snapshot(project_root):
    _, workbook, path = project_paths(project_root)
    result = dict(snapshot=None, sourceChanged=False, monthChanged=False)
    if not path.exists():
        return result
    before_hash = ledger_source.source_hash(workbook)
    before_month = dashboard_data.current_month()
    before_months = (dashboard_data.source_tracking_months(workbook, ledger_store.read_state(workbook), before_month)
                     if ledger_source.is_database(workbook) else None)
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError('App快照超过大小上限。')
    with path.open('rb') as stream:
        payload = stream.read(MAX_SNAPSHOT_BYTES + 1)
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise ValueError('App快照超过大小上限。')
    snapshot = app_export.validate_app_snapshot(json.loads(payload))
    # A save/month change during read must never be accepted as current.
    after_hash = ledger_source.source_hash(workbook)
    after_month = dashboard_data.current_month()
    result['sourceChanged'] = not (before_hash == after_hash == snapshot['sourceHash'])
    result['monthChanged'] = not (before_month == after_month == snapshot['calendarMonth'])
    if before_months is not None:
        after_months = dashboard_data.source_tracking_months(workbook, ledger_store.read_state(workbook), after_month)
        result['sourceChanged'] |= not (before_months == after_months == [view['month'] for view in snapshot['months']])
    if not result['sourceChanged'] and not result['monthChanged']:
        result['snapshot'] = snapshot
    return result


def _publish(root, snapshot_path):
    gate = app_export.publish_gate(root / '.runtime')
    if not gate['enabled']:
        return dict(ok=None, published=False, skipped=True, reason=gate['reason'])
    try:
        # Native publisher rechecks its own secure gate and cloud readback.
        # Never expose publisher stdout/stderr (which may contain private data).
        completed = subprocess.run(
            [gate['publisher'], '--publish', str(snapshot_path)],
            capture_output=True, encoding='utf-8', errors='replace', timeout=300,
            cwd=str(root), shell=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if completed.returncode == 0:
            return dict(ok=True, published=True)
        return dict(ok=False, published=False, error='本机展示已更新；App发布未确认，请检查发布状态后重试。')
    except (OSError, ValueError, subprocess.SubprocessError):
        return dict(ok=False, published=False, error='本机展示已更新；App发布未完成，请稍后重试。')


def refresh_snapshot(project_root):
    root, workbook, path = project_paths(project_root)
    # Reuse the same OS advisory-lock implementation in a separate namespace:
    # sync_snapshot still obtains dashboard.lock internally. This outer lock
    # serializes the entire desktop export+publish across application windows
    # and processes; the publisher owns its independent ledger transaction.
    with dashboard_data.sync_lock(root / '.runtime' / 'desktop-bridge'):
        app_export.export_snapshot(workbook)
        loaded = load_snapshot(root)
        if loaded['snapshot'] is None:
            raise RuntimeError('同步后来源或月份已变化；请重新同步展示。')
        snapshot = loaded['snapshot']
        publication = _publish(root, path)
        # Preserve the independently validated, exact prepublication snapshot
        # and the publisher's known outcome. A late save/month rollover or a
        # failed final read cannot undo that outcome. Flags/warning describe
        # freshness separately; consumers must not label a warned result latest.
        result = dict(snapshot=snapshot, appPublish=publication,
                      sourceChanged=False, monthChanged=False)
        try:
            final = load_snapshot(root)
            result['sourceChanged'] = final['sourceChanged']
            result['monthChanged'] = final['monthChanged']
            if final['snapshot'] is not None:
                # Another exporter may have produced a *current* replacement
                # during publication. Our returned snapshot is still the one
                # independently validated before the publisher ran.
                result['sourceChanged'] |= final['snapshot']['sourceHash'] != snapshot['sourceHash']
                result['sourceChanged'] |= ([view['month'] for view in final['snapshot']['months']]
                                            != [view['month'] for view in snapshot['months']])
                result['monthChanged'] |= final['snapshot']['calendarMonth'] != snapshot['calendarMonth']
            elif not result['sourceChanged'] and not result['monthChanged']:
                result['warning'] = '未能再次核验展示是否最新；保留已验证快照，请重新同步展示。'
        except Exception:
            # Never leak filesystem paths, financial data or parser diagnostics.
            # False flags here mean no change was observed, not proven current.
            result['warning'] = '未能再次核验展示是否最新；保留已验证快照，请重新同步展示。'
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('load', 'refresh', 'budget-status', 'budget-preview', 'budget-save'))
    parser.add_argument('--project-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command.startswith('budget-'):
            import budget_settings
            if args.command == 'budget-status':
                result = budget_settings.status(args.project_root)
            else:
                payload = budget_settings.read_payload(sys.stdin.buffer)
                operation = budget_settings.preview if args.command == 'budget-preview' else budget_settings.save
                result = operation(args.project_root, payload)
        else:
            operation = load_snapshot if args.command == 'load' else refresh_snapshot
            result = operation(args.project_root)
        sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        return 0
    except Exception:
        # Paths, financial contents, native Excel/publisher diagnostics stay out
        # of the UI response and shared logs. Existing snapshots stay intact.
        message = ('预算读取、预检或保存失败；账本可能已变化，请刷新预算并检查输入后重试。\n'
                   if args.command.startswith('budget-') else '本地展示读取或同步失败；请检查主项目配置及账本来源后重试。\n')
        sys.stderr.write(message)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
