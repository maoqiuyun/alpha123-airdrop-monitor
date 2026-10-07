#!/usr/bin/env python3
"""Check Alpha123 today's airdrops and send deduplicated Mac notifications."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import base64
import sys
import subprocess
import copy
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
URL = 'https://alpha123.uk/api/data?fresh=0'
REMINDERS = (300, 240, 180, 120, 60, 30, 15, 5)


def start_time(item):
    if not item.get('date') or not item.get('time'):
        return None
    stamp = dt.datetime.fromisoformat(f'{item["date"]}T{item["time"]}').replace(tzinfo=TZ)
    if item.get('phase') == 2:
        stamp += dt.timedelta(hours=18)
    return stamp


def plan_notifications(data, state, now):
    if not isinstance(data, dict) or not isinstance(data.get('airdrops'), list):
        raise ValueError('Invalid airdrop API response')
    records = copy.deepcopy(state.get('records', {}))
    legacy_seen = set(state.get('seen', [])) if state.get('date') == now.date().isoformat() else set()
    events = []
    for item in data['airdrops']:
        stamp = start_time(item)
        if stamp and stamp <= now:
            continue
        if stamp is None and item.get('date') and item['date'] < now.date().isoformat():
            continue
        if stamp is None and not item.get('date') and str(item.get('status', '')).lower() not in ('ongoing', 'active', 'live'):
            continue
        identity = key(item)
        record = records.get(identity)
        crossed = [minute for minute in REMINDERS if stamp and now >= stamp - dt.timedelta(minutes=minute)]
        if record is None:
            record = {'discovered_at': now.isoformat(), 'reminders': crossed}
            records[identity] = record
            if identity not in legacy_seen:
                events.append({**item, '_notice': '发现空投'})
            continue
        if crossed:
            latest = crossed[-1]
            if latest not in record['reminders']:
                label = f'{latest // 60}小时' if latest >= 60 else f'{latest}分钟'
                events.append({**item, '_notice': f'开始前{label}提醒'})
                # A delayed check sends only the latest reminder, never a burst of older ones.
                record['reminders'] = sorted(set(record['reminders']) | set(crossed), reverse=True)
    records = {identity: record for identity, record in records.items()
               if dt.datetime.fromisoformat(record['discovered_at']) > now - dt.timedelta(days=30)}
    return events, {'version': 2, 'records': records}


def today_items(data, now):
    if not isinstance(data, dict) or not isinstance(data.get('airdrops'), list):
        raise ValueError('Invalid airdrop API response')
    result = []
    for item in data['airdrops']:
        date, time = item.get('date'), item.get('time')
        if date:
            stamp = dt.datetime.fromisoformat(f'{date}T{time or "14:00"}').replace(tzinfo=TZ)
            if time and item.get('phase') == 2:
                stamp += dt.timedelta(hours=18)
            included = stamp.date() == now.date()
        elif str(item.get('status', '')).lower() in ('ongoing', 'active', 'live'):
            timestamp = item.get('system_timestamp')
            included = not timestamp or dt.datetime.fromtimestamp(float(timestamp), TZ).date() == now.date()
        else:
            included = False
        if included:
            result.append(item)
    return result


def key(item):
    fields = {field: item.get(field) for field in ('id', 'token', 'date', 'time', 'phase', 'type')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def notify(items):
    lines = []
    for item in items:
        name = item.get('token') or item.get('name') or '新项目'
        stamp = start_time(item)
        time_text = stamp.strftime('%m月%d日 %H:%M（北京时间）') if stamp else '时间待公布'
        lines.append(f'{item.get("_notice", "发现空投")}：{name}；空投时间：{time_text}')
    message = '\n'.join(lines)
    channel = os.environ.get('NOTIFY_CHANNEL', 'bark' if os.environ.get('GITHUB_ACTIONS') or os.environ.get('BARK_URL') else 'mac')
    if channel == 'bark':
        parsed = urllib.parse.urlsplit(os.environ['BARK_URL'])
        device_key = parsed.path.strip('/').split('/')[0]
        if parsed.scheme != 'https' or not parsed.netloc or not device_key:
            raise ValueError('BARK_URL must be an HTTPS device push URL')
        endpoint = f'https://{parsed.netloc}/push'
        result = request_json(endpoint, {'device_key': device_key,
            'title': '币安空投提醒', 'body': message, 'group': 'alpha123',
            'url': 'https://alpha123.uk/zh/'})
        if result.get('code') != 200:
            raise RuntimeError('Bark rejected notification')
    elif channel == 'telegram':
        endpoint = f'https://api.telegram.org/bot{os.environ["TELEGRAM_BOT_TOKEN"]}/sendMessage'
        result = request_json(endpoint, {'chat_id': os.environ['TELEGRAM_CHAT_ID'],
                                       'text': message})
        if result.get('ok') is not True:
            raise RuntimeError('Telegram rejected notification')
    elif channel == 'mac':
        subprocess.run(['/opt/homebrew/bin/terminal-notifier', '-title', '币安空投提醒',
                        '-message', message, '-sound', 'Glass', '-open', 'https://alpha123.uk/zh/',
                        '-group', 'alpha123-today'], check=True, timeout=20)
    else:
        raise ValueError('Unknown notification channel')


def request_json(url, payload=None, headers=None, method=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body,
        headers={'Content-Type': 'application/json', 'User-Agent': 'alpha123-monitor',
                 **(headers or {})}, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def public_data():
    request = urllib.request.Request(URL, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        'Referer': 'https://alpha123.uk/zh/', 'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def github_state(state=None):
    repo = os.environ['GITHUB_REPOSITORY']
    token = os.environ['GH_STATE_TOKEN']
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}
    endpoint = f'https://api.github.com/repos/{repo}/contents/monitor-state.json'
    try:
        record = request_json(endpoint, headers=headers)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        record = None
    if state is None:
        return json.loads(base64.b64decode(record['content'])) if record else {}
    payload = {'message': 'Update airdrop reminder state [skip ci]',
               'content': base64.b64encode(json.dumps(state).encode()).decode()}
    if record:
        payload['sha'] = record['sha']
    request_json(endpoint, payload, headers, 'PUT')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--test-notification', action='store_true')
    args = parser.parse_args()
    if args.test_notification:
        items = today_items(public_data(), dt.datetime.now(TZ))
        if not items:
            raise ValueError('No current-day airdrops available for this test')
        notify([{**item, '_notice': '测试通知（当前页面数据）'} for item in items])
        print(json.dumps({'test_airdrops': [{'name': item.get('token') or item.get('name'),
              'beijing_time': start_time(item).isoformat() if start_time(item) else None} for item in items]}, ensure_ascii=False))
        print('Test notification accepted by push service')
        return
    now = dt.datetime.now(TZ)
    if now.hour < 10 and not args.dry_run and not args.fixture:
        print(json.dumps({'checked_at': now.isoformat(), 'skipped': 'quiet_hours'}), flush=True)
        return
    with (ROOT / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if args.fixture:
            data = json.loads(args.fixture.read_text())
        else:
            print('Reading public airdrop data', flush=True)
            request = urllib.request.Request(URL, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
                'Referer': 'https://alpha123.uk/zh/', 'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(request, timeout=30) as response:
                    data = json.load(response)
            except urllib.error.HTTPError as error:
                print(f'Public website response: HTTP {error.code}; server={error.headers.get("Server", "unknown")}', flush=True)
                raise
        now = dt.datetime.now(TZ)
        items = today_items(data, now)
        print('Public data read successfully', flush=True)
        state_path = ROOT / 'state.json'
        cloud = bool(os.environ.get('GITHUB_ACTIONS'))
        print('Reading notification state', flush=True)
        state = github_state() if cloud else (json.loads(state_path.read_text()) if state_path.exists() else {})
        new_items, next_state = plan_notifications(data, state, now)
        print(json.dumps({'checked_at': now.isoformat(), 'today_count': len(items),
                          'new_count': len(new_items), 'dry_run': args.dry_run}, ensure_ascii=False), flush=True)
        if args.dry_run:
            return
        if new_items:
            notify(new_items)
        if cloud:
            if next_state != state:
                print('Saving notification state', flush=True)
                github_state(next_state)
        else:
            temporary = state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(next_state))
            os.replace(temporary, state_path)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Do not print URLs/errors that can contain push credentials.
        status = f' HTTP {error.code}' if isinstance(error, urllib.error.HTTPError) else ''
        print(f'Monitor failed ({type(error).__name__}){status}; check network and configured secrets.', file=sys.stderr)
        sys.exit(1)
