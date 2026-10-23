"""Transport routing, safe retry classification and CLI selection regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from core import Engine, transient_connection_error
from rpc import RPC,configure_proxy,find_codex,CodexCompatibilityError

class ProxyTests(unittest.TestCase):
    def test_local_timeout_closes_live_transport_before_retry(self):
        from unittest.mock import Mock
        import tempfile
        from pathlib import Path
        from core import Store
        with tempfile.TemporaryDirectory() as root:
            engine = Engine(Store(Path(root) / 'db'), Path(root) / 'empty', clock=lambda: 100)
            transport = engine.rpc = Mock()
            transport.is_alive.return_value = True
            error = TimeoutError('account/rateLimits/read 超时')
            error.rpc_diagnostics = {'method': 'account/rateLimits/read', 'processAlive': True}
            engine.failed_connection(error)
            transport.close.assert_called_once()
            self.assertIsNone(engine.rpc)
            self.assertEqual(engine.next_poll, 110)

    def test_returned_network_error_keeps_transport_until_retry_budget_exhausted(self):
        from unittest.mock import Mock
        import tempfile
        from pathlib import Path
        from core import Store
        with tempfile.TemporaryDirectory() as root:
            engine = Engine(Store(Path(root) / 'db'), Path(root) / 'empty', clock=lambda: 100)
            engine.rpc = Mock()
            engine.rpc.is_alive.return_value = True
            err = RuntimeError('error sending request')
            err.rpc_diagnostics = {'method': 'account/rateLimits/read', 'processAlive': True}
            engine.failed_connection(err)
            engine.rpc.close.assert_not_called()
            transport = engine.rpc
            engine.failed_connection(err)
            transport.close.assert_not_called()
            engine.failed_connection(err)
            engine.failed_connection(err)
            transport.close.assert_not_called()
            engine.failed_connection(err)
            transport.close.assert_called_once()
            self.assertIsNone(engine.rpc)
            self.assertEqual(engine.connection()['recoveryAt'], 130)

    def test_windows_proxy_replaces_stale_environment_before_spawn(self):
        env = {'HTTPS_PROXY': 'http://127.0.0.1:9999', 'ALL_PROXY': 'http://127.0.0.1:9999', 'NO_PROXY': 'example.local'}
        with patch('rpc.os.name', 'nt'), patch('rpc.urllib.request.getproxies_registry',
                  return_value={'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}, create=True):
            route = configure_proxy(env)
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:7897')
        self.assertEqual(env['HTTP_PROXY'], env['HTTPS_PROXY'])
        self.assertNotIn('ALL_PROXY', env)
        self.assertIn('127.0.0.1', env['NO_PROXY'])
        self.assertEqual(route['source'], 'windows')

    def test_explicit_override_wins_and_diagnostics_never_include_credentials(self):
        env = {'QUOTA_HTTP_PROXY': 'http://secret:password@127.0.0.1:7897'}
        route = configure_proxy(env)
        self.assertEqual(env['HTTPS_PROXY'], env['QUOTA_HTTP_PROXY'])
        self.assertEqual(route['proxy'], 'http://127.0.0.1:7897')
        self.assertNotIn('password', json.dumps(route))

    def test_environment_survives_when_system_proxy_disabled(self):
        env = {'HTTPS_PROXY': 'http://127.0.0.1:7897'}
        with patch('rpc.urllib.request.getproxies_registry', return_value={}, create=True):
            configure_proxy(env)
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:7897')

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
            'account/read: workspace routing discovery unauthorized (401)',
            'account/read: selected workspace missing from routing discovery',
            'account/read: workspace routing discovery missing backend origin',
            'account changed during workspace routing discovery',
        ):
            with self.subTest(message=message):
                self.assertFalse(transient_connection_error(RuntimeError(message)))

    def test_stored_read_error_is_recognized_for_restart_migration(self):
        message = "account/rateLimits/read：{'code': -32603, 'message': 'HTTP 503 Service Unavailable'}"
        self.assertTrue(transient_connection_error(message))

class RuntimeSelectionTests(unittest.TestCase):
    def test_npm_stable_is_selected_before_desktop_path(self):
        with tempfile.TemporaryDirectory() as temp:
            package=Path(temp)/'npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64/bin/codex.exe'
            package.parent.mkdir(parents=True);package.touch()
            with patch.dict('os.environ',{'APPDATA':temp},clear=True),patch('rpc.shutil.which',return_value=r'C:\desktop\codex.exe'):
                self.assertEqual(find_codex(),str(package))
            with patch.dict('os.environ',{'APPDATA':temp,'QUOTA_CODEX_EXE':str(package)},clear=True):
                self.assertEqual(find_codex(),str(package))

    def test_spawn_resolves_proxy_before_initializing_http_clients(self):
        for proxy in ('http://127.0.0.1:7897',None):
            with self.subTest(proxy=proxy),tempfile.TemporaryDirectory() as home:
                env={'CODEX_HOME':home,'USERPROFILE':home}
                if proxy:env['QUOTA_HTTP_PROXY']=proxy
                process=Mock();process.poll.return_value=0
                with patch.dict('os.environ',env,clear=True),patch('rpc.urllib.request.getproxies_registry',return_value={},create=True),patch('rpc.find_codex',return_value=r'C:\npm\codex.exe'),patch('rpc.subprocess.Popen',return_value=process) as spawn,patch.object(RPC,'call',return_value={}),patch('rpc.threading.Thread'):
                    rpc=RPC(home)
                    arguments=spawn.call_args.args[0];child=spawn.call_args.kwargs['env']
                    self.assertIn('features.respect_system_proxy=false',arguments)
                    self.assertIn('features.system_proxy_fallback=false',arguments)
                    if proxy:self.assertEqual(child['HTTPS_PROXY'],proxy)
                    self.assertNotIn('OPENAI_API_KEY',child)
                    rpc.close()

    def test_diagnostics_include_route_without_credentials(self):
        env={'QUOTA_HTTP_PROXY':'http://name:secret@127.0.0.1:7897'}
        self.assertNotIn('secret',json.dumps(configure_proxy(env)))

class CompatibilityStopTests(unittest.TestCase):
    def test_incompatible_cli_stops_without_repeated_network_recovery(self):
        from core import Store
        from test_quota import FakeRPC,raw
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'state.sqlite3');clock=[100]
            fake=FakeRPC([CodexCompatibilityError('Codex CLI 不兼容；请升级当前程序'),raw(149)])
            e=Engine(store,Path(folder)/'workspace',rpc_factory=lambda _:fake,clock=lambda:clock[0])
            e.tick(check_only=True)
            self.assertEqual(fake.reads,1);self.assertTrue(e.connection()['stopped']);self.assertTrue(e.connection()['upgradeRequired']);self.assertNotIn('recoveryAt',e.connection())
            clock[0]=1000;e.tick(check_only=True);self.assertEqual(fake.reads,1)
            e.reconnect();e.tick(check_only=True);self.assertTrue(e.linked);self.assertEqual(fake.sent,0)
