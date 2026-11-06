"""Update published Account products while preserving the current owner's ledger and identity.

All commands are read-only unless --apply is supplied. The protection policy in this
module is deliberately independent from downloaded source code. No git reset/pull,
ledger migration, refresh, initialization, pairing or cloud publication is performed.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing, contextmanager
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import urllib.parse
import uuid
import zipfile

REPOSITORY = 'Milozpace/Account-Preview'
PROFILES = {'personal': {'identifier':'com.account.probe', 'desktopConfig':'AccountDesktop'},
            'preview': {'identifier':'com.account.preview', 'desktopConfig':'AccountPreview'}}
ROOT_FILES = {'bootstrap.py','bootstrap.ps1','bootstrap.sh','requirements.lock','requirements.txt',
              'pyproject.toml','release.json','.python-version'}
ARCHIVE_ROOT_FILES = ROOT_FILES | {'AGENTS.md','README.md','NOTICE','NOTICE.md','.gitignore','.gitattributes','.editorconfig'}
ARCHIVE_DIRS = {'scripts','app','dashboard','docs','tests','templates','.agents','.github'}
FORBIDDEN_PARTS = {'.git','.venv','node_modules','target','dist','build','.gradle','__pycache__','.runtime','data','local','sra','archive','exports'}
VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-preview\.([1-9]\d*)\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
STATE = '.runtime/product/state.json'
SKILL_ROOT = '.agents/skills/account-maintenance'
SKILL_ENTRY = SKILL_ROOT+'/SKILL.md'
GUIDANCE = 'docs/版本衔接指引.md'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def atomic_bytes(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.product-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path,(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8'))


def create_json_if_missing(path, value):
    """Publish a complete configuration atomically without replacing another choice."""
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix='.owner-setting-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
            stream.flush(); os.fsync(stream.fileno())
        try: os.link(temporary,path)
        except FileExistsError: return False
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def redirected(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


def checked_root(value):
    path = Path(value)
    if not path.is_absolute(): raise ValueError('--root 必须是明确的绝对路径。')
    for component in (path,*path.parents):
        if redirected(component): raise ValueError('项目根目录不能包含符号链接或 junction。')
    if not path.is_dir(): raise ValueError('项目根目录不存在。')
    return path.resolve(strict=True)


def safe_name(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('不安全的发布路径。')
    path=PurePosixPath(value)
    if path.is_absolute() or path.as_posix()!=value or any(p in {'.','..',''} for p in path.parts):
        raise ValueError('不安全的发布路径。')
    for part in path.parts:
        if part.endswith((' ','.')) or any(c in part for c in '<>"|?*') or re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):
            raise ValueError('发布路径包含平台保留名称。')
    return path


def checked_path(root, name):
    relative=safe_name(name); current=root
    for part in relative.parts:
        if current.is_dir():
            matches=[p.name for p in current.iterdir() if p.name.casefold()==part.casefold()]
            if matches and matches!=[part]: raise ValueError('本机路径存在大小写别名冲突。')
        current=current/part
        if redirected(current): raise ValueError('受管理路径不能包含符号链接或 junction。')
        if current.exists() and current!=root/relative and not current.is_dir():
            raise ValueError('发布路径父级不是目录。')
    if current.exists() and not current.is_file(): raise ValueError('受管理路径不是普通文件。')
    return current


def managed_name(name):
    path=safe_name(name)
    if len(path.parts)==1: return name in ROOT_FILES
    if path.parts[0] not in {'scripts','app','dashboard'}: return False
    if any(p.casefold() in FORBIDDEN_PARTS for p in path.parts): return False
    if any(p.startswith('.env') or p.lower().endswith(('.keystore','.jks','.p12','.pem','.key','.pyc')) for p in path.parts): return False
    return True


def official_skill_name(name):
    safe_name(name)
    return name==SKILL_ENTRY or name.startswith(SKILL_ROOT+'/references/')


def official_skill_source(source):
    """Select the one official skill and verify every local Markdown dependency.

    Generic documentation may be referenced but remains in releaseDocs. No other
    skill, owner instructions or local configuration becomes update-managed.
    """
    files={name:data for name,data in source.items() if official_skill_name(name)}
    declared=[name for name in source if name.startswith(SKILL_ROOT+'/')]
    if not declared: return {}
    if SKILL_ENTRY not in files or set(declared)!=set(files):
        raise ValueError('官方 Skill 不完整或包含未支持的资源；须有 SKILL.md 和 references。')
    for name,data in files.items():
        if not name.lower().endswith('.md'): continue
        try: text=data.decode('utf-8-sig')
        except UnicodeError: raise ValueError('官方 Skill Markdown 必须是 UTF-8。') from None
        # Inline Markdown links, images and reference-style link definitions.
        links=re.findall(r'!?\[[^\]]*\]\(\s*(<[^>]*>|[^\s)]+)',text)
        links+=re.findall(r'^\s*\[[^\]]+\]:\s*(<[^>]*>|\S+)',text,re.MULTILINE)
        for link in links:
            target=urllib.parse.unquote(link.strip('<>')).split('#',1)[0]
            if not target: continue
            scheme=urllib.parse.urlsplit(target).scheme
            if scheme in {'http','https','mailto'}: continue
            if scheme: raise ValueError('官方 Skill 不支持此本地引用协议。')
            if target.startswith(('/','\\')) or '\\' in target or '?' in target:
                raise ValueError('官方 Skill 本地引用路径无效。')
            resolved=posixpath.normpath(posixpath.join(posixpath.dirname(name),target))
            safe_name(resolved)
            if resolved not in source:
                raise ValueError('官方 Skill 缺少引用文件：'+resolved)
            if resolved.startswith('.agents/') and resolved not in files:
                raise ValueError('官方 Skill 不能依赖其他 Skill 或未管理资源。')
    return files


def skill_status(root, state=None):
    saved=(state or {}).get('officialSkill')
    report={'path':str(checked_path(root,SKILL_ENTRY)),'version':None,'sourceVersion':None,'state':'untracked'}
    if saved is None: return report
    report.update({key:saved.get(key) for key in ('version','sourceVersion','commit')})
    modified=False
    for name,expected in saved['files'].items():
        path=checked_path(root,name)
        if not path.is_file() or digest(path.read_bytes())!=expected: modified=True
    report['state']='modified' if modified else ('synchronized' if saved['supported'] else 'unsupported')
    return report


def skill_next(skill):
    if skill['state'] in {'synchronized','pending'}:
        return f"记账前必须读取官方 Skill：{skill['path']}；通用说明读取 releaseDocs 的本版本副本，本机所有者确认继续保留。"
    if skill['state']=='modified': return '官方 Skill 有本地变化；先保留并处理冲突，不将其视为已同步。'
    return '此状态尚无已同步的官方 Skill；旧版不支持时不得声称新版记账流程已就绪。'


def guidance_status(root, version=None, expected=None, *, pending=False):
    path = checked_path(root, f'.runtime/product/releases/{version}/source/{GUIDANCE}') if version else None
    state = 'unsupported'
    if expected is not None:
        if not isinstance(expected, str) or not SHA.fullmatch(expected):
            raise ValueError('版本衔接指引来源哈希无效。')
        state = 'pending' if pending else ('available' if path and path.is_file()
                 and digest(path.read_bytes()) == expected else 'modified')
    return {'path': str(path) if path else None, 'version': version, 'state': state}


def update_next(skill, guidance):
    if guidance['state'] in {'available', 'pending'}:
        instruction = f"更新后必须重新读取同版累计版本衔接指引：{guidance['path']}；检查跨版尚未完成事项，程序更新不等于业务衔接完成。"
    elif guidance['state'] == 'modified':
        instruction = '版本衔接指引缺失或被修改；先核对固定来源，不得声称衔接完成。'
    else:
        instruction = '此版本未提供已校验的版本衔接指引；明确报告，不能推断业务衔接已完成。'
    return skill_next(skill) + ' ' + instruction


def previous_skill_baseline(root, state):
    """Recover a legacy baseline only from an authenticated, archived source ZIP."""
    version=state.get('version')
    if not version or not state.get('manifestSha256'): return {}
    owner_version_tuple(version,state['profile'])
    stage=f'.runtime/product/releases/{version}'
    manifest_path=checked_path(root,stage+'/release-manifest.json')
    if not manifest_path.is_file(): return {}
    raw=manifest_path.read_bytes()
    if digest(raw)!=state['manifestSha256']: raise ValueError('先前 Skill 发布清单哈希冲突。')
    manifest=json.loads(raw)
    if manifest.get('version')!=version or manifest.get('commit')!=state.get('commit'):
        raise ValueError('先前 Skill 来源身份冲突。')
    asset=manifest.get('source',{})
    asset_name=asset.get('asset')
    if not isinstance(asset_name,str) or len(safe_name(asset_name).parts)!=1 or not SHA.fullmatch(asset.get('sha256','')):
        raise ValueError('先前 Skill 源码索引无效。')
    archive=checked_path(root,stage+'/'+asset_name)
    if not archive.is_file(): return {}
    if digest(archive.read_bytes())!=asset['sha256']: raise ValueError('先前 Skill 源码 ZIP 哈希冲突。')
    source=zip_contents(archive,source=True)
    expected=state.get('sourceVersion',version)
    try: product=json.loads(source['release.json'])
    except (KeyError,ValueError): raise ValueError('先前 Skill 源码版本缺失。') from None
    if product.get('version')!=expected: raise ValueError('先前 Skill 源码版本冲突。')
    return {name:digest(data) for name,data in official_skill_source(source).items()}


def baseline(root):
    result={}
    for top in ('scripts','app','dashboard'):
        directory=root/top
        if redirected(directory): raise ValueError('程序目录不能重定向。')
        if directory.is_dir():
            for current, directories, files in os.walk(directory,followlinks=False):
                parent=Path(current)
                for name in list(directories):
                    candidate=parent/name
                    if redirected(candidate): raise ValueError('程序目录含符号链接或 junction。')
                    if name.casefold() in FORBIDDEN_PARTS: directories.remove(name)
                for name in files:
                    path=parent/name; relative=path.relative_to(root).as_posix()
                    if managed_name(relative): result[relative]=digest(checked_path(root,relative).read_bytes())
    for name in ROOT_FILES:
        path=checked_path(root,name)
        if path.is_file(): result[name]=digest(path.read_bytes())
    return result


def load_state(root):
    path=checked_path(root,STATE)
    if not path.is_file(): raise ValueError('此项目尚未 adopt；既有个人项目须显式 --profile personal。')
    value=read_json(path)
    if value.get('format')!=1 or value.get('root')!=str(root) or value.get('repository')!=REPOSITORY or value.get('profile') not in PROFILES or not isinstance(value.get('managed'),dict):
        raise ValueError('更新状态无效或属于另一个项目。')
    for name,sha in value['managed'].items():
        if not managed_name(name) or not isinstance(sha,str) or not SHA.fullmatch(sha): raise ValueError('更新基线无效。')
    skill=value.get('officialSkill')
    if skill is not None:
        if not isinstance(skill,dict) or skill.get('path')!=SKILL_ENTRY or not isinstance(skill.get('supported'),bool) or not isinstance(skill.get('files'),dict):
            raise ValueError('官方 Skill 更新基线无效。')
        for name,sha in skill['files'].items():
            if not official_skill_name(name) or not isinstance(sha,str) or not SHA.fullmatch(sha):
                raise ValueError('官方 Skill 文件基线无效。')
        if skill['supported'] and SKILL_ENTRY not in skill['files']: raise ValueError('官方 Skill 入口基线缺失。')
    return value


def ledger_report(root, expected_schema=1):
    source=checked_path(root,'data/source.json'); database=checked_path(root,'data/account.sqlite3')
    if not source.exists() and not database.exists(): return {'initialized':False,'sourceHash':None}
    if not source.is_file() or read_json(source)!={'kind':'sqlite','path':'account.sqlite3'} or not database.is_file():
        raise ValueError('账本来源不完整；拒绝回退或初始化。')
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as connection:
        schema=connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if not schema or schema[0]!=str(expected_schema): raise ValueError('账本 schema 不兼容；不自动迁移。')
    # Import before replacing code; this trusted module stays loaded for postflight.
    import ledger_store
    result=ledger_store.verify(database)
    if not result.get('ok'): raise ValueError('当前账本核验失败；停止更新。')
    return {'initialized':True,'sourceHash':ledger_store.source_hash(database)}


class ProtectedChange(ValueError):
    def __init__(self, count=None):
        self.count=count
        detail=f'{count} 项' if count is not None else '无法核对的'
        super().__init__(f'检测到 {detail}所有者文件变化；保留更改并停止更新，不自动恢复私人文件。')


def protected_snapshot(root):
    """Hash owner material in memory only; never follow redirects or open native vaults.

    These fingerprints and relative names are intentionally absent from journals and
    CLI reports. The active SQLite database is compared separately by business hash.
    """
    result={}
    def fail_walk(_error):
        raise ProtectedChange()
    def capture(path):
        if redirected(path): raise ProtectedChange()
        if not path.is_file(): raise ProtectedChange()
        with path.open('rb') as stream:
            result[path.relative_to(root).as_posix()]=hashlib.file_digest(stream,'sha256').hexdigest()
    try:
        for name in ('local','sra','docs'):
            directory=root/name
            if redirected(directory): raise ProtectedChange()
            if not directory.exists(): continue
            if not directory.is_dir(): raise ProtectedChange()
            for current,directories,files in os.walk(directory,followlinks=False,onerror=fail_walk):
                parent=Path(current)
                if any(redirected(parent/item) for item in directories): raise ProtectedChange()
                for item in files: capture(parent/item)
        for path in root.iterdir():
            if path.suffix.lower() in {'.md','.xlsx'}: capture(path)
        source=root/'data/source.json'
        if source.exists() or redirected(source):
            if redirected(source.parent): raise ProtectedChange()
            capture(source)
    except (OSError,ValueError) as error:
        if isinstance(error,ProtectedChange): raise
        raise ProtectedChange() from None
    return result


def protected_delta(before, after):
    return sum(before.get(name)!=after.get(name) for name in before.keys()|after.keys())


def assert_protected(root, expected):
    count=protected_delta(expected,protected_snapshot(root))
    if count: raise ProtectedChange(count)


def status(root):
    root=checked_root(root)
    skill=skill_status(root)
    guidance=guidance_status(root)
    if not checked_path(root,STATE).exists(): return {'ok':True,'root':str(root),'adopted':False,'dryRun':True,'releaseDocs':None,
            'officialSkill':skill,'agentGuidance':guidance,'next':update_next(skill,guidance),
            'pending':checked_path(root,'.runtime/product/pending.json').exists(),
            'updateLocked':checked_path(root,'.runtime/product/update.lock').exists()}
    value=load_state(root)
    version=value.get('version')
    if version: owner_version_tuple(version,value['profile'])
    release_docs=root/f'.runtime/product/releases/{version}/source' if version else None
    skill=skill_status(root,value)
    guidance=guidance_status(root,version,value.get('guidanceSha256'))
    return {'ok':True,'root':str(root),'adopted':True,'profile':value['profile'],'version':value.get('version'),
            'repository':REPOSITORY,'managedFiles':len(value['managed']),'dryRun':True,
            'officialSkill':skill,'agentGuidance':guidance,'next':update_next(skill,guidance),
            'releaseDocs':str(release_docs) if release_docs else None,
            'pending':checked_path(root,'.runtime/product/pending.json').exists(),
            'updateLocked':checked_path(root,'.runtime/product/update.lock').exists()}


def preserve_legacy_display(root, profile, *, apply=False, initialized=None):
    """Capture the old owner's explicit source constant without importing their code."""
    if profile != 'personal': return {'changed':False}
    config=checked_path(root,'local/display.json')
    if config.exists(): return {'changed':False,'source':'existing-local-config'}
    legacy=checked_path(root,'scripts/dashboard_data.py')
    if not legacy.is_file(): return {'changed':False}
    if legacy.stat().st_size>2*1024*1024: raise ValueError('旧展示工具过大；请人工核对展示起点，不执行旧代码。')
    try: syntax=ast.parse(legacy.read_text(encoding='utf-8-sig'))
    except (SyntaxError,UnicodeError): raise ValueError('无法只读核对旧展示设置；请先检查旧源码。') from None
    values=[]
    for node in syntax.body:
        targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
        if any(isinstance(target,ast.Name) and target.id=='TRACKING_START_MONTH' for target in targets):
            if not isinstance(node.value,ast.Constant) or not isinstance(node.value.value,str):
                raise ValueError('旧展示起点不是明确月份常量；请确认，不猜测或执行旧代码。')
            values.append(node.value.value)
    if not values: return {'changed':False}
    if len(values)!=1 or not re.fullmatch(r'\d{4}-\d{2}',values[0]): raise ValueError('旧展示起点配置不明确。')
    try: date.fromisoformat(values[0]+'-01')
    except ValueError: raise ValueError('旧展示起点月份无效。') from None
    if initialized is None: initialized=ledger_report(root)['initialized']
    if not initialized: return {'changed':False}
    if apply and not create_json_if_missing(config,{'trackingStartMonth':values[0]}):
        return {'changed':False,'source':'concurrent-local-config'}
    return {'changed':apply,'planned':True,'trackingStartMonth':values[0],'source':'legacy-owner-config'}


