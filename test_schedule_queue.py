import tempfile
from pathlib import Path
import unittest
import threading
from unittest.mock import patch

from core import Store, Engine
from schedule_queue import ScheduleQueue


class ScheduleQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'quota.sqlite3')
        self.engine = Engine(self.store, Path(self.tmp.name) / 'empty', clock=lambda: 1000)
        self.store.set('current', {'primary': {'resetsAt': 2000}})
        self.queue = ScheduleQueue(self.engine)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pause_rejects_edits_clear_and_previously_queued_edits(self):
        with self.engine.lock:
            self.queue.submit(24000)
        self.engine.set_paused(True)
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.queue.submit(25200)
        self.queue.drain()
        self.assertIsNone(self.store.get('schedule'))
        self.assertEqual(self.queue.status()['last']['status'], 'failed')
        self.assertIn('暂停', self.queue.status()['last']['error'])
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.queue.submit(clear=True)

    def test_detection_busy_queues_then_applies_once_without_error(self):
        with self.engine.lock:
            operation = self.queue.submit(24000)
            self.assertEqual(operation['status'], 'queued')
            self.assertNotIn('error', operation)
            self.assertIsNone(self.store.get('schedule'))
            self.assertEqual(self.queue.status()['pending'], 1)
        self.queue.drain(); self.queue.drain()
        self.assertEqual(self.store.get('schedule')['sendAt'], 6000)
        self.assertEqual(self.queue.status()['pending'], 0)
        self.assertEqual(len([r for r in self.store.recent('events') if r['kind'] == 'schedule']), 1)

    def test_read_pending_waits_then_uses_latest_quota_for_validation(self):
        self.engine.read_requested = 'check'
        self.queue.submit(24000)
        self.assertEqual(self.queue.status()['pending'], 1)
        self.store.set('current', {'primary': {'resetsAt': 7000}})
        self.engine.read_requested = None
        self.queue.drain()
        self.assertEqual(self.queue.status()['last']['status'], 'failed')
        self.assertIn('正常刷新', self.queue.status()['last']['error'])
        self.assertEqual(self.queue.status()['pending'], 0)
        self.assertIsNone(self.store.get('schedule'))

    def test_fifo_keeps_time_edits_and_clear_in_request_order(self):
        with self.engine.lock:
            self.queue.submit(24000)
            self.queue.submit(25200)
            self.queue.submit(clear=True)
        self.queue.drain()
        rows=[r for r in reversed(self.store.recent('events')) if r['kind']=='schedule']
        self.assertEqual([r['detail']['action'] for r in rows], ['create','update','clear'])
        self.assertIsNone(self.store.get('schedule'))

    def test_restart_retains_pending_edit(self):
        with self.engine.lock:
            self.queue.submit(24000)
        ScheduleQueue(self.engine).drain()
        self.assertEqual(self.store.get('schedule')['sendAt'], 6000)

    def test_invalid_format_is_rejected_without_queuing(self):
        for value in (None,True,1.1,24001,float('nan'),float('inf'),100):
            with self.subTest(value=value),self.assertRaises(ValueError): self.queue.submit(value)
        self.assertEqual(self.queue.status()['pending'], 0)

    def test_concurrent_drains_do_not_apply_twice(self):
        with self.engine.lock:
            self.queue.submit(24000)
        workers=[threading.Thread(target=self.queue.drain) for _ in range(6)]
        for t in workers:t.start()
        for t in workers:t.join()
        self.assertEqual(len([r for r in self.store.recent('events') if r['kind']=='schedule']),1)

    def test_submit_waits_for_other_drain_before_returning_idle_validation_error(self):
        entered = threading.Event()
        results = []
        original_drain = self.queue.drain

        def drain(**kwargs):
            entered.set()
            return original_drain(**kwargs)

        with patch.object(self.queue, 'drain', side_effect=drain):
            self.queue.lock.acquire()
            worker = threading.Thread(target=lambda: results.append(self.queue.submit(19500)))
            try:
                worker.start()
                self.assertTrue(entered.wait(2))
                self.assertEqual(results, [])
            finally:
                self.queue.lock.release()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]['status'], 'failed')
        self.assertIn('正常刷新', results[0]['error'])
