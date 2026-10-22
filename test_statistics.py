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
        archived = self.store.log_dir / 'quota-starter-2026-10.jsonl'
        self.assertEqual(len(archived.read_text(encoding='utf-8').splitlines()), 40)

    def test_trigger_archive_contains_both_request_types_and_restarts_without_duplicates(self):
        self.store.log('check', {'primary': {'usedPercent': 20}}, self.now[0])
        self.store.log('trigger', {'status': 'completed', 'evidence': 'result'}, self.now[0]+1)
        self.store.log('control', {'action': 'pause'}, self.now[0]+2)
        Store(self.path, clock=lambda: self.now[0])
        rows = [json.loads(line) for line in (self.store.log_dir / 'quota-starter-2026-10.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['kind'] for r in rows], ['check', 'trigger', 'control'])
        self.assertEqual(rows[0]['detail']['primary']['usedPercent'], 20)
        self.assertEqual(rows[1]['detail']['evidence'], 'result')
        self.assertFalse((self.path.parent / 'triggers').exists())

    def test_trigger_archive_retention_catches_up_and_does_not_recreate_expired_files(self):
        self.store.log('check', {}, stamp(2026, 8))
        self.store.log('check', {}, stamp(2026, 9))
        self.now[0] = stamp(2026, 11, 5)
        self.store.log('check', {}, self.now[0])
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-08.jsonl').exists())
        self.assertTrue((self.store.log_dir / 'quota-starter-2026-09.jsonl').exists())
        self.store.log('check', {}, stamp(2026, 8))
        self.assertFalse((self.store.log_dir / 'quota-starter-2026-08.jsonl').exists())

    def test_legacy_trigger_archive_merges_without_duplicate_ids_and_keeps_backup(self):
        self.store.log('check', {}, self.now[0])
        legacy = self.path.parent / 'triggers'
        legacy.mkdir()
        current = (self.store.log_dir / 'quota-starter-2026-10.jsonl').read_text(encoding='utf-8')
        record = json.loads(current)
        record.update(requestKind='quota', evidence='旧检测证据')
        extra = dict(record, id=900, kind='trigger', detail={'evidence':'完整旧结果'})
        original = json.dumps(record, ensure_ascii=False)+'\n'+json.dumps(extra, ensure_ascii=False)+'\n'
        source = legacy / 'quota-triggers-2026-10.jsonl'
        source.write_text(original,encoding='utf-8')
        Store(self.path, clock=lambda:self.now[0])
        Store(self.path, clock=lambda:self.now[0])
        rows = [json.loads(line) for line in (self.store.log_dir/'quota-starter-2026-10.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual([r['id'] for r in rows], [1,900])
        self.assertEqual(rows[0]['evidence'],'旧检测证据')
        self.assertEqual(rows[1]['detail']['evidence'],'完整旧结果')
        self.assertFalse(source.exists())
        backups = list((self.path.parent/'legacy-archive-backups').glob('*.bak'))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_text(encoding='utf-8'),original)

    def test_invalid_legacy_archive_keeps_source_and_retry_succeeds(self):
        legacy = self.path.parent/'triggers'
        legacy.mkdir()
        source = legacy/'quota-triggers-2026-10.jsonl'
        source.write_text('invalid',encoding='utf-8')
        self.store.log('check',{},self.now[0])
        self.assertTrue(source.exists())
        self.assertIsNotNone(self.store._archive_error)
        source.write_text('',encoding='utf-8')
        self.store.log('control',{'action':'pause'},self.now[0]+1)
        self.assertFalse(source.exists())
        self.assertIsNone(self.store._archive_error)

    def test_conflicting_event_id_preserves_both_files(self):
        self.store.log('check',{},self.now[0])
        target=self.store.log_dir/'quota-starter-2026-10.jsonl'
        record=json.loads(target.read_text(encoding='utf-8'))
        record['detail']={'evidence':'conflict'}
        legacy=self.path.parent/'triggers';legacy.mkdir()
        source=legacy/'quota-triggers-2026-10.jsonl'
        source.write_text(json.dumps(record)+'\n',encoding='utf-8')
        before=target.read_bytes()
        Store(self.path,clock=lambda:self.now[0])
        self.assertTrue(source.exists())
        self.assertEqual(target.read_bytes(),before)

    def test_failed_atomic_merge_keeps_original_and_retries(self):
        self.store.log('check',{},self.now[0])
        target=self.store.log_dir/'quota-starter-2026-10.jsonl'
        legacy=self.path.parent/'triggers';legacy.mkdir()
        source=legacy/'quota-triggers-2026-10.jsonl'
        source.write_bytes(target.read_bytes())
        with patch.object(Path,'replace',side_effect=PermissionError('locked')):
            Store(self.path,clock=lambda:self.now[0])
        self.assertTrue(source.exists())
        Store(self.path,clock=lambda:self.now[0])
        self.assertFalse(source.exists())
        self.assertEqual(len(target.read_text(encoding='utf-8').splitlines()),1)

    def test_reset_change_must_match_countdown_before_claiming_success(self):
        self.assertEqual(evidence(normalize(raw(20000)), normalize(raw(38002)), 20002, True),
                         '发送小额请求，ChatGPT将于5小时后刷新额度')
        self.assertNotIn('ChatGPT将于', evidence(normalize(raw(20000)), normalize(raw(50000)), 20002, True))
        self.assertNotIn('ChatGPT将于', evidence(normalize(raw(20000)), normalize(raw(38002)), 20002, False))


if __name__ == '__main__':
    unittest.main()
