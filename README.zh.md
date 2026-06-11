[English](README.md) | 中文

# Claude Wake

**5 小时额度恢复后，自动叫醒 Claude Code 接着干。**

当你的交互式 Claude Code 撞到 5 小时额度上限时，Claude Wake 会从会话记录里读出
"几点恢复"，精确睡到那一刻，再用 tmux 把"继续"敲进你的会话，让 Claude 接着干。
当它触到三道停止闸之一时停下，并通知你。

这是一个很小的本地工具：**纯 Python 标准库 + tmux，不用安装任何东西。**
后端只绑定 `127.0.0.1`，且从不读取你的 Claude 凭据。

![dashboard](assets/screenshot.png)

---

## 工作原理

```
监视最新会话记录（~/.claude/projects/<编码后的 work_dir>/*.jsonl）
      |
      v
解析恢复时间
   - 优先：  "usage limit reached|<unix 时间戳>"   （精确）
   - 回退：  "resets 3pm" / "resets 1:40am"        （人类可读，按本地时区）
      |
      v
睡到恢复时刻（+ 缓冲），控制台里显示实时倒计时
      |
      v
tmux send-keys "继续"  ->  Claude 接着干
```

全程一个 Python 网页后端 + 一个看门狗线程，无外部依赖。注入发生在你那唯一的
活跃 tmux 会话里，所以永远不会冲突。

### 三道停止闸

1. **完成标记** —— Claude 在回复里输出 `ALL_DONE`（只在 assistant 回复里检测，
   所以指示语里回显 `ALL_DONE` 不会误触发）。
2. **停止时间** —— 你设的停点（如 `08:00`），到点后不再开新轮。
3. **最多轮数** —— 续够 N 轮后兜底刹车（`0` = 不限）。

---

## 快速上手

### Windows + WSL2

1. 把 `config.example.json` 复制为 `config.json`，填好 `work_dir` / `tmux_session`
   （或直接运行一次——会自动生成默认 `config.json`）。
2. **双击 `start.bat`**。它会打开三样东西：
   - 一个后端窗口（**关掉它 = 停止整个工具**）；
   - 控制台页面（有 Edge 就用 app 模式独立窗口，否则用默认浏览器）；
   - 一个跑着 Claude 的 tmux 终端。
3. 在 Claude 终端里派好活，然后到控制台选一个**停止时间**，点**开始**。
   去睡觉即可；额度恢复时它会替你叫 Claude 继续。

### Linux / macOS

```bash
cp config.example.json config.json   # 然后编辑 work_dir / tmux_session
./start.sh                           # 起后端 + 打开控制台
# 在另一个终端里把 Claude 跑在 tmux 里：
tmux new -A -s claude-work claude
```

---

## 配置说明

所有设置都在 `config.json`（已 gitignore）。你可以在控制台的**设置**抽屉里改，
无需手编 JSON。

| 字段 | 含义 |
|------|------|
| `port` | 控制台端口（默认 `8770`），改了需重启后端。 |
| `tmux_session` | Claude 所在的 tmux 会话名（默认 `claude-work`）。 |
| `work_dir` | Claude 的工作目录，用于定位会话记录。留空 = 扫所有项目取最新。 |
| `claude_launch_args` | `start_claude.sh` 启动 `claude` 时追加的参数。 |
| `continue_text` | 恢复后注入的续跑文本。 |
| `done_marker` | 完成标记（默认 `ALL_DONE`）。 |
| `poll_sec` | 轮询间隔秒（默认 `30`）。 |
| `buffer_sec` | 恢复时刻后再多等的秒数（默认 `60`）。 |
| `telegram_bot_token` | 与 `telegram_chat_id` 一起填，启用 Telegram 通知（手机收）。 |
| `telegram_chat_id` | Telegram chat id。 |
| `default_until` | 控制台"几点停"的默认值。 |
| `default_max_rounds` | 默认最多轮数（`0` = 不限）。 |
| `lang` | 通知语言：`en` 或 `zh`。 |

---

## 通知

Claude Wake 停止时会通知你，按可用性依次尝试：

1. **Telegram** —— 填了 `telegram_bot_token` + `telegram_chat_id`（手机收）。
2. **Linux 桌面** —— `notify-send`。
3. **macOS** —— `osascript` 通知。
4. **Windows** —— `powershell.exe` 弹窗。

可用**高级 -> 测试：发一条通知**按钮验证通路。

---

## 安全

- 后端**只绑定 `127.0.0.1`**，外网不可达。
- **从不读取你的 Claude 凭据**；只读会话记录文本和 tmux 屏幕，用于检测撞限与恢复时间。
- 你的 Telegram token 存在本地、**已 gitignore** 的 `config.json` 里；控制台把它脱敏为
  `***`，从不回显。

---

## 局限

- 依赖 Claude CLI 会话记录 / 屏幕的文案格式。若未来 CLI 版本改了措辞，解析恢复时间的
  正则可能需要更新。
- 电脑和 WSL 必须**整夜开着**——别 `wsl --shutdown`、别休眠/断电，否则后台进程会停。
- 需要 **tmux**（Claude 必须跑在 tmux 会话里，`send-keys` 才能注入）。

---

## 常见问题

**为什么必须用 tmux？**
Claude Wake 用 `tmux send-keys` 注入"继续"。tmux 提供一个稳定、有名字的会话作为目标；
没有它就没有可靠的办法往你的活跃会话里打字。

**控制台显示"没找到交互会话"。**
说明 Claude 没跑在 tmux 里。运行 `tmux new -A -s claude-work claude`（或双击 `start.bat`）。

**点"开始"没反应 / 页面打不开。**
确认后端窗口还开着，且 `port` 没被占用。

**到点没自动续。**
看 `logs/run-*.log`，里面记了它解析到的恢复时间。最可能是会话记录的措辞没被正则匹配上。

**drive 模式是什么？**
默认关。开启后，即使没撞限，每当 Claude 一轮自然结束也补送"继续"，持续推进多轮自治。
更费额度，只靠 `ALL_DONE` 才停。整夜桥接撞限用不到它。

---

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
