"""Durable one-shot jobs; task execution never holds the quota engine lock."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
import tomllib
import uuid

from core import normalize, transient_connection_error
from rpc import RPC, configure_proxy, find_codex

EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max', 'ultra')
DEFAULT_MODELS = ['gpt-6-astra', 'gpt-6.1-sol']
MAX_CONTENT = 200_000


def windows_sandbox_mode(env):
    # Keep the user's Windows isolation backend when ignoring unrelated Codex
    # configuration. With no backend, Windows may reject every shell command.
    path = Path(env.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    config = tomllib.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    mode = config.get('windows', {}).get('sandbox', 'elevated')
    if mode not in ('elevated', 'unelevated', 'mxc'):
        raise ValueError('Windows 沙箱配置无效，请在官方 Codex 中完成沙箱设置')
    return mode


def available_models(rpc):
    models, cursor, seen = [], None, set()
    while True:
        page = rpc.call('model/list', {'cursor': cursor, 'limit': 100})
        models.extend(m for m in page['data'] if not m.get('hidden'))
        cursor = page.get('nextCursor')
        if not cursor:
            return models
        if cursor in seen:
            raise ValueError('模型目录分页重复')
        seen.add(cursor)


def absolute_path(value, *, directory=False):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError('请输入完整路径')
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError('请使用绝对路径')
    path = path.resolve()
    if directory:
        if not path.is_dir():
            raise ValueError('目录不存在，请先创建或选择已有目录')
    elif path.suffix.lower() != '.md':
        raise ValueError('任务文件必须是 .md 文档')
    return path


def read_markdown(path):
    path = absolute_path(path)
    if path.stat().st_size > MAX_CONTENT:
        raise ValueError('任务文档不能超过 200 KB')
    content = path.read_text(encoding='utf-8-sig')
    if not content.strip():
        raise ValueError('任务文档内容不能为空')
    return content


def choose_path(kind):
    """Run the native picker in a disposable process, outside HTTP/quota threads."""
    if kind not in ('directory', 'open', 'save'):
        raise ValueError('路径选择类型无效')
    script = '''import sys, tkinter as tk
from tkinter import filedialog
root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True)
kind=sys.argv[1]
options=dict(parent=root, title='选择任务目录' if kind=='directory' else '选择任务 MD 文档')
if kind=='directory': result=filedialog.askdirectory(**options)
elif kind=='open': result=filedialog.askopenfilename(**options,filetypes=[('Markdown','*.md')])
else: result=filedialog.asksaveasfilename(**options,defaultextension='.md',filetypes=[('Markdown','*.md')])
print(result); root.destroy()
'''
    import sys
    result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, kind],
                            capture_output=True, text=True, encoding='utf-8', timeout=180,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('无法打开路径选择窗口，请手动填写完整路径')
    return result.stdout.strip()


class QuotaNotFull(RuntimeError):
    pass


class TaskRunner:
    def __init__(self, rpc_factory=RPC, popen=subprocess.Popen):
        self.rpc_factory, self.popen = rpc_factory, popen

    def preflight(self, task):
        rpc, deadline = None, time.time() + 60
        try:
            for attempt in range(5):
                try:
                    if rpc is None:
                        rpc = self.rpc_factory(Path(task['cwd']), deadline=deadline)
                    account = rpc.account()
                    raw = rpc.limits()
                    break
                except Exception as exc:
                    if not transient_connection_error(exc) or attempt == 4 or time.time() + 10 >= deadline:
                        raise
                    if rpc is not None and (isinstance(exc, (TimeoutError, ConnectionError)) or not rpc.is_alive()):
                        rpc.close()
                        rpc = None
                    # Retry only read-only preflight, never a submitted task.
                    time.sleep(10)
            identity = account.get('email') or raw.get('accountId')
            if not identity or hashlib.sha256(identity.encode()).hexdigest()[:24] != task['account']:
                raise ValueError('账户与创建任务时不一致，请使用原账户登录')
            snap = normalize(raw)
            if snap['plan'] not in (None, 'plus'):
                raise ValueError('任务仅使用已验证的 Plus 订阅')
            if task['mode'] == 'quota' and snap['primary']['usedPercent'] != 0:
                raise QuotaNotFull('五小时额度尚未回满，继续等待')
            if snap['blocked']:
                raise ValueError('额度受限，本次任务未发送')
            models = available_models(rpc)
            model = next((m for m in models if m['model'] == task['model'] and not m.get('hidden')), None)
            if not model:
                raise ValueError('本机 Codex 未提供此模型：' + task['model'])
            supported = [e['reasoningEffort'] for e in model['supportedReasoningEfforts']]
            if task['effort'] not in supported:
                raise ValueError('该模型不支持所选推理强度，可用：' + ' / '.join(supported))
            if rpc.account() != account:
                raise ValueError('发送前账户发生变化，任务已停止')
        finally:
            if rpc is not None:
                rpc.close()

    @staticmethod
    def terminate(proc):
        if proc.poll() is not None:
            return
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    def run(self, task, update, cancelled):
        env = os.environ.copy()
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            env.pop(key, None)
        # A nested CLI must create its own workspace-write session, rather than
        # attach to the dashboard launcher/parent agent's permission profile.
        # Keep CODEX_HOME and network restrictions; no sandbox bypass is used.
        for key in ('CODEX_PERMISSION_PROFILE', 'CODEX_THREAD_ID', 'CODEX_SESSION_ID',
                    'CODEX_APP_TOOLS_PIPE_PATH', 'CODEX_TOOL_CALL_ID',
                    'CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY'):
            env.pop(key, None)
        configure_proxy(env)
        args = [find_codex(), '--no-daemon', 'exec', '--ignore-user-config', '--json', '--color', 'never',
                '--skip-git-repo-check', '-C', task['cwd'],
                '-m', task['model']]
        for setting in ('model_provider="openai"', 'forced_login_method="chatgpt"',
                        'approval_policy="never"', 'service_tier="default"',
                        'default_permissions=":workspace"',
                        'features.respect_system_proxy=false', 'features.system_proxy_fallback=false',
                        'model_reasoning_effort=' + json.dumps(task['effort'])):
            args.extend(['-c', setting])
        if os.name == 'nt':
            args.extend(['-c', 'windows.sandbox=' + json.dumps(windows_sandbox_mode(env))])
        schema = {'type': 'object', 'properties': {
            'taskStatus': {'type': 'string', 'enum': ['completed', 'failed']},
            'summary': {'type': 'string'}}, 'required': ['taskStatus', 'summary'], 'additionalProperties': False}
        schema_temp = tempfile.TemporaryDirectory(prefix='quota-task-schema-')
        schema_path = Path(schema_temp.name) / 'result.json'
        schema_path.write_text(json.dumps(schema), encoding='utf-8')
        args.extend(['--output-schema', str(schema_path)])
        args.append('-')
        try:
            proc = self.popen(args, cwd=task['cwd'], env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding='utf-8', errors='replace', bufsize=1,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception:
            schema_temp.cleanup()
            raise
        events, errors = queue.Queue(maxsize=256), []
        reader_stop = threading.Event()
        def read_events():
            try:
                for line in proc.stdout:
                    while not reader_stop.is_set():
                        try:
                            events.put(line, timeout=.5)
                            break
                        except queue.Full:
                            continue
                    if reader_stop.is_set():
                        return
            finally:
                if not reader_stop.is_set():
                    events.put(None)
        def read_errors():
            for line in proc.stderr:
                errors.append(line)
                del errors[:-20]
        threading.Thread(target=read_events, daemon=True).start()
        threading.Thread(target=read_errors, daemon=True).start()
        result, completed, failure, report = '', False, None, None
        deadline = time.monotonic() + 86400
        try:
            proc.stdin.write(task['content'] + '\n\n'
                             '执行完毕后按指定 JSON 格式报告实际结果。只有任务要求全部完成才将 taskStatus 设为 completed；'
                             '权限拦截、命令无法执行或任务未完成必须设为 failed，并在 summary 说明原因。'
                             '不要将仅回复消息或会话结束当作任务完成。summary 使用自然语言，包含完整成果。')
            proc.stdin.close()
            while True:
                if cancelled():
                    raise InterruptedError('任务已中断；已产生的文件修改请在工作目录检查')
                if time.monotonic() > deadline:
                    raise TimeoutError('执行超过 24 小时，结果未知，未自动重试')
                try:
                    line = events.get(timeout=.5)
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get('type')
                if kind == 'thread.started':
                    update(threadId=event.get('thread_id'), progress='正在执行')
                elif kind in ('item.started', 'item.updated', 'item.completed'):
                    item = event.get('item', {})
                    label = {'command_execution': '正在执行命令', 'file_change': '正在修改文件',
                             'reasoning': '正在思考', 'agent_message': '正在生成回复'}.get(item.get('type'))
                    if label:
                        update(progress=label)
                    if kind == 'item.completed' and item.get('type') == 'agent_message':
                        text = item.get('text', '')
                        try:
                            candidate = json.loads(text)
                            if isinstance(candidate, dict) and candidate.get('taskStatus') in ('completed', 'failed') and isinstance(candidate.get('summary'), str):
                                report = candidate
                                text = candidate['summary']
                        except ValueError:
                            pass
                        result = (result + text + '\n')[-200_000:]
                        update(result=result)
                elif kind == 'turn.completed':
                    completed = True
                    failure = None
                    update(progress='任务已完成', usage=event.get('usage'))
                elif kind in ('turn.failed', 'error'):
                    failure = str(event.get('error', {}).get('message') if isinstance(event.get('error'), dict)
                                  else event.get('message') or event.get('error') or 'Codex 执行失败')
            code = proc.wait(timeout=10)
            if failure or code != 0 or not completed:
                raise RuntimeError(failure or (''.join(errors)[-2000:] if code else '') or
                                   '未收到完成确认，结果未知，未自动重试')
            if not report:
                raise RuntimeError('未收到任务结果报告，结果未知，未自动重试')
            if report['taskStatus'] != 'completed':
                raise RuntimeError(report['summary'] or 'Codex 报告任务未完成')
            return result
        finally:
            reader_stop.set()
            self.terminate(proc)
            for stream in (proc.stdout, proc.stderr):
                stream.close()
            schema_temp.cleanup()


class TaskScheduler:
    def __init__(self, store, runner=None, clock=time.time, catalog_factory=RPC, default_directory=None):
        self.store, self.clock = store, clock
        # User documents belong to per-user app data, never the source/install folder.
        self.default_directory = Path(default_directory or
            Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) /
            'QuotaStarter' / 'tasks').resolve()
        self.runner = runner or TaskRunner()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.create_lock = threading.Lock()
        self.thread = None
        self.catalog_factory = catalog_factory
        self.catalog_lock = threading.Lock()
        self.model_cache = None
        self.model_cache_at = 0
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scheduled_tasks '
                       '(id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL, data TEXT NOT NULL)')
            # Claim is durable before launch. A crash must never repeat a submitted job.
            for row in db.execute("SELECT * FROM scheduled_tasks WHERE status='running'").fetchall():
                data = json.loads(row['data'])
                data.update(status='failed', finishedAt=self.clock(),
                            progress='后台已退出，结果未知，请检查工作目录；不会自动重试')
                self.write(db, data)
                self.event(db, data, 'recovered', reason=data['progress'])

    @contextmanager
    def db(self):
        # Commit task transitions and their audit event together; Store also
        # archives committed events to the existing monthly JSONL log.
        with self.store.connect() as db:
            yield db

    def event(self, db, task, action, **extra):
        detail = {key: task[key] for key in ('id', 'title', 'status', 'cwd', 'model', 'effort', 'mode', 'runAt') if key in task}
        detail.update(action=action, **extra)
        db.execute('INSERT INTO events(time,kind,detail) VALUES (?,?,?)',
                   (self.clock(), 'task', json.dumps(detail, ensure_ascii=False)))

    @staticmethod
    def write(db, task):
        db.execute('INSERT OR REPLACE INTO scheduled_tasks VALUES (?,?,?,?)',
                   (task['id'], task['status'], task['createdAt'], json.dumps(task, ensure_ascii=False)))

    def settings(self):
        return {'directory': self.store.get('taskDirectory') or str(self.default_directory),
                'models': list(dict.fromkeys(DEFAULT_MODELS + self.store.get('taskModels', [])))}

    def catalog(self, force=False):
        if not force and self.model_cache is not None and self.clock() - self.model_cache_at < 300:
            return self.model_cache
        if not self.catalog_lock.acquire(False):
            raise RuntimeError('正在检测本机模型，请稍后再试')
        rpc = None
        try:
            cwd = self.store.path.parent / 'task-catalog'
            cwd.mkdir(exist_ok=True)
            rpc = self.catalog_factory(cwd, deadline=time.time() + 40)
            models = available_models(rpc)
            result = [{'model': m['model'], 'label': m.get('displayName') or m['model'],
                       'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])]}
                      for m in models]
            if not result:
                raise ValueError('本机 Codex 未返回可选模型')
            self.model_cache, self.model_cache_at = result, self.clock()
            self.store.log('task', {'action': 'models', 'models': [m['model'] for m in result]}, now=self.clock())
            return result
        finally:
            if rpc is not None:
                rpc.close()
            self.catalog_lock.release()

    def save_settings(self, body):
        directory = body.get('directory', '')
        if not isinstance(directory, str):
            raise ValueError('默认目录格式无效')
        if directory:
            directory = directory.strip()
            if Path(directory).resolve() != self.default_directory:
                directory = str(absolute_path(directory, directory=True))
            else:
                directory = str(self.default_directory)
        models = body.get('models', [])
        if not isinstance(models, list) or len(models) > 50 or any(
                not isinstance(m, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}', m) for m in models):
            raise ValueError('模型 ID 格式无效')
        if directory == str(self.default_directory):
            self.default_directory.mkdir(parents=True, exist_ok=True)
        self.store.set('taskDirectory', directory)
        self.store.set('taskModels', list(dict.fromkeys(models)))
        self.store.log('task', {'action': 'settings', **self.settings()}, now=self.clock())

    def snapshot(self, detail_id=None):
        if detail_id is not None and (not isinstance(detail_id, str) or not re.fullmatch(r'[a-f0-9]{32}', detail_id)):
            raise ValueError('任务标识无效')
        with self.db() as db:
            if detail_id:
                row = db.execute("SELECT data FROM scheduled_tasks WHERE id=? AND status!='deleted'", (detail_id,)).fetchone()
                if not row:
                    raise ValueError('任务不存在')
                return json.loads(row[0])
            rows = db.execute("SELECT data FROM scheduled_tasks WHERE status!='deleted' ORDER BY "
                              "CASE WHEN status IN ('waiting','running') THEN 0 ELSE 1 END, created DESC LIMIT 200").fetchall()
            total = db.execute("SELECT COUNT(*) FROM scheduled_tasks WHERE status!='deleted'").fetchone()[0]
            counts = {r[0]: r[1] for r in db.execute("SELECT status,COUNT(*) FROM scheduled_tasks WHERE status!='deleted' GROUP BY status")}
        tasks = [{k: v for k, v in json.loads(r[0]).items() if k not in ('content', 'result')}
                 for r in rows]
        return {'tasks': tasks, 'total': total, 'counts': counts, 'settings': self.settings()}

    def create(self, body):
        return self.save_task(body)

    def edit(self, body):
        return self.save_task(body, editing=True)

    def save_task(self, body, editing=False):
        task_id, now = body.get('id'), self.clock()
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('任务标识无效，请重新打开新建窗口')
        # Browser/session retries carry the same ID, including after lost responses.
        with self.create_lock, self.db() as db:
            # Serialize edits with the scheduler's durable claim, so a running
            # task can never execute a mixture of old and new fields.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
            original = json.loads(row[0]) if row else None
            if editing and (not original or original['status'] != 'waiting' or original.get('submittedAt')):
                raise ValueError('只有尚未执行的等待中任务可以修改，请刷新任务状态')
            if row and not editing:
                return json.loads(row[0])
            mode, at = body.get('mode'), body.get('runAt')
            if mode not in ('time', 'quota'):
                raise ValueError('请选择开始方式')
            if mode == 'time' and (isinstance(at, bool) or not isinstance(at, (int, float)) or
                                   not math.isfinite(at) or not now < at <= now + 366 * 86400):
                raise ValueError('请选择未来一年内的开始时间（北京时间）')
            model, effort = body.get('model'), body.get('effort')
            if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}', model):
                raise ValueError('模型 ID 格式无效')
            if effort not in EFFORTS:
                raise ValueError('推理强度无效')
            title = body.get('title', '')
            if not isinstance(title, str) or not title.strip() or len(title) > 100:
                raise ValueError('请输入 1 至 100 字的任务名称')
            current = self.store.get('current') or {}
            account = current.get('account')
            if editing:
                account = original['account']
            if not account:
                raise ValueError('请先成功检测一次额度，确认创建任务的账户')
            path_value = original['path'] if editing else body.get('path')
            if not path_value:
                directory = self.settings()['directory']
                if Path(directory) == self.default_directory:
                    self.default_directory.mkdir(parents=True, exist_ok=True)
                path_value = str(Path(directory) / (time.strftime('%Y%m%d-%H%M%S') + '-' + task_id[:8] + '.md'))
            path = absolute_path(path_value)
            if not isinstance(body.get('cwd'), str) or not body['cwd'].strip():
                raise ValueError('请选择执行目录；任务只能修改该目录内的文件，文档保存目录不代表目标项目目录')
            cwd = absolute_path(body['cwd'], directory=True)
            source = 'snapshot' if editing else body.get('source', 'new')
            if source == 'existing':
                content = read_markdown(str(path))
            elif source == 'new' or editing and source == 'snapshot':
                content = body.get('content')
                if not isinstance(content, str) or not content.strip() or len(content.encode('utf-8')) > MAX_CONTENT:
                    raise ValueError('请输入不超过 200 KB 的任务内容')
                if not path.parent.is_dir():
                    raise ValueError('文档所在目录不存在')
                if source == 'new' and path.exists():
                    raise ValueError('文件已存在，请另选文件名；读取已有文件请切换至“已有 MD”')
            else:
                raise ValueError('文档来源无效')
            task = dict(id=task_id, title=title.strip(), status='waiting', createdAt=now,
                        mode=mode, runAt=at if mode == 'time' else None, model=model, effort=effort,
                        path=str(path), cwd=str(cwd), content=content, account=account,
                        progress='等待开始时间' if mode == 'time' else '等待五小时额度回满', result='')
            if editing:
                task['createdAt'] = original['createdAt']
                task['updatedAt'] = now
            if source == 'new':
                # Exclusive create never overwrites a user's document, even in a race.
                with path.open('x', encoding='utf-8', newline='\n') as output:
                    output.write(content)
            self.write(db, task)
            self.event(db, task, 'edited' if editing else 'created')
        return task

    def cancel(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('任务标识无效')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            task = json.loads(row[0])
            if task['status'] == 'waiting':
                task.update(status='cancelled', finishedAt=self.clock(), progress='已取消')
                self.event(db, task, 'cancelled', reason=task['progress'])
            elif task['status'] == 'running':
                if task.get('cancelRequested'):
                    return
                task.update(cancelRequested=True, progress='正在中断，已产生的修改会保留')
                self.event(db, task, 'cancel_requested')
            self.write(db, task)

    def update(self, task_id, **fields):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
            task = json.loads(row[0])
            previous = task.copy()
            task.update(fields, updatedAt=self.clock())
            self.write(db, task)
            if fields.get('status') and fields['status'] != previous['status']:
                self.event(db, task, fields['status'], reason=task.get('progress', ''),
                           resultPath=task.get('resultPath'))
            elif fields.get('submittedAt') and not previous.get('submittedAt'):
                self.event(db, task, 'submitted')
            elif fields.get('threadId') and fields['threadId'] != previous.get('threadId'):
                self.event(db, task, 'session_started', threadId=fields['threadId'])
            elif fields.get('progress') in ('正在执行命令', '正在修改文件') and fields['progress'] != previous.get('progress'):
                self.event(db, task, 'execution_step', message=fields['progress'])

    def delete(self, task_id):
        if not isinstance(task_id, str) or not re.fullmatch(r'[a-f0-9]{32}', task_id):
            raise ValueError('任务标识无效')
        with self.db() as db:
            # Serialize with the scheduler claim so a deleted waiting task can
            # never start. Retain a tombstone to reject stale creation retries.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM scheduled_tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ValueError('任务不存在')
            task = json.loads(row[0])
            if task['status'] == 'running':
                raise ValueError('运行中的任务请先中断，结束后再删除')
            if task['status'] == 'deleted':
                return
            task.update(status='deleted', deletedAt=self.clock())
            self.write(db, task)
            self.event(db, task, 'deleted')

    def due(self, task, current, now):
        if task.get('retryAt', 0) > now:
            return False
        if task['mode'] == 'time':
            return task['runAt'] <= now
        if current.get('account') != task['account']:
            return False
        primary = current.get('primary') or {}
        checked = current.get('checkedAt', 0)
        return (now - checked <= 90 and primary.get('usedPercent') == 0 or
                isinstance(primary.get('resetsAt'), (float, int)) and now >= primary['resetsAt'])

    def tick(self):
        if (self.stop.is_set() or self.store.get('paused', False) or self.store.get('sending', False)
                or not self.lock.acquire(False)):
            return
        try:
            now, current = self.clock(), self.store.get('current') or {}
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                rows = db.execute("SELECT data FROM scheduled_tasks WHERE status='waiting' ORDER BY created").fetchall()
                task = next((json.loads(r[0]) for r in rows if self.due(json.loads(r[0]), current, now)), None)
                if not task:
                    return
                task.update(status='running', startedAt=now, progress='正在校验账户、额度和模型')
                self.write(db, task)
                self.event(db, task, 'started')
            task_id = task['id']
            cancelled = lambda: self.stop.is_set() or bool(self.snapshot(task_id).get('cancelRequested'))
            try:
                self.runner.preflight(task)
                if cancelled():
                    raise InterruptedError('已取消，任务未发送')
                self.update(task_id, submittedAt=self.clock(), progress='正在启动 Codex')
                result = self.runner.run(task, lambda **f: self.update(task_id, **f), cancelled)
                if cancelled():
                    raise InterruptedError('任务已中断，请检查工作目录中的实际结果')
                # Results use a unique sibling file; the prompt is an immutable snapshot.
                result_path = Path(task['path']).with_name(Path(task['path']).stem + '-' + task_id[:8] + '.result.md')
                try:
                    with result_path.open('x', encoding='utf-8', newline='\n') as output:
                        output.write(result)
                except OSError as exc:
                    self.update(task_id, status='completed', finishedAt=self.clock(), result=result,
                                progress='已完成，结果文档保存失败，请从详情复制回复：' + str(exc)[:1000])
                    return
                self.update(task_id, status='completed', finishedAt=self.clock(), result=result,
                            resultPath=str(result_path), progress='已完成')
            except QuotaNotFull as exc:
                if cancelled():
                    self.update(task_id, status='cancelled', finishedAt=self.clock(), progress='已取消，任务未发送')
                else:
                    self.update(task_id, status='waiting', retryAt=self.clock() + 60, progress=str(exc))
            except InterruptedError as exc:
                self.update(task_id, status='cancelled', finishedAt=self.clock(), progress=str(exc))
            except Exception as exc:
                self.update(task_id, status='failed', finishedAt=self.clock(), progress=str(exc)[:3000])
        finally:
            self.lock.release()

    def start(self):
        if self.thread:
            return
        def run():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    # A storage outage must not kill the scheduler or touch quota polling.
                    pass
                self.stop.wait(.5)
        self.thread = threading.Thread(target=run, daemon=True, name='scheduled-tasks')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=8)
