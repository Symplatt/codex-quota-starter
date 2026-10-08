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
        self.cached = None
        self.checked = 0

    def apply(self, action='status'):
        if action not in ('status', 'enable', 'disable'):
            raise ValueError('Invalid startup action')
        with self.lock:
            if action == 'status' and self.cached is not None and time.monotonic() - self.checked < 60:
                return self.cached
            if os.name != 'nt':
                return {'available': False, 'enabled': None, 'error': '仅 Windows 支持自启动设置'}
            try:
                result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                    '-ExecutionPolicy', 'Bypass', '-File', str(self.root / 'Autostart-Control.ps1'),
                    '-Action', action], capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip()[:600] or 'Windows 自启动设置操作失败')
                self.cached = json.loads(result.stdout.strip())
            except Exception as exc:
                self.cached = {'available': False, 'enabled': None, 'error': str(exc)[:600]}
            self.checked = time.monotonic()
            return self.cached
