"""Regression coverage for quota checks, bounded recovery and background status."""
import io
import json
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import Mock, patch

from core import Engine, normalize, RECOVERY_INTERVAL
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

    def test_monthly_logs_keep_full_events_and_resume_without_duplicates(self):
        from datetime import datetime, timezone
        from core import Store
        before = datetime(2026, 10, 31, 15, 59, tzinfo=timezone.utc).timestamp()
        after = before + 60
        self.store.log('control', {'action': 'pause'}, before)
        self.store.log('check', {'full': '检测明细'}, after)
        Store(self.store.path)
        october = self.store.log_dir / 'quota-starter-2026-10.jsonl'
        november = self.store.log_dir / 'quota-starter-2026-11.jsonl'
        self.assertEqual(len(october.read_text(encoding='utf-8').splitlines()), 1)
        rows = [json.loads(line) for line in november.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['detail'], {'full': '检测明细'})
        self.assertTrue(rows[0]['time'].startswith('2026-11-01T00:00:'))

    def test_monthly_logs_retry_after_directory_failure(self):
        from core import Store
        original = self.store.log_dir
        blocked = original.parent / 'blocked'
        blocked.write_text('occupied', encoding='utf-8')
        self.store.log_dir = blocked
        self.store.log('control', {'action': 'pause'}, 100)
        self.assertEqual(len(self.store.recent('events')), 1)
        self.store.log_dir = original
        Store(self.store.path)
        rows = (original / 'quota-starter-1970-01.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])['kind'], 'control')

    def test_schedule_actions_survive_routine_checks_in_activity(self):
        e = self.engine(FakeRPC([raw(149)]), 100)
        self.store.set('current', {'primary': {'resetsAt': 149}})
        e.set_schedule(200)
        e.set_schedule(210)
        e.set_schedule()
        e.request_check()
        for _ in range(35):
            self.store.log('check', {})
        rows = self.store.activity()
        self.assertEqual([r['detail']['action'] for r in rows], ['check', 'clear', 'update', 'create'])
        self.assertEqual(rows[2]['detail']['previous']['sendAt'], 200)
        self.assertEqual(rows[2]['detail']['refreshAt'], 18210)
        self.assertEqual(rows[1]['detail']['previous']['sendAt'], 210)

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
        self.assertEqual(e.next_poll, 170)

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
        self.assertEqual(e.next_poll, 170)

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


class PollIntervalTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def test_all_idle_states_wait_sixty_seconds(self):
        cases = [('active', raw(300), False), ('near_reset', raw(111), False),
                 ('at_reset', raw(110, 100), False), ('stale_reset', raw(1, 100), False),
                 ('weekly_limit', raw(100, 10, 100), False), ('paused', raw(100), True)]
        for name, value, paused in cases:
            with self.subTest(name=name):
                self.store.set('paused', paused)
                fake = FakeRPC([value])
                e = self.engine(fake, 110)
                e.tick()
                self.assertEqual(e.next_poll, 170)
                self.assertEqual(fake.sent, 0)

    def test_schedule_changes_preserve_poll_and_due_plan_waits_for_it(self):
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 100)
        e.tick()
        e.set_schedule(150)
        self.assertEqual(e.next_poll, 160)
        e.sleeper(50)
        # Match the background scheduler's wake-up condition.
        if e.clock() >= e.next_poll:
            e.tick()
        self.assertEqual(fake.reads, 1)
        self.assertEqual(fake.sent, 0)
        e.sleeper(10)
        if e.clock() >= e.next_poll:
            e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertEqual(e.next_poll, 220)
        e.set_schedule()
        self.assertEqual(e.next_poll, 220)

    def test_claimed_boundary_does_not_shorten_poll(self):
        e = self.engine(FakeRPC([raw(100)]), 110)
        self.store.claim('prior', 'after:100', 1, {})
        self.store.set('guard:prior', 0)
        self.store.set('sending', False)
        e.tick()
        self.assertIn('边界已处理', e.status)
        self.assertEqual(e.next_poll, 170)

    def test_successful_send_uses_completion_time_for_next_poll(self):
        fake = FakeRPC([raw(100), raw(100), raw(18120)])
        e = self.engine(fake, 110)
        original = fake.send_minimal
        def delayed(prepared):
            e.sleeper(10)
            return original(prepared)
        fake.send_minimal = delayed
        e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertEqual(e.next_poll, 180)
        self.assertEqual(e.next_poll - self.store.get('current')['checkedAt'], 60)

    def test_schedule_edit_does_not_bypass_failure_retry(self):
        self.store.set('current', {'primary': {'resetsAt': 149}})
        e = self.engine(FakeRPC([TimeoutError('offline')]), 110)
        e.tick()
        e.set_schedule(200)
        self.assertEqual(e.next_poll, 120)
        self.assertEqual(e.connection()['failures'], 1)


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def failed_round(self, engine):
        for _ in range(5):
            engine.tick()
            if not engine.connection().get('recoveryAt'):
                engine.sleeper(10)

    def test_three_single_probes_per_hour_and_restart_preserves_wait(self):
        fake = FakeRPC([TimeoutError('offline')])
        e = self.engine(fake, 100)
        self.failed_round(e)
        self.assertEqual(fake.reads, 5)
        start = e.clock()
        for index in range(1, 4):
            due = start + index * 1200
            restarted = self.engine(fake, due - 1)
            restarted.tick()
            self.assertEqual(fake.reads, 4 + index)
            restarted.sleeper(1)
            restarted.tick()
            self.assertEqual(fake.reads, 5 + index)
            self.assertEqual(restarted.next_poll, due + 1200)
            restarted.sleeper(10)
            restarted.tick()
            self.assertEqual(fake.reads, 5 + index)
        self.assertEqual(fake.sent, 0)

    def test_restart_during_probe_does_not_repeat_it(self):
        self.store.set('connection', {'startedAt': 100, 'nextAt': 100, 'failures': 0,
                                     'autoRecovery': True, 'recoveryStartedAt': 100})
        fake = FakeRPC([raw()])
        e = self.engine(fake, 101)
        e.tick()
        self.assertEqual(fake.reads, 0)
        self.assertEqual(e.next_poll, 1300)

    def test_old_one_minute_wait_migrates_once(self):
        self.store.set('connection', {'startedAt': 100, 'nextAt': 200, 'failures': 5,
                                     'stopped': False, 'recoveryAt': 200})
        fake = FakeRPC([raw()])
        e = self.engine(fake, 150)
        self.assertEqual(e.next_poll, 1340)
        self.assertEqual(self.engine(fake, 160).next_poll, 1340)

    def test_manual_reconnect_bypasses_twenty_minute_wait(self):
        fake = FakeRPC([TimeoutError('offline')] * 5 + [raw(20000, 0)])
        e = self.engine(fake, 100)
        self.failed_round(e)
        e.reconnect()
        e.tick()
        self.assertTrue(e.linked)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 200)

    def test_upstream_timeout_rebuilds_live_transport_before_read_only_retry(self):
        fake = FakeRPC([TimeoutError('routing timed out'), raw()])
        fake.is_alive = lambda: True
        fake.close = Mock()
        e = self.engine(fake, 100)
        e.tick()
        fake.close.assert_called_once()
        self.assertIsNone(e.rpc)
        e.sleeper(10)
        e.tick()
        self.assertTrue(e.linked)
        fake.close.assert_called_once()
        self.assertEqual(fake.sent, 0)

    def test_completed_send_verification_timeout_remains_recoverable(self):
        fake = FakeRPC([raw(100), raw(100), TimeoutError('offline')])
        e = self.engine(fake, 110)
        e.tick()
        self.assertEqual(self.store.recent('attempts')[0]['status'], 'completed')
        self.assertFalse(self.store.get('sending'))
        self.assertTrue(e.connection()['recoverable'])
        for _ in range(4):
            e.sleeper(10)
            e.tick()
        self.assertFalse(e.connection()['stopped'])
        self.assertTrue(e.connection()['recoveryAt'])
        self.assertEqual(fake.sent, 1)

    def test_outage_past_due_recovers_then_sends_plan_once(self):
        fake = FakeRPC([TimeoutError('offline')] * 5 + [raw(149)])
        e = self.engine(fake, 100)
        self.store.set('current', {'primary': {'resetsAt': 149}})
        plan = e.set_schedule(150)
        self.failed_round(e)
        self.assertEqual(e.next_poll, 1340)
        self.assertEqual(self.store.get('schedule')['id'], plan['id'])
        e.sleeper(RECOVERY_INTERVAL)
        e.tick()
        self.assertTrue(e.linked)
        self.assertEqual(fake.sent, 0)  # Recovery first verifies the account/quota.
        self.assertEqual(e.next_poll, 1400)
        e.sleeper(60)
        e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertEqual(self.store.get('schedule')['status'], 'submitted')
        self.engine(fake, 1460).tick()
        self.assertEqual(fake.sent, 1)

    def test_recovery_respects_pause_and_changed_server_window(self):
        for paused, end in [(True, 149), (False, 3000)]:
            with self.subTest(paused=paused):
                self.store.set('connection', {})
                self.store.set('current', {'primary': {'resetsAt': 149}})
                fake = FakeRPC([TimeoutError('offline')] * 5 + [raw(end)])
                e = self.engine(fake, 100)
                e.set_schedule(150)
                e.set_paused(paused)
                self.failed_round(e)
                e.sleeper(RECOVERY_INTERVAL)
                e.tick()
                e.sleeper(60)
                e.tick()
                self.assertEqual(fake.sent, 0)
                self.assertEqual(self.store.get('paused'), paused)
                self.assertEqual(self.store.get('schedule')['status'], 'pending' if paused else 'cancelled')

    def test_upgrade_recovers_legacy_read_timeout_and_keeps_plan(self):
        # An unrelated old unresolved attempt must not prevent read-only recovery.
        self.store.claim('old', 'cycle:1', 0, {})
        self.store.set('sending', False)
        self.store.set('connection', {'startedAt': 1, 'nextAt': 61, 'failures': 3,
                                     'stopped': True, 'error': 'account/read: workspace routing discovery timed out'})
        self.store.set('schedule', {'id': 'existing', 'sendAt': 150, 'status': 'pending'})
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 200)
        self.assertFalse(e.connection()['stopped'])
        self.assertEqual(e.next_poll, 1400)
        restarted = self.engine(fake, 220)
        self.assertEqual(restarted.next_poll, 1400)
        restarted.tick()
        self.assertEqual(fake.reads, 0)
        restarted.sleeper(1180)
        restarted.tick()
        self.assertTrue(restarted.linked)
        self.assertEqual(self.store.get('schedule')['id'], 'existing')
        self.assertEqual(fake.sent, 0)

    def test_non_network_errors_still_stop(self):
        fake = FakeRPC([ValueError('服务器额度窗口无效')])
        e = self.engine(fake, 100)
        self.failed_round(e)
        self.assertTrue(e.connection()['stopped'])
        e.sleeper(600)
        e.tick()
        self.assertEqual(fake.reads, 5)

    def test_unknown_send_does_not_migrate_to_auto_recovery(self):
        self.store.set('sending', True)
        self.store.set('connection', {'stopped': True, 'error': 'account/read: workspace routing discovery failed'})
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 200)
        e.tick()
        self.assertTrue(e.connection()['stopped'])
        self.assertEqual(fake.reads, 0)
        self.assertEqual(self.engine(fake, 900).phase, 'stopped')

    def test_legacy_unknown_submission_blocks_migration_even_after_sending_cleared(self):
        attempt = self.store.claim('acct', 'cycle:149', 100, {})
        self.store.finish(attempt, 'acct', 'unknown', {}, None, 120)
        self.store.set('sending', False)
        self.store.set('connection', {'stopped': True, 'error': 'account/read: workspace routing discovery failed'})
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 200)
        e.tick()
        self.assertTrue(e.connection()['stopped'])
        self.assertEqual(fake.reads, 0)


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

    def test_windows_pipe_write_failure_is_a_transport_error(self):
        for error in (OSError(22, 'Invalid argument'), ValueError('closed file')):
            with self.subTest(error=error):
                rpc = self.rpc()
                rpc.write_lock = threading.Lock()
                rpc.proc = Mock()
                rpc.proc.stdin.write.side_effect = error
                with self.assertRaises(ConnectionError):
                    rpc.call('account/read')
                self.assertTrue(rpc.disconnected.is_set())
                self.assertFalse(rpc.is_alive())
                self.assertEqual(rpc.pending, {})

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
