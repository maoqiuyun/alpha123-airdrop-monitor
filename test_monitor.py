import base64
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
    def test_discovery_and_each_countdown_only_once(self):
        item = {'id': 'event', 'token': 'US', 'date': '2026-10-08', 'time': '23:00'}
        data = {'airdrops': [item]}
        start = monitor.start_time(item)
        events, state = monitor.plan_notifications(data, {}, start - dt.timedelta(hours=6))
        self.assertEqual(events[0]['_notice'], '发现空投')
        for minute in monitor.REMINDERS:
            now = start - dt.timedelta(minutes=minute)
            events, state = monitor.plan_notifications(data, state, now)
            self.assertEqual(len(events), 1, minute)
            self.assertIn('提醒', events[0]['_notice'])
            events, state = monitor.plan_notifications(data, state, now + dt.timedelta(seconds=1))
            self.assertEqual(events, [])
        self.assertEqual(monitor.plan_notifications(data, state, start)[0], [])

    def test_late_discovery_does_not_send_old_reminders_and_delays_do_not_burst(self):
        item = {'id': 'event', 'token': 'US', 'date': '2026-10-08', 'time': '23:00'}
        start = monitor.start_time(item)
        data = {'airdrops': [item]}
        events, state = monitor.plan_notifications(data, {}, start - dt.timedelta(minutes=20))
        self.assertEqual([e['_notice'] for e in events], ['发现空投'])
        events, state = monitor.plan_notifications(data, state, start - dt.timedelta(minutes=4))
        self.assertEqual([e['_notice'] for e in events], ['开始前5分钟提醒'])
        self.assertEqual(monitor.plan_notifications(data, {}, start + dt.timedelta(minutes=1))[0], [])

    def test_future_day_and_unknown_time(self):
        now = dt.datetime(2026, 10, 7, 22, tzinfo=monitor.TZ)
        data = {'airdrops': [{'token': 'Tomorrow', 'date': '2026-10-08', 'time': '12:00'},
                             {'token': 'TBD', 'date': '2026-10-08'}]}
        events, state = monitor.plan_notifications(data, {}, now)
        self.assertEqual(len(events), 2)
        self.assertEqual(monitor.plan_notifications(data, state, now + dt.timedelta(hours=1))[0], [])

    def test_today_and_phase_two_rollover(self):
        now = dt.datetime(2026, 10, 6, 12, tzinfo=monitor.TZ)
        data = {'airdrops': [
            {'token': 'A', 'date': '2026-10-06', 'time': '10:00'},
            {'token': 'B', 'date': '2026-10-07'},
            {'token': 'C', 'date': '2026-10-05', 'time': '12:00', 'phase': 2}]}
        self.assertEqual([i['token'] for i in monitor.today_items(data, now)], ['A', 'C'])

    def test_bark_secret_is_sent_in_body(self):
        with patch.dict(os.environ, {'NOTIFY_CHANNEL': 'bark', 'BARK_URL': 'https://api.day.app/test-key'}):
            with patch.object(monitor, 'request_json', return_value={'code': 200}) as send:
                monitor.notify([{'token': 'TEST'}])
                url, body = send.call_args.args
                self.assertEqual(url, 'https://api.day.app/push')
                self.assertEqual(body['device_key'], 'test-key')

    def test_notify_failure_does_not_mark_seen_and_success_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'fixture.json'
            fixture.write_text(json.dumps({'airdrops': [{'token': 'TEST', 'date': dt.datetime.now(monitor.TZ).date().isoformat()}]}))
            with patch.object(monitor, 'ROOT', root), patch.dict(os.environ, {}, clear=True), patch('sys.argv', ['monitor.py', '--fixture', str(fixture)]):
                with patch.object(monitor, 'notify', side_effect=RuntimeError('test')):
                    with self.assertRaises(RuntimeError):
                        monitor.main()
                    self.assertFalse((root / 'state.json').exists())
                with patch.object(monitor, 'notify') as notify:
                    monitor.main()
                    monitor.main()
                    self.assertEqual(notify.call_count, 1)

    def test_weekend_scheduled_run_skips_fetch(self):
        for date in (dt.datetime(2026, 10, 10, 12, tzinfo=monitor.TZ),
                     dt.datetime(2026, 10, 11, 12, tzinfo=monitor.TZ)):
            class Clock(dt.datetime):
                @classmethod
                def now(cls, tz=None):
                    return date

            with patch.object(monitor.dt, 'datetime', Clock), patch.object(monitor.urllib.request, 'build_opener') as fetch:
                with patch('sys.argv', ['monitor.py']):
                    monitor.main()
                fetch.assert_not_called()

    def test_cloud_state_write_keeps_existing_sha(self):
        old = {'date': '2026-10-06', 'seen': []}
        record = {'sha': 'test-sha', 'content': base64.b64encode(json.dumps(old).encode()).decode()}
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'example/monitor', 'GH_STATE_TOKEN': 'test-token'}):
            with patch.object(monitor, 'request_json', return_value=record) as request:
                self.assertEqual(monitor.github_state(), old)
                monitor.github_state({'date': '2026-10-06', 'seen': ['new']})
                self.assertEqual(request.call_args.args[1]['sha'], 'test-sha')
                self.assertEqual(request.call_args.args[3], 'PUT')


if __name__ == '__main__':
    unittest.main()
