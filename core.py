"""Persistent cycle scheduler with bounded retries; all times are Unix seconds/UTC."""
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from contextlib import contextmanager, closing
import threading
import time
import uuid

from rpc import RPC

WINDOW = 18000
POLL_INTERVAL = 60
ATTEMPT_LIMIT = 5
RETRY_INTERVAL = 10
RETRY_BUDGET = 60
RECOVERY_INTERVAL = 20 * 60
BEIJING = timezone(timedelta(hours=8))


def transient_connection_error(error):
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    message = str(error).lower()
    return any(text in message for text in (
        'workspace routing discovery', 'error sending request', 'timed out',
        'connection reset', 'connection refused', 'connection closed',
        'temporarily unavailable', 'dns error', 'codex app server 已断开',
        'broken pipe', 'pipe has been ended', '管道已结束', '管道正在被关闭',
        'account/read 超时', 'account/ratelimits/read 超时'))


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError('服务器额度数据格式无效')
    buckets = raw.get('rateLimitsByLimitId')
    if buckets is not None and not isinstance(buckets, dict):
        raise ValueError('服务器额度桶格式无效')
    bucket = buckets.get('codex') if buckets is not None else raw.get('rateLimits')
    if not isinstance(bucket, dict) or bucket.get('limitId') != 'codex':
        raise ValueError('服务器未返回 codex 目标额度桶，停止触发')
    primary = bucket.get('primary')
    if not isinstance(primary, dict) or primary.get('windowDurationMins') != 300:
        raise ValueError('服务器未返回 300 分钟窗口，停止触发')
    def checked(window):
        if not isinstance(window, dict):
            raise ValueError('额度窗口数据缺失')
        for field in ('usedPercent', 'resetsAt', 'windowDurationMins'):
            value = window.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('额度窗口字段不完整')
        if not 0 <= window['usedPercent'] <= 100 or window['resetsAt'] <= 0 or window['windowDurationMins'] <= 0:
            raise ValueError('额度窗口字段无效')
        return {k: window[k] for k in ('usedPercent', 'resetsAt', 'windowDurationMins')}
    primary = checked(primary)
    secondary = checked(bucket['secondary']) if bucket.get('secondary') is not None else None
    blocked = (raw.get('ordinaryUsageAllowed') is False or primary['usedPercent'] >= 100
               or (secondary is not None and secondary['usedPercent'] >= 100)
               or bool(bucket.get('rateLimitReachedType')) or bool(bucket.get('spendControlReached')))
    # Explicit individual limits must also be respected, including unknown future shapes.
    if bucket.get('individualLimit'):
        blocked = True
    return {'primary': primary, 'secondary': secondary, 'blocked': blocked,
            'plan': bucket.get('planType'), 'bucket': 'codex'}


def candidate(snapshot, previous, now, manual=False):
    if snapshot['blocked']:
        return None, '额度受限，等待服务器恢复'
    end = snapshot['primary']['resetsAt']
    used = snapshot['primary']['usedPercent']
    if end <= now - 2:
        return f'after:{end}', '已越过服务器重置边界'
    if manual:
        return f'cycle:{end}', '手动触发'
    if used == 0:
        return f'cycle:{end}', '服务器显示空闲额度，尝试启动一次'
    if previous and abs(previous['primary']['resetsAt'] - end) > 5:
        return None, '服务器窗口已变化且已有使用，无需额外启动'
    return None, '等待服务器重置时间'


def evidence(before, after, started, completed):
    if not completed:
        return '请求未确认完成；周期起点未验证'
    if not after:
        return '请求完成；后续额度读取失败，周期起点未验证'
    old, new = before['primary']['resetsAt'], after['primary']['resetsAt']
    if abs(old - new) <= 5:
        return '发送小额请求，该请求并未导致下次额度刷新时间出现变化（相差≤5秒视为不变）'
    if old <= started and abs(new - started - WINDOW) <= 120:
        return '发送小额请求，ChatGPT将于5小时后刷新额度'
    return '观察到重置时间变化；尚不足以判定周期锚定规则'


