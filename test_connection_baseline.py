"""Protect unchanged 1.5.23 behavior and explicitly approved pre.3 connection repairs."""
import ast
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent
BASELINE = json.loads((ROOT / 'test_fixtures' / 'connection-baseline.json').read_text(encoding='utf-8'))
CHANGES = json.loads((ROOT / 'test_fixtures' / 'connection-changes.json').read_text(encoding='utf-8'))


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def methods(source):
    result = {}
    def visit(items, prefix=''):
        for node in items:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + '.')
            elif isinstance(node, ast.FunctionDef):
                result[prefix + node.name] = node
    visit(ast.parse(source).body)
    return result


class ConnectionBaselineTests(unittest.TestCase):
    def test_transport_and_launchers_match_approved_sources(self):
        for filename, expected in {**BASELINE['files'], **CHANGES['files']}.items():
            with self.subTest(file=filename):
                self.assertEqual(digest((ROOT / filename).read_text(encoding='utf-8')), expected)

    def test_connection_methods_match_approved_sources(self):
        source = (ROOT / 'core.py').read_text(encoding='utf-8')
        found = methods(source)
        for name, expected in {**BASELINE['methods'], **CHANGES['methods']}.items():
            with self.subTest(method=name):
                self.assertEqual(digest(ast.get_source_segment(source, found[name])), expected)

    def test_retry_and_recovery_constants_match_approved_policy(self):
        tree = ast.parse((ROOT / 'core.py').read_text(encoding='utf-8'))
        values = {target.id: ast.dump(node.value, include_attributes=False)
                  for node in tree.body if isinstance(node, ast.Assign)
                  for target in node.targets if isinstance(target, ast.Name) and target.id in {**BASELINE['constants'], **CHANGES['constants']}}
        self.assertEqual(values, {**BASELINE['constants'], **CHANGES['constants']})

    def test_tick_retry_branch_matches_approved_recovery(self):
        source = (ROOT / 'core.py').read_text(encoding='utf-8')
        tick = ast.get_source_segment(source, methods(source)['Engine.tick'])
        start = tick.index('            now = self.clock()\n            retry = self.connection()')
        end = tick.index('            self._phase(', start)
        self.assertEqual(digest(tick[start:end]), CHANGES['tickRetryBlock'])

    def test_default_web_port_is_31026(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        signature = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'create_server')
        defaults = dict(zip([a.arg for a in signature.args.args][-len(signature.args.defaults):], signature.args.defaults))
        self.assertEqual(ast.literal_eval(defaults['port']), 31026)


if __name__ == '__main__':
    unittest.main()
