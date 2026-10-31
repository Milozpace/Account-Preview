"""Allowlisted source export, privacy audit and reproducible trial bundle assembly."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.0-preview.1'
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


def source_archive(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
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


def bundle(platform, binaries, installer, output):
    if platform not in {'windows-x64', 'macos-arm64', 'macos-x64'}: raise ValueError('发布平台无效。')
    suffix = '.exe' if platform == 'windows-x64' else ''
    files = {f'bin/{name}{suffix}': Path(binaries) / (name + suffix)
             for name in ('ledger_cli', 'bill_inbox_cli', 'sync_diagnose')}
    files['install/' + Path(installer).name] = Path(installer)
    if not all(p.is_file() for p in files.values()): raise ValueError('安装程序或必需工具缺失。')
    manifest = {'format': 1, 'version': VERSION, 'platform': platform,
                'files': {name: sha(p) for name, p in files.items()}}
    output = Path(output)
    if output.exists(): raise ValueError('输出已存在，拒绝覆盖。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
        for name, path in files.items(): z.write(path, name)
    return {'ok': True, 'output': str(output), 'sha256': sha(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('audit')
    src = commands.add_parser('source'); src.add_argument('--output', required=True, type=Path)
    pack = commands.add_parser('bundle')
    for option in ('platform', 'binaries', 'installer', 'output'): pack.add_argument('--' + option, required=True)
    checksums = commands.add_parser('checksums'); checksums.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'audit': result = audit_sources(ROOT)
    elif args.command == 'source': result = source_archive(ROOT, args.output)
    elif args.command == 'bundle': result = bundle(args.platform, args.binaries, args.installer, args.output)
    else:
        paths = sorted(p for p in args.directory.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
        (args.directory / 'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in paths), encoding='utf-8')
        result = {'ok': True, 'files': len(paths)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
