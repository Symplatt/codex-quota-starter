"""Read-only HTTP recovery without weakening authentication or send protection."""
import unittest

from core import ATTEMPT_LIMIT, RECOVERY_INTERVAL, RETRY_INTERVAL, transient_connection_error
import test_quota as fixtures
from test_quota import FakeRPC


class ConnectionErrorClassificationTests(unittest.TestCase):
    def test_read_only_temporary_http_status_forms_are_recoverable(self):
        forms = (
            'HTTP {code} Service Unavailable',
            'HTTP/1.1 {code} Service Unavailable',
            'HTTP status server error ({code} Service Unavailable)',
            'HTTP status client error ({code} Too Many Requests)',
            'HTTP status code: {code}',
            'status code: {code}',
            'status={code}',
        )
        for code in (408, 425, 429, 500, 502, 503, 504):
            for form in forms:
                with self.subTest(code=code, form=form):
                    error = RuntimeError(
                        'account/rateLimits/read：failed to fetch codex rate limits: '
                        + form.format(code=code))
                    self.assertTrue(transient_connection_error(error))

    def test_rpc_diagnostics_identify_read_method_without_text_prefix(self):
        error = RuntimeError('HTTP status server error (503 Service Unavailable)')
        error.rpc_diagnostics = {'method': 'account/read'}
        self.assertTrue(transient_connection_error(error))

    def test_permanent_http_errors_override_generic_network_text(self):
        for code in (400, 401, 403, 404, 405, 422, 501, 505):
            with self.subTest(code=code):
                error = RuntimeError(
                    f'account/read: HTTP {code}; error sending request')
                self.assertFalse(transient_connection_error(error))

    def test_numeric_payload_and_other_methods_are_not_http_recovery(self):
        for message in (
            'account/rateLimits/read：服务器额度数据格式无效，值为 503',
            'account/rateLimits/read：账户发生变化，停止本轮发送',
            'account/read：仅允许 ChatGPT 订阅登录',
            'account/read：当前不是 Plus 账户',
            'model/list：HTTP 503',
            'turn/start：HTTP 429',
            'HTTP 503',
        ):
            with self.subTest(message=message):
                self.assertFalse(transient_connection_error(RuntimeError(message)))

    def test_stored_read_error_is_recognized_for_restart_migration(self):
        message = "account/rateLimits/read：{'code': -32603, 'message': 'HTTP 503 Service Unavailable'}"
        self.assertTrue(transient_connection_error(message))


class ReadHTTPRecoveryTests(unittest.TestCase):
    setUp = fixtures.QuotaTests.setUp
    tearDown = fixtures.QuotaTests.tearDown
    engine = fixtures.QuotaTests.engine

    def fail_round(self, engine):
        for index in range(ATTEMPT_LIMIT):
            engine.tick()
            if index + 1 < ATTEMPT_LIMIT:
                engine.sleeper(RETRY_INTERVAL)

    def test_temporary_http_error_uses_existing_bounded_recovery(self):
        fake = FakeRPC([RuntimeError('account/rateLimits/read：HTTP 503 Service Unavailable')])
        engine = self.engine(fake, 100)
        self.fail_round(engine)
        self.assertEqual(fake.reads, ATTEMPT_LIMIT)
        self.assertFalse(engine.connection()['stopped'])
        self.assertEqual(engine.next_poll, engine.clock() + RECOVERY_INTERVAL)
        engine.sleeper(RECOVERY_INTERVAL)
        engine.tick()
        self.assertEqual(fake.reads, ATTEMPT_LIMIT + 1)
        self.assertEqual(engine.next_poll, engine.clock() + RECOVERY_INTERVAL)
        self.assertEqual(fake.sent, 0)

    def test_authentication_failure_still_stops_without_periodic_probe(self):
        fake = FakeRPC([RuntimeError('account/read：HTTP 401 Unauthorized')])
        engine = self.engine(fake, 100)
        self.fail_round(engine)
        self.assertTrue(engine.connection()['stopped'])
        engine.sleeper(RECOVERY_INTERVAL)
        engine.tick()
        self.assertEqual(fake.reads, ATTEMPT_LIMIT)
        self.assertEqual(fake.sent, 0)

    def test_unknown_send_does_not_enable_http_recovery(self):
        fake = FakeRPC([])
        engine = self.engine(fake, 100)
        self.store.set('sending', True)
        for _ in range(ATTEMPT_LIMIT):
            engine.failed_connection(RuntimeError('account/rateLimits/read：HTTP 503 Service Unavailable'))
        self.assertTrue(engine.connection()['stopped'])
        self.assertNotIn('recoveryAt', engine.connection())


if __name__ == '__main__':
    unittest.main()
