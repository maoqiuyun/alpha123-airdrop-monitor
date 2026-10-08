#!/bin/zsh
set -eu

script_dir="${0:A:h}"
task_label="co.local.alpha123-monitor"
task_target="$HOME/Library/LaunchAgents/$task_label.plist"
python_bin="$(command -v python3)"

if [[ ! -x /opt/homebrew/bin/terminal-notifier ]]; then
  print -u2 "Install terminal-notifier first: brew install terminal-notifier"
  exit 1
fi

if [[ -e "$task_target" ]]; then
  print -u2 "A schedule already exists at $task_target; refusing to overwrite."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
"$python_bin" - "$task_target" "$python_bin" "$script_dir" <<'PY'
import plistlib
import sys
from pathlib import Path

target, python_bin, script_dir = sys.argv[1:]
script_dir = Path(script_dir)
payload = {
    'Label': 'co.local.alpha123-monitor',
    'ProcessType': 'Background',
    'ProgramArguments': [python_bin, str(script_dir / 'monitor.py')],
    'RunAtLoad': True,
    'StandardErrorPath': str(script_dir / 'error.log'),
    'StandardOutPath': str(script_dir / 'monitor.log'),
    'StartInterval': 300,
}
with open(target, 'wb') as output:
    plistlib.dump(payload, output)
PY

launchctl bootstrap "gui/$(id -u)" "$task_target"
launchctl print "gui/$(id -u)/$task_label"