class Store:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.log_dir = self.path.parent / "logs"
        self.trigger_dir = self.log_dir  # Compatibility alias; one active archive.
        self._archive_lock = threading.Lock()
        self._archive_error = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY, account TEXT NOT NULL, cycle TEXT NOT NULL,
                    started REAL NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS claims (
                    account TEXT NOT NULL, cycle TEXT NOT NULL, attempt INTEGER NOT NULL,
                    PRIMARY KEY(account, cycle));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, time REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quota_samples (
                    account TEXT NOT NULL, minute INTEGER NOT NULL, checked REAL NOT NULL,
                    primary_used REAL NOT NULL, secondary_used REAL,
                    PRIMARY KEY(account, minute));
            ''')
        self._archive_events()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
            if db.total_changes:
                self._archive_events()
        finally:
            db.close()

    def _archive_events(self):
        # All events, including routine quota reads and model-request evidence,
        # share the same monthly JSONL; SQLite remains the retry source.
        archives = ((self.log_dir, 'quota-starter', 'logArchiveId'),)
        with self._archive_lock:
            try:
                for folder, *_ in archives:
                    folder.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(self.path, timeout=15)) as archive_db:
                    today = datetime.fromtimestamp(self.clock(), BEIJING)
                    month_number = today.year * 12 + today.month - 1 - 2
                    year, month = divmod(month_number, 12)
                    cutoff = f'{year:04d}-{month + 1:02d}'
                    retention_month = today.strftime('%Y-%m')
                    retention = archive_db.execute("SELECT value FROM state WHERE key='logRetentionMonth'").fetchone()
                    if not retention or json.loads(retention[0]) != retention_month:
                        # Delete only our exact monthly names. An offline month
                        # boundary is caught at startup; failed deletions retry.
                        for folder, prefix, *_ in archives:
                            for path in folder.glob(prefix + '-*.jsonl'):
                                match = re.fullmatch(re.escape(prefix) + r'-(\d{4}-(?:0[1-9]|1[0-2]))\.jsonl', path.name)
                                if match and match[1] < cutoff:
                                    path.unlink()
                        cutoff_stamp = datetime(year, month + 1, 1, tzinfo=BEIJING).timestamp()
                        archive_db.execute('DELETE FROM quota_samples WHERE minute < ?', (cutoff_stamp,))
                        archive_db.execute("INSERT OR REPLACE INTO state VALUES ('logRetentionMonth',?)",
                                           (json.dumps(retention_month),))
                        archive_db.commit()
                    for folder, prefix, cursor_key in archives:
                        row = archive_db.execute('SELECT value FROM state WHERE key=?', (cursor_key,)).fetchone()
                        last_id = int(json.loads(row[0])) if row else 0
                        while True:
                            rows = archive_db.execute(
                                'SELECT id,time,kind,detail FROM events WHERE id>? ORDER BY id LIMIT 1000',
                                (last_id,)).fetchall()
                            if not rows:
                                break
                            grouped = {}
                            for event_id, stamp, kind, detail in rows:
                                date = datetime.fromtimestamp(stamp, BEIJING)
                                # Expired backlog must not recreate deleted files.
                                if date.strftime('%Y-%m') < cutoff:
                                    continue
                                detail = json.loads(detail)
                                record = {'id': event_id, 'time': date.isoformat(), 'kind': kind, 'detail': detail}
                                grouped.setdefault(date.strftime('%Y-%m'), []).append(json.dumps(record, ensure_ascii=False) + '\n')
                            for month, lines in grouped.items():
                                with (folder / f'{prefix}-{month}.jsonl').open('a', encoding='utf-8') as output:
                                    output.writelines(lines)
                                    output.flush()
                                    os.fsync(output.fileno())
                            last_id = rows[-1][0]
                            archive_db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (cursor_key, json.dumps(last_id)))
                            archive_db.commit()
                # Flush the database backlog/cursor first so merging legacy IDs
                # cannot be followed by re-appending those same database events.
                self._merge_legacy_triggers(cutoff)
                self._archive_error = None
            except (OSError, sqlite3.Error, ValueError) as exc:
                if str(exc) != self._archive_error:
                    print(f'日志归档暂时失败，将在下次写入时重试：{exc}', file=sys.stderr)
                    self._archive_error = str(exc)

    def _merge_legacy_triggers(self, cutoff):
        legacy = self.path.parent / 'triggers'
        if not legacy.is_dir():
            return
        for source in legacy.glob('quota-triggers-*.jsonl'):
            match = re.fullmatch(r'quota-triggers-(\d{4}-(?:0[1-9]|1[0-2]))\.jsonl', source.name)
            if not match:
                continue
            if match[1] < cutoff:
                source.unlink()  # Same existing calendar retention policy.
                continue
            original = source.read_bytes()
            target = self.log_dir / f'quota-starter-{match[1]}.jsonl'
            merged = {}
            # Legacy metadata is retained; existing general-log fields win only
            # after checking the authoritative event content matches.
            texts = [original.decode('utf-8-sig')]
            if target.exists():
                texts.append(target.read_text(encoding='utf-8-sig'))
            for content in texts:
                for line in content.splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if not isinstance(record, dict) or not all(k in record for k in ('time', 'kind', 'detail')):
                        raise ValueError('旧归档格式无效，保留原文件：' + source.name)
                    key = ('id', record['id']) if isinstance(record.get('id'), int) else ('raw', line)
                    previous = merged.get(key)
                    if previous and any(previous.get(k) != record.get(k) for k in ('time', 'kind', 'detail')):
                        raise ValueError('旧归档事件编号冲突，保留原文件：' + source.name)
                    merged[key] = {**(previous or {}), **record}
            backup_dir = self.path.parent / 'legacy-archive-backups'
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / (source.name + '.' + hashlib.sha256(original).hexdigest()[:12] + '.bak')
            if not backup.exists():
                with backup.open('xb') as output:
                    output.write(original)
                    output.flush()
                    os.fsync(output.fileno())
            if backup.read_bytes() != original:
                raise OSError('旧归档备份校验失败，保留原文件：' + source.name)
            temporary = target.with_suffix('.merge.tmp')
            with temporary.open('w', encoding='utf-8') as output:
                for record in sorted(merged.values(), key=lambda r: (str(r['time']),
                                     r['id'] if isinstance(r.get('id'), int) else 0)):
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(target)
            source.unlink()
        # Keep unrelated user files; only remove the directory when empty.
        if not any(legacy.iterdir()):
            legacy.rmdir()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def log(self, kind, detail, now=None):
        with self.connect() as db:
            db.execute('INSERT INTO events(time,kind,detail) VALUES (?,?,?)',
                       (time.time() if now is None else now, kind, json.dumps(detail, ensure_ascii=False)))
            # Roughly a month of normal polling; attempts and their evidence are retained.
            db.execute("DELETE FROM events WHERE id < (SELECT MAX(id)-60000 FROM events) "
                       "AND id <= COALESCE((SELECT CAST(value AS INTEGER) FROM state WHERE key='logArchiveId'),0)")

    def guard_until(self, db=None):
        if db is None:
            with self.connect() as connection:
                return self.guard_until(connection)
        # Account/read and rateLimits/read can expose different identity fields across hosts.
        # This single-account utility deliberately shares the submission guard across identities.
        rows = db.execute("SELECT value FROM state WHERE key LIKE 'guard:%'").fetchall()
        return max((float(json.loads(row[0])) for row in rows), default=0)

    def claim(self, account, cycle, now, detail, schedule_id=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if now < self.guard_until(db):
                return None
            if db.execute('SELECT 1 FROM claims WHERE cycle=?', (cycle,)).fetchone():
                return None
            schedule = None
            if schedule_id:
                row = db.execute("SELECT value FROM state WHERE key='schedule'").fetchone()
                schedule = json.loads(row[0]) if row else None
                if not schedule or schedule['id'] != schedule_id or schedule['status'] != 'pending':
                    return None
            cursor = db.execute('INSERT INTO attempts(account,cycle,started,status,detail) VALUES (?,?,?,?,?)',
                                (account, cycle, now, 'reserved', json.dumps(detail, ensure_ascii=False)))
            attempt = cursor.lastrowid
            db.execute('INSERT INTO claims VALUES (?,?,?)', (account, cycle, attempt))
            if schedule:
                schedule.update(status='submitted', attemptId=attempt, submittedAt=now)
                db.execute("UPDATE state SET value=? WHERE key='schedule'", (json.dumps(schedule),))
            # Persist BEFORE turn/start. A crash/timeout cannot cause immediate duplicate submission.
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                       ('guard:' + account, json.dumps(now + WINDOW + 120)))
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('sending', 'true'))
            return attempt

    def cancel_invalid_schedule(self, reset_at, now, *, used_percent=None, reconnected=False):
        # An idle window can report a rolling reset of now + five hours before
        # any request. That timestamp alone must not discard the user's plan.
        # Legacy snapshots without usage wait for a fresh read before cancelling.
        active_window = (not isinstance(used_percent, bool) and isinstance(used_percent, (int, float))
                         and math.isfinite(used_percent) and 0 < used_percent <= 100)
        if not active_window and not reconnected:
            return False
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key='schedule'").fetchone()
            schedule = json.loads(row[0]) if row else None
            if not schedule or schedule['status'] != 'pending' or schedule['sendAt'] > reset_at:
                return False
            # A fresh reconnect supersedes the idle-window exception only when
            # the planned send is strictly behind the newly reported reset.
            if not active_window and schedule['sendAt'] == reset_at:
                return False
            schedule.update(status='cancelled', cancelledAt=now, serverResetAt=reset_at,
                            reason='服务器刷新时间已达到或超过计划发送时间')
            db.execute("UPDATE state SET value=? WHERE key='schedule'", (json.dumps(schedule),))
            db.execute('INSERT INTO events(time,kind,detail) VALUES (?,?,?)',
                       (now, 'schedule_cancelled', json.dumps(schedule, ensure_ascii=False)))
            notice = {'id': schedule.get('id') or uuid.uuid4().hex, 'time': now,
                      'message': '定时计划已失效，已恢复自动'}
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                       ('scheduleNotice', json.dumps(notice, ensure_ascii=False)))
            return True

    def record_submission(self, attempt, account, detail, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE attempts SET detail=? WHERE id=?',
                       (json.dumps(detail, ensure_ascii=False), attempt))
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                       ('guard:' + account, json.dumps(now + WINDOW + 120)))

    def finish(self, attempt, account, status, detail, after, now):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE attempts SET status=?,detail=? WHERE id=?',
                       (status, json.dumps(detail, ensure_ascii=False), attempt))
            if after and after['primary']['resetsAt'] > now:
                end = after['primary']['resetsAt']
                db.execute('INSERT OR IGNORE INTO claims VALUES (?,?,?)', (account, f'cycle:{end}', attempt))
                # Only a completed request can shorten the uncertainty guard.
                if status == 'completed':
                    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('guard:' + account, json.dumps(end)))

    def recent(self, table, limit=20):
        if table not in ('events', 'attempts'):
            raise ValueError('Invalid table')
        with self.connect() as db:
            rows = db.execute(f'SELECT * FROM {table} ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows]

    def activity(self, limit=50):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE kind != 'check' ORDER BY id DESC LIMIT ?",
                              (limit,)).fetchall()
        events = [{**dict(row), 'detail': json.loads(row['detail'])} for row in rows]
        # Attempts remain the authoritative evidence even if a crash prevented
        # writing the final trigger event. Merge by attempt ID to avoid duplicates.
        recorded = {e['detail'].get('attempt') for e in events if e['kind'] == 'trigger'}
        for attempt in self.recent('attempts', limit):
            if attempt['id'] in recorded:
                continue
            detail = dict(attempt['detail'])
            if detail.get('before') and detail.get('after'):
                start = detail.get('submissions', [{}])[-1].get('startedAt', attempt['started'])
                detail['evidence'] = evidence(detail['before'], detail['after'], start, attempt['status'] == 'completed')
            detail.update(attempt=attempt['id'], status=attempt['status'])
            events.append({'id': 'attempt:' + str(attempt['id']), 'time': attempt['started'],
                           'kind': 'trigger', 'detail': detail})
        return sorted(events, key=lambda e: e['time'], reverse=True)[:limit]

    def sample(self, account, snapshot, now):
        with self.connect() as db:
            secondary = snapshot.get('secondary')
            db.execute('INSERT INTO quota_samples VALUES (?,?,?,?,?) '
                       'ON CONFLICT(account,minute) DO UPDATE SET checked=excluded.checked, '
                       'primary_used=excluded.primary_used, secondary_used=excluded.secondary_used '
                       'WHERE excluded.checked >= quota_samples.checked',
                       (account, math.floor(now / 60) * 60, now,
                        snapshot['primary']['usedPercent'], secondary['usedPercent'] if secondary else None))

    def quota_history(self, now=None):
        current = self.get('current')
        if not current or not current.get('account'):
            return []
        now = self.clock() if now is None else now
        with self.connect() as db:
            rows = db.execute('SELECT minute,checked,primary_used,secondary_used FROM quota_samples '
                              'WHERE account=? AND minute>=? AND minute<=? ORDER BY minute',
                              (current['account'], math.floor(now / 60) * 60 - 86400, now)).fetchall()
        return [{'minute': row['minute'], 'checkedAt': row['checked'],
                 'primaryRemaining': 100 - row['primary_used'],
                 'secondaryRemaining': None if row['secondary_used'] is None else 100 - row['secondary_used']}
                for row in rows]

    def trigger_history(self, limit=15):
        with self.connect() as db:
            rows = db.execute("SELECT started,status,detail,'minimal' AS requestKind FROM attempts "
                              "UNION ALL SELECT time,'completed',detail,'quota' FROM events WHERE kind='check' "
                              "ORDER BY 1 DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for row in rows:
            detail = json.loads(row['detail'])
            if row['requestKind'] == 'quota':
                detail = {'evidence': '已发送请求获取当前额度信息'}
            elif detail.get('before') and detail.get('after'):
                started = detail.get('submissions', [{}])[-1].get('startedAt', row['started'])
                detail['evidence'] = evidence(detail['before'], detail['after'], started, row['status'] == 'completed')
            result.append({**dict(row), 'detail': detail})
        return result


class Engine:
    def __init__(self, store, cwd, rpc_factory=RPC, clock=time.time, sleeper=time.sleep):
        self.store, self.cwd, self.rpc_factory, self.clock = store, Path(cwd), rpc_factory, clock
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.rpc = None
        self.next_poll = 0
        self.status = '正在启动'
        self.phase = 'connecting'
        self.linked = False
        self.read_requested = None
        self.pause_event = threading.Event()
        self.sleeper = sleeper
        if self.store.get('sending', False):
            self.stop_connection()
        retry = self.store.get('connection', {})
        last_attempt = self.store.recent('attempts', 1)
        # Older releases permanently stopped after read-only network failures.
        # Read-only transport failures from older versions can be retried safely.
        if (retry.get('stopped') and retry.get('stopReason') in (None, 'manual_required')
                and not self.store.get('sending', False)
                and not (last_attempt and last_attempt[0]['status'] in ('reserved', 'unknown')
                         and last_attempt[0]['started'] >= retry.get('startedAt', 0))
                and transient_connection_error(retry.get('error', ''))):
            self.stop_connection(recoverable=True)
            retry = self.connection()
        if retry.get('recoveryAt') and retry.get('recoveryInterval') != RECOVERY_INTERVAL:
            # Upgrade the old one-minute wait once, without postponing on every restart.
            retry['recoveryAt'] = max(self.clock(), retry['recoveryAt'] - 60 + RECOVERY_INTERVAL)
            retry['nextAt'] = retry['recoveryAt']
            retry['recoveryInterval'] = RECOVERY_INTERVAL
            self.store.set('connection', retry)
        if retry.get('stopped'):
            self.status = '已断开，请点击重新连接'
            self.phase = 'stopped'
        elif retry:
            self.next_poll = retry.get('nextAt', 0)
            self.status = ('连接暂时不可用，等待自动重连' if retry.get('recoveryAt')
                           else '连接/检测失败：等待重试')
            self.phase = 'retry_wait'
        current = self.store.get('current')
        reset_at = (current or {}).get('primary', {}).get('resetsAt')
        if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool) and math.isfinite(reset_at):
            # Also reconcile plans inherited from older versions while disconnected.
            self.store.cancel_invalid_schedule(
                reset_at, self.clock(), used_percent=current['primary'].get('usedPercent'))

    def connection(self):
        return self.store.get('connection', {})

    def request_check(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('检测或请求正在进行')
        try:
            if self.connection().get('stopped'):
                raise RuntimeError('已断开，请点击重新连接')
            if self.read_requested:
                raise RuntimeError('正在等待检测')
            if self.connection().get('failures'):
                raise RuntimeError('正在等待重试，请稍后再检测')
            self.read_requested = 'check'
            self.status = '正在检测额度'
            self.phase = 'checking'
            self.next_poll = 0
            self.store.log('control', {'action': 'check'}, self.clock())
        finally:
            self.lock.release()

    def set_paused(self, paused):
        self.store.set('paused', paused)
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()
        self.store.log('control', {'action': 'pause' if paused else 'resume'})

    def set_poll_while_paused(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('检测开关必须是布尔值')
        self.store.set('pollWhilePaused', enabled)
        self.store.log('control', {'action': 'poll_while_paused', 'enabled': enabled}, self.clock())
        if enabled and self.store.get('paused', False):
            self.next_poll = 0

    def polling_enabled(self):
        return (not self.store.get('paused', False)
                or self.store.get('pollWhilePaused', False))

    def reconnect(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('检测或请求正在进行')
        try:
            if self.read_requested == 'reconnect':
                return
            self.close()
            self.store.set('connection', {})
            self.store.set('sending', False)
            self.status = '正在重新连接'
            self.phase = 'connecting'
            self.linked = False
            self.read_requested = 'reconnect'
            self.next_poll = 0
            self.store.log('control', {'action': 'reconnect'})
        finally:
            self.lock.release()

    def stop_connection(self, *, recoverable=False):
        state = self.connection()
        state['stopped'] = not recoverable
        state['stopReason'] = 'network' if recoverable else 'manual_required'
        state.pop('recoveryAt', None)
        if recoverable:
            state['recoveryAt'] = max(self.clock(), state.get('recoveryStartedAt', self.clock()) + RECOVERY_INTERVAL)
            state['nextAt'] = state['recoveryAt']
            state['recoveryInterval'] = RECOVERY_INTERVAL
        self.store.set('connection', state)
        self.next_poll = state['recoveryAt'] if recoverable else 0
        self.status = ('连接暂时不可用，等待自动重连' if recoverable
                       else '已断开，请点击重新连接')
        self.read_requested = None
        self.phase = 'retry_wait' if recoverable else 'stopped'
        self.linked = False
        self.close()
        if recoverable:
            self.store.log('connection_recovery', {'nextAt': self.next_poll, 'error': state.get('error')})
        else:
            self.store.log('connection_stopped', {'message': self.status, 'error': state.get('error')})

    def failed_connection(self, error):
        now = self.clock()
        state = self.connection()
        state.setdefault('startedAt', now)
        state['failures'] = state.get('failures', 0) + 1
        state['nextAt'] = min(now + RETRY_INTERVAL, state['startedAt'] + RETRY_BUDGET)
        state['error'] = str(error)[:1200]
        state['recoverable'] = (transient_connection_error(error)
                                and not self.store.get('sending', False))
        state['readOnlyRecovery'] = state['recoverable']
        self.store.set('connection', state)
        self.linked = False
        diagnostics = getattr(error, 'rpc_diagnostics', {})
        # A live stdio child can still have broken HTTP/proxy state. Recreate it
        # before the next bounded retry so routing and transport are rebuilt.
        self.close()
        self.store.log('connection_failure', {'error': state['error'], 'failures': state['failures'],
                                             'nextAt': state['nextAt'], 'diagnostics': diagnostics}, now)
        if state.get('autoRecovery') or state['failures'] >= ATTEMPT_LIMIT or now >= state['startedAt'] + RETRY_BUDGET:
            self.stop_connection(recoverable=state['recoverable'])
        else:
            self.next_poll = state['nextAt']
            self.phase = 'retry_wait'
            self.status = f'连接/检测失败：{error}；等待第 {state["failures"] + 1}/{ATTEMPT_LIMIT} 次尝试'
        return self.status

    def connect(self):
        state = self.connection()
        if not state:
            state = {'startedAt': self.clock(), 'failures': 0, 'nextAt': self.clock()}
            self.store.set('connection', state)
        deadline = state['startedAt'] + RETRY_BUDGET
        if not self.rpc:
            self.rpc = (RPC(str(self.cwd), deadline=deadline) if self.rpc_factory is RPC
                        else self.rpc_factory(str(self.cwd)))
        self.rpc.deadline = deadline

    def close(self):
        if self.rpc:
            rpc = self.rpc
            self.rpc = None
            rpc.close()

    def set_schedule(self, send_at=None, *, refresh_at=None):
        if refresh_at is not None:
            if (isinstance(refresh_at, bool) or not isinstance(refresh_at, (int, float))
                    or not math.isfinite(refresh_at) or refresh_at % 60):
                raise ValueError('请选择整分钟的刷新时间')
            send_at = refresh_at - WINDOW
        if send_at is not None:
            if (isinstance(send_at, bool) or not isinstance(send_at, (int, float))
                    or not math.isfinite(send_at) or send_at <= self.clock()
                    or send_at > self.clock() + 366 * 86400):
                raise ValueError('请选择未来一年内的时间')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('正在检测或发送，请稍后再修改时间')
        try:
            if self.read_requested:
                raise RuntimeError('正在等待检测，请稍后再修改时间')
            if send_at is not None:
                current = self.store.get('current')
                if not current:
                    raise ValueError('请先检测 GPT 刷新时间')
                if send_at <= current['primary']['resetsAt']:
                    raise ValueError('只能选择 GPT 正常刷新时刻之后的时间')
            if refresh_at is not None and send_at < self.store.guard_until():
                raise ValueError('所选刷新时间早于防重复保护结束后的可选时间')
            previous = self.store.get('schedule')
            schedule = None if send_at is None else {
                'id': uuid.uuid4().hex, 'sendAt': send_at,
                'createdAt': self.clock(), 'status': 'pending'}
            self.store.set('schedule', schedule)
            action = 'clear' if schedule is None else ('update' if previous and previous.get('status') == 'pending' else 'create')
            self.store.log('schedule', {'action': action, 'previous': previous, **(schedule or {}),
                                       'refreshAt': send_at + WINDOW if send_at is not None else None}, self.clock())
            return schedule
        finally:
            self.lock.release()

    def timing(self):
        schedule = self.store.get('schedule')
        current = self.store.get('current')
        server = current['primary']['resetsAt'] if current else None
        if schedule and schedule['status'] == 'pending':
            send_at = schedule['sendAt']
            mode = 'manual'
        else:
            send_at = max(server + 2, self.store.guard_until()) if server else None
            mode = 'auto'
        return {'mode': mode, 'schedule': schedule, 'sendAt': send_at,
                'estimatedResetAt': send_at + WINDOW if send_at else None,
                'serverResetAt': server,
                'minSendAt': math.floor(max(server, self.clock())) + 1 if server else None,
                'minRefreshAt': math.ceil((max(math.floor(max(server, self.clock())) + 1,
                                               self.store.guard_until()) + WINDOW) / 60) * 60 if server else None,
                'maxRefreshAt': math.floor((self.clock() + 366 * 86400 + WINDOW) / 60) * 60}

    def select_candidate(self, snapshot, previous, now, manual):
        if self.store.cancel_invalid_schedule(snapshot['primary']['resetsAt'], now,
                                              used_percent=snapshot['primary']['usedPercent']):
            return None, '计划已取消：服务器刷新时间已达到或超过计划发送时间', None
        schedule = self.store.get('schedule')
        if not manual and schedule and schedule['status'] == 'pending':
            if now < schedule['sendAt']:
                return None, '等待手动设定时间', None
            cycle, reason = candidate(snapshot, previous, now, manual=True)
            return cycle, ('按手动时间发送' if cycle else reason), schedule['id']
        cycle, reason = candidate(snapshot, previous, now, manual)
        return cycle, reason, None

    def _phase(self, phase, status):
        self.phase, self.status = phase, status

    def _ok(self, status):
        previous = self.connection()
        if previous.get('failures') or previous.get('autoRecovery'):
            self.store.log('connection_recovered', {'message': '已恢复连接，账户和额度核验成功',
                                                   'failures': previous.get('failures', 0)}, self.clock())
        self.store.set('connection', {})
        # All successful operations resume the same periodic cadence. Only explicit
        # checks/reconnects and failed-operation retries may wake it sooner.
        self.next_poll = self.clock() + POLL_INTERVAL
        if self.rpc:
            self.rpc.deadline = None
        self._phase('idle', status)
        return status

    def _read(self, expected=None):
        account = self.rpc.account()
        raw = self.rpc.limits()
        identity = account.get('email') or raw.get('accountId')
        if not identity:
            raise ValueError('无法识别账户，停止触发以保护防重复状态')
        if expected and (identity != expected[0] or
                         (expected[1] and raw.get('accountId') != expected[1])):
            raise ValueError('账户发生变化，停止本轮发送')
        snapshot = normalize(raw)
        if snapshot['plan'] not in (None, 'plus'):
            raise ValueError('目标额度桶不是 Plus')
        self.linked = True
        return (identity, raw.get('accountId')), snapshot

    def _publish(self, identity, snapshot, *, reconnected=False):
        account_key = hashlib.sha256(identity[0].encode()).hexdigest()[:24]
        now = self.clock()
        previous = self.store.get('snapshot:' + account_key)
        weekly_key = 'weekly-reset:' + account_key
        weekly = self.store.get(weekly_key)
        # Cache only at the full-quota boundary, including the first full read
        # after upgrade. Routine polling must not move the displayed estimate.
        if snapshot['primary']['usedPercent'] == 0 and (
                weekly is None or previous is None or previous['primary']['usedPercent'] != 0):
            weekly = {'resetsAt': (snapshot.get('secondary') or {}).get('resetsAt'),
                      'checkedAt': now}
            self.store.set(weekly_key, weekly)
        self.store.set('snapshot:' + account_key, snapshot)
        self.store.set('current', {'checkedAt': now, 'account': account_key, **snapshot,
                                  'weeklyResetAt': (weekly or {}).get('resetsAt')})
        self.store.sample(account_key, snapshot, now)
        cancelled = self.store.cancel_invalid_schedule(
            snapshot['primary']['resetsAt'], now, used_percent=snapshot['primary']['usedPercent'],
            reconnected=reconnected)
        return account_key, cancelled

    def _wait_retry(self):
        state = self.connection()
        delay = max(0, state['nextAt'] - self.clock())
        # Pause wakes a real retry wait immediately; tests inject an advancing clock.
        if self.sleeper is time.sleep:
            self.pause_event.wait(delay)
        else:
            self.sleeper(delay)
        if self.clock() >= state['startedAt'] + RETRY_BUDGET:
            self.stop_connection()
            return False
        return not self.store.get('paused', False)

    def _send_reserved(self, attempt, account_key, identity, before, prepared, detail):
        status = 'unknown'
        first = True
        while True:
            if self.store.get('paused', False):
                detail['stoppedReason'] = '已暂停，停止本轮发送重试'
                break
            try:
                if not first:
                    self._phase('connecting', '正在重新连接')
                    self.connect()
                    _, fresh = self._read(identity)
                    self._publish(identity, fresh)
                    if fresh['blocked']:
                        detail['stoppedReason'] = '额度受限，停止本轮发送重试'
                        break
                    if (fresh['primary']['resetsAt'] > before['primary']['resetsAt'] + 5
                            and fresh['primary']['usedPercent'] > 0):
                        detail['stoppedReason'] = '新窗口已有使用，停止本轮发送重试'
                        break
                    self._phase('preparing', '已连接，正在准备小额请求')
                    prepared = self.rpc.prepare(self.cwd)
                    _, fresh = self._read(identity)
                    self._publish(identity, fresh)
                    if fresh['blocked'] or self.store.get('paused', False):
                        detail['stoppedReason'] = '额度受限或已暂停，停止本轮发送重试'
                        break
                    if (fresh['primary']['resetsAt'] > before['primary']['resetsAt'] + 5
                            and fresh['primary']['usedPercent'] > 0):
                        detail['stoppedReason'] = '新窗口已有使用，停止本轮发送重试'
                        break
                if self.clock() >= self.connection()['startedAt'] + RETRY_BUDGET:
                    self.stop_connection()
                    break
                self._phase('sending', '已连接，正在发送小额请求')
                detail.setdefault('submissions', []).append({'startedAt': self.clock(), 'prepared': prepared})
                self.store.record_submission(attempt, account_key, detail, self.clock())
                result = self.rpc.send_minimal(prepared)
                status = result['status']
                detail['result'] = result
                if status == 'completed':
                    break
                raise RuntimeError('请求未完成：' + status)
            except ValueError as exc:
                detail['stoppedReason'] = str(exc)
                self.stop_connection()
                break
            except Exception as exc:
                status = 'unknown'
                detail.setdefault('retries', []).append({'time': self.clock(), 'error': str(exc)[:1200]})
                self.store.finish(attempt, account_key, status, detail, None, self.clock())
                self.failed_connection(exc)
                if self.connection().get('stopped'):
                    break
                if not self._wait_retry():
                    if not self.connection().get('stopped'):
                        detail['stoppedReason'] = '已暂停，停止本轮发送重试'
                    break
            first = False
        return status

    def tick(self, manual=False, *, check_only=False):
        if not self.lock.acquire(blocking=False):
            return '检测或请求正在进行'
        attempt = None
        detail = {}
        try:
            if not self.polling_enabled() and not (manual or check_only or self.read_requested):
                self.status = '已暂停，自动额度检测已关闭'
                return self.status
            now = self.clock()
            retry = self.connection()
            if retry.get('stopped'):
                return self.status
            if retry.get('recoveryAt'):
                if now < retry['recoveryAt']:
                    return self.status
                retry = {'startedAt': now, 'nextAt': now, 'failures': 0,
                         'autoRecovery': True, 'recoveryStartedAt': now, 'recoverable': True}
                self.store.set('connection', retry)
                self.read_requested = 'reconnect'
            elif retry.get('autoRecovery'):
                # A restart during a probe must not start another probe in that slot.
                self.stop_connection(recoverable=True)
                return self.status
            if retry and now >= retry['startedAt'] + RETRY_BUDGET:
                self.stop_connection(recoverable=retry.get('recoverable', False))
                return self.status
            if retry and now < retry['nextAt']:
                return self.status
            requested = self.read_requested or ('reconnect' if retry.get('readOnlyRecovery') else None)
            self._phase('connecting' if not self.rpc else 'checking',
                        '正在连接' if not self.rpc else '正在检测额度')
            started = self.clock()
            self.connect()
            identity, snap = self._read()
            account_key = hashlib.sha256(identity[0].encode()).hexdigest()[:24]
            previous = self.store.get('snapshot:' + account_key)
            reconnected = requested == 'reconnect' or bool(retry.get('readOnlyRecovery'))
            _, cancelled = self._publish(identity, snap, reconnected=reconnected)
            now = self.clock()
            self.store.log('check', {**snap, 'durationSeconds': round(now - started, 3)}, now)
            if requested or check_only:
                self.read_requested = None
                message = '已连接，额度已更新' if requested == 'reconnect' else '当前额度已更新'
                if self.store.get('paused', False):
                    message = '已暂停；' + message
                if cancelled:
                    message += '；计划已取消：服务器刷新时间已达到或超过计划发送时间'
                return self._ok(message)
            if cancelled:
                return self._ok('计划已取消：服务器刷新时间已达到或超过计划发送时间')
            if self.store.get('paused', False):
                return self._ok('已暂停发送，继续检测额度')
            cycle, reason, schedule_id = self.select_candidate(snap, previous, now, manual)
            if not cycle:
                return self._ok(reason)
            if now < self.store.guard_until():
                return self._ok('本周期已有发送记录，等待下一窗口；结果未知时保留防重复保护')
            with self.store.connect() as db:
                claimed = db.execute('SELECT 1 FROM claims WHERE cycle=?', (cycle,)).fetchone()
            if claimed:
                return self._ok('该重置边界已处理，等待服务器给出新窗口')
            self._phase('preparing', '已连接，正在准备小额请求')
            prepared = self.rpc.prepare(self.cwd)
            _, fresh = self._read(identity)
            _, cancelled = self._publish(identity, fresh)
            now = self.clock()
            if cancelled:
                return self._ok('计划已取消：服务器刷新时间已达到或超过计划发送时间')
            cycle, reason, schedule_id = self.select_candidate(fresh, snap, now, manual)
            if not cycle or self.store.get('paused', False):
                return self._ok(reason if cycle is None else '已暂停')
            detail = {'before': fresh, 'prepared': prepared, 'reason': reason,
                      'manual': manual, 'scheduleId': schedule_id}
            attempt = self.store.claim(account_key, cycle, now, detail, schedule_id)
            if attempt is None:
                return self._ok('本周期已处理')
            status = self._send_reserved(attempt, account_key, identity, fresh, prepared, detail)
            if status == 'completed':
                # Persist the acknowledged send before the separate read-only check.
                # A failed verification must not be mistaken for an unknown submission.
                self.store.finish(attempt, account_key, status, detail, None, self.clock())
                self.store.set('sending', False)
            after = None
            if self.rpc and not self.connection().get('stopped'):
                try:
                    self._phase('verifying', '已连接，正在核验请求结果')
                    _, after = self._read(identity)
                    self._publish(identity, after)
                except Exception as exc:
                    detail['afterError'] = str(exc)[:1200]
                    self.failed_connection(exc)
            detail['after'] = after
            last_start = detail.get('submissions', [{}])[-1].get('startedAt', now)
            detail['evidence'] = evidence(fresh, after, last_start, status == 'completed')
            detail['finishedAt'] = self.clock()
            self.store.finish(attempt, account_key, status, detail, after, self.clock())
            self.store.log('trigger', {'attempt': attempt, 'status': status, **detail})
            self.store.set('sending', False)
            attempt = None
            if self.connection().get('stopped') or detail.get('afterError'):
                return self.status
            return self._ok(detail.get('stoppedReason') or detail['evidence'])
        except Exception as exc:
            if attempt is not None:
                detail['error'] = str(exc)[:1200]
                self.store.finish(attempt, account_key, 'unknown', detail, None, self.clock())
                self.store.set('sending', False)
                self.stop_connection()
            else:
                self.failed_connection(exc)
            if attempt is not None:
                self.store.log('error', {'message': self.status, 'error': str(exc)[:1200]})
            return self.status
        finally:
            try:
                self.store.set('health', {'status': self.status, 'updatedAt': self.clock(), 'nextPoll': self.next_poll})
            finally:
                self.lock.release()
