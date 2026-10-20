"""Minute sampling, request descriptions and Beijing calendar retention."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import BEIJING, Store, evidence, normalize
from test_quota import raw


def stamp(year, month, day=1, hour=0):
    return datetime(year, month, day, hour, tzinfo=BEIJING).timestamp()


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = [stamp(2026, 10, 1)]
        self.path = Path(self.tmp.name) / 'quota.sqlite3'
        self.store = Store(self.path, clock=lambda: self.now[0])

    def tearDown(self):
        self.tmp.cleanup()

    def test_retention_catches_up_and_only_removes_own_expired_months(self):
        for month in ('2025-01', '2026-07', '2026-08', '2026-09', '2026-10'):
            (self.store.log_dir / f'quota-starter-{month}.jsonl').write_text('log')
        unrelated = self.store.log_dir / 'other-2026-07.jsonl'
        unrelated.write_text('keep')
        invalid = self.store.log_dir / 'quota-starter-2026-00.jsonl'
        invalid.write_text('keep')
        self.now[0] = stamp(2026, 11, 5)
        Store(self.path, clock=lambda: self.now[0])
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-08.jsonl').exists())
        self.assertTrue((self.store.log_dir / 'quota-starter-2026-09.jsonl').exists())
        self.assertTrue((self.store.log_dir / 'quota-starter-2026-10.jsonl').exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue(invalid.exists())

    def test_october_first_retains_august_and_year_rollover_retains_november(self):
        self.now[0] = stamp(2026, 9, 30, 23)
        for month in (7, 8, 9):
            self.store.log('check', {}, stamp(2026, month, 30))
        self.now[0] = stamp(2026, 10, 1)
        self.store.log('check', {}, self.now[0])
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-07.jsonl').exists())
        self.assertTrue((self.store.log_dir / 'quota-starter-2026-08.jsonl').exists())
        self.now[0] = stamp(2027, 1, 1)
        self.store.log('check', {}, self.now[0])
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-10.jsonl').exists())
        self.assertTrue((self.store.log_dir / 'quota-starter-2027-01.jsonl').exists())

    def test_expired_backlog_advances_cursor_without_recreating_old_logs(self):
        with self.store.connect() as db:
            db.execute('INSERT INTO events(time,kind,detail) VALUES (?, ?, ?)',
                       (stamp(2026, 7), 'check', '{}'))
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-07.jsonl').exists())
        self.assertEqual(self.store.get('logArchiveId'), 1)

    def test_failed_cleanup_retries_without_marking_month_complete(self):
        old = self.store.log_dir / 'quota-starter-2026-08.jsonl'
        old.write_text('old')
        self.now[0] = stamp(2026, 11)
        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            self.store.log('check', {}, self.now[0])
        self.assertEqual(self.store.get('logRetentionMonth'), '2026-10')
        self.store.log('check', {}, self.now[0])
        self.assertFalse(old.exists())
        self.assertEqual(self.store.get('logRetentionMonth'), '2026-11')

    def test_samples_keep_latest_read_per_minute_and_survive_restart(self):
        start = self.now[0]
        self.store.set('current', {'account': 'one'})
        self.store.sample('one', normalize(raw(used=10)), start+1)
        self.store.sample('one', normalize(raw(used=13)), start+59)
        self.store.sample('one', normalize(raw(used=11)), start+10)
        self.store.sample('one', normalize(raw(used=20)), start+121)
        restarted = Store(self.path, clock=lambda: start+130)
        rows = restarted.quota_history()
        self.assertEqual([p['primaryRemaining'] for p in rows], [87, 80])
        self.assertEqual(rows[1]['minute']-rows[0]['minute'], 120)
        self.assertEqual(rows[0]['secondaryRemaining'], 80)

    def test_history_separates_accounts_and_bounds_to_last_day(self):
        now = self.now[0]
        self.store.set('current', {'account': 'one'})
        for account, when in [('one', now-86460), ('one', now), ('two', now)]:
            self.store.sample(account, normalize(raw()), when)
        self.assertEqual(len(self.store.quota_history()), 1)
        self.store.set('current', {'account': 'two'})
        self.assertEqual(len(self.store.quota_history()), 1)

    def test_weekly_missing_stays_unknown(self):
        self.store.set('current', {'account': 'one'})
        snapshot = normalize(raw())
        snapshot['secondary'] = None
        self.store.sample('one', snapshot, self.now[0])
        self.assertIsNone(self.store.quota_history()[0]['secondaryRemaining'])

    def test_trigger_history_merges_reads_without_creating_submission_claims(self):
        self.store.log('check', normalize(raw()), self.now[0])
        self.store.log('control', {'action': 'pause'}, self.now[0]+1)
        rows = self.store.trigger_history()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['requestKind'], 'quota')
        self.assertEqual(rows[0]['detail']['evidence'], '已发送请求获取当前额度信息')
        self.assertEqual(self.store.recent('attempts'), [])
        self.assertEqual(self.store.guard_until(), 0)

    def test_old_small_request_is_described_without_rewriting_its_evidence(self):
        detail = {'before': normalize(raw(20000)), 'after': normalize(raw(20005)), 'evidence': 'legacy'}
        attempt = self.store.claim('one', 'cycle:20000', 20002, detail)
        self.store.finish(attempt, 'one', 'completed', detail, None, 20003)
        row = self.store.trigger_history()[0]
        self.assertEqual(row['requestKind'], 'minimal')
        self.assertEqual(row['detail']['evidence'], '发送小额请求，该请求并未导致下次额度刷新时间出现变化（相差≤5秒视为不变）')
        self.assertEqual(self.store.recent('attempts')[0]['detail']['evidence'], 'legacy')

    def test_trigger_card_keeps_only_fifteen_latest_rows_but_archive_keeps_all(self):
        self.store.claim('one', 'cycle:one', 100, {})
        for i in range(40):
            self.store.log('check', {}, self.now[0]+i)
        rows = self.store.trigger_history()
        self.assertEqual(len(rows), 15)
        self.assertEqual([r['started'] for r in rows], [self.now[0]+i for i in range(39, 24, -1)])
        archived = self.store.trigger_dir / 'quota-triggers-2026-10.jsonl'
        self.assertEqual(len(archived.read_text(encoding='utf-8').splitlines()), 40)

    def test_trigger_archive_contains_both_request_types_and_restarts_without_duplicates(self):
        self.store.log('check', {'primary': {'usedPercent': 20}}, self.now[0])
        self.store.log('trigger', {'status': 'completed', 'evidence': 'result'}, self.now[0]+1)
        self.store.log('control', {'action': 'pause'}, self.now[0]+2)
        Store(self.path, clock=lambda: self.now[0])
        rows = [json.loads(line) for line in (self.store.trigger_dir / 'quota-triggers-2026-10.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['requestKind'] for r in rows], ['quota', 'minimal'])
        self.assertEqual(rows[0]['evidence'], '已发送请求获取当前额度信息')
        self.assertEqual(rows[1]['evidence'], 'result')

    def test_trigger_archive_retention_catches_up_and_does_not_recreate_expired_files(self):
        self.store.log('check', {}, stamp(2026, 8))
        self.store.log('check', {}, stamp(2026, 9))
        self.now[0] = stamp(2026, 11, 5)
        self.store.log('check', {}, self.now[0])
        self.assertFalse((self.store.trigger_dir / 'quota-triggers-2026-08.jsonl').exists())
        self.assertTrue((self.store.trigger_dir / 'quota-triggers-2026-09.jsonl').exists())
        self.store.log('check', {}, stamp(2026, 8))
        self.assertFalse((self.store.trigger_dir / 'quota-triggers-2026-08.jsonl').exists())

    def test_trigger_archive_backfills_existing_database_and_retries_unwritable_folder(self):
        self.store.log('check', {}, self.now[0])
        blocked = self.path.parent / 'blocked-triggers'
        blocked.write_text('occupied')
        folder = self.store.trigger_dir
        self.store.trigger_dir = blocked
        self.store.log('check', {}, self.now[0]+1)
        self.store.trigger_dir = folder
        # Simulate an upgrade from a database that only had the general log archive.
        with self.store.connect() as db:
            db.execute("DELETE FROM state WHERE key='triggerArchiveId'")
        # Replayed events may append to an existing file after an interrupted cursor
        # save, but a fresh trigger archive reconstructs all retained database events.
        fresh = self.path.parent / 'fresh-triggers'
        self.store.trigger_dir = fresh
        with self.store.connect() as db:
            db.execute("DELETE FROM state WHERE key='triggerArchiveId'")
        self.assertEqual(len((fresh / 'quota-triggers-2026-10.jsonl').read_text(encoding='utf-8').splitlines()), 2)

    def test_reset_change_must_match_countdown_before_claiming_success(self):
        self.assertEqual(evidence(normalize(raw(20000)), normalize(raw(38002)), 20002, True),
                         '发送小额请求，ChatGPT将于5小时后刷新额度')
        self.assertNotIn('ChatGPT将于', evidence(normalize(raw(20000)), normalize(raw(50000)), 20002, True))
        self.assertNotIn('ChatGPT将于', evidence(normalize(raw(20000)), normalize(raw(38002)), 20002, False))


if __name__ == '__main__':
    unittest.main()
