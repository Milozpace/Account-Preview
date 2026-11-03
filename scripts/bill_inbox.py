"""Receive encrypted phone uploads through the fixed native companion.

This module never writes the ledger. A receipt is downstream of local evidence,
and a failed receipt remains retryable independently of a successful import.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile

import account
import ledger_store

MAX_BYTES = 4 * 1024 * 1024
MAX_RECEIVE_PAGES = 750
MAX_XLSX_TOTAL = 32 * 1024 * 1024
MAX_XLSX_MEMBER = 16 * 1024 * 1024
MAX_XLSX_ENTRIES = 4096


class Inbox:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runtime = self.root / '.runtime' / 'bill-inbox'
        self.manifest = self.runtime / 'received.json'
        self.archive = self.root / 'sra'

    def lock(self):
        # Same OS lock convention as ledger writes; released even after a crash.
        return ledger_store._writer_lock(self.runtime / 'transaction')

    def _run(self, *args):
        from runtime_tools import helper_path
        executable = helper_path(self.root, 'bill_inbox_cli')
        if not executable.is_file():
            raise RuntimeError('账单收取组件尚未安装')
        try:
            result = subprocess.run([str(executable), *map(str, args)], shell=False,
                                    capture_output=True, encoding='utf-8', errors='strict',
                                    timeout=120 if args and args[0] == 'receive' else 90,
                                    cwd=self.root)
            if result.returncode or len(result.stdout) > 1024 * 1024:
                raise ValueError()
            payload = json.loads(result.stdout.lstrip('\ufeff'))
            if not isinstance(payload, dict):
                raise ValueError()
            return payload
        except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
            # Native stdout/stderr can contain transport details; never echo them.
            raise RuntimeError('账单收取或回执失败，请检查本机同步配置后重试') from None

    def _load(self):
        if not self.manifest.exists():
            return {'version': 1, 'files': [], 'receiveCursor': None}
        try:
            payload = json.loads(self.manifest.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload['files'], list):
                raise ValueError()
            cursor = payload.setdefault('receiveCursor', None)
            if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r'[0-9a-f]{64}', cursor)):
                raise ValueError()
            seen = set()
            for entry in payload['files']:
                if (not isinstance(entry['id'], str) or entry['id'] in seen
                        or entry['id'] != entry['sha256']
                        or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                        or entry['state'] not in ('received', 'recorded')
                        or not isinstance(entry['receiptPending'], bool)
                        or not Path(entry['sourcePath']).resolve().is_relative_to(self.archive.resolve())):
                    raise ValueError()
                seen.add(entry['id'])
            return payload
        except (ValueError, KeyError, TypeError):
            raise ValueError('本机账单收取清单无效，请保留文件并检查') from None

    def _save(self, payload):
        self.runtime.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.received-', suffix='.json', dir=self.runtime)
        temporary = Path(name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.manifest)
        finally:
            temporary.unlink(missing_ok=True)

    def status(self):
        payload = self._load()
        return {'ok': True, **payload, 'pending': sum(e['state'] != 'recorded' or e['receiptPending'] for e in payload['files'])}

    @staticmethod
    def _safe_name(value):
        if (not isinstance(value, str) or not value or value != value.strip() or '..' in value
                or len(value.encode('utf-8')) > 200
                or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '<>:"/\\|?*' for c in value)
                or value.endswith((' ', '.')) or value in ('.', '..')
                or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1, 10)), *('LPT'+str(i) for i in range(1, 10))}
                or Path(value).suffix.lower() not in ('.csv', '.xlsx')):
            raise ValueError('账单文件名或格式无效')
        return value

    def _validated(self, item, directory):
        name = self._safe_name(item['fileName'])
        identity, digest, size = item['id'], item['sha256'], item['byteCount']
        path = Path(item['path'])
        if (not isinstance(identity, str) or identity != digest
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or type(size) is not int or not 0 < size <= MAX_BYTES
                or not path.is_absolute() or path.is_symlink()
                or not path.resolve().is_relative_to(directory.resolve())
                or not path.is_file() or path.stat().st_size != size or account.sha(path) != digest):
            raise ValueError('账单路径、大小或哈希核验失败')
        # Native temporary path may have no source extension. Parse using original
        # name in a private validation folder before exposing anything under sra.
        validation = Path(tempfile.mkdtemp(prefix='validation-', dir=directory))
        candidate = validation / name
        with candidate.open('xb') as stream:
            stream.write(path.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        try:
            rows, _ = self._parse_source(candidate)
            if not rows:
                raise ValueError()
            if account.sha(candidate) != digest:
                raise ValueError()
        except Exception:
            raise ValueError('账单内容未通过现有解析器核验') from None
        return identity, digest, name, candidate

    @staticmethod
    def _parse_source(path):
        if not 0 < path.stat().st_size <= MAX_BYTES:
            raise ValueError('原账单大小超限')
        if path.suffix.lower() == '.xlsx':
            # Inspect central-directory metadata only. Never extract archives;
            # bound openpyxl's potential XML expansion before invoking it.
            with zipfile.ZipFile(path) as archive:
                entries = archive.infolist()
                if (len(entries) > MAX_XLSX_ENTRIES
                        or sum(entry.file_size for entry in entries) > MAX_XLSX_TOTAL
                        or any(entry.file_size > MAX_XLSX_MEMBER or entry.flag_bits & 1 for entry in entries)):
                    raise ValueError('XLSX展开大小、成员数量或加密格式不受支持')
        return account.parse_sources([path])

    def _archive(self, candidate, name, digest):
        if self.archive.exists():
            for existing in self.archive.iterdir():
                if existing.is_file() and not existing.is_symlink() and existing.suffix.lower() in ('.csv', '.xlsx') and account.sha(existing) == digest:
                    return existing.resolve()
        self.archive.mkdir(parents=True, exist_ok=True)
        target = self.archive / name
        if target.exists():
            target = self.archive / (Path(name).stem + '-' + digest + Path(name).suffix)
        try:
            # Hard link publishes complete, flushed bytes without overwrite.
            os.link(candidate, target)
        except FileExistsError:
            if not target.is_file() or target.is_symlink() or account.sha(target) != digest:
                raise ValueError('原账单归档路径冲突') from None
        if account.sha(target) != digest:
            raise ValueError('原账单归档哈希不一致')
        return target.resolve()

    def _ack(self, entry):
        try:
            if self._run('acknowledge', entry['id'], entry['state']).get('ok') is not True:
                raise ValueError()
            entry['receiptPending'] = False
            return True
        except Exception:
            entry['receiptPending'] = True
            return False

    def fetch(self, apply=False):
        if not apply:
            return {'ok': True, 'dry_run': True, 'action': 'inbox-fetch', 'requiresApply': True}
        with self.lock():
            payload = self._load()
            downloads = self.runtime / 'downloads'
            downloads.mkdir(parents=True, exist_ok=True)
            accepted = rejected = receipt_errors = 0
            cursor = payload['receiveCursor']
            result = dict(ledgerChanged=False, pages=0, scanComplete=False)
            retained = []
            for _ in range(MAX_RECEIVE_PAGES):
                directory = Path(tempfile.mkdtemp(prefix='receive-', dir=downloads)).resolve()
                try:
                    response = self._run('receive', str(directory), *([cursor] if cursor else []))
                    if not isinstance(response.get('files'), list):
                        raise ValueError()
                    # The shipped companion always returns nextCursor. Missing
                    # cursor remains compatible with pre-paging test adapters.
                    paged = 'nextCursor' in response
                    next_cursor = response.get('nextCursor')
                    if paged and (len(response['files']) > 1 or (next_cursor is not None and (
                            not isinstance(next_cursor, str) or not re.fullmatch(r'[0-9a-f]{64}', next_cursor)
                            or (cursor is not None and next_cursor <= cursor)))
                            or (response['files'] and (next_cursor is None or response['files'][0].get('id') != next_cursor))):
                        raise ValueError()
                except Exception:
                    retained.append(str(directory))
                    result['error'] = '本轮收取未完成；已归档文件保留，下次从已保存游标继续，请重试'
                    break
                page_rejected = 0
                for item in response['files']:
                    try:
                        identity, digest, name, candidate = self._validated(item, directory)
                        previous = next((e for e in payload['files'] if e['id'] == identity), None)
                        if previous and previous['sha256'] != digest:
                            raise ValueError()
                        source = self._archive(candidate, name, digest)
                    except Exception:
                        rejected += 1
                        page_rejected += 1
                        continue
                    entry = previous or dict(id=identity, sha256=digest, fileName=name, sourcePath=str(source),
                                             state='received', batchId=None, receiptPending=True)
                    entry['sourcePath'] = str(source)
                    if not previous:
                        payload['files'].append(entry)
                    # Persist provenance before any cloud acknowledgement.
                    self._save(payload)
                    if entry['state'] == 'received' and not self._ack(entry):
                        receipt_errors += 1
                    accepted += 1
                # Persist only completed pages. A crash before this save repeats
                # the page safely through hash identity and archive deduplication.
                cursor = next_cursor
                payload['receiveCursor'] = cursor
                self._save(payload)
                result['pages'] += 1
                if page_rejected:
                    retained.append(str(directory))
                else:
                    # Delete only this invocation's verified staging directory.
                    target = directory.resolve()
                    if target.parent != downloads.resolve() or not target.name.startswith('receive-'):
                        raise ValueError('拒绝清理收取临时目录以外的路径')
                    try:
                        shutil.rmtree(target)
                    except OSError:
                        result['cleanupPending'] = True
                        retained.append(str(target))
                if not paged or next_cursor is None:
                    result['scanComplete'] = True
                    break
            if not result['scanComplete'] and 'error' not in result:
                result['error'] = '本轮已达收取分页上限；已保存进度，请再次执行继续'
            if retained:
                result.update(retainedDownloads=retained[-1], retainedDownloadDirectories=retained)
            result.update(ok=not (rejected or receipt_errors or result.get('error')), received=accepted,
                          rejected=rejected, receiptErrors=receipt_errors, partial=not result['scanComplete'],
                          receiveCursor=cursor)
            return result

    def _proof(self, entry, database, rows, allow_noop):
        return self._proof_result(entry, database, rows, allow_noop).get('batchId')

    def _proof_result(self, entry, database, rows, allow_noop):
        """Return sanitized evidence, including why no recorded receipt is possible."""
        source = Path(entry['sourcePath'])
        try:
            if not source.is_file():
                return dict(reason='source-missing')
            if source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction()):
                return dict(reason='source-redirected')
            if account.sha(source) != entry['sha256']:
                return dict(reason='source-changed')
            parsed, _ = self._parse_source(source)
            new, _, conflicts = account.prepare_records(rows, parsed)
            if account.sha(source) != entry['sha256']:
                return dict(reason='source-changed')
            if not parsed:
                return dict(reason='source-empty')
            if conflicts:
                return dict(reason='source-conflict', conflictCount=len(conflicts))
            if new:
                return dict(reason='unimported-records', newRecords=len(new))
            uri = Path(database).resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                for batch, sources in connection.execute("SELECT batch_id,source_files_json FROM import_batches WHERE kind='import' ORDER BY revision DESC"):
                    if any(s.get('sha256', '').lower() == entry['sha256'] for s in json.loads(sources or '[]')):
                        return dict(batchId=batch)
            if entry['sha256'] in allow_noop or (entry['state'] == 'recorded' and entry.get('batchId') == 'existing-ledger'):
                return dict(batchId='existing-ledger')
        except Exception:
            return dict(reason='proof-error')
        return dict(reason='missing-provenance')

    def receipts(self, database, apply=False, allow_noop=(), locked=False):
        if not apply:
            return {'ok': True, 'dry_run': True, 'action': 'inbox-receipts', 'requiresApply': True}
        if not locked:
            with self.lock():
                return self.receipts(database, apply=True, allow_noop=allow_noop, locked=True)
        payload = self._load()
        if not payload['files']:
            return dict(ok=True, recorded=0, receiptErrors=0, ledgerVerified=True, pending=[], blocked=[])
        if not ledger_store.verify(database).get('ok'):
            return dict(ok=False, recorded=0, receiptErrors=0, ledgerVerified=False,
                        error='账本核验失败，未发送已记账回执')
        rows = ledger_store.read_state(database)['rows']
        recorded = errors = 0
        pending, blocked = [], []
        for entry in payload['files']:
            proof = self._proof_result(entry, database, rows, allow_noop)
            batch = proof.get('batchId')
            if not batch:
                detail = dict(uploadId=entry['id'], **proof)
                (pending if proof.get('reason') == 'unimported-records' else blocked).append(detail)
            if batch and (entry['state'] != 'recorded' or entry['receiptPending']):
                entry.update(state='recorded', batchId=batch, receiptPending=True)
                self._save(payload)
                if not self._ack(entry):
                    errors += 1
                recorded += 1
            elif entry['state'] == 'received' and entry['receiptPending'] and proof.get('reason') == 'unimported-records':
                # Recheck local archive before retrying even the received receipt.
                source = Path(entry['sourcePath'])
                if not source.is_file() or source.is_symlink() or account.sha(source) != entry['sha256'] or not self._ack(entry):
                    errors += 1
            self._save(payload)
        return dict(ok=not (errors or blocked), recorded=recorded, receiptErrors=errors,
                    ledgerVerified=True, pending=pending, blocked=blocked)
