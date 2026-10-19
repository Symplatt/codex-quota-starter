"""Official Codex App Server stdio client. Never reads/copies authentication files."""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib
from version import VERSION


class CodexCompatibilityError(RuntimeError):
    """The installed CLI rejected an interface required by this application."""


def find_codex():
    override = os.environ.get('QUOTA_CODEX_EXE')
    if override:
        p = Path(override)
        if p.is_file() and p.suffix.lower() == '.exe':
            return str(p)
        raise RuntimeError('QUOTA_CODEX_EXE 必须指向官方 codex.exe')
    direct = shutil.which('codex.exe')
    if direct:
        return direct
    roots = [Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex']
    shim = shutil.which('codex') or shutil.which('codex.cmd')
    if shim:
        roots.append(Path(shim).parent / 'node_modules/@openai/codex')
    for root in roots:
        matches = list(root.glob('node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe'))
        if matches:
            return str(matches[0])
    raise RuntimeError('未找到 Codex CLI。请安装官方 @openai/codex 后使用 codex login 登录 ChatGPT。')


class RPC:
    def __init__(self, cwd, deadline=None):
        self.deadline = deadline
        self.pending = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.sequence = 0
        self.events = queue.Queue()
        self.collect_events = False
        self.disconnected = threading.Event()
        env = os.environ.copy()
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            env.pop(key, None)
        self.codex_exe = find_codex()
        args = [self.codex_exe, 'app-server', '--listen', 'stdio://']
        overrides = ['model_provider="openai"', 'forced_login_method="chatgpt"',
                     'web_search="disabled"', 'project_doc_max_bytes=0',
                     'features.shell_tool=false', 'features.multi_agent=false',
                     'features.apps=false', 'features.plugins=false',
                     'features.memories=false', 'features.hooks=false',
                     'features.respect_system_proxy=true',
                     'skills.max_context_tokens=1', 'service_tier="default"']
        # Disable user-configured MCP tools without exposing their configuration.
        home = Path(env.get('CODEX_HOME', str(Path.home() / '.codex')))
        config_path = home / 'config.toml'
        if config_path.exists():
            config = tomllib.loads(config_path.read_text(encoding='utf-8'))
            if config.get('model_providers', {}).get('openai') or config.get('chatgpt_base_url'):
                raise RuntimeError('检测到自定义 OpenAI 提供方或 ChatGPT 地址；无法确认官方订阅路径，停止启动。')
            for name in config.get('mcp_servers', {}):
                if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                    raise RuntimeError('MCP 配置名称包含特殊字符，无法安全禁用；停止启动。')
                overrides.append('mcp_servers.' + name + '.enabled=false')
        for override in overrides:
            args.extend(['-c', override])
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=(None if env.get('QUOTA_RPC_DEBUG') else subprocess.DEVNULL),
                                     text=True, encoding='utf-8', bufsize=1,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'quota_starter',
                      'title': 'Quota Starter', 'version': VERSION}})
            self._send({'method': 'initialized', 'params': {}})
        except Exception:
            self.close()
            raise

    def _send(self, message):
        with self.write_lock:
            try:
                self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.disconnected.set()
                raise ConnectionError('Codex App Server 已断开（写入管道失败）') from exc

    def is_alive(self):
        return not self.disconnected.is_set() and self.proc.poll() is None

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if 'method' in msg and 'id' in msg:
                    # No tool execution, approval, user input, or externally supplied tokens.
                    self._send({'id': msg['id'], 'error': {'code': -32601,
                                'message': 'This minimal client does not support tools or approvals'}})
                elif 'id' in msg:
                    with self.lock:
                        waiting = self.pending.get(msg['id'])
                    if waiting:
                        waiting.put(msg)
                elif self.collect_events and msg.get('method') in (
                        'account/rateLimits/updated', 'thread/tokenUsage/updated',
                        'item/completed', 'turn/completed'):
                    self.events.put(msg)
        except (OSError, ValueError):
            pass
        finally:
            self.disconnected.set()
            with self.lock:
                for waiting in self.pending.values():
                    waiting.put({'error': {'message': 'Codex App Server 已断开'}})

    def call(self, method, params=None, timeout=45):
        started = time.monotonic()
        retry_deadline = getattr(self, 'deadline', None)
        if retry_deadline is not None:
            timeout = min(timeout, retry_deadline - time.time())
            if timeout <= 0:
                raise TimeoutError('重试总时长已达到上限')
        with self.lock:
            if self.disconnected.is_set():
                raise ConnectionError(f'{method}：Codex App Server 已断开')
            self.sequence += 1
            seq = self.sequence
            waiting = self.pending[seq] = queue.Queue()
        try:
            self._send({'id': seq, 'method': method, 'params': params or {}})
            result = waiting.get(timeout=timeout)
            if 'error' in result:
                error = result['error']
                # Older CLIs accept empty params but reject the new quota option.
                # Detect the protocol response, not a guessed minimum version.
                if (method == 'account/rateLimits/read' and isinstance(error, dict)
                        and (error.get('code') == -32601 or
                             (error.get('code') in (-32600, -32602) and any(
                                 text in str(error.get('message', '')).lower()
                                 for text in ('expected unit', 'excluderesetcreditdetails'))))):
                    compatibility = self.compatibility_error()
                    compatibility.rpc_server_error = error
                    raise compatibility
                raise RuntimeError(f'{method}：' + str(result['error'])[:1200])
            if 'result' not in result:
                raise RuntimeError(f'{method}：服务器响应缺少结果')
            return result['result']
        except queue.Empty:
            suffix = '；提交请求的结果可能未知' if method == 'turn/start' else ''
            exc = TimeoutError(f'{method} 超时{suffix}')
            exc.rpc_diagnostics = {'method': method,
                                   'elapsedSeconds': round(time.monotonic() - started, 3),
                                   'processAlive': self.is_alive()}
            raise exc from None
        except Exception as exc:
            exc.rpc_diagnostics = {'method': method,
                                   'elapsedSeconds': round(time.monotonic() - started, 3),
                                   'processAlive': self.is_alive()}
            if isinstance(exc, CodexCompatibilityError):
                exc.rpc_diagnostics['serverError'] = exc.rpc_server_error
            raise
        finally:
            with self.lock:
                self.pending.pop(seq, None)

    def compatibility_error(self):
        version = '无法读取'
        try:
            # Query the same executable as the server; PATH may contain several CLIs.
            remaining = (self.deadline - time.time()) if self.deadline is not None else 3
            if remaining > 0:
                result = subprocess.run([self.codex_exe, '--version'], capture_output=True,
                                        text=True, encoding='utf-8', timeout=min(3, remaining),
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                match = re.fullmatch(r'codex-cli ([0-9A-Za-z.+-]+)', result.stdout.strip())
                if result.returncode == 0 and match:
                    version = match.group(1)
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
        error = CodexCompatibilityError(
            f'Codex CLI 版本过低或接口不兼容（当前版本：{version}），不支持本软件的额度读取接口或参数。'
            '请在 PowerShell 运行 npm install -g @openai/codex@latest，'
            '完成后点击“重新连接”。若仍提示，请确认升级的是下列程序。'
            f'当前程序：{self.codex_exe}')
        return error

    def account(self):
        account = self.call('account/read', {'refreshToken': False}).get('account')
        if not account or account.get('type') != 'chatgpt':
            raise RuntimeError('仅允许 ChatGPT 订阅登录；请在终端运行 codex login。API Key 登录不会发送请求。')
        if account.get('planType') != 'plus':
            raise RuntimeError('当前不是 Plus 账户；本工具仅对已验证的 Plus 五小时额度启用。')
        return account

    def limits(self):
        # Reset-credit details are unrelated to quota polling and add another HTTP
        # request (with its own timeout) to every check on recent CLI versions.
        return self.call('account/rateLimits/read', {'excludeResetCreditDetails': True})

    def choose_model(self):
        models, cursor = [], None
        seen = set()
        while True:
            page = self.call('model/list', {'cursor': cursor, 'limit': 100})
            models.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                break
            if cursor in seen:
                raise RuntimeError('模型目录分页重复，停止检测')
            seen.add(cursor)
        for name in ('gpt-6-luna', 'gpt-5.6-luna', 'gpt-5.4-mini'):
            for model in models:
                if model['model'] == name and not model.get('hidden'):
                    efforts = {e['reasoningEffort'] for e in model['supportedReasoningEfforts']}
                    effort = next((e for e in ('none', 'minimal', 'low') if e in efforts),
                                  model['defaultReasoningEffort'])
                    return name, effort
        raise RuntimeError('官方模型目录未返回支持的轻量模型；停止触发，避免自动改用高消耗模型。')

    def prepare(self, cwd):
        self.account()
        model, effort = self.choose_model()
        result = self.call('thread/start', {
            'model': model, 'modelProvider': 'openai', 'cwd': str(cwd),
            'ephemeral': True, 'approvalPolicy': 'never', 'sandbox': 'read-only',
            'serviceTier': 'default',
            'baseInstructions': 'Reply only with OK. Do not use tools or reasoning prose.',
            'developerInstructions': 'Return only OK. Do not call tools.',
        })
        if result.get('modelProvider') != 'openai' or result['thread'].get('modelProvider') != 'openai':
            raise RuntimeError('线程未确认使用官方 OpenAI 提供方，停止发送。')
        return {'threadId': result['thread']['id'], 'model': model, 'effort': effort}

    def send_minimal(self, prepared):
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break
        self.collect_events = True
        try:
            return self._send_turn(prepared)
        finally:
            self.collect_events = False

    def _send_turn(self, prepared):
        result = self.call('turn/start', {'threadId': prepared['threadId'],
                          'input': [{'type': 'text', 'text': 'Reply OK.'}],
                          'effort': prepared['effort']})
        turn_id = result['turn']['id']
        deadline, messages, usage, buckets = time.monotonic() + 120, [], None, []
        while time.monotonic() < deadline:
            retry_deadline = getattr(self, 'deadline', None)
            if retry_deadline is not None and time.time() >= retry_deadline:
                raise TimeoutError('重试总时长已达到上限')
            try:
                remaining = min(1, deadline - time.monotonic())
                if retry_deadline is not None:
                    remaining = min(remaining, retry_deadline - time.time())
                event = self.events.get(timeout=max(0, remaining))
            except queue.Empty:
                if self.disconnected.is_set() or self.proc.poll() is not None:
                    raise RuntimeError('发送后 App Server 退出；结果未知')
                continue
            method, params = event.get('method'), event.get('params', {})
            if method == 'account/rateLimits/updated':
                buckets.append(params)
            if params.get('threadId') != prepared['threadId']:
                continue
            if method == 'thread/tokenUsage/updated':
                usage = params.get('tokenUsage')
            if method == 'item/completed' and params.get('item', {}).get('type') == 'agentMessage':
                messages.append(params['item'].get('text', ''))
            if method == 'turn/completed' and params['turn']['id'] == turn_id:
                return {'status': params['turn']['status'], 'turnId': turn_id,
                        'reply': '\n'.join(messages)[:200], 'usage': usage,
                        'rateLimitEvents': buckets, 'error': params['turn'].get('error')}
        try:
            self.call('turn/interrupt', {'threadId': prepared['threadId'], 'turnId': turn_id}, timeout=5)
        except Exception:
            pass
        raise TimeoutError('请求完成通知超时，已尝试中断')

    def close(self):
        self.collect_events = False
        self.disconnected.set()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()
