"""Bounded loading of large task documents, archives and dashboard samples."""
from datetime import datetime
import gc
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tracemalloc
import unittest

from core import BEIJING, Store
from tasks import TaskScheduler


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 11, 8, 12, tzinfo=BEIJING).timestamp()
        self.store = Store(self.root / 'quota.sqlite3', clock=lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, number, **fields):
        return dict(id=f'{number:032x}', status='waiting', createdAt=number,
                    title=f'Task {number}', mode='time', runAt=self.now + 1000,
                    path=str(self.root / f'{number}.md'), cwd=str(self.root),
                    model='gpt-6-astra', effort='high', content='p' * 200_000,
                    result='r' * 200_000, **fields)

    def test_task_list_does_not_load_all_document_bodies(self):
        tasks = TaskScheduler(self.store, clock=lambda: self.now)
        with tasks.db() as db:
            for number in range(48):
                tasks.write(db, self.task(number))
        gc.collect()
        tracemalloc.start()
        try:
            snapshot = tasks.snapshot()
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(snapshot['total'], 48)
        self.assertEqual(len(snapshot['tasks']), 48)
        self.assertLess(peak, 4 * 1024 * 1024)
        self.assertTrue(all('content' not in task and 'result' not in task for task in snapshot['tasks']))
        detail = tasks.snapshot(f'{17:032x}')
        self.assertEqual(detail['content'], 'p' * 200_000)
        self.assertEqual(detail['result'], 'r' * 200_000)

    def test_dispatch_loads_one_due_document_and_preserves_order(self):
        class Runner:
            def __init__(self):
                self.calls = []
            def preflight(self, task):
                self.calls.append(task['id'])
                assert task['content'] == 'p' * 200_000
            def run(self, task, update, cancelled):
                return 'done'
        runner = Runner()
        tasks = TaskScheduler(self.store, runner, clock=lambda: self.now)
        with tasks.db() as db:
            for number in range(48):
                task = self.task(number)
                if number in (24, 25):
                    task['runAt'] = self.now
                tasks.write(db, task)
        gc.collect()
        tracemalloc.start()
        try:
            tasks.tick()
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * 1024 * 1024)
        self.assertEqual(runner.calls, [f'{24:032x}'])
        self.assertEqual(tasks.snapshot(f'{24:032x}')['status'], 'completed')
        self.assertEqual(tasks.snapshot(f'{25:032x}')['status'], 'waiting')

    def test_compact_samples_keep_every_rendered_point_in_all_chart_modes(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is required to compare the actual chart function')
        self.store.set('current', {'account': 'one'})
        with self.store.connect() as db:
            # Cover exact hour boundaries, missing hours, null weekly readings,
            # and minute/check timestamps that fall into different hour buckets.
            for index in range(10081):
                minute = self.now - (10080 - index) * 60
                if index % 287 in range(190, 270):
                    continue
                secondary = None if index % 17 == 0 else index % 101
                db.execute('INSERT INTO quota_samples VALUES (?,?,?,?,?)',
                           ('one', minute, minute + (0 if index % 3 == 0 else 13), index % 101, secondary))
        full = self.store.quota_history(now=self.now + 13)
        compact = self.store.quota_history(now=self.now + 13, compact=True)
        self.assertLessEqual(len(compact), 640)
        self.assertGreater(len(full), len(compact) * 10)
        script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const html = fs.readFileSync('ui.html', 'utf8');
const source = html.slice(html.indexOf('function chartData('), html.indexOf('function chartScale('));
const chart = new Function(source + '; return chartData;')();
const {full, compact, now} = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const key of ['primaryRemaining', 'secondaryRemaining']) {
  const duration = key === 'primaryRemaining' ? 18000 : 7 * 86400;
  for (const planning of [false, true]) {
    for (const offset of [0, 1, 59, 3600, duration - 60]) {
      const start = now - duration + offset, end = now;
      assert.deepEqual(chart(compact, start, end, key, planning), chart(full, start, end, key, planning));
    }
  }
}
'''
        result = subprocess.run([node, '-e', script], input=json.dumps(
            {'full': full, 'compact': compact, 'now': self.now + 13}),
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_large_legacy_archive_merges_with_bounded_memory_and_exact_backup(self):
        legacy = self.root / 'triggers'
        legacy.mkdir()
        source = legacy / 'quota-triggers-2026-11.jsonl'
        record = {'time': '2026-11-08T00:00:00+08:00', 'kind': 'trigger', 'detail': {'text': 'x' * 4096}}
        with source.open('w', encoding='utf-8') as output:
            for number in range(1000):
                output.write(json.dumps(dict(record, id=number)) + '\n')
        with source.open('rb') as content:
            expected_digest = hashlib.file_digest(content, 'sha256').hexdigest()
        gc.collect()
        tracemalloc.start()
        try:
            self.store._archive_events()
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertIsNone(self.store._archive_error)
        self.assertLess(peak, 2 * 1024 * 1024)
        self.assertFalse(source.exists())
        target = self.store.log_dir / 'quota-starter-2026-11.jsonl'
        with target.open(encoding='utf-8') as content:
            for number, line in enumerate(content):
                self.assertEqual(json.loads(line), dict(record, id=number))
        self.assertEqual(number, 999)
        backups = list((self.root / 'legacy-archive-backups').glob('*.bak'))
        self.assertEqual(len(backups), 1)
        with backups[0].open('rb') as saved:
            self.assertEqual(hashlib.file_digest(saved, 'sha256').hexdigest(), expected_digest)


if __name__ == '__main__':
    unittest.main()
