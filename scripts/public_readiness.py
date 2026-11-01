"""Read-only scan of every reachable Git revision before a visibility decision."""
import argparse
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

PROTECTED = {'data','local','sra','.runtime','.venv','exports','archive','.codex-attachments','.codex-remote-attachments'}
PATTERNS = [r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
            r'\bgh[pousr]_[A-Za-z0-9]{30,}\b', r'\bgithub_pat_[A-Za-z0-9_]{30,}\b',
            r'C:[\\/]+Users[\\/]+[0-9]{5}(?:[\\/]|\b)']


def inspect(root):
    root=Path(root).resolve()
    def git(*args, data=None):
        return subprocess.run(['git','-C',str(root),*args],input=data,capture_output=True,check=True).stdout
    revisions=git('rev-list','--all').decode().splitlines()
    if not revisions: raise ValueError('没有可检查的Git历史')
    locations={}; binary=set(); findings=[]
    for commit in revisions:
        for entry in git('ls-tree','-rz','--full-tree',commit).split(b'\0'):
            if not entry: continue
            header,name=entry.split(b'\t',1)
            mode,kind,oid=header.decode().split()
            if kind!='blob': continue
            name=name.decode('utf-8',errors='replace')
            locations.setdefault(oid,[])
            if any(item['path']==name for item in locations[oid]): continue
            item={'path':name,'commit':commit}
            locations[oid].append(item)
            path=PurePosixPath(name)
            if (any(p.casefold() in PROTECTED for p in path.parts)
                    or path.name=='PROJECT_MEMORY.md'
                    or path.suffix.lower() in {'.csv','.xlsx','.sqlite3','.sqlite','.db','.jks','.keystore','.pem','.key','.p12','.pfx'}):
                findings.append({**item,'reason':'protected-path'})
            if mode=='120000': findings.append({**item,'reason':'symlink-manual-review'})
    objects = dict(locations)
    for commit in revisions:
        objects[commit] = [{'path':'<commit-metadata>', 'commit':commit}]
    for line in git('for-each-ref','--format=%(objecttype) %(objectname)','refs/tags').decode().splitlines():
        kind, oid = line.split()
        if kind == 'tag': objects[oid] = [{'path':'<tag-metadata>', 'commit':oid}]
    raw=git('cat-file','--batch',data=('\n'.join(objects)+'\n').encode())
    reader=io.BytesIO(raw)
    for oid in objects:
        header=reader.readline().split()
        if len(header)!=3 or header[0].decode()!=oid: raise ValueError('Git对象读取失败')
        payload=reader.read(int(header[2])); reader.read(1)
        if b'\0' in payload:
            binary.add(oid); continue
        content=payload.decode('utf-8',errors='replace')
        if any(re.search(pattern,content) for pattern in PATTERNS):
            findings.extend({**item,'reason':'sensitive-pattern'} for item in objects[oid])
    return dict(ok=not findings,revisions=len(revisions),blobs=len(locations),findings=findings,
                binaryBlobsRequiringManualReview=len(binary),manualReviewRequired=True,visibilityChanged=False,
                note='仅已知模式及受保护路径检查；公开前还需人工复核内容、作者信息、许可证、Issue、Release和Actions产物/日志。工具不改可见性或历史。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    try: report=inspect(args.root)
    except (OSError,ValueError,subprocess.SubprocessError):
        report={'ok':False,'error':'历史检查失败；保留仓库现场并核对Git状态','visibilityChanged':False}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
