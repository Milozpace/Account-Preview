"""Check reflection entry points in the final optimized APK, not source rules."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import struct
import zipfile


REGISTRARS = (
    'com.google.mlkit.common.internal.CommonComponentRegistrar',
    'com.google.mlkit.vision.common.internal.VisionCommonRegistrar',
    'com.google.mlkit.vision.barcode.internal.BarcodeRegistrar',
)


def verify_native_alignment(apk: Path):
    """Check all arm64 ELF PT_LOAD segments, not just APK ZIP alignment."""
    results = []
    with zipfile.ZipFile(apk) as archive:
        names = [name for name in archive.namelist() if name.startswith('lib/arm64-v8a/') and name.endswith('.so')]
        for name in names:
            info = archive.getinfo(name)
            if info.file_size > 100 * 1024 * 1024: raise ValueError('native library too large')
            data = archive.read(name)
            if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01': raise ValueError('invalid arm64 ELF')
            offset = struct.unpack_from('<Q', data, 32)[0]
            entry_size, count = struct.unpack_from('<HH', data, 54)
            if entry_size < 56 or not count or offset + entry_size * count > len(data): raise ValueError('invalid ELF program headers')
            segments = [offset + i * entry_size for i in range(count)
                        if struct.unpack_from('<I', data, offset + i * entry_size)[0] == 1]
            aligned = all(struct.unpack_from('<Q', data, entry + 48)[0] >= 16384
                          and struct.unpack_from('<Q', data, entry + 48)[0] % 16384 == 0
                          and struct.unpack_from('<Q', data, entry + 8)[0] % 16384 == struct.unpack_from('<Q', data, entry + 16)[0] % 16384
                          for entry in segments)
            zip_aligned = True
            if info.compress_type == zipfile.ZIP_STORED:
                with apk.open('rb') as stream:
                    stream.seek(info.header_offset)
                    local = stream.read(30)
                if len(local) != 30 or local[:4] != b'PK\x03\x04': raise ValueError('invalid ZIP local header')
                name_length, extra_length = struct.unpack_from('<HH', local, 26)
                zip_aligned = (info.header_offset + 30 + name_length + extra_length) % 16384 == 0
            results.append({'library': name, 'ok': bool(segments) and aligned and zip_aligned})
    return {'ok': bool(results) and all(item['ok'] for item in results), 'libraries': results}


def find_apkanalyzer():
    executable = 'apkanalyzer.bat' if os.name == 'nt' else 'apkanalyzer'
    found = shutil.which(executable)
    if found:
        return found
    for variable in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if sdk := os.environ.get(variable):
            candidate = Path(sdk)/'cmdline-tools'/'latest'/'bin'/executable
            if candidate.is_file():
                return str(candidate)
    raise ValueError('Android SDK apkanalyzer is required (PATH or ANDROID_HOME).')


def verify(apk: Path, apkanalyzer: str):
    if not apk.is_file():
        raise ValueError('APK does not exist.')
    results = []
    for registrar in REGISTRARS:
        output = subprocess.run(
            [apkanalyzer, 'dex', 'code', '--class', registrar, str(apk.resolve())],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            check=True, timeout=90,
        ).stdout
        # ComponentDiscovery uses Class.getDeclaredConstructor().newInstance().
        # A kept class name alone is insufficient under R8 full mode.
        constructor = bool(re.search(
            r'(?m)^\.method (?=[^\n]*\bpublic\b)[^\n]*<init>\(\)V\s*$', output,
        ))
        results.append({'class': registrar, 'publicNoArgConstructor': constructor})
    alignment = verify_native_alignment(apk)
    return {'ok': all(item['publicNoArgConstructor'] for item in results) and alignment['ok'],
            'registrars': results, 'nativeAlignment': alignment}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', type=Path)
    parser.add_argument('--apkanalyzer', help='Override Android SDK apkanalyzer path.')
    args = parser.parse_args()
    try:
        result = verify(args.apk, args.apkanalyzer or find_apkanalyzer())
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        result = {'ok': False, 'error': str(error)}
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
