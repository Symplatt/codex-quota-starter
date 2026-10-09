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
    def __init__(self, cwd):
        self.pending = {}
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.sequence = 0
        self.events = queue.Queue()
        env = os.environ.copy()
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            env.pop(key, None)
        args = [find_codex(), 'app-server', '--listen', 'stdio://']
        overrides = ['model_provider="openai"', 'forced_login_method="chatgpt"',
                     'web_search="disabled"', 'project_doc_max_bytes=0',
                     'features.shell_tool=false', 'features.multi_agent=false',
                     'features.apps=false', 'features.plugins=false',
                     'features.memories=false', 'features.hooks=false',
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
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
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
                else:
                    self.events.put(msg)
        finally:
            with self.lock:
                for waiting in self.pending.values():
                    waiting.put({'error': {'message': 'Codex App Server 已断开'}})

    def call(self, method, params=None, timeout=45):
        with self.lock:
            self.sequence += 1
            seq = self.sequence
            waiting = self.pending[seq] = queue.Queue()
        try:
            self._send({'id': seq, 'method': method, 'params': params or {}})
            result = waiting.get(timeout=timeout)
            if 'error' in result:
                raise RuntimeError(str(result['error'])[:1200])
            return result['result']
        except queue.Empty:
            raise TimeoutError(f'{method} 超时；提交请求的结果可能未知') from None
        finally:
            with self.lock:
                self.pending.pop(seq, None)

    def account(self):
        account = self.call('account/read', {'refreshToken': False}).get('account')
        if not account or account.get('type') != 'chatgpt':
            raise RuntimeError('仅允许 ChatGPT 订阅登录；请在终端运行 codex login。API Key 登录不会发送请求。')
        if account.get('planType') != 'plus':
            raise RuntimeError('当前不是 Plus 账户；本工具仅对已验证的 Plus 五小时额度启用。')
        return account

    def limits(self):
        return self.call('account/rateLimits/read')

    def choose_model(self):
        models, cursor = [], None
        while True:
            page = self.call('model/list', {'cursor': cursor, 'limit': 100})
            models.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                break
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
        while not self.events.empty():
            self.events.get_nowait()
        result = self.call('turn/start', {'threadId': prepared['threadId'],
                          'input': [{'type': 'text', 'text': 'Reply OK.'}],
                          'effort': prepared['effort']})
        turn_id = result['turn']['id']
        deadline, messages, usage, buckets = time.monotonic() + 120, [], None, []
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=1)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError('发送后 App Server 退出；结果未知，本周期不重试')
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
        raise TimeoutError('请求完成通知超时，已尝试中断；本周期不重复发送')

    def close(self):
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
