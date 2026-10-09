import json
import re
import time
import unittest
import test_quota as fixtures

FakeRPC, raw = fixtures.FakeRPC, fixtures.raw


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        fixtures.QuotaTests.setUp(self)
        self.store.set('current', {'primary': {'resetsAt': 149}})
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine
    def test_schedule_waits_even_with_empty_quota(self):
        fake=FakeRPC([raw(20000,0)])
        e=self.engine(fake,100); e.set_schedule(150); e.tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(e.next_poll,150)
        self.assertEqual(e.timing()['estimatedResetAt'],18150)

    def test_due_schedule_sends_once_and_survives_restart(self):
        fake=FakeRPC([raw()]); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,1)
        self.assertEqual(self.store.get('schedule')['status'],'submitted')
        other=FakeRPC([raw()]); self.engine(other,151).tick()
        self.assertEqual(other.sent,0)

    def test_schedule_does_not_bypass_guard(self):
        self.store.claim('prior','cycle:20000',100,{})
        fake=FakeRPC([raw()]); e=self.engine(fake,110); e.set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(self.store.get('schedule')['status'],'pending')

    def test_schedule_waits_for_weekly_recovery(self):
        fake=FakeRPC([raw(20000,1,100)]); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick()
        self.assertEqual(fake.sent,0)
        self.assertEqual(self.store.get('schedule')['status'],'pending')

    def test_schedule_pause_then_resume(self):
        fake=FakeRPC([raw()]); self.engine(fake,100).set_schedule(150)
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

    def test_unknown_scheduled_result_never_resubmits(self):
        fake=FakeRPC([raw()],send_error=True); self.engine(fake,100).set_schedule(150)
        self.engine(fake,150).tick(); self.engine(fake,151).tick()
        self.assertEqual(fake.sent,1)
        self.assertEqual(self.store.get('schedule')['status'],'submitted')


class ScheduleHTTPTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HTTPTests.tearDownClass.__func__)
    request = fixtures.HTTPTests.request
    def setUp(self):
        self.engine.store.set('current',{'primary':{'resetsAt':int(time.time())+3600}})
    def test_schedule_save_clear_and_status(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':re.search("const token='([^']+)'",page)[1]}
        target=int(time.time())+86400
        self.assertEqual(self.request('POST','/api/schedule',headers,json.dumps({'sendAt':target}))[0],200)
        status=json.loads(self.request('GET','/api/status')[1])
        self.assertEqual(status['version'],'1.1.1')
        self.assertEqual(status['timing']['sendAt'],target)
        self.assertEqual(status['timing']['estimatedResetAt'],target+18000)
        self.assertEqual(self.request('POST','/api/schedule/clear',headers)[0],200)
        self.assertIsNone(self.engine.store.get('schedule'))

    def test_schedule_endpoint_validation(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':re.search("const token='([^']+)'",page)[1]}
        for body in ['{}','null','[]','bad','{"sendAt":null}','{"sendAt":-1}']:
            self.assertEqual(self.request('POST','/api/schedule',headers,body)[0],400)
        self.assertEqual(self.request('POST','/api/schedule',body='{}')[0],403)

    def test_http_rejects_before_and_equal_reset(self):
        _,page=self.request('GET','/')
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':re.search("const token='([^']+)'",page)[1]}
        reset=self.engine.store.get('current')['primary']['resetsAt']
        for value in [reset-1,reset]:
            code,body=self.request('POST','/api/schedule',headers,json.dumps({'sendAt':value}))
            self.assertEqual(code,400)
            self.assertIn('正常刷新',body)
        self.assertEqual(self.request('POST','/api/schedule',headers,json.dumps({'sendAt':reset+1}))[0],200)

    def test_help_page_and_undecorated_home(self):
        code,help_page=self.request('GET','/help')
        self.assertEqual(code,200)
        for text in ['手动触发一次','暂停 / 恢复运行','立即检测','保存','恢复自动','v1.1.1']:
            self.assertIn(text,help_page)
        page=self.request('GET','/')[1]
        self.assertNotIn('LOCAL AUTOMATION',page)
        self.assertNotIn('跟随服务器',page)
