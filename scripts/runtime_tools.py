"""Resolve portable native companions without old developer-machine paths."""
import json
import os
from pathlib import Path

HELPERS = ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')

def helper_path(root, name):
    if name not in HELPERS: raise ValueError('未知原生组件')
    root = Path(root).resolve()
    value = '.runtime/bin/' + name + ('.exe' if os.name == 'nt' else '')
    manifest = root / '.runtime/helpers.json'
    if manifest.exists():
        try:
            config = json.loads(manifest.read_text(encoding='utf-8'))
            if config.get('version') != 1 or not isinstance(config.get('helpers'), dict): raise ValueError()
            if set(config['helpers']) - set(HELPERS): raise ValueError()
            value = config['helpers'].get(name, value)
        except (OSError, ValueError, AttributeError):
            raise ValueError('原生组件清单无效') from None
    if not isinstance(value, str) or not value.strip() or Path(value).is_absolute():
        raise ValueError('原生组件路径必须为项目内相对路径')
    candidate = (root/value).resolve()
    if not candidate.is_relative_to(root/'.runtime/bin'):
        raise ValueError('原生组件必须位于.runtime/bin内')
    return candidate

def helper_status(root):
    result = {}
    for name in HELPERS:
        try:
            path = helper_path(root, name)
            result[name] = dict(present=path.is_file(), path=str(path), executable=path.is_file() and os.access(path, os.X_OK))
        except ValueError:
            result[name] = dict(present=False, error='原生组件清单无效')
    return result
