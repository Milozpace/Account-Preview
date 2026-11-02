"""Check reflection entry points in the final optimized APK, not source rules."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


REGISTRARS = (
    'com.google.mlkit.common.internal.CommonComponentRegistrar',
    'com.google.mlkit.vision.common.internal.VisionCommonRegistrar',
    'com.google.mlkit.vision.barcode.internal.BarcodeRegistrar',
)


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
    return {'ok': all(item['publicNoArgConstructor'] for item in results),
            'registrars': results}


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
