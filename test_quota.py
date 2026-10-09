import concurrent.futures
import copy
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest

from core import Engine, Store, candidate, evidence, normalize
from app import create_server
from rpc import RPC


def raw(end=20000, used=10, weekly=20):
    bucket = {'limitId': 'codex', 'planType': 'plus',
              'primary': {'resetsAt': end, 'usedPercent': used, 'windowDurationMins': 300},
              'secondary': {'resetsAt': 90000, 'usedPercent': weekly, 'windowDurationMins': 10080}}
    return {'accountId': 'test-account', 'rateLimits': bucket, 'rateLimitsByLimitId': {'codex': bucket}}


class FakeRPC:
    def __init__(self, values, send_error=False):
        self.values = values
        self.send_error = send_error
        self.sent = 0
        self.reads = 0

    def account(self):
        return {'type': 'chatgpt', 'planType': 'plus', 'email': 'test@example.test'}

    def limits(self):
        value = self.values[min(self.reads, len(self.values)-1)]
        self.reads += 1
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def prepare(self, cwd):
        return {'threadId': 'fake-thread', 'model': 'gpt-6-luna', 'effort': 'low'}

    def send_minimal(self, prepared):
        self.sent += 1
        if self.send_error:
            raise TimeoutError('lost response')
        return {'status': 'completed', 'reply': 'OK', 'turnId': 'fake-turn'}

    def close(self):
        pass


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'state.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, fake, now=20002):
        return Engine(self.store, Path(self.tmp.name)/'empty', rpc_factory=lambda _: fake, clock=lambda: now)

    def test_multi_bucket_is_authoritative(self):
        r = raw(); r['rateLimitsByLimitId'] = {'other': r['rateLimits']}
        with self.assertRaises(ValueError): normalize(r)

    def test_legacy_codex_bucket(self):
        r = raw(); del r['rateLimitsByLimitId']
        self.assertEqual(normalize(r)['bucket'], 'codex')

    def test_unknown_percentage_not_zero(self):
        r=raw(); r['rateLimits']['primary']['usedPercent']=None
        with self.assertRaises(ValueError): normalize(r)

    def test_wrong_duration_rejected(self):
        r=raw(); r['rateLimits']['primary']['windowDurationMins']=60
        with self.assertRaises(ValueError): normalize(r)

    def test_nan_rejected(self):
        r=raw(); r['rateLimits']['primary']['resetsAt']=float('nan')
        with self.assertRaises(ValueError): normalize(r)

    def test_weekly_blocks_even_past_timestamp(self):
        self.assertIsNone(candidate(normalize(raw(100,0,100)),None,200)[0])

    def test_primary_exhausted_blocks(self):
        self.assertIsNone(candidate(normalize(raw(100,100)),None,200)[0])

    def test_server_disallows_ordinary_usage(self):
        r=raw(); r['ordinaryUsageAllowed']=False
        self.assertTrue(normalize(r)['blocked'])

    def test_secondary_missing_fields_fail_closed(self):
        r=raw(); del r['rateLimits']['secondary']['usedPercent']
        with self.assertRaises(ValueError): normalize(r)

    def test_wait_in_active_window(self):
        self.assertIsNone(candidate(normalize(raw()),None,100)[0])

    def test_empty_window_bootstrap(self):
        self.assertEqual(candidate(normalize(raw(20000,0)),None,100)[0], 'cycle:20000')

    def test_not_early_at_boundary(self):
        self.assertIsNone(candidate(normalize(raw()),None,20001)[0])

    def test_boundary_two_seconds_later(self):
        self.assertEqual(candidate(normalize(raw()),None,20002)[0], 'after:20000')

    def test_concurrent_user_already_started(self):
        self.assertIsNone(candidate(normalize(raw(38000,1)),normalize(raw()),20002)[0])

    def test_one_submission_across_restart(self):
        fake=FakeRPC([raw(),raw(),raw(38002,0)])
        self.engine(fake).tick()
        other=FakeRPC([raw(38002,0)])
        self.engine(other,20100).tick(manual=True)
        self.assertEqual(fake.sent,1); self.assertEqual(other.sent,0)
        self.assertEqual(len(self.store.recent('attempts')),1)

    def test_next_window_can_trigger(self):
        fake=FakeRPC([raw(),raw(),raw(38002,0)])
        self.engine(fake).tick()
        other=FakeRPC([raw(38002,1),raw(38002,1),raw(56004,0)])
        self.engine(other,38004).tick()
        self.assertEqual(other.sent,1)

    def test_timeout_does_not_retry(self):
        fake=FakeRPC([raw()],send_error=True)
        self.engine(fake).tick()
        self.engine(fake,20100).tick(manual=True)
        self.assertEqual(fake.sent,1)
        self.assertEqual(self.store.recent('attempts')[0]['status'],'unknown')

    def test_claim_survives_pre_send_crash(self):
        self.assertIsNotNone(self.store.claim('acct','after:100',100,{}))
        recovered=Store(self.store.path)
        self.assertIsNone(recovered.claim('acct','cycle:18100',110,{}))

    def test_identity_field_change_does_not_bypass_guard(self):
        self.store.claim('account-id-hash','cycle:20000',100,{})
        self.assertIsNone(self.store.claim('email-hash','cycle:20000',110,{}))
        self.assertIsNone(self.store.claim('email-hash','cycle:20001',110,{}))

    def test_missing_account_id_after_restart_does_not_repeat(self):
        fake=FakeRPC([raw(),raw(),raw(38002,0)])
        self.engine(fake).tick()
        other_raw=raw(38002,0); del other_raw['accountId']
        other=FakeRPC([other_raw])
        self.engine(other,20100).tick(manual=True)
        self.assertEqual(other.sent,0)

    def test_old_identity_claim_still_blocks_after_guard_expires(self):
        self.store.claim('old-identity','cycle:20000',100,{})
        self.assertIsNone(self.store.claim('new-identity','cycle:20000',20000,{}))

    def test_concurrent_claims_at_most_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.store.claim('acct','after:100',100,{}),range(8)))
        self.assertEqual(sum(x is not None for x in results),1)

    def test_paused_does_not_send(self):
        self.store.set('paused',True)
        fake=FakeRPC([raw()]); self.engine(fake).tick(manual=True)
        self.assertEqual(fake.sent,0)

    def test_preflight_rechecks_weekly(self):
        fake=FakeRPC([raw(),raw(20000,10,100)])
        self.engine(fake).tick()
        self.assertEqual(fake.sent,0)

    def test_account_switch_during_preparation(self):
        changed=raw(); changed['accountId']='other'
        fake=FakeRPC([raw(),changed]); self.engine(fake).tick()
        self.assertEqual(fake.sent,0)

    def test_no_guess_when_read_fails(self):
        fake=FakeRPC([TimeoutError('offline')]); e=self.engine(fake); e.tick()
        self.assertEqual(fake.sent,0); self.assertGreater(e.next_poll,20002)

    def test_delayed_reset_is_not_fabricated(self):
        e=evidence(normalize(raw()),normalize(raw()),20002,True)
        self.assertIn('不能证明',e)

    def test_one_second_jitter_is_not_new_cycle_evidence(self):
        e=evidence(normalize(raw()),normalize(raw(20001)),20002,True)
        self.assertIn('不能证明',e)
        self.assertEqual(candidate(normalize(raw(20001)),normalize(raw()),100)[1],'等待服务器重置时间')

    def test_alignment_is_evidence_not_proof(self):
        e=evidence(normalize(raw()),normalize(raw(38002)),20002,True)
        self.assertIn('支持',e); self.assertIn('仍可能',e)

    def test_api_key_rejected(self):
        rpc=object.__new__(RPC)
        rpc.call=lambda *args,**kwargs:{'account':{'type':'apiKey'}}
        with self.assertRaises(RuntimeError): rpc.account()

    def test_no_expensive_model_fallback(self):
        rpc=object.__new__(RPC)
        rpc.call=lambda *args,**kwargs:{'data':[{'model':'gpt-6-astra','hidden':False}]}
        with self.assertRaises(RuntimeError): rpc.choose_model()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.engine=Engine(Store(Path(cls.tmp.name)/'db'),Path(cls.tmp.name)/'empty')
        class FakeStartup:
            enabled=True
            def apply(self,action):
                if action != 'status': self.enabled=action=='enable'
                return {'available':True,'enabled':self.enabled,'error':None}
        cls.server=create_server(cls.engine,port=18769,startup=FakeStartup())
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.tmp.cleanup()

    def request(self,method,path,headers=None,body=None):
        c=http.client.HTTPConnection('127.0.0.1',18769)
        c.request(method,path,headers=headers or {},body=body)
        r=c.getresponse(); result=(r.status,r.read().decode()); c.close(); return result

    def test_status_and_no_secret(self):
        status,body=self.request('GET','/api/status')
        self.assertEqual(status,200); self.assertEqual(json.loads(body)['service'],'quota-starter')

    def test_cross_origin_rejected(self):
        self.assertEqual(self.request('POST','/api/pause',{'Origin':'https://evil.example'})[0],403)

    def test_dns_rebinding_rejected(self):
        self.assertEqual(self.request('GET','/api/status',{'Host':'evil.example:18769'})[0],403)

    def test_pause_resume_persists(self):
        _,page=self.request('GET','/')
        token=re.search("const token='([^']+)'",page)[1]
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':token}
        self.assertEqual(self.request('POST','/api/pause',headers)[0],200)
        self.assertTrue(self.engine.store.get('paused'))
        self.assertEqual(self.request('POST','/api/trigger',headers)[0],409)
        self.assertEqual(self.request('POST','/api/resume',headers)[0],200)
        self.assertFalse(self.engine.store.get('paused'))

    def test_manual_trigger_returns_result_without_queue(self):
        from unittest.mock import patch
        _,page=self.request('GET','/')
        token=re.search("const token='([^']+)'",page)[1]
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':token}
        self.engine.store.set('paused',False)
        with patch.object(self.engine,'tick',return_value='本周期已处理') as tick:
            status,body=self.request('POST','/api/trigger',headers)
            self.assertEqual(status,200)
            self.assertEqual(json.loads(body)['message'],'本周期已处理')
            tick.assert_called_once_with(manual=True)
        with self.engine.lock:
            with patch.object(self.engine,'tick') as tick:
                self.assertEqual(self.request('POST','/api/trigger',headers)[0],409)
                tick.assert_not_called()

    def test_autostart_toggle(self):
        _,page=self.request('GET','/')
        token=re.search("const token='([^']+)'",page)[1]
        headers={'Origin':'http://127.0.0.1:18769','X-Quota-Token':token}
        self.assertEqual(self.request('POST','/api/autostart/disable',headers)[0],200)
        self.assertFalse(json.loads(self.request('GET','/api/status')[1])['autostart']['enabled'])
        self.assertEqual(self.request('POST','/api/autostart/enable',headers)[0],200)
        self.assertTrue(json.loads(self.request('GET','/api/status')[1])['autostart']['enabled'])

    def test_autostart_requires_origin_token(self):
        self.assertEqual(self.request('POST','/api/autostart/disable')[0],403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
