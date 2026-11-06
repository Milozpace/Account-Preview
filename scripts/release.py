"""Allowlisted source export, privacy audit and reproducible trial bundle assembly."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import io
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile
try:
    import product_version
    import personal_version
except ModuleNotFoundError:
    from scripts import product_version, personal_version

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'README.md', 'AGENTS.md', '.gitignore', 'bootstrap.py', 'bootstrap.ps1', 'bootstrap.sh',
              'requirements.lock', 'pyproject.toml', 'release.json', 'NOTICE.md', '.gitattributes', '.editorconfig'}
ALLOWED_DIRS = {'scripts', 'tests', 'docs', 'templates', '.agents', '.github', 'dashboard', 'app'}
EXTENSIONS = {'.py', '.ps1', '.sh', '.md', '.json', '.toml', '.lock', '.ts', '.css', '.html', '.rs',
              '.yml', '.yaml', '.kt', '.kts', '.java', '.gradle', '.properties', '.xml', '.pro', '.png',
              '.ico', '.icns', '.svg', '.webp'}
EXCLUDED = {'node_modules', 'target', 'dist', 'build', '.runtime', '.gradle', '.idea', '.cxx', '__pycache__',
            '.git', '.venv', '.tauri', 'local', 'data', 'sra', 'exports', 'archive'}
GENERATED = {'tauri.settings.gradle', 'tauri.properties', 'local.properties', 'key.properties', 'keystore.properties',
             'proguard-tauri.pro', 'tauri.build.gradle.kts'}


def source_files(root):
    root = Path(root).resolve()
    for directory, dirs, names in os.walk(root):
        base = Path(directory)
        dirs[:] = sorted(name for name in dirs if name not in EXCLUDED and not (base/name).is_symlink()
                         and (base != root or name in ALLOWED_DIRS))
        relative = base.relative_to(root).as_posix()
        if relative.startswith(('app/src-tauri/gen/android/app/src/main/assets', 'app/src-tauri/gen/android/app/src/main/jniLibs')) or ('app/src-tauri/gen/android/' in relative and base.name == 'generated'):
            dirs[:] = []; continue
        for name in sorted(names):
            p = base/name; rel = p.relative_to(root)
            if p.is_symlink() or not p.is_file(): continue
            if len(rel.parts) == 1:
                if p.name in ROOT_FILES: yield p
            elif p.name not in GENERATED:
                if p.suffix.lower() in EXTENSIONS or p.name in {'gradlew', 'gradlew.bat', 'gradle-wrapper.jar'}: yield p


def audit_sources(root):
    root = Path(root).resolve()
    findings = []
    patterns = [r'C:[\\/]+Users[\\/]+[0-9]{5}(?:[\\/]|\b)',
                r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'\bgh[pousr]_[A-Za-z0-9]{30,}\b', r'\bgithub_pat_[A-Za-z0-9_]{30,}\b']
    files = list(source_files(root))
    for p in files:
        if p.suffix.lower() in {'.png', '.ico', '.icns', '.webp', '.jar'}: continue
        content = p.read_text(encoding='utf-8', errors='replace')
        if any(re.search(pattern, content) for pattern in patterns): findings.append(p.relative_to(root).as_posix())
    return {'ok': not findings, 'files': len(files), 'findings': findings,
            'note': '路径白名单及已知敏感模式检查；发布前仍须审查新加入的文件。'}


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(root, *args, binary=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=False)
    if result.returncode: raise ValueError('Git 校验失败：' + result.stderr.decode('utf-8', errors='replace'))
    return result.stdout if binary else result.stdout.decode('utf-8').strip()


def fixed_commit(root, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit or ''): raise ValueError('须提供完整固定 Git commit。')
    if git(root, 'rev-parse', 'HEAD') != commit: raise ValueError('源码 commit 必须是当前 HEAD。')
    if git(root, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('源码工作区必须干净（clean）；先处理未提交改动。')
    return commit


def source_archive(root, output, commit=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if commit is not None:
        fixed_commit(root, commit)
        # git archive reads the fixed tree, never the working-tree bytes.
        content = git(root, 'archive', '--format=zip', commit, binary=True)
        with tempfile.TemporaryDirectory() as folder:
            snapshot = Path(folder)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for info in archive.infolist():
                    path = Path(info.filename)
                    if path.is_absolute() or '..' in path.parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('固定源码含不安全路径或符号链接。')
                    target = snapshot/path
                    if info.is_dir(): target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(archive.read(info))
            return source_archive(snapshot, output)
    report = audit_sources(root)
    if not report['ok']: raise ValueError('源码隐私检查失败：' + ','.join(report['findings']))
    if output.exists(): raise ValueError('输出已存在，拒绝覆盖。')
    output.parent.mkdir(parents=True, exist_ok=True)
    selected = list(source_files(root))
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for p in selected:
            info = zipfile.ZipInfo(p.relative_to(root).as_posix())
            info.create_system = 3
            mode = 0o100755 if p.suffix == '.sh' or p.name == 'gradlew' else 0o100644
            info.external_attr = mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, p.read_bytes())
    return {'ok': True, 'output': str(output), 'sha256': sha(output), 'files': len(selected)}


def bundle(platform, binaries, installer, output, profile='preview', root=ROOT, commit=None, personal_version_file=None):
    data = personal_version.build_data(root, personal_version_file) if profile == 'personal' else product_version.load(root)
    if profile not in data['profiles'] or platform not in data['profiles'][profile]['platforms']:
        raise ValueError('发布平台或安装档案无效。')
    selected = data['profiles'][profile]
    conf_path = Path(root)/'app/src-tauri/tauri.conf.json'
    if conf_path.exists():
        conf = json.loads(conf_path.read_text(encoding='utf-8'))
        if conf['identifier'] != selected['identifier'] or conf['version'] != data['nativeVersion']:
            raise ValueError('构建目录的身份/版本与包档案不符；先 prepare build profile。')
        mismatches=product_version.check(root)
        if mismatches: raise ValueError('构建生成版本尚未统一：'+','.join(mismatches))
    commit = commit or git(root, 'rev-parse', 'HEAD')
    if not re.fullmatch('[0-9a-f]{40}', commit): raise ValueError('构建须绑定完整 commit。')
    suffix = '.exe' if platform == 'windows-x64' else ''
    files = {f'bin/{name}{suffix}': Path(binaries) / (name + suffix)
             for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')} if platform != 'android-arm64' else {}
    files['install/' + Path(installer).name] = Path(installer)
    if not all(p.is_file() for p in files.values()): raise ValueError('安装程序或必需工具缺失。')
    expected_suffix = '.apk' if platform == 'android-arm64' else '.exe' if platform == 'windows-x64' else '.dmg'
    if Path(installer).suffix.lower() != expected_suffix: raise ValueError('安装程序格式与平台不符。')
    manifest = {'format': 1, 'version': data['version'], 'nativeVersion': data['nativeVersion'],
                'versionPolicy': 'unified', 'androidVersionCode': data['androidVersionCode'],
                'platform': platform, 'profile': profile, 'identifier': selected['identifier'], 'commit': commit,
                'files': {name: sha(p) for name, p in files.items()}}
    if profile == 'personal':
        manifest.update(distribution='personal-local', sourceVersion=data['sourceVersion'])
    output = Path(output)
    if output.exists(): raise ValueError('输出已存在，拒绝覆盖。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
        for name, path in files.items(): z.write(path, name)
    return {'ok': True, 'output': str(output), 'sha256': sha(output)}


def verify_bundle(path, data, profile, platform, commit):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len({name.casefold() for name in names}) or 'manifest.json' not in names:
            raise ValueError('组件清单重复或缺失。')
        if archive.getinfo('manifest.json').file_size > 128*1024: raise ValueError('组件清单过大。')
        manifest = json.loads(archive.read('manifest.json'))
        expected = dict(format=1, version=data['version'], nativeVersion=data['nativeVersion'],
                        versionPolicy='unified', androidVersionCode=data['androidVersionCode'],
                        profile=profile, platform=platform, commit=commit,
                        identifier=data['profiles'][profile]['identifier'])
        if data.get('distribution') == 'personal-local':
            expected.update(distribution='personal-local', sourceVersion=data['sourceVersion'])
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError('组件版本、提交、身份或平台不匹配：' + path.name)
        files = manifest.get('files')
        if not isinstance(files, dict) or set(names) != {'manifest.json', *files}:
            raise ValueError('组件文件集合与清单不一致。')
        suffix = '.exe' if platform == 'windows-x64' else ''
        helpers = {f'bin/{name}{suffix}' for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')} if platform != 'android-arm64' else set()
        installers = [name for name in files if name.startswith('install/')]
        extension = '.apk' if platform == 'android-arm64' else '.exe' if suffix else '.dmg'
        if len(installers) != 1 or not installers[0].endswith(extension) or set(files) != helpers | set(installers):
            raise ValueError('组件缺少必需工具或安装程序。')
        for name, digest in files.items():
            info = archive.getinfo(name)
            if '\\' in name or name.startswith('/') or '..' in Path(name).parts or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('组件包含不安全路径。')
            if not re.fullmatch('[0-9a-f]{64}', digest or '') or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('组件文件哈希不匹配。')


def checksums(directory):
    paths = sorted(p for p in Path(directory).iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    (Path(directory)/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in paths), encoding='utf-8')
    return {'ok': True, 'files': len(paths)}


def prepare_release(root, directory, commit, notes, distribution='public', personal_version_file=None):
    raw_notes = Path(notes).absolute()
    root, directory, notes = Path(root).resolve(), Path(directory).resolve(), Path(notes).resolve()
    fixed_commit(root, commit)
    if distribution not in {'public', 'personal-local'}: raise ValueError('未知发布范围。')
    data = personal_version.build_data(root, personal_version_file) if distribution == 'personal-local' else product_version.load(root)
    profiles = ('personal',) if distribution == 'personal-local' else ('preview',)
    if not notes.is_file() or not notes.read_text(encoding='utf-8-sig').strip():
        raise ValueError('须提供本人撰写的非空 notes 文件；工具不生成发布说明。')
    if notes.is_relative_to(root/'templates') or raw_notes.is_relative_to(root/'templates'):
        raise ValueError('模板目录不是本人发布说明；请另存并填写本机 notes 文件。')
    if re.search(r'请本人填写|待填写|\b(?:TBD|TODO)\b', notes.read_text(encoding='utf-8-sig'), flags=re.IGNORECASE):
        raise ValueError('发布说明仍含占位内容；请本人完成后再准备。')
    if not directory.is_dir(): raise ValueError('候选组件目录不存在。')
    source_name = f"Account-{'Personal' if distribution == 'personal-local' else 'Preview'}-{data['version']}-source.zip"
    component_names = {f"Account-{profile.title()}-{data['version']}-{platform}.zip"
                       for profile in profiles for platform in data['profiles'][profile]['platforms']}
    allowed = component_names | {source_name, 'release-manifest.json', 'SHA256SUMS'}
    # Notes are only used as the Release body; a mixed staging directory must never become an upload list.
    if notes.is_relative_to(directory) or any(p.name not in allowed or not p.is_file() or p.is_symlink() for p in directory.iterdir()):
        raise ValueError('发布目录含非允许附件；公开包不得混入个人包或 Markdown 说明附件。')
    manifest = dict(format=1, versionPolicy='unified', version=data['version'], tag=data['tag'], channel=data['channel'],
                    androidVersionCode=data['androidVersionCode'],
                    commit=commit, nativeVersion=data['nativeVersion'], ledgerSchema=1,
                    profiles={}, notesSha256=sha(notes), distribution=distribution)
    if distribution == 'personal-local':
        manifest.update(sourceVersion=data['sourceVersion'], androidVersionCode=data['androidVersionCode'])
    for profile in profiles:
        config = data['profiles'][profile]
        platforms = {}
        for platform in config['platforms']:
            name = f"Account-{profile.title()}-{data['version']}-{platform}.zip"
            path = directory/name
            if not path.is_file() or path.is_symlink(): raise ValueError('缺少候选平台组件：' + name)
            verify_bundle(path, data, profile, platform, commit)
            platforms[platform] = {'asset': name, 'sha256': sha(path)}
        manifest['profiles'][profile] = dict(identifier=config['identifier'], desktopConfig=config['desktopConfig'], platforms=platforms)
    # Compare an existing source asset against a fresh deterministic export; refuse stale archives.
    with tempfile.TemporaryDirectory() as folder:
        candidate = Path(folder)/source_name
        source_archive(root, candidate, commit=commit)
        try:
            import product_update
        except ModuleNotFoundError:
            from scripts import product_update
        source_contents=product_update.zip_contents(candidate, source=True)
        if not product_update.official_skill_source(source_contents):
            raise ValueError('新发布源码必须包含核心记账 Skill 及其完整引用文件。')
        manifest['snapshotSchema']=product_update.source_snapshot_schema(source_contents,required=True)
        source = directory/source_name
        if source.exists():
            if source.is_symlink() or sha(source) != sha(candidate): raise ValueError('源码包与固定 commit 不符。')
        else: source.write_bytes(candidate.read_bytes())
    manifest['source'] = {'asset': source_name, 'sha256': sha(source)}
    output = directory/'release-manifest.json'
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    checksums(directory)
    return {'ok': True, 'manifest': str(output), 'commit': commit, 'published': False,
            'distribution': distribution, 'assets': sorted(allowed)}


def public_assets(root, directory, commit, notes):
    """Verify the exact public allowlist immediately before uploading; never return personal or notes assets."""
    directory = Path(directory)
    result = prepare_release(root, directory, commit, notes)
    return [str(directory/name) for name in result['assets']]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('audit')
    src = commands.add_parser('source'); src.add_argument('--output', required=True, type=Path)
    src.add_argument('--commit', required=True)
    commands.add_parser('version-check')
    pack = commands.add_parser('bundle')
    for option in ('platform', 'binaries', 'installer', 'output'): pack.add_argument('--' + option, required=True)
    pack.add_argument('--profile', choices=['preview', 'personal'], default='preview')
    pack.add_argument('--personal-version-file', type=Path)
    sums = commands.add_parser('checksums'); sums.add_argument('--directory', type=Path, required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--directory', required=True, type=Path)
    prepare.add_argument('--commit', required=True)
    prepare.add_argument('--notes', required=True, type=Path)
    prepare.add_argument('--distribution', choices=['public','personal-local'], default='public')
    prepare.add_argument('--personal-version-file', type=Path)
    args = parser.parse_args()
    if args.command == 'audit': result = audit_sources(ROOT)
    elif args.command == 'source': result = source_archive(ROOT, args.output, commit=args.commit)
    elif args.command == 'version-check':
        mismatches = product_version.check(ROOT); result = {'ok': not mismatches, 'mismatches': mismatches}
    elif args.command == 'bundle': result = bundle(args.platform, args.binaries, args.installer, args.output, profile=args.profile, personal_version_file=args.personal_version_file)
    elif args.command == 'prepare': result = prepare_release(ROOT, args.directory, args.commit, args.notes, args.distribution, args.personal_version_file)
    else: result = checksums(args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
