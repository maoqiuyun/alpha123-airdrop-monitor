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
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
TZ = dt.timezone(dt.timedelta(hours=8))
URL = 'https://alpha123.uk/api/data?fresh=0'


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
    names = ', '.join(str(item.get('token') or item.get('name') or '新项目') for item in items)
    message = f'今日空投新增 {len(items)} 项：{names}。请查看 alpha123.uk/zh/'
    channel = os.environ.get('NOTIFY_CHANNEL', 'bark')
    if channel == 'bark':
        parsed = urllib.parse.urlsplit(os.environ['BARK_URL'])
        device_key = parsed.path.strip('/').split('/')[0]
        if parsed.scheme != 'https' or not parsed.netloc or not device_key:
            raise ValueError('BARK_URL must be an HTTPS device push URL')
        endpoint = f'https://{parsed.netloc}/push'
        result = request_json(endpoint, {'device_key': device_key,
            'title': 'Alpha123 今日空投', 'body': message, 'group': 'alpha123',
            'url': 'https://alpha123.uk/zh/'})
        if result.get('code') != 200:
            raise RuntimeError('Bark rejected notification')
    elif channel == 'telegram':
        endpoint = f'https://api.telegram.org/bot{os.environ["TELEGRAM_BOT_TOKEN"]}/sendMessage'
        result = request_json(endpoint, {'chat_id': os.environ['TELEGRAM_CHAT_ID'],
                                       'text': message})
        if result.get('ok') is not True:
            raise RuntimeError('Telegram rejected notification')
    else:
        raise ValueError('Unknown notification channel')


def request_json(url, payload=None, headers=None, method=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body,
        headers={'Content-Type': 'application/json', 'User-Agent': 'alpha123-monitor',
                 **(headers or {})}, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
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
        notify([{'token': '测试项目（模拟数据）'}])
        print('Test notification accepted by push service')
        return
    with (ROOT / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if args.fixture:
            data = json.loads(args.fixture.read_text())
        else:
            request = urllib.request.Request(URL, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://alpha123.uk/zh/'})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=30) as response:
                data = json.load(response)
        now = dt.datetime.now(TZ)
        items = today_items(data, now)
        state_path = ROOT / 'state.json'
        cloud = bool(os.environ.get('GITHUB_ACTIONS'))
        state = github_state() if cloud else (json.loads(state_path.read_text()) if state_path.exists() else {})
        seen = set(state.get('seen', [])) if state.get('date') == now.date().isoformat() else set()
        new_items = [item for item in items if key(item) not in seen]
        print(json.dumps({'checked_at': now.isoformat(), 'today_count': len(items),
                          'new_count': len(new_items), 'dry_run': args.dry_run}, ensure_ascii=False), flush=True)
        if args.dry_run:
            return
        if new_items:
            notify(new_items)
        seen.update(key(item) for item in items)
        next_state = {'date': now.date().isoformat(), 'seen': sorted(seen)}
        if cloud:
            if next_state != state:
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
        print(f'Monitor failed ({type(error).__name__}); check network and configured secrets.', file=sys.stderr)
        sys.exit(1)
