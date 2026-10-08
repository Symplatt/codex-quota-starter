"""Run read-only diagnostics, or one explicitly requested live minimal turn."""
import argparse
import json
from pathlib import Path
from core import Engine, Store

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--send-once', action='store_true', help='Submit one real subscription request, respecting deduplication')
args = parser.parse_args()
store = Store(root / 'data/quota.sqlite3')
engine = Engine(store, root / 'data/empty-workspace')
try:
    if not args.send_once:
        # Read-only diagnostic must never call tick: an empty window can auto-trigger.
        from rpc import RPC
        rpc = RPC(str(engine.cwd))
        try:
            account = rpc.account()
            from core import normalize
            print(json.dumps({'auth': account['type'], 'plan': account['planType'],
                              'quota': normalize(rpc.limits()), 'model': rpc.choose_model()}, ensure_ascii=False, indent=2))
        finally:
            rpc.close()
    else:
        print(engine.tick(manual=True))
        print(json.dumps(store.recent('attempts', 1), ensure_ascii=False, indent=2))
finally:
    engine.close()