def adopt(root, *, profile=None, apply=False):
    root=checked_root(root)
    if checked_path(root,STATE).exists():
        state=load_state(root)
        if profile is not None and profile!=state['profile']: raise ValueError('既有 profile 不能更换；身份保持独立。')
        display=preserve_legacy_display(root,state['profile'],apply=apply)
        return {**status(root),'unchanged':not display['changed'],'dryRun':not apply,'display':display}
    if profile not in PROFILES: raise ValueError('未知既有项目须显式 --profile personal 或 preview。')
    ledger=ledger_report(root)
    value={'format':1,'root':str(root),'repository':REPOSITORY,'profile':profile,'version':None,'managed':baseline(root)}
    if not value['managed']: raise ValueError('没有可采用的共享程序；请先取得产品源码。')
    display=preserve_legacy_display(root,profile,apply=apply,initialized=ledger['initialized'])
    if apply: atomic_json(checked_path(root,STATE),value)
    return {'ok':True,'root':str(root),'profile':profile,'managedFiles':len(value['managed']),'dryRun':not apply,'ledger':ledger,'display':display}


def version_tuple(version):
    # Keep this parser local: updater protection must not execute downloaded modules.
    match=re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|preview|rc)\.(0|[1-9]\d*))?',version or '') if isinstance(version,str) else None
    if not match or (match[4]=='preview' and match[5]=='0'):
        raise ValueError('产品版本须为 X.Y.Z 或 X.Y.Z-{alpha,beta,rc,preview}.N。')
    return (*map(int,match.group(1,2,3)),
            {'alpha':0,'beta':1,'preview':2,'rc':3,None:4}[match[4]],int(match[5] or 0))


