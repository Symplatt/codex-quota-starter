"""No-console watchdog. Task Scheduler restarts this supervisor if it exits."""
import datetime
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parent
data = root / 'data'
data.mkdir(exist_ok=True)
# Unique live supervisor per installation, released automatically on process death.
lockfile = (data / 'supervisor.lock').open('a+b')
if os.name == 'nt':
    import msvcrt
    lockfile.seek(0)
    if lockfile.read(1) == b'':
        lockfile.write(b'0')
        lockfile.flush()
    lockfile.seek(0)
    try:
        msvcrt.locking(lockfile.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(0)
(data / 'supervisor.pid').write_text(str(os.getpid()), encoding='ascii')
while True:
    logpath = data / 'supervisor.log'
    if logpath.exists() and logpath.stat().st_size > 2_000_000:
        logpath.replace(data / 'supervisor.previous.log')
    with logpath.open('a', encoding='utf-8') as log:
        log.write(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} starting service\n')
        log.flush()
        process = subprocess.Popen([sys.executable, '-X', 'utf8', str(root / 'app.py')],
                                   cwd=root, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (data / 'service.pid').write_text(str(process.pid), encoding='ascii')
        code = process.wait()
        log.write(f'exit={code}\n')
    if code in (0, 42):
        break
    time.sleep(10)
