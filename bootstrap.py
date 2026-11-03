"""Prepare this checkout's local runtime; default is a read-only plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent
PYTHON_VERSION = '3.12.12'


def source_context(root, expected_root=None, *, apply=False):
    root = Path(root).resolve(strict=True)
    if apply and expected_root is None:
        raise ValueError('--apply 必须提供 --expected-root 本项目绝对路径；先核对 dry-run 的 root。')
    if expected_root is not None:
        expected = Path(expected_root)
        if not expected.is_absolute() or expected.resolve() != root:
            raise ValueError('--expected-root 必须是本份启动器所在项目的绝对路径；未执行任何配置。')
    release = json.loads((root/'release.json').read_text(encoding='utf-8'))
    if not isinstance(release,dict) or not all(isinstance(release.get(key),str) for key in ('repository','version','tag')):
        raise ValueError('release.json 源码身份或版本无效。')
    repo = release.get('repository', '')
    if repo != 'Milozpace/Account-Preview' or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.]+)?', release.get('version','')) or release.get('tag') != 'v'+release['version']:
        raise ValueError('release.json 源码身份或版本无效。')
    provenance = {'kind':'source-archive', 'repository':repo}
    if (root/'.git').exists():
        try:
            remote = subprocess.run(['git','-C',str(root),'config','--get','remote.origin.url'],check=True,capture_output=True,text=True).stdout.strip()
            if remote.startswith('git@github.com:'):
                origin_repo = remote[len('git@github.com:'):]
            else:
                parsed = urlsplit(remote)
                if parsed.scheme not in {'https','ssh'} or parsed.hostname != 'github.com': raise ValueError()
                origin_repo = parsed.path.lstrip('/')
            if origin_repo.endswith('.git'): origin_repo=origin_repo[:-4]
            if origin_repo.casefold() != repo.casefold(): raise ValueError()
        except (ValueError, OSError, subprocess.CalledProcessError):
            raise ValueError('Git origin 与 release.json 的规范仓库不匹配；保留现场，未执行配置。') from None
        provenance.update(kind='git', origin='https://github.com/'+repo+'.git')
    return release, provenance


def platform_key(system=None, machine=None):
    system, machine = system or platform.system(), (machine or platform.machine()).lower()
    if system == 'Windows' and machine in {'amd64', 'x86_64'}: return 'windows-x64'
    if system == 'Darwin' and machine in {'arm64', 'aarch64'}: return 'macos-arm64'
    if system == 'Darwin' and machine in {'x86_64', 'amd64'}: return 'macos-x64'
    raise ValueError('此试用版支持 Windows x64 和 macOS Apple Silicon/Intel。')


def project_profile(root):
    sys.path.insert(0, str(ROOT/'scripts'))
    try:
        from runtime_tools import owner_profile
        return owner_profile(root)
    finally:
        sys.path.pop(0)


def config_location(root=ROOT):
    folder = project_profile(root)['desktopConfig']
    if platform.system() == 'Windows':
        parent = os.environ.get('LOCALAPPDATA')
        if not parent: raise ValueError('无法定位本机配置目录。')
        return Path(parent) / folder / 'project.json'
    if platform.system() == 'Darwin':
        return Path.home() / 'Library/Application Support' / folder / 'project.json'
    raise ValueError('不支持的桌面系统。')


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.preview-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def config_lock(config):
    lock = config.with_name(config.name+'.lock')
    try:
        fd=os.open(lock, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    except FileExistsError:
        raise ValueError('桌面配置正在更新或上次更新中断；保留配置和锁文件，核对后重试。') from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def configure_desktop(root, python, *, config=None, apply=False, rebind_from=None):
    root, python = Path(root).resolve(), Path(python).absolute()
    config = Path(config) if config is not None else config_location(root)
    value = {'projectRoot': str(root), 'pythonExecutable': str(python)}
    old_root = None
    if rebind_from is not None:
        old_root=Path(rebind_from)
        if not old_root.is_absolute(): raise ValueError('--rebind-from 必须是旧项目绝对路径。')
        old_root=old_root.resolve()
    def inspect():
        if config.is_symlink(): raise ValueError('配置路径不能是符号链接。')
        raw=config.read_bytes() if config.exists() else None
        old=json.loads(raw.decode('utf-8')) if raw is not None else None
        if raw is not None:
            if not isinstance(old,dict) or set(old)!=set(value) or not isinstance(old.get('projectRoot'),str) or not Path(old['projectRoot']).is_absolute():
                raise ValueError('已有桌面配置结构无效；保留现场并检查。')
            if Path(old['projectRoot']).resolve()!=root and (old_root is None or Path(old['projectRoot']).resolve()!=old_root):
                raise ValueError('此身份已绑定另一项目；关闭App后明确 --rebind-from 当前旧绝对根才能改绑，或使用独立系统用户。')
        elif old_root is not None:
            raise ValueError('--rebind-from 无当前旧绑定可匹配；保留现场并检查。')
        return raw,old
    raw,old=inspect()
    if old==value: return {'changed':False,'status':'unchanged','path':str(config)}
    if not apply: return {'changed':False,'dryRun':True,'status':'planned','path':str(config)}
    if not python.is_file(): raise ValueError('项目 Python 环境尚未准备。')
    config.parent.mkdir(parents=True,exist_ok=True)
    with config_lock(config):
        current,current_old=inspect()
        if current!=raw: raise ValueError('桌面配置在检查后已改变；拒绝覆盖，请重新检查。')
        backup=None
        if current_old and Path(current_old['projectRoot']).resolve()!=root:
            fd,name=tempfile.mkstemp(prefix=config.name+'.backup-', dir=config.parent)
            with os.fdopen(fd,'wb') as stream:
                stream.write(current); stream.flush(); os.fsync(stream.fileno())
            backup=name
        # Cooperating writers hold the lock; detect external edits before replace.
        if (config.read_bytes() if config.exists() else None)!=raw:
            raise ValueError('桌面配置刚被其他程序修改；拒绝覆盖，请重新检查。')
        atomic_json(config,value)
    return {'changed':True,'dryRun':False,'status':'completed','path':str(config), **({'backup':backup} if backup else {})}


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_member(name):
    path = PurePosixPath(name)
    if '\\' in name or ':' in name or path.is_absolute() or path.as_posix() != name or any(p in {'..', '.'} for p in path.parts):
        raise ValueError('发布包包含不安全路径。')
    if not path.parts or path.parts[0] not in {'bin', 'install'} or len(path.parts) != 2:
        raise ValueError('发布包内容不在允许清单内。')
    return path


def stage_bundle(root, archive, target):
    """Verify every byte before writing a versioned helper set; switch one manifest."""
    root = Path(root).resolve()
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        names = [p.filename for p in infos]
        if len({name.casefold() for name in names}) != len(names) or 'manifest.json' not in names:
            raise ValueError('发布包清单缺失或路径重复。')
        if sum(p.file_size for p in infos) > 1024 * 1024 * 1024:
            raise ValueError('发布包解压体积超过上限。')
        if z.getinfo('manifest.json').file_size > 128 * 1024: raise ValueError('清单过大。')
        manifest = json.loads(z.read('manifest.json'))
        version = manifest.get('version', '')
        if manifest.get('format') != 1 or manifest.get('platform') != target or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.]+)?', version):
            raise ValueError('发布包版本或平台不匹配。')
        if manifest.get('profile') is not None or manifest.get('distribution')=='personal-local':
            profile=project_profile(root)
            if manifest.get('profile')!=profile['name'] or manifest.get('identifier')!=profile['identifier']:
                raise ValueError('安装包与本机已采用的身份不匹配；个人包须明确采用 personal 身份。')
        source_release = root / 'release.json'
        if manifest.get('distribution')=='personal-local' and not source_release.is_file():
            raise ValueError('个人本地包必须与已核验的共享源码版本匹配。')
        if source_release.exists():
            expected = json.loads(source_release.read_text(encoding='utf-8'))
            personal_local=(manifest.get('profile')=='personal' and manifest.get('identifier')=='com.account.probe'
                            and manifest.get('distribution')=='personal-local'
                            and manifest.get('sourceVersion')==expected.get('version')
                            and manifest.get('nativeVersion')==version
                            and re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',version))
            if expected.get('version') != version and not personal_local:
                raise ValueError('源码与安装包版本不匹配；请使用同一已发布标签的源码和组件。')
        files = manifest.get('files')
        if not isinstance(files, dict) or set(names) != {'manifest.json', *files}:
            raise ValueError('发布包文件与清单不一致。')
        suffix = '.exe' if target == 'windows-x64' else ''
        required = {f'bin/{n}{suffix}' for n in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')}
        if not required.issubset(files): raise ValueError('发布包缺少必需工具。')
        installers = [p for p in files if p.startswith('install/')]
        if len(installers) != 1: raise ValueError('发布包必须包含一个桌面安装程序。')
        for info in infos:
            if info.filename == 'manifest.json': continue
            safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16): raise ValueError('发布包不能包含符号链接。')
            if hashlib.sha256(z.read(info)).hexdigest() != files[info.filename]:
                raise ValueError('发布包文件校验失败。')
        runtime = root / '.runtime'
        helper_parent = runtime / 'bin'
        if runtime.is_symlink() or helper_parent.is_symlink(): raise ValueError('运行目录不能重定向。')
        helper_parent.mkdir(parents=True, exist_ok=True)
        folder = helper_parent / (version + '-' + target)
        if folder.exists():
            if folder.is_symlink() or any(not (folder / p).is_file() or digest(folder / p) != sha for p, sha in files.items()):
                raise ValueError('同版本本地工具与发布清单不一致，保留现场并停止。')
        else:
            with tempfile.TemporaryDirectory(prefix='.stage-', dir=helper_parent) as temp:
                temp = Path(temp)
                for name in files:
                    p = temp / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(z.read(name))
                    if name.startswith('bin/') and suffix == '': p.chmod(0o700)
                atomic_json(temp / 'manifest.json', manifest)
                os.replace(temp, folder)
        selected = {name: (folder / f'bin/{name}{suffix}').relative_to(root).as_posix()
                    for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')}
        atomic_json(runtime / 'helpers.json', {'version': 1, 'release': version, 'helpers': selected})
        return {'version': version, 'platform': target, 'installer': str(folder / installers[0]), 'helpers': selected}


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def setup_native(root, *, initialize=False, enable_publish=False):
    root = Path(root).resolve()
    source = root/'data/source.json'
    if not source.is_file() or json.loads(source.read_text(encoding='utf-8')) != {'kind':'sqlite','path':'account.sqlite3'} or not (root/'data/account.sqlite3').is_file():
        raise ValueError('请先完成本项目空账本初始化与 verify，再准备同步身份。')
    sys.path.insert(0, str(ROOT/'scripts'))
    try:
        from runtime_tools import helper_path
        publisher = helper_path(root, 'ledger_cli')
    finally:
        sys.path.pop(0)
    status = json.loads(run([publisher, '--status-json'], capture_output=True, text=True, encoding='utf-8').stdout)
    if not isinstance(status.get('initialized'), bool) or not isinstance(status.get('publishEnabled'), bool):
        raise ValueError('原生组件状态格式不匹配，请使用本版本发布包。')
    if initialize and not status['initialized']:
        run([publisher, '--init'])
        status = json.loads(run([publisher, '--status-json'], capture_output=True, text=True, encoding='utf-8').stdout)
        if status.get('initialized') is not True: raise ValueError('原生身份尚未确认建立。')
    if enable_publish:
        if status['initialized'] is not True: raise ValueError('先使用 --initialize-identity 创建独立身份。')
        if not status['publishEnabled']: run([publisher, '--enable-publish'])
        local_gate = root/'.runtime/ledger/publish.json'
        settings = json.loads(local_gate.read_text(encoding='utf-8')) if local_gate.exists() else {}
        settings['enabled'] = True
        atomic_json(local_gate, settings)
    return {'initialized': status['initialized'], 'publishEnabled': enable_publish or status['publishEnabled'],
            'cloud': 'unverified', 'next': '身份已存在时保留；只有实际发布回读成功才能确认云端同步。'}


def prepare_python(root, uv=None):
    root = Path(root)
    python = root / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
    if uv is None:
        names = ['uv.exe'] if os.name == 'nt' else [
            ('uv-aarch64-apple-darwin/uv' if platform.machine() == 'arm64' else 'uv-x86_64-apple-darwin/uv')]
        # An existing owner may have no runtime yet. Borrow only the trusted
        # tools' uv executable; managed CPython/cache still live in the owner root.
        uv = next((base/'.runtime/bootstrap'/name for base in (root, ROOT) for name in names
                   if (base/'.runtime/bootstrap'/name).is_file()), None)
    if uv:
        uv = Path(uv).resolve(strict=True)
        env = {**os.environ, 'UV_PYTHON_INSTALL_DIR': str(root / '.runtime/python'),
               'UV_CACHE_DIR': str(root / '.runtime/uv-cache')}
        if not python.exists(): run([uv, 'venv', '--python', PYTHON_VERSION, '--managed-python', root / '.venv'], env=env)
        run([uv, 'pip', 'sync', '--python', python, '--require-hashes', root / 'requirements.lock'], env=env)
    else:
        if sys.version_info[:2] != (3, 12): raise ValueError('请运行 bootstrap.ps1 或 bootstrap.sh，准备项目专用 Python 3.12。')
        if not python.exists(): run([sys.executable, '-m', 'venv', root / '.venv'])
        run([python, '-m', 'pip', 'install', '--require-hashes', '-r', root / 'requirements.lock'])
    run([python, '-c', 'import sqlite3,openpyxl; assert sqlite3.sqlite_version_info >= (3,37,0)'])
    return python


def download_release(root, repo, tag, target):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo): raise ValueError('仓库须为 OWNER/REPO。')
    if not re.fullmatch(r'v[0-9][A-Za-z0-9.-]*', tag): raise ValueError('发布标签无效。')
    name = f'Account-Preview-{tag[1:]}-{target}.zip'
    with tempfile.TemporaryDirectory(prefix='account-release-') as temp:
        downloaded=False
        if shutil.which('gh'):
            try:
                run(['gh', 'release', 'download', tag, '--repo', repo, '--pattern', name,
                     '--pattern', 'SHA256SUMS', '--dir', temp],capture_output=True)
                downloaded=True
            except (OSError,subprocess.CalledProcessError):
                for file in (name,'SHA256SUMS'): (Path(temp)/file).unlink(missing_ok=True)
        if not downloaded:
            for file,limit in (('SHA256SUMS',128*1024),(name,1024*1024*1024)):
                request=urllib.request.Request(f'https://github.com/{repo}/releases/download/{tag}/{file}',headers={'User-Agent':'Account-Bootstrap'})
                try:
                    with urllib.request.urlopen(request,timeout=60) as response:
                        content=response.read(limit+1)
                except (urllib.error.URLError,TimeoutError) as error:
                    raise ValueError('无法下载公开发布包；核对网络、已发布标签和资产。也可从发布页下载同版ZIP与SHA256SUMS后使用 --bundle。若仓库实际受限，再在本机处理访问权限。') from error
                if len(content)>limit: raise ValueError('发布资产过大。')
                (Path(temp)/file).write_bytes(content)
        hashes = {}
        for line in (Path(temp) / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
            sha, file = line.split(maxsplit=1); hashes[file.lstrip('*')] = sha
        archive = Path(temp) / name
        if hashes.get(name) != digest(archive): raise ValueError('下载包 SHA256 校验失败。')
        return stage_bundle(root, archive, target)


def install_app(installer):
    installer = Path(installer).resolve(strict=True)
    if platform.system() == 'Windows':
        run([installer, '/S'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'installed': True, 'platform': 'Windows'}
    mounted = run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-plist', installer], capture_output=True)
    mounts = [p['mount-point'] for p in plistlib.loads(mounted.stdout)['system-entities'] if 'mount-point' in p]
    if len(mounts) != 1: raise ValueError('无法识别安装映像。')
    try:
        applications = list(Path(mounts[0]).glob('*.app'))
        if len(applications) != 1: raise ValueError('安装映像须含一个应用。')
        destination = Path.home() / 'Applications/Account Preview.app'
        destination.parent.mkdir(parents=True, exist_ok=True)
        run(['ditto', applications[0], destination])
        return {'installed': True, 'path': str(destination), 'firstOpen': '请在系统设置中确认此应用的首次打开。'}
    finally:
        run(['hdiutil', 'detach', mounts[0]])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-root', type=Path, help='--apply 必填：本份启动器所在项目的绝对路径')
    parser.add_argument('--rebind-from', type=Path, help='先关闭App再显式改绑；必须匹配当前绑定的旧绝对根，并选择 --configure-desktop --apply')
    parser.add_argument('--uv', type=Path)
    parser.add_argument('--repo')
    parser.add_argument('--tag', help='默认使用本份源码release.json的tag，不跟随开发分支或混装旧包')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--configure-desktop', action='store_true')
    parser.add_argument('--install-app', action='store_true')
    parser.add_argument('--initialize-identity', action='store_true')
    parser.add_argument('--enable-publish', action='store_true')
    args = parser.parse_args(argv)
    if args.repo and args.bundle: parser.error('--repo 与 --bundle 不能同时使用。')
    release, provenance = source_context(ROOT,args.expected_root,apply=args.apply)
    if args.rebind_from is not None and (not args.apply or not args.configure_desktop or not args.rebind_from.is_absolute()):
        raise ValueError('--rebind-from 必须提供旧绝对根，并同时选择 --apply --expected-root 和 --configure-desktop。')
    if args.install_app and not (args.repo or args.bundle): raise ValueError('--install-app 需要 --repo 或 --bundle。')
    if args.repo and args.repo.casefold()!=release['repository'].casefold(): raise ValueError('--repo 与本份源码仓库不匹配。')
    tag = args.tag or release['tag']
    if args.repo and tag != release['tag']:
        raise ValueError('指定标签与本份源码不匹配；先取得该标签的完整源码。')
    target = platform_key()
    # Reject binding conflicts before environment preparation, downloads or writes.
    if args.apply and args.configure_desktop:
        planned_python=ROOT/('.venv/Scripts/python.exe' if os.name=='nt' else '.venv/bin/python')
        configure_desktop(ROOT,planned_python,rebind_from=args.rebind_from)
    requested={'python':True,'components':bool(args.repo or args.bundle),'desktop':args.configure_desktop,'app':args.install_app,'native':bool(args.initialize_identity or args.enable_publish)}
    steps={name:{'requested':selected,'status':'planned' if selected else 'skipped'} for name,selected in requested.items()}
    result = {'ok': True, 'dryRun': not args.apply, 'root': str(ROOT.resolve()), 'platform': target,
              'source':provenance, 'steps':steps,
              'sourceVersion': release['version'], 'releaseTag': tag,
              'next': '运行account.py doctor；首次 mini 按入门说明用全零技术预算占位、零滚存，记录预算未制定；已有账本不重新初始化或重置参数。'}
    if args.apply:
        python = prepare_python(ROOT, args.uv)
        result['python'] = str(python)
        steps['python']['status']='completed'
        if args.repo: result['release'] = download_release(ROOT, args.repo, tag, target)
        elif args.bundle: result['release'] = stage_bundle(ROOT, args.bundle, target)
        if requested['components']: steps['components']['status']='completed'
        if args.configure_desktop:
            result['desktop'] = configure_desktop(ROOT, python, apply=True,rebind_from=args.rebind_from)
            steps['desktop']['status']=result['desktop']['status']
        if args.install_app:
            if 'release' not in result: raise ValueError('--install-app 需要 --repo 或 --bundle。')
            result['app'] = install_app(result['release']['installer'])
            steps['app']['status']='completed'
        if args.initialize_identity or args.enable_publish:
            result['native'] = setup_native(ROOT, initialize=args.initialize_identity, enable_publish=args.enable_publish)
            steps['native']['status']='completed'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    # JSON is consumed by agents and redirected terminals on either desktop OS.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try: raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'next': '已完成的本地步骤保留；处理错误后重试，不重新初始化账本。'}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
