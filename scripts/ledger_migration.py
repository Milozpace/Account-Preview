"""Explicit candidate creation and independently-audited authority cutover."""
import json
import os
from pathlib import Path
import uuid

import account
import ledger_source


def activate(root, audit_path, *, apply=False):
    root = Path(root).resolve()
    if (root/'.git').is_file() or any(p.lower() in {'archive','worktrees'} for p in root.parts):
        raise ValueError('候选开发工作树和历史存档不能启用为正式账本')
    database = root/'data'/'account.sqlite3'
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    comparison = audit.get('databaseComparison',{})
    if audit.get('ok') is not True or comparison.get('status') != 'passed':
        raise ValueError('尚未完成独立Excel与数据库全量对账，禁止启用')
    import ledger_store
    report = ledger_store.verify(database)
    if not report.get('ok'):
        raise ValueError('候选数据库核验未通过，禁止启用')
    if audit.get('sourceHash') != ledger_store.source_hash(database):
        raise ValueError('验收后候选数据库已变化，请重新对账')
    workbook = root/'个人财物工作簿.xlsx'
    if audit.get('workbookHash') != account.sha(workbook):
        raise ValueError('验收后原工作簿已变化，请重新迁移对账')
    config = root/'data'/'source.json'
    if config.exists():
        raise ValueError('已有来源配置；禁止重复切换或覆盖')
    result = dict(ok=True,dry_run=not apply,sourceKind='sqlite',sourceHash=audit['sourceHash'],
                  workbookPreserved=True,clientInstallPerformed=False,cloudPublishPerformed=False)
    if apply:
        # Hard-link publication is atomic and refuses concurrent pre-existing config.
        draft=config.with_name('.source-'+uuid.uuid4().hex+'.json')
        try:
            draft.write_text(json.dumps({'kind':'sqlite','path':'account.sqlite3'}),encoding='utf-8')
            if ledger_store.source_hash(database)!=audit['sourceHash'] or account.sha(workbook)!=audit['workbookHash']:
                raise ValueError('切换前来源发生变化，拒绝启用')
            os.link(draft,config)
        finally:
            draft.unlink(missing_ok=True)
        result['activated']=True
    return result


def command(args, root):
    if args.command=='activate-sqlite':
        return activate(root,args.audit,apply=args.apply)
    import ledger_store
    if args.command=='restore':
        if not args.apply:
            return dict(ok=True,dry_run=True,action='restore',destinationExists=args.output.exists())
        return ledger_store.restore(args.input,args.output)
    source=args.workbook or Path(root)/'个人财物工作簿.xlsx'
    if args.database is not None:
        raise ValueError('migrate-excel需要工作簿来源，不能同时传--database')
    if not args.apply:
        inspected=account.inspect(source)
        return dict(ok=True,dry_run=True,action='migrate-excel',rows=inspected['rows'],
                    sourceHash=inspected['sha256'],destinationExists=args.output.exists(),activated=False)
    return ledger_store.migrate_excel(source,args.output)
