"""Persist manual timing edits until the current quota operation releases its lock."""
import json
import math
import threading
import uuid

from core import WINDOW


class ScheduleQueue:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store
        self.lock = threading.Lock()

    def submit(self, refresh_at=None, *, clear=False):
        if self.store.get('paused', False):
            raise ValueError('软件已暂停，请先恢复运行再修改时间')
        if not clear and (isinstance(refresh_at, bool) or not isinstance(refresh_at, (int, float))
                          or not math.isfinite(refresh_at) or refresh_at % 60):
            raise ValueError('请选择整分钟的刷新时间')
        if not clear and not self.engine.clock() < refresh_at - WINDOW <= self.engine.clock() + 366 * 86400:
            raise ValueError('请选择未来一年内的时间')
        operation = {'id': uuid.uuid4().hex, 'refreshAt': refresh_at, 'clear': clear,
                     'status': 'queued', 'time': self.engine.clock()}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT value FROM state WHERE key='scheduleQueue'").fetchone()
            pending = json.loads(row[0]) if row else []
            if len(pending) >= 50:
                raise ValueError('等待保存的时间设置过多，请稍后再试')
            pending.append(operation)
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('scheduleQueue', json.dumps(pending)))
            db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('scheduleQueueResult', json.dumps(operation)))
        # Wait only for another queue drain; Engine.set_schedule itself never waits
        # for detection. This preserves immediate validation errors when idle.
        self.drain(wait=True)
        result = self.store.get('scheduleQueueResult', {})
        return result if result.get('id') == operation['id'] else operation

    def drain(self, *, wait=False):
        if not self.lock.acquire(wait):
            return
        try:
            while True:
                pending = self.store.get('scheduleQueue', [])
                if not pending:
                    return
                operation = dict(pending[0])
                try:
                    if operation['clear']:
                        self.engine.set_schedule()
                    else:
                        self.engine.set_schedule(refresh_at=operation['refreshAt'])
                except RuntimeError as exc:
                    if str(exc).startswith(('正在检测或发送', '正在等待检测')):
                        return
                    operation.update(status='failed', error=str(exc))
                except (ValueError, OSError) as exc:
                    operation.update(status='failed', error=str(exc))
                else:
                    operation['status'] = 'applied'
                operation['finishedAt'] = self.engine.clock()
                with self.store.connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute("SELECT value FROM state WHERE key='scheduleQueue'").fetchone()
                    current = json.loads(row[0]) if row else []
                    current = [op for op in current if op['id'] != operation['id']]
                    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('scheduleQueue', json.dumps(current)))
                    db.execute('INSERT OR REPLACE INTO state VALUES (?,?)', ('scheduleQueueResult', json.dumps(operation)))
                if operation['status'] == 'failed':
                    self.store.log('schedule_queue_failed', operation, self.engine.clock())
        finally:
            self.lock.release()

    def status(self):
        return {'pending': len(self.store.get('scheduleQueue', [])),
                'last': self.store.get('scheduleQueueResult')}
