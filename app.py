"""Loopback-only dashboard + background scheduler (Python 3.11+, standard library)."""
import argparse
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import Engine, Store
from windows_settings import Autostart

ROOT = Path(__file__).resolve().parent


def create_server(engine, host='127.0.0.1', port=8769, startup=None):
    csrf = secrets.token_urlsafe(32)
    startup = startup or Autostart(ROOT)
    origin = f'http://127.0.0.1:{port}'
    page = (ROOT / 'ui.html').read_text(encoding='utf-8').replace('__CSRF__', csrf)
    class Handler(BaseHTTPRequestHandler):
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
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{port}'

        def do_GET(self):
            if not self.host_ok():
                return self.send(403, {'error': 'Host rejected'})
            if self.path == '/':
                return self.send(200, page, 'text/html; charset=utf-8')
            if self.path == '/api/status':
                return self.send(200, {'service': 'quota-starter', 'version': '1.0.0',
                     'status': engine.status, 'busy': engine.lock.locked(),
                     'paused': engine.store.get('paused', False),
                     'current': engine.store.get('current'), 'nextPoll': engine.next_poll,
                     'autostart': startup.apply('status'),
                     'attempts': engine.store.recent('attempts', 10),
                     'events': engine.store.recent('events', 30)})
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            if (not self.host_ok() or self.headers.get('Origin') != origin
                    or not secrets.compare_digest(self.headers.get('X-Quota-Token', ''), csrf)):
                return self.send(403, {'error': 'Origin/token rejected'})
            if self.path == '/api/pause':
                engine.store.set('paused', True)
                engine.store.log('control', {'action': 'pause'})
                return self.send(200, {'message': '已暂停；已提交的请求可能仍会完成'})
            if self.path == '/api/resume':
                engine.store.set('paused', False)
                engine.store.log('control', {'action': 'resume'})
                engine.next_poll = 0
                return self.send(200, {'message': '已恢复自动检测与发送'})
            if self.path == '/api/trigger':
                if engine.store.get('paused', False):
                    return self.send(409, {'error': '请先恢复运行'})
                if engine.lock.locked():
                    return self.send(409, {'error': '检测或请求正在进行'})
                threading.Thread(target=engine.tick, kwargs={'manual': True}, daemon=True).start()
                return self.send(202, {'message': '手动触发已排队；仍遵守额度检查与周期去重'})
            if self.path == '/api/check':
                engine.next_poll = 0
                return self.send(202, {'message': '已安排检测'})
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
    try:
        server = create_server(engine)
    except OSError:
        return 42  # Existing instance or occupied fixed port; supervisor must not loop.
    stop = threading.Event()
    def run():
        while not stop.is_set():
            if time.time() >= engine.next_poll:
                engine.tick()
            stop.wait(0.5)
    threading.Thread(target=run, daemon=True).start()
    store.log('service', {'action': 'start', 'pid': __import__('os').getpid()})
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        engine.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
