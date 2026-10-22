"""Paused sampling and consolidated evidence regressions."""
import json
import unittest
from unittest.mock import patch

from core import Engine
from test_quota import csrf_token
import test_quota as fixtures
from test_quota import FakeRPC, raw


class PausedSamplingTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def test_default_pause_does_not_read_send_or_sample(self):
        fake = FakeRPC([raw()])
        e = self.engine(fake, 100)
        e.set_paused(True)
        e.tick()
        self.assertEqual(fake.reads, 0)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(self.store.quota_history(), [])

    def test_enabled_pause_reads_samples_and_persists_without_sending(self):
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 100)
        e.set_paused(True)
        e.set_poll_while_paused(True)
        e.tick()
        self.assertEqual(fake.reads, 1)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 160)
        self.assertEqual(len(self.store.quota_history(now=100)), 1)
        restarted = self.engine(fake, 160)
        restarted.tick()
        self.assertEqual(fake.reads, 2)
        e.set_poll_while_paused(False)
        e.tick()
        self.assertEqual(fake.reads, 2)

    def test_manual_check_and_reconnect_work_with_paused_polling_off(self):
        fake = FakeRPC([raw(149)])
        e = self.engine(fake, 100)
        e.set_paused(True)
        e.request_check()
        e.tick()
        e.reconnect()
        e.tick()
        self.assertEqual(fake.reads, 2)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 160)

    def test_activity_keeps_unfinished_attempt_and_deduplicates_final_event(self):
        attempt = self.store.claim('account', 'cycle:one', 100, {'reason': '计划到点'})
        for _ in range(55):
            self.store.log('check', {}, 101)
        rows = self.store.activity()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['detail']['status'], 'reserved')
        self.store.log('trigger', {'attempt': attempt, 'status': 'completed', 'evidence': '真实结果'}, 102)
        rows = self.store.activity()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['detail']['evidence'], '真实结果')




class SettingsHTTPTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.HTTPTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.HTTPTests.tearDownClass.__func__)
    setUp = fixtures.HTTPTests.setUp
    request = fixtures.HTTPTests.request
    def test_setting_requires_boolean_and_persists_and_favicon_is_served(self):
        _, page = self.request('GET', '/')
        import re
        token = csrf_token(page)
        headers = {'Origin': 'http://127.0.0.1:18769', 'X-Quota-Token': token, 'Content-Type': 'application/json'}
        for enabled in (True, False):
            code, _ = self.request('POST', '/api/settings/poll-while-paused', headers, json.dumps({'enabled': enabled}))
            self.assertEqual(code, 200)
            self.assertEqual(self.engine.store.get('pollWhilePaused'), enabled)
        self.assertEqual(self.request('POST', '/api/settings/poll-while-paused', headers, '{"enabled":"false"}')[0], 400)
        self.assertEqual(self.request('POST', '/api/settings/poll-while-paused', body='{"enabled":true}')[0], 403)
        code, icon = self.request('GET', '/favicon.svg')
        self.assertEqual(code, 200)
        self.assertIn('<svg', icon)
