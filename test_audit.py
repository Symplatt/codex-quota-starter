"""Regression coverage for quota checks, bounded recovery and background status."""
import io
import json
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from core import Engine, normalize
from rpc import RPC
import test_quota as fixtures
from test_quota import FakeRPC, raw
from windows_settings import Autostart


class AuditTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def schedule(self, engine):
        self.store.set('current', {'primary': {'resetsAt': 100}})
        engine.set_schedule(200)

    def test_periodic_check_cancels_before_due_and_logs_once(self):
        fake = FakeRPC([raw(300)])
        e = self.engine(fake, 110)
        self.schedule(e)
        e.tick()
        self.assertEqual(self.store.get('schedule')['status'], 'cancelled')
        self.assertEqual(e.timing()['mode'], 'auto')
        self.assertEqual(e.timing()['sendAt'], 302)
        self.engine(fake, 120).tick()
        self.assertEqual(fake.sent, 0)
        self.assertEqual(len([x for x in self.store.recent('events') if x['kind'] == 'schedule_cancelled']), 1)

    def test_equal_reset_cancels_during_paused_read_only_check(self):
        fake = FakeRPC([raw(200)])
        e = self.engine(fake, 110)
        self.schedule(e)
        e.set_paused(True)
        e.tick(check_only=True)
        self.assertEqual(self.store.get('schedule')['status'], 'cancelled')
        self.assertEqual(fake.sent, 0)

    def test_reconnect_checks_schedule_without_sending(self):
        fake = FakeRPC([raw(300, 0)])
        e = self.engine(fake, 110)
        self.schedule(e)
        e.stop_connection()
        e.reconnect()
        e.tick()
        self.assertEqual(self.store.get('schedule')['status'], 'cancelled')
        self.assertEqual(fake.reads, 1)
        self.assertEqual(fake.sent, 0)
        self.assertTrue(e.linked)
        self.assertEqual(e.phase, 'idle')
        self.assertEqual(e.next_poll, 115)

    def test_final_preflight_cancels_when_reset_moves(self):
        fake = FakeRPC([raw(100), raw(300)])
        e = self.engine(fake, 110)
        self.schedule(e)
        later = self.engine(fake, 200)
        later.tick()
        self.assertEqual(fake.sent, 0)
        self.assertEqual(self.store.recent('attempts'), [])
        self.assertEqual(self.store.get('schedule')['status'], 'cancelled')

    def test_valid_future_schedule_survives_periodic_check(self):
        fake = FakeRPC([raw(199)])
        e = self.engine(fake, 110)
        self.schedule(e)
        e.tick()
        self.assertEqual(self.store.get('schedule')['status'], 'pending')
        self.assertEqual(fake.sent, 0)

    def test_upgrade_cancels_known_invalid_plan_without_reconnecting(self):
        e = self.engine(FakeRPC([raw(300)]), 110)
        self.schedule(e)
        self.store.set('current', {'primary': {'resetsAt': 300}})
        e.stop_connection()
        fake = FakeRPC([raw(300)])
        restarted = self.engine(fake, 120)
        self.assertEqual(self.store.get('schedule')['status'], 'cancelled')
        restarted.tick()
        self.assertEqual(fake.reads, 0)
        self.assertTrue(restarted.connection()['stopped'])

    def test_queued_check_is_read_only_even_with_empty_quota(self):
        fake = FakeRPC([raw(300, 0)])
        e = self.engine(fake, 110)
        e.request_check()
        with self.assertRaises(RuntimeError):
            e.request_check()
        e.tick()
        self.assertEqual(fake.sent, 0)
        self.assertEqual(fake.reads, 1)
        self.assertIsNone(e.read_requested)
        self.assertEqual(e.next_poll, 140)

    def test_actual_read_completion_time_and_connection_phase(self):
        fake = FakeRPC([raw(100)])
        e = self.engine(fake, 110)
        original = fake.limits
        def slow_read():
            e.sleeper(2)
            return original()
        fake.limits = slow_read
        def send(prepared):
            self.assertTrue(e.linked)
            self.assertTrue(e.lock.locked())
            self.assertEqual(e.phase, 'sending')
            self.assertEqual(self.store.get('current')['checkedAt'], 114)
            return {'status': 'completed'}
        fake.send_minimal = send
        e.tick()
        self.assertEqual(self.store.get('current')['checkedAt'], 116)
        self.assertEqual(e.phase, 'idle')

    def test_retry_guard_tracks_last_submission(self):
        fake = FakeRPC([raw(100)], send_error=True)
        e = self.engine(fake, 110)
        e.tick()
        row = self.store.recent('attempts')[0]
        self.assertEqual([s['startedAt'] for s in row['detail']['submissions']], [110, 120, 130, 140, 150])
        self.assertEqual(self.store.guard_until(), 150 + 18000 + 120)
        self.assertTrue(e.connection()['stopped'])
        e.tick(check_only=True)
        self.assertEqual(fake.sent, 5)

    def test_retry_final_read_stops_if_another_request_started_window(self):
        fake = FakeRPC([raw(100), raw(100), raw(100), raw(19000, 1)], send_error=True)
        e = self.engine(fake, 110)
        e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertIn('新窗口已有使用', e.status)

    def test_pause_wakes_retry_and_prevents_second_send(self):
        fake = FakeRPC([raw(100)], send_error=True)
        e = self.engine(fake, 110)
        def pause_in_wait(seconds):
            e.set_paused(True)
        e.sleeper = pause_in_wait
        e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertIn('已暂停', e.status)
        self.assertFalse(self.store.get('sending'))

    def test_health_write_error_does_not_leak_lock(self):
        e = self.engine(FakeRPC([raw()]), 110)
        original = self.store.set
        def fail_health(key, value):
            if key == 'health':
                raise OSError('disk error')
            return original(key, value)
        with patch.object(self.store, 'set', side_effect=fail_health):
            with self.assertRaises(OSError):
                e.tick(check_only=True)
        self.assertFalse(e.lock.locked())

    def test_invalid_server_shapes_have_explicit_errors(self):
        for value in [None, [], {'rateLimitsByLimitId': []}, {'rateLimits': []}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize(value)


class RPCTests(unittest.TestCase):
    def rpc(self):
        rpc = object.__new__(RPC)
        rpc.lock = threading.Lock()
        rpc.pending = {}
        rpc.sequence = 0
        rpc.deadline = None
        rpc.events = queue.Queue()
        rpc.collect_events = False
        rpc.disconnected = threading.Event()
        return rpc

    def test_reader_eof_fails_calls_immediately_and_ignores_idle_events(self):
        rpc = self.rpc()
        rpc.proc = Mock(stdout=io.StringIO('null\n[]\ninvalid\n' + json.dumps({'method': 'item/completed'}) + '\n'))
        rpc._read()
        self.assertTrue(rpc.events.empty())
        with self.assertRaises(ConnectionError):
            rpc.call('account/read')

    def test_pending_call_wakes_on_eof(self):
        rpc = self.rpc()
        rpc.proc = Mock(stdout=io.StringIO(''))
        rpc._send = lambda message: rpc._read()
        with self.assertRaisesRegex(RuntimeError, 'account/read'):
            rpc.call('account/read')
        self.assertEqual(rpc.pending, {})

    def test_call_deadline_prevents_write(self):
        rpc = self.rpc()
        rpc.deadline = time.time() - 1
        rpc._send = Mock()
        with self.assertRaises(TimeoutError):
            rpc.call('turn/start')
        rpc._send.assert_not_called()

    def test_repeated_model_page_is_rejected(self):
        rpc = self.rpc()
        rpc.call = Mock(return_value={'data': [], 'nextCursor': 'repeat'})
        with self.assertRaisesRegex(RuntimeError, '分页重复'):
            rpc.choose_model()
        self.assertEqual(rpc.call.call_count, 2)

    def test_completion_already_queued_during_turn_start_is_received(self):
        rpc = self.rpc()
        def call(method, params):
            self.assertTrue(rpc.collect_events)
            rpc.events.put({'method': 'turn/completed', 'params': {'threadId': 't', 'turn': {'id': 'r', 'status': 'completed'}}})
            return {'turn': {'id': 'r'}}
        rpc.call = call
        result = rpc.send_minimal({'threadId': 't', 'effort': 'low'})
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(rpc.collect_events)


class AutostartTests(unittest.TestCase):
    def test_status_does_not_wait_for_powershell_or_spawn_duplicates(self):
        startup = Autostart(Path('.'))
        started, release, done = threading.Event(), threading.Event(), threading.Event()
        def execute(action):
            started.set()
            release.wait(2)
            done.set()
            return {'available': True, 'enabled': True}
        with patch('windows_settings.os.name', 'nt'), patch.object(startup, '_execute', side_effect=execute) as run:
            try:
                self.assertIsNone(startup.apply()['enabled'])
                self.assertTrue(started.wait(1))
                for _ in range(10):
                    self.assertIsNone(startup.apply()['enabled'])
                self.assertEqual(run.call_count, 1)
            finally:
                release.set()
                self.assertTrue(done.wait(1))

    def test_status_failure_does_not_replace_enabled_with_false(self):
        startup = Autostart(Path('.'))
        with patch('windows_settings.subprocess.run', side_effect=TimeoutError('busy')):
            result = startup._execute('status')
        self.assertIsNone(result['enabled'])
        self.assertFalse(result['available'])
