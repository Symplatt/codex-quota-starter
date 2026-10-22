from version import VERSION
import json
import re
import time
import unittest
from test_quota import csrf_token
import test_quota as fixtures

FakeRPC, raw = fixtures.FakeRPC, fixtures.raw


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        fixtures.QuotaTests.setUp(self)
        self.store.set('current', {'primary': {'resetsAt': 149}})
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine
    def test_schedule_waits_even_with_empty_quota(self):
        fake=FakeRPC([raw(149,0)])
        e=self.engine(fake,100); e.set_schedule(150); e.tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(e.next_poll,160)
        self.assertEqual(e.timing()['estimatedResetAt'],18150)

    def test_three_am_recovery_waits_for_three_thirty_with_rolling_reset(self):
        reset_at, send_at, refresh_at = 3 * 3600, 3 * 3600 + 30 * 60, 8 * 3600 + 30 * 60
        self.store.set('current', {'primary': {'resetsAt': reset_at, 'usedPercent': 10}})
        # Empty quota may report a moving now + 5h timestamp until a request starts it.
        values = [raw(reset_at)] + [raw(now + 18000, 0) for now in range(reset_at, send_at, 60)]
        values += [raw(refresh_at, 0), raw(refresh_at + 1, 0), raw(refresh_at, 1)]
        fake = FakeRPC(values)
        e = self.engine(fake, reset_at - 60)
        plan = e.set_schedule(refresh_at=refresh_at)
        e.tick()
        for now in range(reset_at, send_at, 60):
            e.sleeper(60)
            e.tick()
            self.assertEqual(e.clock(), now)
            self.assertEqual(fake.sent, 0)
            self.assertEqual(self.store.get('schedule')['status'], 'pending')
            self.assertEqual(e.timing()['sendAt'], send_at)
            self.assertEqual(e.next_poll, now + 60)
        e.sleeper(60)
        e.tick()
        self.assertEqual(fake.sent, 1)
        self.assertEqual(self.store.get('schedule')['status'], 'submitted')
        attempt = self.store.recent('attempts')[0]
        self.assertEqual(attempt['detail']['scheduleId'], plan['id'])
        self.assertEqual(attempt['detail']['submissions'][0]['startedAt'], send_at)
        self.assertEqual(e.next_poll, send_at + 60)
        e.sleeper(60)
        e.tick()
        self.engine(fake, send_at + 120).tick()
        self.assertEqual(fake.sent, 1)
        self.assertEqual(len(self.store.recent('attempts')), 1)
        self.assertFalse(any(row['kind'] == 'schedule_cancelled' for row in self.store.recent('events', 100)))

    def test_restart_preserves_plan_with_empty_or_unknown_cached_usage(self):
        for stopped in (False, True):
            for primary in ({'resetsAt': 300, 'usedPercent': 0}, {'resetsAt': 300}):
                with self.subTest(stopped=stopped, primary=primary):
                    self.store.set('schedule', None)
                    self.store.set('connection', {})
                    self.store.set('current', {'primary': {'resetsAt': 149}})
                    fake = FakeRPC([raw(300, 0)])
                    e = self.engine(fake, 100)
                    plan = e.set_schedule(200)
                    self.store.set('current', {'primary': primary})
                    if stopped:
                        e.stop_connection()
                    restarted = self.engine(fake, 120)
                    self.assertEqual(self.store.get('schedule')['id'], plan['id'])
                    self.assertEqual(self.store.get('schedule')['status'], 'pending')
                    restarted.tick()
                    self.assertEqual(fake.reads, 0 if stopped else 1)
                    self.assertEqual(fake.sent, 0)
                    self.assertEqual(self.store.get('schedule')['status'], 'pending')

    def test_reconnect_clears_idle_plan_behind_reset_without_sending(self):
        fake = FakeRPC([raw(300, 0)])
        e = self.engine(fake, 100)
        plan = e.set_schedule(200)
        e.reconnect()
        e.tick()
        self.assertEqual(e.timing()['mode'], 'auto')
        self.assertEqual(self.store.get('scheduleNotice')['id'], plan['id'])
        self.assertIn('恢复自动', self.store.get('scheduleNotice')['message'])
        self.assertEqual(fake.reads, 1)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 160)
        e.reconnect()
        e.tick()
        self.assertEqual(len([r for r in self.store.recent('events') if r['kind'] == 'schedule_cancelled']), 1)

    def test_reconnect_preserves_idle_plan_equal_to_or_after_reset(self):
        for end in (199, 200):
            with self.subTest(end=end):
                self.store.set('current', {'primary': {'resetsAt': 149}})
                e = self.engine(FakeRPC([raw(end, 0)]), 100)
                e.set_schedule(200)
                e.reconnect()
                e.tick()
                self.assertEqual(e.timing()['mode'], 'manual')

    def test_ordinary_poll_keeps_idle_plan_but_reconnect_revalidates(self):
        fake = FakeRPC([raw(149, 0), raw(300, 0)])
        e = self.engine(fake, 100)
        e.set_schedule(200)
        e.tick(check_only=True)
        e.tick(check_only=True)
        self.assertEqual(e.timing()['mode'], 'manual')
        e.reconnect()
        e.tick()
        self.assertEqual(e.timing()['mode'], 'auto')
        self.assertEqual(fake.sent, 0)

    def test_failed_reconnect_leaves_plan_and_notice_untouched(self):
        e = self.engine(FakeRPC([TimeoutError('offline')]), 100)
        e.set_schedule(200)
        e.reconnect()
        e.tick()
        self.assertEqual(e.timing()['mode'], 'manual')
        self.assertIsNone(self.store.get('scheduleNotice'))

    def test_manual_check_network_recovery_also_revalidates_idle_plan(self):
        fake = FakeRPC([TimeoutError('offline'), raw(300, 0)])
        e = self.engine(fake, 100)
        e.set_schedule(200)
        e.request_check()
        e.tick()
        self.assertEqual(e.connection()['failures'], 1)
        e.sleeper(10)
        e.tick()
        self.assertEqual(e.timing()['mode'], 'auto')
        self.assertTrue(e.linked)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 170)

    def test_due_schedule_sends_once_and_survives_restart(self):
        fake=FakeRPC([raw(149)]); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,1)
        self.assertEqual(self.store.get('schedule')['status'],'submitted')
        other=FakeRPC([raw(149)]); self.engine(other,151).tick()
        self.assertEqual(other.sent,0)

    def test_schedule_does_not_bypass_guard(self):
        self.store.claim('prior','cycle:20000',100,{})
        fake=FakeRPC([raw(149)]); e=self.engine(fake,110); e.set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(self.store.get('schedule')['status'],'pending')

    def test_schedule_waits_for_weekly_recovery(self):
        fake=FakeRPC([raw(149,1,100)]); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(self.store.get('schedule')['status'],'pending')

    def test_schedule_pause_then_resume(self):
        fake=FakeRPC([raw(149)]); self.engine(fake,100).set_schedule(150)
        self.store.set('paused',True); self.engine(fake,150).tick()
        self.assertEqual(fake.sent,0)
        self.store.set('paused',False); self.engine(fake,160).tick()
        self.assertEqual(fake.sent,1)

    def test_clear_schedule_restores_automatic_policy(self):
        fake=FakeRPC([raw()]); e=self.engine(fake,100); e.set_schedule(150)
        e.set_schedule(); e.tick()
        self.assertIsNone(self.store.get('schedule'))
        self.assertEqual(e.timing()['mode'],'auto')
        self.assertEqual(fake.sent,0)

    def test_schedule_invalid_times(self):
        e=self.engine(FakeRPC([raw()]),100)
        for value in [True,float('nan'),float('inf'),'150',99,100,40000000]:
            with self.subTest(value=value),self.assertRaises(ValueError): e.set_schedule(value)

    def test_schedule_strictly_after_gpt_reset(self):
        e=self.engine(FakeRPC([raw()]),100)
        for value in [148,149]:
            with self.assertRaisesRegex(ValueError,'正常刷新'): e.set_schedule(value)
        e.set_schedule(150)
        self.assertEqual(e.timing()['minSendAt'],150)

    def test_schedule_requires_known_reset_but_can_clear(self):
        e=self.engine(FakeRPC([raw()]),100); self.store.set('current',None)
        with self.assertRaisesRegex(ValueError,'先检测'): e.set_schedule(150)
        self.assertIsNone(e.timing()['minSendAt'])
        e.set_schedule()

    def test_schedule_rechecks_changed_reset(self):
        e=self.engine(FakeRPC([raw()]),100)
        self.store.set('current',{'primary':{'resetsAt':200}})
        with self.assertRaises(ValueError): e.set_schedule(150)
        self.assertEqual(e.timing()['minSendAt'],201)

    def test_expired_reset_requires_future_time(self):
        e=self.engine(FakeRPC([raw()]),200)
        self.assertEqual(e.timing()['minSendAt'],201)
        e.set_schedule(201)

    def test_schedule_edit_while_busy_is_rejected(self):
        e=self.engine(FakeRPC([raw()]),100)
        with e.lock:
            with self.assertRaises(RuntimeError): e.set_schedule(150)

    def test_schedule_claim_is_consumed_before_send(self):
        e=self.engine(FakeRPC([raw()]),100); plan=e.set_schedule(150)
        self.assertIsNotNone(self.store.claim('acct','cycle:20000',150,{},plan['id']))
        self.assertEqual(type(self.store)(self.store.path).get('schedule')['status'],'submitted')

    def test_replaced_schedule_cannot_be_claimed(self):
        e=self.engine(FakeRPC([raw()]),100); old=e.set_schedule(150); e.set_schedule(200)
        self.assertIsNone(self.store.claim('acct','cycle:20000',150,{},old['id']))

    def test_unknown_scheduled_result_stops_after_bounded_retries(self):
        fake=FakeRPC([raw(149)],send_error=True); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick(); self.engine(fake,151).tick()
        self.assertEqual(fake.sent,5)
        self.assertEqual(self.store.get('schedule')['status'],'submitted')

    def test_refresh_target_converts_to_send_time_and_persists(self):
        e=self.engine(FakeRPC([raw()]),100)
        e.set_schedule(refresh_at=18180)
        self.assertEqual(e.timing()['sendAt'],180)
        self.assertEqual(e.timing()['estimatedResetAt'],18180)
        self.assertEqual(self.engine(FakeRPC([raw()]),110).timing()['sendAt'],180)
        self.assertEqual(e.timing()['minRefreshAt'],18180)

    def test_refresh_target_rejects_seconds_and_invalid_types(self):
        e=self.engine(FakeRPC([raw()]),100)
        for value in [True, '18180', float('nan'), float('inf'), 18181, 18060]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                e.set_schedule(refresh_at=value)

    def test_refresh_target_respects_guard_and_upper_bound(self):
        e=self.engine(FakeRPC([raw()]),100)
        self.store.claim('prior','cycle:20000',100,{})
        lower=e.timing()['minRefreshAt']
        with self.assertRaises(ValueError): e.set_schedule(refresh_at=lower-60)
        e.set_schedule(refresh_at=lower)
        upper=e.timing()['maxRefreshAt']
        e.set_schedule(refresh_at=upper)
        with self.assertRaises(ValueError): e.set_schedule(refresh_at=upper+60)


class ScheduleHTTPTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HTTPTests.tearDownClass.__func__)
    request = fixtures.HTTPTests.request
    def setUp(self):
        self.engine.store.set('current',{'primary':{'resetsAt':int(time.time())+3600}})
    def test_schedule_save_clear_and_status(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':csrf_token(page)}
        target=(int(time.time())//60+1440)*60
        self.assertEqual(self.request('POST','/api/schedule',headers,json.dumps({'refreshAt':target}))[0],200)
        status=json.loads(self.request('GET','/api/status')[1])
        self.assertEqual(status['version'],VERSION)
        self.assertEqual(status['timing']['sendAt'],target-18000)
        self.assertEqual(status['timing']['estimatedResetAt'],target)
        self.assertEqual(self.request('POST','/api/schedule/clear',headers)[0],200)
        self.assertIsNone(self.engine.store.get('schedule'))

    def test_schedule_endpoint_validation(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':csrf_token(page)}
        for body in ['{}','null','[]','bad','{"sendAt":null}','{"sendAt":-1}']:
            self.assertEqual(self.request('POST','/api/schedule',headers,body)[0],400)
        self.assertEqual(self.request('POST','/api/schedule',body='{}')[0],403)

    def test_http_rejects_before_and_equal_reset(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':csrf_token(page)}
        reset=self.engine.store.get('current')['primary']['resetsAt']
        for value in [(reset+18000)//60*60-60,(reset+18000)//60*60]:
            code,body=self.request('POST','/api/schedule',headers,json.dumps({'refreshAt':value}))
            self.assertEqual(code,400)
            self.assertIn('正常刷新',body)
        self.assertEqual(self.request('POST','/api/schedule',headers,json.dumps({'refreshAt':((reset+18000)//60+1)*60}))[0],200)

    def test_help_page_and_undecorated_home(self):
        code,help_page=self.request('GET','/help')
        self.assertEqual(code,200)
        for text in ['暂停 / 恢复运行','重新检测','选择时间','恢复自动','v'+VERSION]:
            self.assertIn(text,help_page)
        page=self.request('GET','/')[1]
        self.assertNotIn('LOCAL AUTOMATION',page)
        self.assertNotIn('跟随服务器',page)
