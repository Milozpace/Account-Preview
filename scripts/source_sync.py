"""Explicit, baseline-aware source transfers for the one-time legacy transition.

Future shared development belongs in Preview. This command never scans a whole
personal checkout, merges histories, commits, or publishes anything.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid

ALLOWED_ROOTS = {'scripts', 'tests', 'app', 'dashboard'}
EXTENSIONS = {'.py', '.ps1', '.sh', '.ts', '.css', '.html', '.rs', '.json', '.toml', '.lock'}
FORBIDDEN_PARTS = {'node_modules', 'target', 'build', 'dist', 'data', 'local', 'sra',
                   'exports', 'archive', '.runtime', '.git', '.venv', '__pycache__', 'gen'}
PROFILE_FILES = {'app/package.json', 'app/package-lock.json', 'app/src-tauri/Cargo.toml',
                 'app/src-tauri/Cargo.lock', 'app/src-tauri/tauri.conf.json'}


def safe_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('同步清单必须是受控的相对源码路径')
    parts = name.split('/')
    path = PurePosixPath(name)
    if (path.is_absolute() or any(p in {'', '.', '..'} for p in parts)
            or parts[0] not in ALLOWED_ROOTS or len(parts) < 2
            or any(p.lower() in FORBIDDEN_PARTS for p in parts)
            or path.suffix not in EXTENSIONS
            or name.casefold() in {p.casefold() for p in PROFILE_FILES}):
        raise ValueError('同步清单包含私人、生成、身份或非源码文件')
    return name


def confined(root, name):
    path = root / name
    if path.resolve() != path or not path.resolve().is_relative_to(root):
        raise ValueError('源码路径不能经链接跳转')
    if path.exists() and not path.is_file():
        raise ValueError('源码位置不是普通文件')
    return path


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def sensitive(payload):
    value = payload.decode('utf-8')
    return any(re.search(pattern, value) for pattern in (
        r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        r'\bgh[pousr]_[A-Za-z0-9]{30,}\b', r'\bgithub_pat_[A-Za-z0-9_]{30,}\b',
        r'C:[\\/]+Users[\\/]+[0-9]{5}(?:[\\/]|\b)'))


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, draft = tempfile.mkstemp(prefix='.sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.replace(draft, path)
    finally:
        Path(draft).unlink(missing_ok=True)


@contextmanager
def sync_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('已有同步操作或中断锁；核对现场后再重试') from None
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd); path.unlink()


def synchronize(source, target, names, *, apply=False):
    source, target = Path(source), Path(target)
    if not source.is_absolute() or not target.is_absolute():
        raise ValueError('源与目标须为绝对目录')
    if source.resolve() != source or target.resolve() != target:
        raise ValueError('同步根不能通过链接跳转')
    if (source == target or source.is_relative_to(target) or target.is_relative_to(source)
            or not source.is_dir() or not target.is_dir()):
        raise ValueError('同步根须为互不包含的两个现有目录')
    if not isinstance(names, list) or not names:
        raise ValueError('显式提供非空源码文件清单')
    names = [safe_name(name) for name in names]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('清单存在重复路径或大小写别名')
    key = hashlib.sha256(str(source).casefold().encode()).hexdigest()[:20]
    state_path = target / f'.runtime/product/source-sync/{key}.json'
    if state_path.resolve() != state_path:
        raise ValueError('同步状态目录不能跳转')
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {'format': 1, 'files': {}}
    if not isinstance(state, dict) or state.get('format') != 1 or not isinstance(state.get('files'), dict):
        raise ValueError('同步基线无效；保留现场，不能重新覆盖建立基线')
    changes, payloads, original = [], {}, {}
    for name in names:
        src, dst = confined(source, name), confined(target, name)
        incoming, current = fingerprint(src), fingerprint(dst)
        base = state['files'].get(name)
        if base is not None and not re.fullmatch(r'[a-f0-9]{64}', str(base)):
            raise ValueError('同步内容基线无效')
        if incoming == current: action = 'unchanged'
        elif base is not None and incoming == base: action = 'target-only'
        elif current == base: action = 'copy' if incoming else 'delete'
        else: action = 'conflict'
        if action == 'copy':
            payloads[name] = src.read_bytes()
            if sensitive(payloads[name]): raise ValueError('源文件疑似包含私人路径或凭据，请人工核对')
        if action in {'copy', 'delete'}: original[name] = dst.read_bytes() if dst.is_file() else None
        changes.append(dict(path=name, action=action, sourceHash=incoming, targetHash=current, baseline=base))
    conflicts = [item['path'] for item in changes if item['action'] == 'conflict']
    result = dict(ok=not conflicts, dryRun=not apply, applied=False, changes=changes, conflicts=conflicts,
                  next='冲突文件先在Preview按语义合并；保留平台适配，不整文件覆盖' if conflicts else '核对清单后加--apply；不提交、不发布')
    if not apply or conflicts: return result
    with sync_lock(state_path.with_suffix('.lock')):
        previous_state = state_path.read_bytes() if state_path.exists() else None
        pending = state_path.with_suffix('.pending.json')
        if pending.exists():
            raise ValueError('上次同步中断；先核对pending清单和恢复副本，不覆盖现场')
        # Fail closed on edits since preflight, including state changes by another invocation.
        if state_path.exists() and json.loads(state_path.read_text(encoding='utf-8')) != state:
            raise ValueError('同步基线发生变化，请重新预检')
        for item in changes:
            if fingerprint(confined(source, item['path'])) != item['sourceHash'] or fingerprint(confined(target, item['path'])) != item['targetHash']:
                raise ValueError('同步期间源码变化，请重新预检')
        rollback = state_path.parent / (key + '-recovery-' + uuid.uuid4().hex)
        rollback.mkdir()
        # Persistent recovery preserves the prior bytes if this process is interrupted.
        recovery = {name: (None if payload is None else hashlib.sha256(payload).hexdigest()) for name, payload in original.items()}
        for name, payload in original.items():
            if payload is not None: atomic_write(rollback / name, payload)
        atomic_write(rollback/'recovery.json', json.dumps(recovery, ensure_ascii=False, indent=2).encode())
        atomic_write(pending, json.dumps({'recovery': rollback.name, 'changes': changes}, ensure_ascii=False, indent=2).encode())
        try:
            for item in changes:
                name, action = item['path'], item['action']
                dst = confined(target, name)
                if action == 'copy': atomic_write(dst, payloads[name])
                elif action == 'delete': dst.unlink()
                if action in {'copy', 'delete', 'unchanged'}:
                    if item['sourceHash'] is None: state['files'].pop(name, None)
                    else: state['files'][name] = item['sourceHash']
            atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2).encode())
            pending.unlink()
        except Exception:
            for name, payload in original.items():
                dst = confined(target, name)
                if payload is None: dst.unlink(missing_ok=True)
                else: atomic_write(dst, payload)
            if previous_state is None: state_path.unlink(missing_ok=True)
            else: atomic_write(state_path, previous_state)
            pending.unlink()
            raise
    result['applied'] = any(item['action'] in {'copy', 'delete'} for item in changes)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--files', type=Path, required=True, help='JSON数组，逐项为显式相对源码路径')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        result = synchronize(args.source, args.target, json.loads(args.files.read_text(encoding='utf-8-sig')), apply=args.apply)
    except (OSError, ValueError) as error:
        result = dict(ok=False, error=str(error), next='保留现场；核对源码清单、同步基线与冲突，不覆盖私人内容')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
