# Mac 本地通知

`mac/monitor.py` 与仓库根目录的云端脚本使用相同的空投检测和提醒规则。在 Mac 上运行时，它使用 `terminal-notifier` 显示桌面通知，每 5 分钟检查一次；北京时间 00:00–10:00 不执行定时检查。

在 Apple silicon Mac 上安装：

```sh
brew install terminal-notifier
zsh mac/install.sh
```

安装脚本会按当前仓库路径生成 `~/Library/LaunchAgents/co.local.alpha123-monitor.plist` 并启动任务。已有同名任务时会停止，避免覆盖正在使用的配置。测试当前网页数据及通知：

```sh
python3 mac/monitor.py --test-notification
```

Mac 通知需要在系统设置中允许 `terminal-notifier` 发送通知。脚本运行后生成的 `state.json`、`monitor.lock` 和日志仅保存在本机，不会提交到 GitHub。
