"""Weekly reset estimates are account-local and sampled only at full-quota boundaries."""
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

    def test_waits_until_full_then_freezes_through_routine_reads(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertIsNone(self.publish(e, 10, 90000))
        self.assertEqual(self.publish(e, 0, 91000), 91000)
        self.assertEqual(self.publish(e, 0, 92000), 91000)
        self.assertEqual(self.publish(e, 1, 93000), 91000)
        self.assertEqual(self.publish(e, 0, 94000), 94000)

    def test_first_full_read_and_restart_do_not_move_saved_estimate(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertEqual(self.publish(e, 0, 90000), 90000)
        restarted = self.engine(fixtures.FakeRPC([]), 160)
        self.assertEqual(self.publish(restarted, 0, 91000), 90000)

    def test_accounts_do_not_share_previous_usage_or_estimates(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertEqual(self.publish(e, 0, 90000, 'one'), 90000)
        self.assertIsNone(self.publish(e, 1, 91000, 'two'))
        self.assertEqual(self.publish(e, 0, 92000, 'two'), 92000)
        self.assertEqual(self.publish(e, 0, 93000, 'one'), 90000)

    def test_missing_weekly_at_boundary_is_not_replaced_by_routine_read(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        self.assertIsNone(self.publish(e, 0, 90000, missing=True))
        self.assertIsNone(self.publish(e, 0, 91000))
        self.assertIsNone(self.publish(e, 1, 92000))
        self.assertEqual(self.publish(e, 0, 93000), 93000)

    def test_full_quota_upgrade_initializes_cache_from_new_read(self):
        e = self.engine(fixtures.FakeRPC([]), 100)
        import hashlib
        key = hashlib.sha256(b'one').hexdigest()[:24]
        self.store.set('snapshot:' + key, normalize(fixtures.raw(20000, 0)))
        self.assertEqual(self.publish(e, 0, 91000), 91000)


if __name__ == '__main__':
    unittest.main()
