"""Weekly reset estimates refresh on explicit checks and every full-quota read."""
import unittest

from core import normalize
import test_quota as fixtures


class WeeklyResetTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def publish(self, engine, used, reset, account='one', missing=False):
        snapshot = normalize(fixtures.raw(20000, used))
        if missing:
            snapshot['secondary'] = None
        else:
            snapshot['secondary']['resetsAt'] = reset
        engine._publish((account, None), snapshot)
        return self.store.get('current')['weeklyResetAt']

    def test_each_full_snapshot_refreshes_but_routine_nonfull_reads_keep_estimate(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertIsNone(self.publish(e, 10, 90000))
        self.assertEqual(self.publish(e, 0, 91000), 91000)
        self.assertEqual(self.publish(e, 0, 92000), 92000)
        self.assertEqual(self.publish(e, 1, 93000), 92000)
        self.assertEqual(self.publish(e, 0, 94000), 94000)

    def test_full_read_after_restart_refreshes_saved_estimate(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertEqual(self.publish(e, 0, 90000), 90000)
        restarted = self.engine(fixtures.FakeRPC([]), 160)
        self.assertEqual(self.publish(restarted, 0, 91000), 91000)

    def test_accounts_do_not_share_previous_usage_or_estimates(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertEqual(self.publish(e, 0, 90000, 'one'), 90000)
        self.assertIsNone(self.publish(e, 1, 91000, 'two'))
        self.assertEqual(self.publish(e, 0, 92000, 'two'), 92000)
        self.assertEqual(self.publish(e, 0, 93000, 'one'), 93000)

    def test_missing_weekly_recovers_on_next_full_read(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertIsNone(self.publish(e, 0, 90000, missing=True))
        self.assertEqual(self.publish(e, 0, 91000), 91000)
        self.assertEqual(self.publish(e, 1, 92000), 91000)
        self.assertEqual(self.publish(e, 0, 93000), 93000)

    def test_full_quota_upgrade_initializes_cache_from_new_read(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        import hashlib
        key = hashlib.sha256(b'one').hexdigest()[:24]
        self.store.set('snapshot:' + key, normalize(fixtures.raw(20000, 0)))
        self.assertEqual(self.publish(e, 0, 91000), 91000)

    def quota(self, used, reset, weekly_used=20):
        raw = fixtures.raw(20000, used, weekly_used)
        raw['rateLimits']['secondary']['resetsAt'] = reset
        return raw

    def test_all_three_controls_refresh_weekly_time_while_five_hour_not_full(self):
        for control in ('request_check', 'reconnect', 'resume'):
            with self.subTest(control=control):
                fake = fixtures.FakeRPC([self.quota(12, 99000)])
                e = self.engine(fake, 100)
                self.publish(e, 0, 90000, 'test@example.test')
                if control == 'resume':
                    e.set_paused(True)
                getattr(e, control)()
                e.tick()
                current = self.store.get('current')
                self.assertEqual(current['weeklyResetAt'], 99000)
                self.assertEqual(current['secondary']['resetsAt'], 99000)
                self.assertEqual(fake.reads, 1)
                self.assertEqual(fake.sent, 0)

    def test_each_full_detection_reads_again_once_even_when_continuously_full(self):
        fake = fixtures.FakeRPC([self.quota(0, at) for at in (90000, 91000, 92000, 93000)])
        e = self.engine(fake, 100)
        e.tick(check_only=True)
        self.assertEqual(fake.reads, 2)
        self.assertEqual(self.store.get('current')['weeklyResetAt'], 91000)
        e.tick(check_only=True)
        self.assertEqual(fake.reads, 4)
        self.assertEqual(self.store.get('current')['weeklyResetAt'], 93000)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.next_poll, 160)

    def test_followup_keeps_both_windows_from_the_same_fresh_response(self):
        fake = fixtures.FakeRPC([self.quota(0, 90000), self.quota(15, 95000, 45)])
        e = self.engine(fake, 100)
        e.tick(check_only=True)
        current = self.store.get('current')
        self.assertEqual(current['primary']['usedPercent'], 15)
        self.assertEqual(current['secondary']['usedPercent'], 45)
        self.assertEqual(current['weeklyResetAt'], 95000)
        self.assertEqual(fake.reads, 2)

    def test_nonfull_automatic_read_does_not_add_a_request(self):
        fake = fixtures.FakeRPC([self.quota(10, 99000)])
        e = self.engine(fake, 100)
        self.publish(e, 0, 90000, 'test@example.test')
        e.tick(check_only=True)
        self.assertEqual(self.store.get('current')['weeklyResetAt'], 90000)
        self.assertEqual(fake.reads, 1)

    def test_missing_weekly_in_explicit_read_clears_old_estimate(self):
        raw = self.quota(10, 99000)
        raw['rateLimits']['secondary'] = None
        e = self.engine(fixtures.FakeRPC([raw]), 100)
        self.publish(e, 0, 90000, 'test@example.test')
        e.request_check(); e.tick()
        self.assertIsNone(self.store.get('current')['weeklyResetAt'])

    def test_failed_followup_preserves_previous_snapshot_and_uses_existing_retry(self):
        fake = fixtures.FakeRPC([self.quota(0, 99000), TimeoutError('read failed')])
        e = self.engine(fake, 100)
        self.publish(e, 0, 90000, 'test@example.test')
        before = self.store.get('current')
        e.tick(check_only=True)
        self.assertEqual(self.store.get('current'), before)
        self.assertEqual(fake.reads, 2)
        self.assertEqual(fake.sent, 0)
        self.assertEqual(e.connection()['failures'], 1)

    def test_followup_rejects_changed_account_and_respects_weekly_block(self):
        changed = self.quota(0, 99000)
        changed['accountId'] = 'different'
        fake = fixtures.FakeRPC([self.quota(0, 90000), changed])
        e = self.engine(fake, 100)
        e.tick(check_only=True)
        self.assertIsNone(self.store.get('current'))
        self.assertEqual(fake.sent, 0)
        self.store.set('connection', {})
        blocked = fixtures.FakeRPC([self.quota(0, 90000), self.quota(0, 99000, 100)])
        e = self.engine(blocked, 20002)
        e.tick()
        self.assertTrue(self.store.get('current')['blocked'])
        self.assertEqual(blocked.reads, 2)
        self.assertEqual(blocked.sent, 0)


if __name__ == '__main__':
    unittest.main()
