"""Persistent at-most-once scheduler; all times are server Unix seconds/UTC."""
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import threading
import time

from rpc import RPC

WINDOW = 18000


def normalize(raw):
    buckets = raw.get('rateLimitsByLimitId')
    bucket = buckets.get('codex') if buckets is not None else raw.get('rateLimits')
    if not bucket or bucket.get('limitId') != 'codex':
        raise ValueError('服务器未返回 codex 目标额度桶，停止触发')
    primary = bucket.get('primary')
    if not primary or primary.get('windowDurationMins') != 300:
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
        return '请求完成；重置时间未改变，不能证明启动了新周期'
    if old <= started and abs(new - started - WINDOW) <= 120:
        return '观察到新重置时间约等于发送时间 + 5 小时；支持首次请求锚定，但并发使用及固定窗口仍可能影响判断'
    return '观察到重置时间变化；尚不足以判定周期锚定规则'


class Store:
    def __init__(self, path):
        self.path = Path(path)
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
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

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
                       (now or time.time(), kind, json.dumps(detail, ensure_ascii=False)))
            # Roughly a month of normal polling; attempts and their evidence are retained.
            db.execute('DELETE FROM events WHERE id < (SELECT MAX(id)-60000 FROM events)')

    def guard_until(self, db=None):
        if db is None:
            with self.connect() as connection:
                return self.guard_until(connection)
        # Account/read and rateLimits/read can expose different identity fields across hosts.
        # This single-account utility deliberately shares the submission guard across identities.
        rows = db.execute("SELECT value FROM state WHERE key LIKE 'guard:%'").fetchall()
        return max((float(json.loads(row[0])) for row in rows), default=0)

    def claim(self, account, cycle, now, detail):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if now < self.guard_until(db):
                return None
            if db.execute('SELECT 1 FROM claims WHERE cycle=?', (cycle,)).fetchone():
                return None
            cursor = db.execute('INSERT INTO attempts(account,cycle,started,status,detail) VALUES (?,?,?,?,?)',
                                (account, cycle, now, 'reserved', json.dumps(detail, ensure_ascii=False)))
            attempt = cursor.lastrowid
            db.execute('INSERT INTO claims VALUES (?,?,?)', (account, cycle, attempt))
            # Persist BEFORE turn/start. A crash/timeout cannot cause immediate duplicate submission.
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)',
                       ('guard:' + account, json.dumps(now + WINDOW + 120)))
            return attempt

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


class Engine:
    def __init__(self, store, cwd, rpc_factory=RPC, clock=time.time):
        self.store, self.cwd, self.rpc_factory, self.clock = store, Path(cwd), rpc_factory, clock
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.rpc = None
        self.failure_count = 0
        self.next_poll = 0
        self.status = '正在启动'

    def close(self):
        if self.rpc:
            self.rpc.close()
            self.rpc = None

    def tick(self, manual=False):
        if not self.lock.acquire(blocking=False):
            return '检测或请求正在进行'
        try:
            now = self.clock()
            if not self.rpc:
                self.rpc = self.rpc_factory(str(self.cwd))
            account = self.rpc.account()
            raw = self.rpc.limits()
            identity = account.get('email') or raw.get('accountId')
            if not identity:
                raise ValueError('无法识别账户，停止触发以保护防重复状态')
            account_key = hashlib.sha256(identity.encode()).hexdigest()[:24]
            snap = normalize(raw)
            if snap['plan'] not in (None, 'plus'):
                raise ValueError('目标额度桶不是 Plus')
            previous = self.store.get('snapshot:' + account_key)
            self.store.set('snapshot:' + account_key, snap)
            self.store.set('current', {'checkedAt': now, 'account': account_key, **snap})
            self.store.log('check', snap, now)
            self.failure_count = 0
            self.next_poll = now + max(2, min(60, snap['primary']['resetsAt'] + 2 - now))
            if snap['primary']['resetsAt'] < now - 60:
                self.next_poll = now + 30
            if self.store.get('paused', False):
                self.status = '已暂停自动及手动发送（继续读取额度）'
                return self.status
            cycle, reason = candidate(snap, previous, now, manual)
            self.status = reason
            if not cycle:
                return reason
            guard = self.store.guard_until()
            if now < guard:
                self.status = '本周期已有发送记录，等待下一窗口；结果未知时保留防重复保护'
                return self.status
            # Preparation does not submit an inference turn. Check persisted claim first.
            with self.store.connect() as db:
                if db.execute('SELECT 1 FROM claims WHERE cycle=?', (cycle,)).fetchone():
                    self.status = '该重置边界已处理，等待服务器给出新窗口'
                    self.next_poll = now + 30
                    return self.status
            self.status = '正在准备最小请求'
            prepared = self.rpc.prepare(self.cwd)
            # Re-read immediately before reserving/sending to respect weekly limits and elapsed preparation.
            fresh_raw = self.rpc.limits()
            fresh_identity = self.rpc.account().get('email') or fresh_raw.get('accountId')
            if (fresh_identity != identity or (raw.get('accountId') and fresh_raw.get('accountId')
                    and raw['accountId'] != fresh_raw['accountId'])):
                raise ValueError('准备期间账户发生变化，停止发送')
            fresh = normalize(fresh_raw)
            now = self.clock()
            cycle, reason = candidate(fresh, snap, now, manual)
            if not cycle or self.store.get('paused', False):
                self.status = reason if cycle is None else '已暂停'
                return self.status
            detail = {'before': fresh, 'prepared': prepared, 'reason': reason, 'manual': manual}
            attempt = self.store.claim(account_key, cycle, now, detail)
            if attempt is None:
                self.status = '本周期已处理'
                return self.status
            self.status = '正在发送最小请求'
            result, after = None, None
            try:
                result = self.rpc.send_minimal(prepared)
                status = result['status']
                detail['result'] = result
            except Exception as exc:
                status = 'unknown'
                detail['error'] = str(exc)[:1200]
            try:
                after = normalize(self.rpc.limits())
                self.store.set('snapshot:' + account_key, after)
                self.store.set('current', {'checkedAt': self.clock(), 'account': account_key, **after})
            except Exception as exc:
                detail['afterError'] = str(exc)[:1200]
            detail['after'] = after
            detail['evidence'] = evidence(fresh, after, now, status == 'completed')
            detail['finishedAt'] = self.clock()
            self.store.finish(attempt, account_key, status, detail, after, self.clock())
            self.store.log('trigger', {'attempt': attempt, 'status': status, **detail})
            self.status = detail['evidence']
            self.next_poll = self.clock() + 30
            # Close ephemeral thread/process to avoid context and tool growth between cycles.
            self.close()
            return self.status
        except Exception as exc:
            self.failure_count += 1
            self.status = '连接/检测失败：' + str(exc)[:1200]
            self.store.log('error', {'message': self.status})
            self.next_poll = self.clock() + min(300, 5 * 2 ** min(self.failure_count, 6))
            self.close()
            return self.status
        finally:
            self.store.set('health', {'status': self.status, 'updatedAt': self.clock(), 'nextPoll': self.next_poll})
            self.lock.release()
