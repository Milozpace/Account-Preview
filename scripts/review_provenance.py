"""Bind reviewed original rows to the declared, unchanged source bytes."""
from pathlib import Path

import account


def assert_hashes(sources):
    for item in sources:
        path = Path(item['path'])
        if not path.is_absolute():
            raise ValueError('正式来源必须使用 prepare 生成的绝对路径')
        for part in (path, *path.parents):
            if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                raise ValueError('来源路径不能经链接或 junction 重定向')
        if not path.is_file() or account.sha(path) != item['sha256'].lower():
            raise ValueError('来源文件缺失或 SHA256 已改变；请重新核对并 prepare')


def validate(approved, sources):
    # Source-less historical imports remain recognizable as legacy provenance.
    # Current account.py prepare always supplies a manifest.
    if sources is None:
        return
    assert_hashes(sources)
    parsed, _ = account.parse_sources([item['path'] for item in sources])
    assert_hashes(sources)
    by_id = {}
    for row in parsed:
        by_id.setdefault(row['记录ID'], []).append(row)
    protected = (*account.RAW, '来源文件', '来源行', '记录ID', '记录类型')
    for row in approved.get('records', []):
        if account.text(row.get('记录类型')) in ('分摊', '派生', '派生分摊', 'allocation', 'DERIVED'):
            continue  # Parent identity and allocation totals are checked by the store.
        incoming = account.normalize_record(row)
        candidates = by_id.get(incoming['记录ID'], [])
        # Require fields actually supplied by the parser, including blank facts.
        # A source that lacks a column does not authorize inventing its value.
        def same(expected):
            for field in protected:
                if field in expected:
                    if field not in row or incoming.get(field) != expected[field]:
                        return False
                elif row.get(field) not in (None, ''):
                    return False
            return True
        if not any(same(expected) for expected in candidates):
            raise ValueError('审核记录的原始字段或来源定位与原件不一致；保留候选并重新 prepare，禁止手工重建原始行')
