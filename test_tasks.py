"""Durable task dispatch, real process streaming and quota isolation regressions."""
import hashlib
import http.client
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import os
import threading
import time
import unittest
from unittest.mock import patch, Mock
import uuid

from app import create_server
from core import Store, Engine
from tasks import TaskRunner, TaskScheduler, QuotaNotFull, read_markdown, windows_sandbox_mode
from test_quota import FakeRPC, raw, csrf_token


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.error = None

    def preflight(self, task):
        if self.error:
            raise self.error

    def run(self, task, update, cancelled):
        self.calls.append(task['id'])
        update(progress='正在生成回复', result='测试结果')
        return '测试结果'


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Windows runners may expose TEMP through an 8.3 alias (RUNNER~1).
        # Compare canonical paths, as the application does for saved directories.
        self.root = Path(self.tmp.name).resolve()
        self.now = 1000
        self.store = Store(self.root / 'quota.sqlite3')
        self.store.set('current', dict(account='test-account', checkedAt=1000,
                                      primary=dict(usedPercent=20, resetsAt=2000)))
        self.runner = FakeRunner()
        self.tasks = TaskScheduler(self.store, self.runner, clock=lambda: self.now,
                                   default_directory=self.root / 'default-tasks')

    def tearDown(self):
        self.tasks.close()
        self.tmp.cleanup()

    def body(self, **values):
        task_id = uuid.uuid4().hex
        return dict(id=task_id, title='整理笔记', mode='time', runAt=self.now + 1,
                    path=str(self.root / (task_id + '.md')), content='请整理资料',
                    model='gpt-6-astra', effort='ultra', **values)

    def create(self, **values):
        body = self.body()
        body.update(values)
        return self.tasks.create(body)

    def test_creation_is_idempotent_and_never_overwrites_documents(self):
        body = self.body()
        first = self.tasks.create(body)
        self.assertEqual(self.tasks.create(body), first)
        self.assertEqual(self.tasks.snapshot()['total'], 1)
        self.assertEqual(Path(body['path']).read_text(encoding='utf-8'), body['content'])
        with self.assertRaisesRegex(ValueError, '文件已存在'):
            self.create(path=body['path'])
        self.assertEqual(Path(body['path']).read_text(encoding='utf-8'), body['content'])

    def test_existing_file_is_snapshotted_without_modification(self):
        path = self.root / '已有.md'
        path.write_text('\ufeff原任务', encoding='utf-8')
        task = self.create(source='existing', path=str(path))
        path.write_text('稍后修改', encoding='utf-8')
        self.assertEqual(self.tasks.snapshot(task['id'])['content'], '原任务')
        self.assertEqual(path.read_text(encoding='utf-8'), '稍后修改')

    def test_edit_preserves_identity_and_document_and_executes_new_snapshot(self):
        task = self.create()
        self.tasks.update(task['id'], retryAt=9999)
        body = dict(task, title='修改后的任务', content='新内容', mode='quota', effort='high')
        changed = self.tasks.edit(body)
        self.assertEqual(changed['id'], task['id'])
        self.assertEqual(changed['createdAt'], task['createdAt'])
        self.assertEqual(changed['account'], task['account'])
        self.assertEqual(changed['content'], '新内容')
        self.assertNotIn('retryAt', changed)
        self.assertEqual(Path(task['path']).read_text(encoding='utf-8'), task['content'])
        self.assertEqual(self.tasks.snapshot()['total'], 1)
        self.now = 2000
        self.tasks.tick()
        self.assertEqual(self.runner.calls, [task['id']])

    def test_edit_rejects_started_finished_invalid_and_missing_tasks(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.tasks.edit(dict(task, content=''))
        self.assertEqual(self.tasks.snapshot(task['id']), task)
        for state in ('running', 'completed', 'failed', 'cancelled'):
            self.tasks.update(task['id'], status=state)
            with self.assertRaisesRegex(ValueError, '尚未执行'):
                self.tasks.edit(dict(task, title='不允许'))
        self.tasks.update(task['id'], status='waiting', submittedAt=1000)
        with self.assertRaises(ValueError):
            self.tasks.edit(task)
        with self.assertRaises(ValueError):
            self.tasks.edit(dict(task, id=uuid.uuid4().hex))

    def test_default_directory_custom_model_and_filenames_persist(self):
        self.tasks.save_settings({'directory': str(self.root), 'models': ['gpt-6-sol', 'gpt-6-sol']})
        a, b = self.create(path=''), self.create(path='')
        self.assertNotEqual(a['path'], b['path'])
        restarted = TaskScheduler(self.store, self.runner)
        self.assertEqual(restarted.settings()['models'], ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-sol'])
        self.assertEqual(restarted.settings()['directory'], str(self.root.resolve()))

    def test_invalid_time_model_effort_content_and_paths_are_rejected_before_write(self):
        cases = [dict(runAt=1000), dict(runAt=True), dict(runAt=float('nan')), dict(runAt=float('inf')),
                 dict(runAt=self.now + 367 * 86400), dict(mode='other'), dict(effort='none'),
                 dict(model='-bad'), dict(model='foo;bar'), dict(content=''), dict(content='中' * 70000),
                 dict(title=''), dict(title=2), dict(path='relative.md'), dict(path=str(self.root / 'x.txt')),
                 dict(cwd=str(self.root / 'missing')), dict(source='other')]
        for case in cases:
            with self.subTest(case=str(case)[:100]), self.assertRaises((ValueError, OSError)):
                self.create(**case)
        self.assertEqual(self.tasks.snapshot()['total'], 0)
        self.assertEqual(list(self.root.glob('*.md')), [])

    def test_settings_validate_before_mutation(self):
        for models in [['model id'], 'abc', [1], ['gpt-a'] * 51]:
            with self.assertRaises(ValueError):
                self.tasks.save_settings({'directory': str(self.root), 'models': models})
        self.assertEqual(self.tasks.settings()['directory'], str(self.root / 'default-tasks'))

    def test_default_documents_use_local_appdata_and_are_created_when_needed(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'Local')}):
            scheduler = TaskScheduler(self.store, self.runner, clock=lambda: self.now)
        expected = self.root / 'Local' / 'QuotaStarter' / 'tasks'
        self.assertEqual(scheduler.settings()['directory'], str(expected))
        self.assertFalse(expected.exists())
        task = scheduler.create(self.body() | {'path': ''})
        self.assertEqual(Path(task['path']).parent, expected)
        self.assertEqual(Path(task['path']).read_text(encoding='utf-8'), '请整理资料')
        scheduler.save_settings({'directory': '', 'models': []})
        self.assertEqual(scheduler.settings()['directory'], str(expected))

    def test_catalog_filters_hidden_models_preserves_efforts_and_caches_independent_reads(self):
        rpc = Mock()
        rpc.call.return_value = {'data': [
            {'model': 'gpt-6-astra', 'displayName': 'GPT-6 Astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}]},
            {'model': 'hidden', 'hidden': True}]}
        scheduler = TaskScheduler(self.store, self.runner, clock=lambda: self.now, catalog_factory=lambda *_, **__: rpc)
        self.assertEqual(scheduler.catalog(), [{'model': 'gpt-6-astra', 'label': 'GPT-6 Astra', 'efforts': ['ultra']}])
        scheduler.catalog(); self.assertEqual(rpc.call.call_count, 1)
        scheduler.catalog(force=True); self.assertEqual(rpc.call.call_count, 2)
        self.assertEqual(self.runner.calls, [])

    def test_no_account_does_not_create_file(self):
        self.store.set('current', None)
        with self.assertRaisesRegex(ValueError, '检测一次'):
            self.create()
        self.assertEqual(list(self.root.glob('*.md')), [])

    def test_due_job_executes_once_and_saves_result(self):
        task = self.create()
        self.tasks.tick()
        self.assertEqual(self.runner.calls, [])
        self.now += 1
        self.tasks.tick(); self.tasks.tick()
        final = self.tasks.snapshot(task['id'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['result'], '测试结果')
        self.assertEqual(Path(final['resultPath']).read_text(encoding='utf-8'), '测试结果')
        self.assertEqual(self.runner.calls, [task['id']])
        self.assertNotIn('content', self.tasks.snapshot()['tasks'][0])

    def test_result_file_failure_keeps_confirmed_completion_and_reply(self):
        task = self.create()
        path = Path(task['path'])
        result_path = path.with_name(path.stem + '-' + task['id'][:8] + '.result.md')
        result_path.write_text('已有结果', encoding='utf-8')
        self.now += 1
        self.tasks.tick()
        final = self.tasks.snapshot(task['id'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['result'], '测试结果')
        self.assertIn('保存失败', final['progress'])
        self.assertEqual(result_path.read_text(encoding='utf-8'), '已有结果')

    def test_recheck_reconnect_and_resume_refresh_both_windows_in_one_read(self):
        for action in ('check', 'reconnect', 'resume'):
            with self.subTest(action=action):
                fake = FakeRPC([raw(20000, used=7, weekly=33)])
                engine = Engine(self.store, self.root / 'empty', rpc_factory=lambda *_: fake,
                                clock=lambda: self.now)
                self.store.set('connection', {})
                self.store.set('current', {'primary': {'usedPercent': 99}, 'secondary': {'usedPercent': 100}})
                engine.next_poll = self.now + 60
                if action == 'check': engine.request_check()
                elif action == 'reconnect': engine.reconnect()
                else:
                    engine.set_paused(True)
                    engine.resume()
                self.assertEqual(engine.next_poll, 0)
                engine.tick()
                current = self.store.get('current')
                self.assertEqual(current['primary']['usedPercent'], 7)
                self.assertEqual(current['secondary']['usedPercent'], 33)
                self.assertEqual(fake.reads, 1)
                self.assertEqual(fake.sent, 0)
                self.assertEqual(engine.next_poll, self.now + 60)

    def test_resume_keeps_stopped_connection_and_unknown_send_guard(self):
        engine = Engine(self.store, self.root / 'empty')
        self.store.set('connection', {'stopped': True})
        self.store.set('sending', True)
        engine.set_paused(True)
        engine.resume()
        self.assertFalse(self.store.get('paused'))
        self.assertTrue(engine.connection()['stopped'])
        self.assertTrue(self.store.get('sending'))
        self.assertIsNone(engine.read_requested)

    def test_crash_and_unknown_failure_are_never_retried(self):
        task = self.create()
        self.tasks.update(task['id'], status='running', submittedAt=1001)
        restarted = TaskScheduler(self.store, self.runner, clock=lambda: 1002)
        final = restarted.snapshot(task['id'])
        self.assertEqual(final['status'], 'failed')
        self.assertIn('结果未知', final['progress'])
        restarted.tick()
        self.assertEqual(self.runner.calls, [])

    def test_preflight_failure_and_cancelled_job_never_send(self):
        a, b = self.create(), self.create()
        self.tasks.cancel(a['id'])
        self.runner.error = ValueError('模型不可用')
        self.now += 2
        self.tasks.tick()
        self.assertEqual(self.tasks.snapshot(a['id'])['status'], 'cancelled')
        self.assertEqual(self.tasks.snapshot(b['id'])['status'], 'failed')
        self.assertEqual(self.runner.calls, [])

    def test_quota_uses_fresh_full_read_or_reset_boundary_and_retains_account(self):
        task = self.create(mode='quota')
        self.tasks.tick(); self.assertEqual(self.runner.calls, [])
        current = self.store.get('current')
        current['primary']['usedPercent'] = 0
        current['checkedAt'] = 800
        self.store.set('current', current)
        self.tasks.tick(); self.assertEqual(self.runner.calls, [])
        current['checkedAt'] = self.now
        self.store.set('current', current)
        self.tasks.tick()
        self.assertEqual(self.runner.calls, [task['id']])
        other = self.create(mode='quota')
        current['account'] = 'other-account'
        self.store.set('current', current)
        self.tasks.tick()
        self.assertEqual(self.tasks.snapshot(other['id'])['status'], 'waiting')

    def test_reset_boundary_rechecks_without_assuming_full_and_cancel_survives_recheck(self):
        task = self.create(mode='quota')
        self.now = 2000
        self.runner.error = QuotaNotFull('尚未回满')
        self.tasks.tick()
        self.assertEqual(self.tasks.snapshot(task['id'])['status'], 'waiting')
        self.assertEqual(self.tasks.snapshot(task['id'])['retryAt'], 2060)
        self.tasks.cancel(task['id']); self.now = 2061
        self.tasks.tick()
        self.assertEqual(self.runner.calls, [])

    def test_pause_stops_dispatch_and_overdue_jobs_run_after_resume(self):
        task = self.create()
        self.store.set('paused', True); self.now += 100
        self.tasks.tick(); self.assertEqual(self.runner.calls, [])
        self.store.set('paused', False); self.tasks.tick()
        self.assertEqual(self.runner.calls, [task['id']])

    def test_long_job_does_not_block_quota_reads_or_creation_or_status(self):
        entered, release = threading.Event(), threading.Event()
        def run(task, update, cancelled):
            entered.set()
            release.wait(5)
            return 'done'
        self.runner.run = run
        task = self.create(); self.now += 1
        thread = threading.Thread(target=self.tasks.tick)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            start = time.monotonic()
            fake = FakeRPC([raw()])
            engine = Engine(self.store, self.root / 'empty', rpc_factory=lambda *_: fake,
                            clock=lambda: self.now)
            engine.tick(check_only=True)
            self.assertEqual(fake.reads, 1)
            self.assertEqual(engine.next_poll, self.now + 60)
            self.assertEqual(self.tasks.snapshot(task['id'])['status'], 'running')
            self.create()
            self.assertLess(time.monotonic() - start, 2)
        finally:
            release.set(); thread.join(5)

    def test_running_cancellation_keeps_no_completion_or_duplicate(self):
        def run(task, update, cancelled):
            self.tasks.cancel(task['id'])
            self.assertTrue(cancelled())
            raise InterruptedError('已中断')
        self.runner.run = run
        task = self.create(); self.now += 1
        self.tasks.tick(); self.tasks.tick()
        self.assertEqual(self.tasks.snapshot(task['id'])['status'], 'cancelled')

    def test_concurrent_ticks_claim_only_once(self):
        self.create(); self.now += 1
        workers = [threading.Thread(target=self.tasks.tick) for _ in range(8)]
        for t in workers: t.start()
        for t in workers: t.join()
        self.assertEqual(len(self.runner.calls), 1)

    def test_md_limit_and_non_utf8_are_rejected(self):
        path = self.root / 'task.md'
        path.write_bytes(b'x' * 200001)
        with self.assertRaises(ValueError): read_markdown(str(path))
        path.write_bytes(b'\xff\xff')
        with self.assertRaises(UnicodeError): read_markdown(str(path))


class RunnerTests(unittest.TestCase):
    def test_windows_isolation_backend_is_preserved_without_loading_other_user_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            for mode in ('elevated', 'unelevated', 'mxc'):
                path.write_text('[windows]\nsandbox="' + mode + '"\n', encoding='utf-8')
                self.assertEqual(windows_sandbox_mode({'CODEX_HOME': directory}), mode)
            path.write_text('[windows]\nsandbox="invalid"\n', encoding='utf-8')
            with self.assertRaises(ValueError): windows_sandbox_mode({'CODEX_HOME': directory})
    def test_read_only_preflight_retries_transient_failures_but_not_identity_errors(self):
        rpc = Mock()
        rpc.account.side_effect = [RuntimeError('account/read：workspace routing discovery failed'),
                                   {'email': 'changed@example.test'}]
        rpc.limits.return_value = raw()
        runner = TaskRunner(rpc_factory=lambda *_, **__: rpc)
        task = dict(cwd=str(Path.cwd()), account='original')
        with patch('tasks.time.sleep') as sleep, self.assertRaisesRegex(ValueError, '账户'):
            runner.preflight(task)
        sleep.assert_called_once_with(10)
        self.assertEqual(rpc.account.call_count, 2)
        rpc.close.assert_called_once()

    def test_preflight_validates_real_catalog_and_identity_without_model_inference(self):
        rpc = Mock()
        rpc.account.return_value = dict(email='test@example.test')
        rpc.limits.return_value = raw()
        rpc.call.return_value = {'data': [{'model': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'ultra'}]}]}
        task = dict(cwd=str(Path.cwd()), account=hashlib.sha256(b'test@example.test').hexdigest()[:24],
                    mode='time', model='gpt-6-astra', effort='ultra')
        runner = TaskRunner(rpc_factory=lambda *_, **__: rpc)
        runner.preflight(task)
        rpc.close.assert_called_once()
        self.assertEqual([c.args[0] for c in rpc.call.call_args_list], ['model/list'])
        task['effort'] = 'low'
        with self.assertRaisesRegex(ValueError, '不支持'): runner.preflight(task)
        task['account'] = 'changed'
        with self.assertRaisesRegex(ValueError, '账户'): runner.preflight(task)

    def run_process(self, events, exit_code=0, cancelled=lambda: False):
        program = 'import sys,json;sys.stdin.read();' + ';'.join('print(' + repr(json.dumps(e)) + ',flush=True)' for e in events) + f';sys.exit({exit_code})'
        captured = {}
        def popen(args, **kwargs):
            captured.update(args=args, env=kwargs['env'])
            return subprocess.Popen([sys.executable, '-c', program], **kwargs)
        runner = TaskRunner(popen=popen)
        updates = []
        with patch('tasks.find_codex', return_value='codex.exe'), patch('tasks.configure_proxy'), patch('tasks.windows_sandbox_mode', return_value='elevated'):
            result = runner.run(dict(cwd=str(Path.cwd()), model='gpt-6-astra', effort='ultra', content='task'),
                                lambda **fields: updates.append(fields), cancelled)
        return result, updates, captured

    def test_cli_stream_requires_final_completion_and_keeps_progress_result_and_model(self):
        result, updates, captured = self.run_process([
            {'type': 'thread.started', 'thread_id': 'thread'},
            {'type': 'item.started', 'item': {'type': 'command_execution'}},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps({'taskStatus': 'completed', 'summary': '结果'})}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 10}},
        ])
        self.assertEqual(result, '结果\n')
        self.assertTrue(any(u.get('progress') == '正在执行命令' for u in updates))
        self.assertIn('--ignore-user-config', captured['args'])
        self.assertIn('--no-daemon', captured['args'])
        self.assertIn('default_permissions=":workspace"', captured['args'])
        self.assertIn('model_reasoning_effort="ultra"', captured['args'])
        self.assertNotIn('OPENAI_API_KEY', captured['env'])
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', captured['args'])
        self.assertNotIn('CODEX_PERMISSION_PROFILE', captured['env'])
        self.assertNotIn('CODEX_APP_TOOLS_PIPE_PATH', captured['env'])
        self.assertIn('--output-schema', captured['args'])

    def test_zero_exit_without_completion_and_explicit_failure_are_not_success(self):
        for events, code in [([], 0), ([{'type': 'turn.failed', 'error': {'message': 'bad model'}}], 0),
                             ([{'type': 'turn.completed'}], 1)]:
            with self.subTest(events=events, code=code), self.assertRaises(RuntimeError):
                self.run_process(events, code)

    def test_conversation_completion_requires_an_explicit_success_report(self):
        for text in ['done', json.dumps({'taskStatus': 'failed', 'summary': '权限拦截，未能完成任务'})]:
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                self.run_process([{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
                                  {'type': 'turn.completed'}])

    def test_cancel_terminates_owned_process(self):
        with self.assertRaises(InterruptedError):
            self.run_process([], cancelled=lambda: True)


class TaskHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'quota.sqlite3')
        self.store.set('current', {'account': 'account'})
        self.engine = Engine(self.store, Path(self.tmp.name) / 'empty')
        self.tasks = TaskScheduler(self.store, FakeRunner())
        startup = Mock(); startup.apply.return_value = {'available': False, 'enabled': False}
        self.server = create_server(self.engine, port=18774, startup=startup, tasks=self.tasks)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.headers = {'Origin': 'http://127.0.0.1:18774', 'X-Quota-Token': csrf_token(self.request('GET', '/')[1]),
                        'Content-Type': 'application/json'}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', 18774)
        conn.request(method, path, headers=headers or {}, body=body)
        response = conn.getresponse(); result = response.status, response.read().decode(); conn.close()
        return result

    def post(self, route, body):
        return self.request('POST', '/api/tasks/' + route, json.dumps(body), self.headers)

    def test_api_lifecycle_and_csrf_and_bounded_bodies(self):
        task_id = uuid.uuid4().hex
        body = dict(id=task_id, title='测试', mode='time', runAt=time.time() + 100,
                    model='gpt-6.1-sol', effort='low', path=str(Path(self.tmp.name) / 'task.md'), content='测试')
        self.assertEqual(self.request('POST', '/api/tasks/create', json.dumps(body))[0], 403)
        self.assertEqual(self.post('create', body)[0], 200)
        self.assertEqual(self.post('create', body)[0], 200)
        self.assertEqual(json.loads(self.request('GET', '/api/tasks')[1])['total'], 1)
        self.assertEqual(self.post('detail', {'id': task_id})[0], 200)
        self.assertEqual(self.post('edit', dict(body, content='修改内容'))[0], 200)
        self.assertEqual(self.tasks.snapshot(task_id)['content'], '修改内容')
        self.assertEqual(self.post('cancel', {'id': task_id})[0], 200)
        self.assertEqual(self.post('edit', body)[0], 400)
        self.assertEqual(self.tasks.snapshot(task_id)['status'], 'cancelled')
        self.assertEqual(self.post('settings', {'directory': self.tmp.name, 'models': ['gpt-6-sol']})[0], 200)
        self.assertEqual(self.post('read', {'path': body['path']})[0], 200)
        self.assertEqual(self.request('POST', '/api/tasks/create', '{}', {**self.headers, 'Content-Length': '900000'})[0], 400)

    def test_invalid_requests_and_picker_cancellation_are_safe(self):
        self.assertEqual(self.post('create', [1])[0], 400)
        self.assertEqual(self.post('detail', {'id': 'missing'})[0], 400)
        self.assertEqual(self.post('detail', {'id': []})[0], 400)
        self.assertEqual(self.post('cancel', {'id': {}})[0], 400)
        self.assertEqual(self.post('read', {'path': 'relative.md'})[0], 400)
        with patch('app.choose_path', return_value=''):
            self.assertEqual(json.loads(self.post('pick', {'kind': 'open'})[1])['path'], '')
        self.assertEqual(self.request('GET', '/tasks.js')[0], 200)

    def test_open_directory_uses_saved_task_and_rejects_missing_directory(self):
        task_id = uuid.uuid4().hex
        body = dict(id=task_id, title='目录快捷操作', mode='time', runAt=time.time() + 100,
                    model='gpt-6.1-sol', effort='low', path=str(Path(self.tmp.name) / 'task.md'),
                    cwd=self.tmp.name, content='测试')
        self.assertEqual(self.post('create', body)[0], 200)
        with patch('app.os.startfile', create=True) as opened:
            self.assertEqual(self.request('POST', '/api/tasks/open-directory', json.dumps({'id': task_id}))[0], 403)
            self.assertEqual(self.post('open-directory', {'id': 'missing'})[0], 400)
            self.assertEqual(self.post('open-directory', {'id': task_id, 'path': 'C:\\untrusted'})[0], 200)
            opened.assert_called_once_with(str(Path(self.tmp.name).resolve()))
            with patch('app.Path.is_dir', return_value=False):
                self.assertEqual(self.post('open-directory', {'id': task_id})[0], 400)
            self.assertEqual(opened.call_count, 1)

    def test_model_catalog_endpoint_and_time_edit_queue_while_checking(self):
        with patch.object(self.tasks, 'catalog', return_value=[{'model': 'gpt-6-sol', 'label': 'GPT-6 Sol'}]) as catalog:
            self.assertEqual(self.post('models', {'force': True})[0], 200)
            catalog.assert_called_once_with(True)
        self.store.set('current', {'primary': {'resetsAt': time.time() + 100}})
        at = int((time.time() + 18000 + 1200) // 60 * 60)
        with self.engine.lock:
            code, body = self.request('POST', '/api/schedule', json.dumps({'refreshAt': at}), self.headers)
            self.assertEqual(code, 202)
            self.assertTrue(json.loads(body)['queued'])
        deadline = time.monotonic() + 2
        while not self.store.get('schedule') and time.monotonic() < deadline: time.sleep(.02)
        self.assertEqual(self.store.get('schedule')['sendAt'], at - 18000)


if __name__ == '__main__':
    unittest.main()
