"""Loopback-only dashboard + background scheduler (Python 3.11+, standard library)."""
import argparse
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import Engine, Store
from windows_settings import Autostart
from version import VERSION
from tasks import TaskScheduler, choose_path, read_markdown
from schedule_queue import ScheduleQueue

ROOT = Path(__file__).resolve().parent


def create_server(engine, host='127.0.0.1', port=8769, startup=None, tasks=None):
    csrf = secrets.token_urlsafe(32)
    startup = startup or Autostart(ROOT)
    tasks = tasks or TaskScheduler(engine.store)
    schedule_queue = ScheduleQueue(engine)
    origin = f'http://127.0.0.1:{port}'
    page = (ROOT / 'ui.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
    help_page = (ROOT / 'help.html').read_text(encoding='utf-8').replace('__VERSION__', VERSION)
    help_content = help_page.split('</header>', 1)[1].split('</main>', 1)[0]
    page = page.replace('__HELP_CONTENT__', help_content)
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def send(self, status, body, content_type='application/json; charset=utf-8'):
            body = body.encode('utf-8') if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{port}'

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': 'Host rejected'})
            if self.path == '/':
                return self.send(200, page, 'text/html; charset=utf-8')
            if self.path == '/help':
                return self.send(200, help_page, 'text/html; charset=utf-8')
            if self.path == '/favicon.svg':
                return self.send(200, (ROOT / 'favicon.svg').read_text(encoding='utf-8'), 'image/svg+xml')
            if self.path == '/tasks.js':
                return self.send(200, (ROOT / 'tasks.js').read_text(encoding='utf-8'), 'text/javascript; charset=utf-8')
            if self.path == '/api/tasks':
                return self.send(200, tasks.snapshot())
            if self.path == '/api/session':
                if (self.headers.get('X-Quota-Session') != 'refresh'
                        or self.headers.get('Origin') not in (None, origin)
                        or self.headers.get('Sec-Fetch-Site') == 'cross-site'):
                    return self.send(403, {'error': '请求来源校验失败'})
                return self.send(200, {'token': csrf})
            if self.path == '/api/status':
                return self.send(200, {'service': 'quota-starter', 'version': VERSION,
                     'installationPath': str(ROOT),
                     'status': engine.status, 'busy': engine.lock.locked() or bool(engine.read_requested),
                     'phase': engine.phase, 'linked': engine.linked,
                     'paused': engine.store.get('paused', False),
                     'pollWhilePaused': engine.store.get('pollWhilePaused', False),
                     'connection': engine.connection(),
                     'scheduleNotice': engine.store.get('scheduleNotice'),
                     'scheduleQueue': schedule_queue.status(),
                     'current': engine.store.get('current'), 'nextPoll': (engine.next_poll if engine.polling_enabled() and not engine.connection().get('stopped') else None),
                     'autostart': startup.apply('status'),
                     'timing': engine.timing(),
                     'attempts': engine.store.recent('attempts', 10),
                     'triggers': engine.store.trigger_history(),
                     'quotaHistory': engine.store.quota_history(),
                     'activity': engine.store.activity(),
                     'events': engine.store.recent('events', 30)})
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin') != origin:
                return self.send(403, {'error': '请求来源校验失败，请从本机软件页面操作'})
            if not secrets.compare_digest(self.headers.get('X-Quota-Token', ''), csrf):
                return self.send(403, {'code': 'stale_session', 'error': '页面连接凭据已过期，请刷新页面后重试'})
            if self.path.startswith('/api/tasks/'):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 850_000:
                        raise ValueError('任务数据大小无效')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('任务数据格式无效')
                    route = self.path.removeprefix('/api/tasks/')
                    if route == 'create':
                        task = tasks.create(body)
                        return self.send(200, {'task': task, 'message': '已添加预设任务'})
                    if route == 'edit':
                        return self.send(200, {'task': tasks.edit(body), 'message': '已修改预设任务'})
                    if route == 'settings':
                        tasks.save_settings(body)
                        return self.send(200, {'message': '已保存任务设置'})
                    if route == 'models':
                        if not isinstance(body.get('force', False), bool):
                            raise ValueError('模型检测参数无效')
                        return self.send(200, {'models': tasks.catalog(body.get('force', False))})
                    if route == 'detail':
                        return self.send(200, {'task': tasks.snapshot(body.get('id', 'invalid'))})
                    if route == 'open-directory':
                        task = tasks.snapshot(body.get('id', 'invalid'))
                        folder = Path(task['cwd'])
                        if not folder.is_absolute() or not folder.is_dir():
                            raise ValueError('执行目录不存在或无法访问')
                        os.startfile(str(folder))
                        return self.send(200, {'message': '已打开执行目录'})
                    if route == 'cancel':
                        tasks.cancel(body.get('id'))
                        return self.send(200, {'message': '已取消或正在中断任务'})
                    if route == 'read':
                        return self.send(200, {'content': read_markdown(body.get('path'))})
                    if route == 'pick':
                        return self.send(200, {'path': choose_path(body.get('kind'))})
                    return self.send(404, {'error': 'Not found'})
                except (ValueError, TypeError, UnicodeError, OSError) as exc:
                    return self.send(400, {'error': str(exc)})
                except (RuntimeError, subprocess.TimeoutExpired) as exc:
                    return self.send(409, {'error': str(exc)})
            if self.path in ('/api/logs/open', '/api/triggers/open'):
                triggers = self.path == '/api/triggers/open'
                folder = engine.store.log_dir.resolve()
                action_name = 'open_triggers' if triggers else 'open_logs'
                label = '日志'
                try:
                    folder.mkdir(parents=True, exist_ok=True)
                    os.startfile(str(folder))
                except OSError as exc:
                    engine.store.log('control', {'action': action_name, 'error': str(exc)})
                    return self.send(500, {'error': '无法打开' + label + '文件夹：' + str(exc)})
                engine.store.log('control', {'action': action_name, 'message': str(folder)})
                return self.send(200, {'message': '已打开' + label + '文件夹'})
            if self.path == '/api/reconnect':
                try:
                    engine.reconnect()
                    return self.send(200, {'message': '正在重新连接'})
                except RuntimeError as exc:
                    return self.send(409, {'error': str(exc)})
            if self.path in ('/api/schedule', '/api/schedule/clear'):
                try:
                    if self.path.endswith('/clear'):
                        operation = schedule_queue.submit(clear=True)
                    else:
                        length = int(self.headers.get('Content-Length', '0'))
                        if not 0 < length <= 1024:
                            raise ValueError('时间数据无效')
                        body = json.loads(self.rfile.read(length))
                        if not isinstance(body, dict) or 'refreshAt' not in body or body['refreshAt'] is None:
                            raise ValueError('请选择时间')
                        operation = schedule_queue.submit(body['refreshAt'])
                    if operation['status'] == 'failed':
                        return self.send(400, {'error': operation['error']})
                    queued = operation['status'] == 'queued'
                    return self.send(202 if queued else 200, {'queued': queued, 'message':
                                     '已排队，检测结束后保存' if queued else
                                     '已恢复自动时间' if self.path.endswith('/clear') else '已保存时间'})
                except (json.JSONDecodeError, TypeError, UnicodeError):
                    return self.send(400, {'error': '时间格式无效'})
                except ValueError as exc:
                    return self.send(400, {'error': str(exc)})
                except RuntimeError as exc:
                    return self.send(409, {'error': str(exc)})
            if self.path == '/api/pause':
                engine.set_paused(True)
                return self.send(200, {'message': '已暂停；已提交的请求可能仍会完成'})
            if self.path == '/api/settings/poll-while-paused':
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1024:
                        raise ValueError('设置数据无效')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict) or not isinstance(body.get('enabled'), bool):
                        raise ValueError('检测开关必须是布尔值')
                    engine.set_poll_while_paused(body['enabled'])
                    return self.send(200, {'message': '已开启暂停后额度检测' if body['enabled'] else '已关闭暂停后额度检测'})
                except (ValueError, TypeError, UnicodeError) as exc:
                    return self.send(400, {'error': str(exc)})
            if self.path == '/api/resume':
                engine.resume()
                if engine.connection().get('stopped'):
                    return self.send(200, {'message': '已解除暂停；连接仍已停止，请点击重新连接'})
                return self.send(200, {'message': '已恢复运行，正在检测五小时及周额度'})
            if self.path == '/api/trigger':
                if engine.connection().get('stopped') or engine.read_requested:
                    return self.send(409, {'error': '连接已停止或正在检测，请稍后重试'})
                if engine.store.get('paused', False):
                    return self.send(409, {'error': '请先恢复运行'})
                if engine.lock.locked():
                    return self.send(409, {'error': '检测或请求正在进行'})
                engine.store.log('control', {'action': 'trigger'})
                result = engine.tick(manual=True)
                return self.send(200, {'message': result})
            if self.path == '/api/check':
                try:
                    engine.request_check()
                    return self.send(202, {'message': '正在检测额度'})
                except RuntimeError as exc:
                    return self.send(409, {'error': str(exc)})
            if self.path in ('/api/autostart/enable', '/api/autostart/disable'):
                action = self.path.rsplit('/', 1)[-1]
                settings = startup.apply(action)
                engine.store.log('control', {'action': 'autostart/' + action, 'result': settings})
                if settings.get('error'):
                    return self.send(500, {'error': settings['error']})
                return self.send(200, {'message': '已开启开机自启动' if settings['enabled'] else '已关闭开机自启动，当前服务继续运行'})
            return self.send(404, {'error': 'Not found'})
    class LocalServer(ThreadingHTTPServer):
        allow_reuse_address = False
        def service_actions(self):
            try:
                schedule_queue.drain()
            except (OSError, sqlite3.Error):
                # Durable edits remain queued during transient storage failures.
                pass
        def server_bind(self):
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()
    return LocalServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    args = parser.parse_args()
    store = Store(args.data / 'quota.sqlite3')
    engine = Engine(store, args.data / 'empty-workspace')
    tasks = TaskScheduler(store)
    try:
        server = create_server(engine, tasks=tasks)
    except OSError:
        return 42  # Existing instance or occupied fixed port; supervisor must not loop.
    stop = threading.Event()
    def run():
        while not stop.is_set():
            if ((engine.polling_enabled() or engine.read_requested) and
                    not engine.connection().get('stopped') and time.time() >= engine.next_poll):
                engine.tick()
            stop.wait(0.5)
    threading.Thread(target=run, daemon=True).start()
    tasks.start()
    store.log('service', {'action': 'start', 'pid': __import__('os').getpid()})
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        tasks.close()
        engine.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
