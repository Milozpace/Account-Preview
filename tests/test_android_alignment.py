import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from scripts.verify_android_apk import verify_native_alignment


def elf(align):
    header = bytearray(64 + 56)
    header[:16] = b'\x7fELF' + bytes([2, 1, 1]) + bytes(9)
    struct.pack_into('<Q', header, 32, 64)
    struct.pack_into('<HH', header, 54, 56, 1)
    struct.pack_into('<I', header, 64, 1)
    struct.pack_into('<Q', header, 64 + 48, align)
    return header


class AndroidAlignmentTests(unittest.TestCase):
    def test_checks_every_load_segment_and_requires_native_library(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'app.apk'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('lib/arm64-v8a/good.so',elf(16384))
                archive.writestr('lib/arm64-v8a/bad.so',elf(4096))
            self.assertFalse(verify_native_alignment(path)['ok'])
            with zipfile.ZipFile(path,'w') as archive: archive.writestr('lib/arm64-v8a/good.so',elf(16384))
            self.assertTrue(verify_native_alignment(path)['ok'])
            with zipfile.ZipFile(path,'w'): pass
            self.assertFalse(verify_native_alignment(path)['ok'])