def owner_version_tuple(version, profile):
    return version_tuple(version)


def installation_baseline(root, state, *, require_android=True):
    """Recover installer ordering from the receipt and its authenticated old index."""
    previous=dict(state)
    version=state.get('version')
    if version and state.get('manifestSha256'):
        path=checked_path(root,f'.runtime/product/releases/{version}/release-manifest.json')
        if path.is_file():
            raw=path.read_bytes()
            if len(raw)>1024*1024 or digest(raw)!=state['manifestSha256']:
                raise ValueError('先前安装版本索引哈希冲突。')
            archived=json.loads(raw)
            if archived.get('version')!=version or archived.get('commit')!=state.get('commit'):
                raise ValueError('先前安装版本索引来源冲突。')
            for key in ('nativeVersion','androidVersionCode'):
                if key in archived:
                    if key in previous and previous[key]!=archived[key]:
                        raise ValueError('先前安装版本回执与索引冲突。')
                    previous[key]=archived[key]
            # Old public preview indexes omitted Android code. Only their archived,
            # hash-bound source can supply it; the mutable project source cannot.
            # Legacy personal-local source has a different installation sequence.
            if require_android and previous.get('androidVersionCode') is None and archived.get('distribution')!='personal-local':
                entry=archived.get('source',{})
                name=entry.get('asset') if isinstance(entry,dict) else None
                expected=entry.get('sha256') if isinstance(entry,dict) else None
                if isinstance(name,str) and len(safe_name(name).parts)==1 and isinstance(expected,str) and SHA.fullmatch(expected):
                    source_path=checked_path(root,f'.runtime/product/releases/{version}/'+name)
                    if source_path.is_file():
                        if digest(source_path.read_bytes())!=expected: raise ValueError('先前安装源码 ZIP 哈希冲突。')
                        source=zip_contents(source_path,source=True)
                        try: product=json.loads(source['release.json'])
                        except (KeyError,ValueError): raise ValueError('先前安装源码缺少产品版本清单。') from None
                        expected_version=archived.get('sourceVersion',version)
                        if (not isinstance(product,dict) or product.get('version')!=expected_version
                                or product.get('tag')!='v'+expected_version or product.get('channel')!='private-preview'):
                            raise ValueError('先前安装源码产品身份冲突。')
                        if 'nativeVersion' in product:
                            if previous.get('nativeVersion') is not None and previous['nativeVersion']!=product['nativeVersion']:
                                raise ValueError('先前安装源码与索引的原生版本冲突。')
                            previous['nativeVersion']=product['nativeVersion']
                        if 'androidVersionCode' in product: previous['androidVersionCode']=product['androidVersionCode']
    native=previous.get('nativeVersion')
    if native is None and version and re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',version):
        native=version  # Legacy personal-local used the same product/native version.
    if native is not None and (not isinstance(native,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',native)):
        raise ValueError('先前原生安装版本无效。')
    code=previous.get('androidVersionCode')
    if code is not None and (type(code) is not int or not 0<code<=2_100_000_000):
        raise ValueError('先前 Android 安装序号无效。')
    if state.get('profile')=='personal':
        file=checked_path(root,'local/personal-version.json')
        if file.is_file():
            legacy=read_json(file)
            if (not isinstance(legacy,dict) or set(legacy)!={'version','androidVersionCode'}
                    or not isinstance(legacy.get('version'),str)
                    or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',legacy['version'])
                    or type(legacy.get('androidVersionCode')) is not int
                    or not 0<legacy['androidVersionCode']<=2_100_000_000):
                raise ValueError('旧个人安装版本基线无效。')
            native=max(filter(None,[native,legacy['version']]),key=lambda value:tuple(map(int,value.split('.'))))
            code=max(code or 0,legacy['androidVersionCode'])
    return native,code


def select_release(releases):
    candidates=[]
    for item in releases:
        tag=item.get('tag_name','')
        if item.get('draft') is not False or not tag.startswith('v'): continue
        # Existing subscribers stay on legacy preview; accepting local SemVer does
        # not subscribe them to stable, alpha, beta, or rc remote deliveries.
        if not VERSION.fullmatch(tag[1:]): continue
        try: key=version_tuple(tag[1:])
        except ValueError: continue
        candidates.append((key,item))
    if not candidates: raise ValueError('没有已发布的 private-preview 版本；draft/candidate 不可更新。')
    return max(candidates,key=lambda item:item[0])[1]


def source_snapshot_schema(source, *, required=False):
    """Read a literal producer contract without importing release code or dependencies."""
    content=source.get('scripts/app_export.py')
    if content is None:
        if required: raise ValueError('新发布源码缺少 App 快照 schema 声明。')
        return 2  # Historical updater indexes before explicit producer inspection.
    try:
        syntax=ast.parse(content)
    except (SyntaxError,UnicodeError,ValueError):
        raise ValueError('无法读取源码 App 快照 schema 声明。') from None
    values=[]
    for node in syntax.body:
        targets=node.targets if isinstance(node,ast.Assign) else [node.target] if isinstance(node,ast.AnnAssign) else []
        if any(isinstance(target,ast.Name) and target.id=='SCHEMA_VERSION' for target in targets):
            value=node.value
            if not isinstance(value,ast.Constant) or type(value.value) is not int:
                raise ValueError('源码 App 快照 schema 须为唯一整数常量。')
            values.append(value.value)
    if len(values)!=1 or values[0] not in {2,3}: raise ValueError('源码 App 快照 schema 不兼容或声明不唯一。')
    return values[0]


def zip_contents(path, *, source=False):
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist(); names=[p.filename for p in infos]
        if len(names)!=len(set(n.casefold() for n in names)): raise ValueError('发布 ZIP 存在大小写别名或重复路径。')
        if sum(info.file_size for info in infos)>1024*1024*1024: raise ValueError('发布包解压过大。')
        result={}
        prefixes={}
        folded_names={name.casefold() for name in names}
        for info in infos:
            name=info.filename
            safe_name(name)
            mode=stat.S_IFMT(info.external_attr>>16)
            if info.is_dir() or mode not in {0,stat.S_IFREG}: raise ValueError('发布 ZIP 只能包含普通文件。')
            parts=PurePosixPath(name).parts
            for length in range(1,len(parts)):
                prefix='/'.join(parts[:length]); folded=prefix.casefold()
                if folded in folded_names or (folded in prefixes and prefixes[folded]!=prefix):
                    raise ValueError('发布 ZIP 父路径冲突或大小写别名。')
                prefixes[folded]=prefix
            if source and not (name in ARCHIVE_ROOT_FILES or (len(parts)>1 and parts[0] in ARCHIVE_DIRS)):
                raise ValueError('源码 ZIP 包含受保护或未知路径。')
            if source and (any(p.casefold() in FORBIDDEN_PARTS for p in parts) or (parts[0] in {'scripts','app','dashboard'} and not managed_name(name))):
                raise ValueError('源码 ZIP 包含受保护内容。')
            result[name]=archive.read(info)
        return result


def validate_release(directory, profile, target):
    directory=checked_root(directory)
    manifest_path=checked_path(directory,'release-manifest.json')
    raw=manifest_path.read_bytes()
    if len(raw)>1024*1024: raise ValueError('发布清单过大。')
    manifest=json.loads(raw)
    version=manifest.get('version')
    local=manifest.get('distribution')=='personal-local'
    if manifest.get('versionPolicy') not in {None,'unified'}: raise ValueError('未知产品版本策略。')
    unified=manifest.get('versionPolicy')=='unified'
    if manifest.get('distribution') not in {None,'public','personal-local'}: raise ValueError('未知发布范围。')
    if local:
        if profile!='personal' or (not unified and (not isinstance(version,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',version))):
            raise ValueError('个人本地包须使用 personal 身份及受支持的版本策略。')
        version_tuple(version)
        source_version=manifest.get('sourceVersion');version_tuple(source_version)
        if not unified and not VERSION.fullmatch(source_version):
            raise ValueError('旧个人索引的共享源码须为历史 preview 版本；新包须声明统一版本策略。')
        if unified and source_version!=version: raise ValueError('统一产品版本须与源码版本一致。')
        if manifest.get('tag') is not None or manifest.get('channel')!='personal-local':
            raise ValueError('个人本地索引不创建公开标签或使用公开频道。')
    else:
        version_tuple(version);source_version=version
        if not unified and not VERSION.fullmatch(version):
            raise ValueError('旧公共索引仅支持历史 preview；新包须声明统一版本策略。')
    if manifest.get('format')!=1 or (not local and (manifest.get('tag')!='v'+version or manifest.get('channel')!='private-preview')) or manifest.get('draft') is True:
        raise ValueError('不是已发布 private-preview 格式；旧版无兼容清单时不可更新。')
    if manifest.get('ledgerSchema')!=1 or type(manifest.get('snapshotSchema')) is not int or manifest['snapshotSchema'] not in {2,3}: raise ValueError('发布 schema 不兼容；不自动迁移。')
    if not re.fullmatch(r'[0-9a-f]{40}',manifest.get('commit','')) or not SHA.fullmatch(manifest.get('notesSha256','')):
        raise ValueError('发布来源提交或说明哈希无效。')
    native=manifest.get('nativeVersion')
    if not isinstance(native,str) or not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',native) or tuple(map(int,native.split('.')))<owner_version_tuple(version,profile)[:3] or (local and not unified and native!=version):
        raise ValueError('原生安装版本无效或早于产品系列。')
    if unified:
        code=manifest.get('androidVersionCode');major,minor,patch=version_tuple(version)[:3]
        if type(code) is not int or not max(1,major*1_000_000+minor*1_000+patch)<=code<=2_100_000_000:
            raise ValueError('统一 Android 安装序号无效。')
    elif 'androidVersionCode' in manifest and (type(manifest['androidVersionCode']) is not int or not 0<manifest['androidVersionCode']<=2_100_000_000):
        raise ValueError('旧索引 Android 安装序号无效。')
    selected=manifest.get('profiles',{}).get(profile)
    if not isinstance(selected,dict) or any(selected.get(k)!=v for k,v in PROFILES[profile].items()): raise ValueError('发布安装身份与本机 profile 不匹配。')
    platform_asset=selected.get('platforms',{}).get(target)
    if not isinstance(platform_asset,dict): raise ValueError('此 profile 无当前桌面平台安装包。')
    assets=[]
    for entry in (manifest.get('source'),platform_asset):
        if not isinstance(entry,dict) or not isinstance(entry.get('asset'),str) or len(safe_name(entry['asset']).parts)!=1 or not SHA.fullmatch(entry.get('sha256','')):
            raise ValueError('发布资产清单无效。')
        path=checked_path(directory,entry['asset'])
        if not path.is_file() or digest(path.read_bytes())!=entry['sha256']: raise ValueError('发布资产 SHA256 校验失败。')
        assets.append(path)
    source=zip_contents(assets[0],source=True)
    official_skill_source(source)
    if source_snapshot_schema(source,required=unified)!=manifest['snapshotSchema']:
        raise ValueError('发布快照 schema 与固定源码生产端不一致。')
    try: source_version=json.loads(source['release.json'])
    except (KeyError,ValueError): raise ValueError('源码缺少产品版本清单。') from None
    expected_source_version=manifest.get('sourceVersion') if local else version
    if any(source_version.get(key)!=value for key,value in {'version':expected_source_version,'tag':'v'+expected_source_version,'channel':'private-preview'}.items()):
        raise ValueError('源码产品版本与发布清单不一致。')
    if unified and any(source_version.get(key)!=manifest[key] for key in ('nativeVersion','androidVersionCode')):
        raise ValueError('源码原生安装版本或 Android 安装序号与发布清单不一致。')
    if not local:
        for key in ('nativeVersion','androidVersionCode'):
            if key in source_version:
                if key in manifest and manifest[key]!=source_version[key]:
                    raise ValueError('公共源码安装版本与索引不一致。')
                manifest[key]=source_version[key]
        if 'androidVersionCode' in manifest and (type(manifest['androidVersionCode']) is not int or not 0<manifest['androidVersionCode']<=2_100_000_000):
            raise ValueError('公共源码 Android 安装序号无效。')
    bundle=zip_contents(assets[1])
    try: inner=json.loads(bundle['manifest.json'])
    except (KeyError,ValueError): raise ValueError('安装包缺少清单。') from None
    for key,value in {'format':1,'version':version,'nativeVersion':manifest['nativeVersion'],'platform':target,
                      'profile':profile,'identifier':PROFILES[profile]['identifier']}.items():
        if inner.get(key)!=value: raise ValueError('内外安装包版本或安装身份不匹配。')
    if inner.get('commit')!=manifest['commit']: raise ValueError('安装包提交与源码不匹配。')
    if inner.get('versionPolicy')!=manifest.get('versionPolicy'):
        raise ValueError('组件与索引的产品版本策略不一致；不能将统一版本包伪装成旧格式。')
    if local and (inner.get('distribution')!='personal-local' or inner.get('sourceVersion')!=manifest['sourceVersion']):
        raise ValueError('个人安装包的共享源码版本或本地范围与索引不匹配。')
    if unified and (inner.get('versionPolicy')!='unified' or inner.get('androidVersionCode')!=manifest['androidVersionCode']):
        raise ValueError('组件产品版本策略或 Android 安装序号与索引不匹配。')
    if 'androidVersionCode' in inner and (type(inner['androidVersionCode']) is not int or inner['androidVersionCode']!=manifest.get('androidVersionCode')):
        raise ValueError('组件与索引的 Android 安装序号不一致。')
    files=inner.get('files')
    if not isinstance(files,dict) or set(bundle)!={'manifest.json',*files}: raise ValueError('安装包文件不匹配清单。')
    suffix='.exe' if target=='windows-x64' else ''
    required={f'bin/{name}{suffix}' for name in ('ledger_cli','bill_inbox_cli','sync_diagnose')}
    if not required.issubset(files) or len([name for name in files if name.startswith('install/')])!=1: raise ValueError('安装包缺少匹配组件。')
    for name,sha in files.items():
        parts=safe_name(name).parts
        if len(parts)!=2 or parts[0] not in {'bin','install'} or not isinstance(sha,str) or not SHA.fullmatch(sha) or digest(bundle[name])!=sha:
            raise ValueError('安装包文件 SHA256 或路径校验失败。')
    return manifest,source,assets[1],digest(raw),inner


def gh_json(endpoint):
    if shutil.which('gh'):
        try:
            result=subprocess.run(['gh','api',endpoint],check=True,capture_output=True,encoding='utf-8')
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError,ValueError): pass
    request=urllib.request.Request('https://api.github.com/'+endpoint,headers={'Accept':'application/vnd.github+json','User-Agent':'Account-Product-Updater'})
    try:
        with urllib.request.urlopen(request,timeout=30) as response: return json.load(response)
    except urllib.error.HTTPError as error:
        raise ValueError('无法读取发布；先核对网络或GitHub请求限额。公开仓库无需登录；若实际受限，再在本机处理权限或用 --release-dir 本地发布目录。') from error


@contextmanager
def remote_release(target, profile):
    if profile=='personal':
        raise ValueError('个人版仅使用本地发布包；请提供 --release-dir 本地个人版本目录，不从公开 Release 下载。')
    releases=[]
    for page in range(1,101):
        batch=gh_json(f'repos/{REPOSITORY}/releases?per_page=100&page={page}')
        if not isinstance(batch,list): raise ValueError('远端发布列表无效。')
        releases.extend(batch)
        if len(batch)<100: break
    release=select_release(releases)
    with tempfile.TemporaryDirectory(prefix='account-product-download-') as temporary:
        folder=Path(temporary)
        assets={entry.get('name'):entry for entry in release.get('assets',[])}
        def fetch(name):
            if len(safe_name(name).parts)!=1 or name not in assets: raise ValueError('已发布版本缺少兼容更新清单或资产。')
            path=folder/name
            if shutil.which('gh'):
                try:
                    subprocess.run(['gh','release','download',release['tag_name'],'--repo',REPOSITORY,'--pattern',name,'--dir',str(folder)],check=True,capture_output=True)
                    return path
                except subprocess.CalledProcessError:
                    path.unlink(missing_ok=True)
            url=assets[name].get('browser_download_url','')
            expected='https://github.com/'+REPOSITORY+'/releases/download/'+release['tag_name']+'/'
            if not url.startswith(expected): raise ValueError('发布下载来源无效。')
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Account-Product-Updater'}),timeout=60) as response:
                atomic_bytes(path,response.read(1024*1024*1024+1))
            if path.stat().st_size>1024*1024*1024: raise ValueError('发布资产过大。')
            return path
        manifest=read_json(fetch('release-manifest.json'))
        if manifest.get('tag')!=release['tag_name']: raise ValueError('远端标签与发布清单不一致。')
        selected=manifest.get('profiles',{}).get(profile,{})
        for item in (manifest.get('source',{}),selected.get('platforms',{}).get(target,{})):
            if not isinstance(item.get('asset'),str): raise ValueError('此版本不支持当前 profile/平台。')
            fetch(item['asset'])
        yield folder


def trusted_bootstrap():
    spec=importlib.util.spec_from_file_location('_account_update_bootstrap',Path(__file__).resolve().parents[1]/'bootstrap.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def desktop_location(profile):
    if sys.platform=='win32':
        base=os.environ.get('LOCALAPPDATA')
        if not base: raise ValueError('无法确定桌面配置目录。')
        return Path(base)/PROFILES[profile]['desktopConfig']/'project.json'
    if sys.platform=='darwin': return Path.home()/'Library/Application Support'/PROFILES[profile]['desktopConfig']/'project.json'
    raise ValueError('仅支持 Windows/macOS 桌面更新。')


def environment_paths(root, *, prepare=False):
    """Check runtime write boundaries without opening files or following redirects."""
    names=['.venv']
    if prepare: names.extend(('.runtime/bootstrap','.runtime/python','.runtime/uv-cache'))
    def reject(_error=None):
        raise ValueError('项目 Python 环境路径不可读取或发生重定向；停止环境准备。')
    for name in names:
        directory=root/name
        for component in (directory,*directory.parents):
            if component==root.parent: break
            if redirected(component): reject()
        if not directory.exists(): continue
        if not directory.is_dir(): reject()
        for current,directories,files in os.walk(directory,followlinks=False,onerror=reject):
            parent=Path(current)
            if any(redirected(parent/item) for item in directories): reject()
            for item in files:
                candidate=parent/item
                if not redirected(candidate): continue
                ordinary_interpreter=(os.name!='nt' and parent.name=='bin' and
                                      re.fullmatch(r'python(?:3(?:\.\d+)?)?',item) and candidate.is_file())
                if not ordinary_interpreter: reject()


def desktop_plan(root, profile, config=None, *, prepare_environment=False):
    python=root/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    # Do not bind an unusable interpreter for an environment not yet configured.
    if not python.is_file() and not prepare_environment: return None
    config=Path(config) if config is not None else desktop_location(profile)
    if not config.is_absolute(): raise ValueError('桌面配置须为绝对路径。')
    for path in (config,*config.parents):
        if redirected(path): raise ValueError('桌面配置路径不能重定向。')
    value={}
    if config.exists():
        value=read_json(config)
        if not isinstance(value,dict) or Path(value.get('projectRoot','')).resolve()!=root: raise ValueError('桌面配置已绑定另一所有者项目；不覆盖。')
    return config,{**value,'projectRoot':str(root),'pythonExecutable':str(python)}


def backup_database(root, destination):
    database=root/'data/account.sqlite3'
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as source:
        with closing(sqlite3.connect(destination)) as backup:
            source.backup(backup)
    import ledger_store
    if not ledger_store.verify(destination)['ok']: raise ValueError('SQLite 恢复副本核验失败。')


@contextmanager
def update_lock(root):
    lock=checked_path(root,'.runtime/product/update.lock')
    lock.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise ValueError('另一个更新正在运行或上次中断；先检查 update.lock 与恢复日志，不自动移除锁。') from None
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump({'pid':os.getpid(),'root':str(root)},stream); stream.flush(); os.fsync(stream.fileno())
        yield
    finally:
        lock.unlink(missing_ok=True)


def update(root, *, release_dir=None, target=None, apply=False, installer=None,
           prepare_environment=False, desktop_config=None, _lock_held=False):
    root=checked_root(root); state=load_state(root)
    if checked_path(root,'.runtime/product/pending.json').exists():
        raise ValueError('发现中断的更新恢复日志；请先检查 pending.json 与恢复目录，不自动重试覆盖。')
    bootstrap=trusted_bootstrap(); target=target or bootstrap.platform_key()
    if target == 'macos-x64': raise ValueError('从 preview.5 起停止支持 Intel Mac。')
    if target not in {'windows-x64','macos-arm64'}: raise ValueError('更新入口只安装桌面；Android 另行按相同 profile 安装。')
    if release_dir is None:
        with remote_release(target,state['profile']) as folder:
            return update(root,release_dir=folder,target=target,apply=apply,installer=installer,
                          prepare_environment=prepare_environment,desktop_config=desktop_config)
    manifest,source,bundle,manifest_hash,inner=validate_release(release_dir,state['profile'],target)
    version=manifest['version']; old_version=state.get('version')
    if old_version and owner_version_tuple(version,state['profile'])<owner_version_tuple(old_version,state['profile']):
        if manifest.get('distribution')=='personal-local' and '-preview.' in old_version:
            raise ValueError('拒绝降级：本机已采用旧个人 preview 安装系列；须先核对已安装原生版本及 Android versionCode，再设置递增的个人版本。')
        raise ValueError('拒绝降级已采用的产品版本。')
    if old_version!=version:
        if old_version and state.get('sourceVersion') and version_tuple(manifest.get('sourceVersion',version))<version_tuple(state['sourceVersion']):
            raise ValueError('拒绝降级已采用的共享源码产品版本。')
        old_native,old_code=installation_baseline(root,state,require_android=manifest.get('versionPolicy')=='unified' or 'androidVersionCode' in manifest)
        legacy_path=checked_path(root,'local/personal-version.json')
        if state['profile']=='personal' and legacy_path.is_file():
            # installation_baseline validates this legacy record; its product
            # version remains an independent floor even before a receipt exists.
            if version_tuple(version)<version_tuple(read_json(legacy_path)['version']):
                raise ValueError('拒绝降级旧个人基线记录的产品版本。')
        if old_version and old_native is None:
            raise ValueError('缺少先前原生安装版本基线；请恢复已校验的旧索引或安装回执。')
        if old_version and old_code is None and manifest.get('versionPolicy')=='unified':
            raise ValueError('缺少先前 Android 安装序号基线；请恢复可信归档索引及其源码 ZIP。')
        if ((old_native is not None and tuple(map(int,manifest['nativeVersion'].split('.')))<=tuple(map(int,old_native.split('.'))))
                or (old_code is not None and (type(manifest.get('androidVersionCode')) is not int
                                             or manifest['androidVersionCode']<=old_code))):
            raise ValueError('新产品的原生安装版本和 Android 安装序号必须严格递增；拒绝降级。')
    ledger=ledger_report(root,manifest['ledgerSchema'])
    protected=protected_snapshot(root)
    desired={name:data for name,data in source.items() if managed_name(name)}
    if not desired: raise ValueError('发布源码没有共享程序。')
    skill_files=official_skill_source(source)
    saved_skill=state.get('officialSkill')
    skill_baseline=saved_skill['files'] if saved_skill else {}
    if skill_files and saved_skill is None:
        for name,data in skill_files.items():
            path=checked_path(root,name)
            if not path.is_file() or path.read_bytes()!=data:
                # Earlier updaters never managed these files. Authenticate any
                # existing different bytes; absent files are new installations.
                skill_baseline={name:sha for name,sha in previous_skill_baseline(root,state).items()
                                if checked_path(root,name).is_file()}
                break
    desired.update(skill_files)
    removed=sorted(set(skill_baseline)-set(skill_files)) if skill_files else []
    changed=[]; conflicts=[]
    for name,data in desired.items():
        path=checked_path(root,name); current=digest(path.read_bytes()) if path.is_file() else None
        expected=skill_baseline.get(name) if official_skill_name(name) else state['managed'].get(name)
        if expected is not None and current!=expected: conflicts.append(name)
        elif expected is None and current is not None and not (official_skill_name(name) and current==digest(data)): conflicts.append(name)
        elif current!=digest(data): changed.append(name)
    # Detect edits of all adopted code, even files this release retains unchanged.
    for name,expected in state['managed'].items():
        if name in desired: continue
        path=checked_path(root,name)
        if not path.is_file() or digest(path.read_bytes())!=expected: conflicts.append(name)
    for name,expected in skill_baseline.items():
        if name in skill_files: continue
        path=checked_path(root,name)
        if not path.is_file() or digest(path.read_bytes())!=expected: conflicts.append(name)
    changed+=removed
    if prepare_environment and Path(sys.executable).absolute().is_relative_to(root/'.venv'):
        raise ValueError('不能修改正在运行的 .venv 解释器；请用项目外部托管 Python 重试 --prepare-python。')
    python=root/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
    environment_paths(root,prepare=prepare_environment)
    requires_environment=not python.is_file() or any(name in changed for name in ('requirements.lock','requirements.txt','pyproject.toml'))
    if apply and requires_environment and not prepare_environment:
        raise ValueError('项目 .venv 缺失或依赖清单改变；请用外部 Python 并显式 --prepare-python 完成依赖准备。')
    helper_folder=root/f'.runtime/bin/{version}-{target}'
    helper_manifest=checked_path(root,'.runtime/helpers.json')
    if helper_folder.exists():
        for name,expected in inner['files'].items():
            candidate=checked_path(root,f'.runtime/bin/{version}-{target}/{name}')
            if not candidate.is_file() or digest(candidate.read_bytes())!=expected:
                raise ValueError('同版本本地原生组件与发布包冲突；保留现场并停止。')
    elif old_version==version:
        raise ValueError('已采用版本的本地原生组件缺失；请检查恢复目录。')
    if old_version==version:
        suffix='.exe' if target=='windows-x64' else ''
        expected_helpers={name:f'.runtime/bin/{version}-{target}/bin/{name}{suffix}' for name in ('ledger_cli','bill_inbox_cli','sync_diagnose')}
        if not helper_manifest.is_file() or read_json(helper_manifest)!={'version':1,'release':version,'helpers':expected_helpers}:
            raise ValueError('本机原生组件选择清单已改变；保留现场并停止。')
    if conflicts: raise ValueError('本机程序与采用基线冲突；保留现场：'+', '.join(sorted(set(conflicts))))
    if old_version==version and state.get('manifestSha256')!=manifest_hash: raise ValueError('同版本发布清单发生变化；保留原版本并停止。')
    binding=desktop_plan(root,state['profile'],desktop_config,prepare_environment=prepare_environment)
    skill_report={'path':str(checked_path(root,SKILL_ENTRY)),
                  'version':version if skill_files else (saved_skill or {}).get('version'),
                  'sourceVersion':manifest.get('sourceVersion',version) if skill_files else (saved_skill or {}).get('sourceVersion'),
                  'commit':manifest['commit'] if skill_files else (saved_skill or {}).get('commit'),
                  'state':'pending' if skill_files else 'unsupported'}
    guidance_hash=digest(source[GUIDANCE]) if GUIDANCE in source else None
    guidance=guidance_status(root,version,guidance_hash,pending=old_version!=version)
    report={'ok':True,'dryRun':not apply,'root':str(root),'profile':state['profile'],'version':version,
            'changed':changed,'removed':removed,'retained':sorted(set(state['managed'])-set(desired)),
            'officialSkill':skill_report,
            'ledger':ledger,'protectedPreserved':True,'protectedDeltaCount':0,
            'binaryInstall':'not-attempted','requiresEnvironment':requires_environment,
            'environment':'requested' if prepare_environment else ('requires-preparation' if requires_environment else 'preserved'),
            'agentGuidance':guidance,
            'releaseDocs':str(root/f'.runtime/product/releases/{version}/source'),
            'next':update_next(skill_report,guidance)}
    skill_record={'path':SKILL_ENTRY,'supported':bool(skill_files),
                  'version':skill_report['version'],'sourceVersion':skill_report['sourceVersion'],'commit':skill_report['commit'],
                  'files':{name:digest(data) for name,data in skill_files.items()} if skill_files else skill_baseline}
    install_required=old_version!=version or any(managed_name(name) for name in changed) or requires_environment
    if old_version==version and not changed and not requires_environment and saved_skill==skill_record and state.get('guidanceSha256')==guidance_hash:
        return {**report,'unchanged':True,'officialSkill':{**skill_report,'state':'synchronized' if skill_files else 'unsupported'}}
    if not apply: return report
    if not _lock_held:
        with update_lock(root):
            return update(root,release_dir=release_dir,target=target,apply=True,installer=installer,
                          prepare_environment=prepare_environment,desktop_config=desktop_config,_lock_held=True)
    # Validate every internal destination before the first write.
    checked_path(root,'.runtime/helpers.json'); checked_path(root,'.runtime/ledger/publish.json')
    release_parent=checked_path(root,f'.runtime/product/releases/{version}/source/release.json').parent
    recovery=checked_path(root,f'.runtime/product/recovery/{version}-{uuid.uuid4().hex}/journal.json').parent
    stage=release_parent.parent
    source_asset=manifest['source']['asset']
    archived_source=checked_path(root,f'.runtime/product/releases/{version}/{source_asset}')
    if archived_source.exists() and digest(archived_source.read_bytes())!=manifest['source']['sha256']:
        raise ValueError('已暂存同版本源码 ZIP 哈希冲突。')
    if stage.exists():
        for name,data in source.items():
            path=checked_path(root,(release_parent/name).relative_to(root).as_posix())
            if not path.is_file() or path.read_bytes()!=data: raise ValueError('已暂存同版本源码不一致；保留现场。')
    else:
        for name in source: checked_path(root,(release_parent/name).relative_to(root).as_posix())
    # Freeze verified bytes before the first write, independent of concurrent asset changes.
    bundle_bytes=bundle.read_bytes()
    source_bytes=checked_path(checked_root(release_dir),source_asset).read_bytes()
    selected=manifest['profiles'][state['profile']]['platforms'][target]
    manifest_bytes=checked_path(checked_root(release_dir),'release-manifest.json').read_bytes()
    if digest(source_bytes)!=manifest['source']['sha256'] or digest(bundle_bytes)!=selected['sha256'] or digest(manifest_bytes)!=manifest_hash:
        raise ValueError('发布资产在预检后改变。')
    for name in zip_contents(bundle):
        if name != 'manifest.json':
            checked_path(root,f".runtime/bin/{version}-{target}/{name}")
    recovery.mkdir(parents=True)
    ledger_backup=recovery/'account.sqlite3'
    if ledger['initialized']: backup_database(root,ledger_backup)
    previous={}
    for name in changed+[STATE,'.runtime/helpers.json','.runtime/ledger/publish.json']:
        path=checked_path(root,name); previous[name]=path.read_bytes() if path.is_file() else None
    binding_old=binding[0].read_bytes() if binding and binding[0].exists() else None
    for name,data in previous.items():
        if data is not None: atomic_bytes(recovery/'files'/name,data)
    if binding_old is not None: atomic_bytes(recovery/'desktop-project.json',binding_old)
    journal={**report,'phase':'prepared','previouslyAbsent':[name for name,data in previous.items() if data is None],
             'ledgerBackup':str(ledger_backup) if ledger['initialized'] else None,'desktopConfig':str(binding[0]) if binding else None}
    atomic_json(recovery/'journal.json',journal)
    pending=checked_path(root,'.runtime/product/pending.json')
    atomic_json(pending,{'recovery':str(recovery),'version':version,'phase':'prepared'})
    attempted=False
    try:
        if not stage.exists():
            for name,data in source.items(): atomic_bytes(release_parent/name,data)
            atomic_bytes(stage/'release-manifest.json',manifest_bytes)
        atomic_bytes(archived_source,source_bytes)
        # Keep the verified bundle for recovery/retry; never use a redownload here.
        staged_bundle=stage/bundle.name
        atomic_bytes(staged_bundle,bundle_bytes)
        assert_protected(root,protected)
        if ledger_report(root,manifest['ledgerSchema'])!=ledger: raise ValueError('预检后账本发生变化；停止更新。')
        if checked_path(root,STATE).read_bytes()!=previous[STATE]: raise ValueError('采用状态在预检后发生变化。')
        for name in changed:
            path=checked_path(root,name)
            if (path.read_bytes() if path.exists() else None)!=previous[name]: raise ValueError('程序在预检后发生冲突。')
            if name in removed: path.unlink()
            else: atomic_bytes(path,desired[name])
        if install_required:
            native=bootstrap.stage_bundle(root,staged_bundle,target)
            if prepare_environment:
                bootstrap.prepare_python(root)
            if binding: atomic_json(binding[0],binding[1])
            gate=root/'.runtime/ledger/publish.json'
            if gate.is_file():
                settings=read_json(gate)
                if not isinstance(settings,dict): raise ValueError('本机发布门禁格式无效。')
                if 'publisher' in settings:
                    settings['publisher']=str(root/native['helpers']['ledger_cli'])
                    atomic_json(gate,settings)
            assert_protected(root,protected)
            attempted=True
            (installer or bootstrap.install_app)(native['installer'])
        assert_protected(root,protected)
        if ledger_report(root,manifest['ledgerSchema'])!=ledger: raise ValueError('更新后账本业务哈希改变；不自动恢复账本，请核对并发记账。')
        assert_protected(root,protected)
        for name,data in skill_files.items():
            path=checked_path(root,name)
            if not path.is_file() or path.read_bytes()!=data: raise ValueError('更新后官方 Skill 内容冲突；停止并恢复程序。')
        if any(checked_path(root,name).exists() for name in removed): raise ValueError('更新后官方 Skill 引用文件冲突。')
        new_state={**state,'version':version,'manifestSha256':manifest_hash,'commit':manifest['commit'],
                   'distribution':manifest.get('distribution','public'),'sourceVersion':manifest.get('sourceVersion',version),
                   'officialSkill':skill_record,'guidanceSha256':guidance_hash,
                   'managed':{**state['managed'],**{name:digest(data) for name,data in desired.items() if managed_name(name)}}}
        new_state['nativeVersion']=manifest['nativeVersion']
        if 'androidVersionCode' in manifest: new_state['androidVersionCode']=manifest['androidVersionCode']
        else: new_state.pop('androidVersionCode',None)
        atomic_json(root/STATE,new_state)
        outcome='installed' if attempted else 'not-attempted'
        journal.update(phase='complete',binaryInstall=outcome)
        atomic_json(recovery/'journal.json',journal)
        pending.unlink()
        guidance=guidance_status(root,version,guidance_hash)
        return {**report,'unchanged':False,'binaryInstall':outcome,'recovery':str(recovery),
                'agentGuidance':guidance,'next':update_next(skill_report,guidance),
                'officialSkill':{**skill_report,'state':'synchronized' if skill_files else 'unsupported'},
                'releaseDocs':str(release_parent),'ledgerBackup':str(ledger_backup) if ledger['initialized'] else None}
    except Exception as error:
        # Personal/concurrent edits are observed and reported, never restored blindly.
        try:
            delta=protected_delta(protected,protected_snapshot(root))
            preserved=delta==0
        except ProtectedChange:
            delta=None; preserved=False
        report.update(protectedPreserved=preserved,protectedDeltaCount=delta)
        journal.update(protectedPreserved=preserved,protectedDeltaCount=delta)
        rollback_errors=[]
        for name,data in reversed(list(previous.items())):
            try:
                path=checked_path(root,name)
                if data is None: path.unlink(missing_ok=True)
                else: atomic_bytes(path,data)
            except Exception as rollback_error: rollback_errors.append(str(rollback_error))
        if binding:
            try:
                if binding_old is None: binding[0].unlink(missing_ok=True)
                else: atomic_bytes(binding[0],binding_old)
            except Exception as rollback_error: rollback_errors.append(str(rollback_error))
        outcome='uncertain-needs-retry' if attempted else 'not-attempted'
        journal.update(phase='failed',error=str(error),binaryInstall=outcome,rollbackErrors=rollback_errors,
                       environment='may-require-repair' if prepare_environment else 'preserved')
        atomic_json(recovery/'journal.json',journal)
        if not rollback_errors: pending.unlink(missing_ok=True)
        return {**report,'ok':False,'error':str(error),'binaryInstall':outcome,'recovery':str(recovery),
                'rollbackErrors':rollback_errors,'environment':journal['environment'],
                'next':'程序和配置已尝试恢复；外部安装未保证回滚，保留恢复目录并核对/重试。不自动恢复业务账本。'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('command',choices=('status','adopt','check','update','apply'))
    parser.add_argument('--profile',choices=tuple(PROFILES))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--release-dir',type=Path)
    parser.add_argument('--prepare-python',action='store_true',help='显式更新项目 Python 锁定依赖；不运行开发工具链')
    args=parser.parse_args(argv)
    if args.command=='status': result=status(args.root)
    elif args.command=='adopt': result=adopt(args.root,profile=args.profile,apply=args.apply)
    else:
        if args.profile: parser.error('更新身份来自 adopt 状态，不能用 --profile 覆盖。')
        result=update(args.root,release_dir=args.release_dir,apply=args.apply and args.command!='check',prepare_environment=args.prepare_python)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 1


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    try: raise SystemExit(main())
    except (OSError,ValueError,sqlite3.DatabaseError,zipfile.BadZipFile,subprocess.SubprocessError) as error:
        print(json.dumps({'ok':False,'error':str(error)},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(1)
