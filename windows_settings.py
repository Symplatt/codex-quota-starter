import json
import os
from pathlib import Path
import subprocess
import threading
import time


class Autostart:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.refreshing = False
        self.cached = None
        self.checked = 0

    def apply(self, action='status'):
        if action not in ('status', 'enable', 'disable'):
            raise ValueError('Invalid startup action')
        if os.name != 'nt':
            return {'available': False, 'enabled': None, 'error': '仅 Windows 支持自启动设置'}
        if action == 'status':
            with self.lock:
                if (time.monotonic() - self.checked >= 60 or self.cached is None) and not self.refreshing:
                    self.refreshing = True
                    threading.Thread(target=self._refresh, daemon=True).start()
                return self.cached or {'available': False, 'enabled': None, 'error': None}
        with self.operation_lock:
            return self._execute(action)

    def _refresh(self):
        try:
            with self.operation_lock:
                self._execute('status')
        finally:
            with self.lock:
                self.refreshing = False

    def _execute(self, action):
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                '-ExecutionPolicy', 'Bypass', '-File', str(self.root / 'Autostart-Control.ps1'),
                '-Action', action], capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(result.stderr.strip()[:600] or 'Windows 自启动设置操作失败')
            settings = json.loads(result.stdout.strip())
            if not isinstance(settings, dict):
                raise ValueError('自启动设置返回格式无效')
        except Exception as exc:
            settings = {'available': False, 'enabled': None, 'error': str(exc)[:600]}
        with self.lock:
            self.cached = settings
            self.checked = time.monotonic()
        return settings
