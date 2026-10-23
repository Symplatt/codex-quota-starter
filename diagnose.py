"""Read-only diagnostics; explicit submissions use the running service's ledger and lock."""
import argparse
import json
import subprocess
import tempfile
import time
import urllib.request
from core import normalize
from rpc import RPC


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send-once', action='store_true', help='Ask the running service to submit one bounded request round')
    args = parser.parse_args()
    if args.send_once:
        origin = 'http://127.0.0.1:8769'
        req = urllib.request.Request(origin + '/api/session', headers={'X-Quota-Session': 'refresh'})
        with urllib.request.urlopen(req, timeout=10) as response:
            token = json.load(response)['token']
        req = urllib.request.Request(origin + '/api/trigger', data=b'', headers={
            'Origin': origin, 'X-Quota-Token': token})
        with urllib.request.urlopen(req, timeout=75) as response:
            print(response.read().decode('utf-8'))
        return
    with tempfile.TemporaryDirectory(prefix='quota-diagnose-') as workspace:
        started = time.monotonic()
        rpc = RPC(workspace, deadline=time.time() + 60)
        try:
            account = rpc.account()
            quota = normalize(rpc.limits())
            cli_version = subprocess.run([rpc.codex_exe, '--version'], capture_output=True,
                                         text=True, timeout=3,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.strip()
            print(json.dumps({'auth': account['type'], 'plan': account['planType'], 'quota': quota,
                              'cliExecutable': rpc.codex_exe, 'cliVersion': cli_version,
                              'transport': rpc.transport,
                              'readSeconds': round(time.monotonic() - started, 3)}, ensure_ascii=False, indent=2))
        finally:
            rpc.close()


if __name__ == '__main__':
    main()
