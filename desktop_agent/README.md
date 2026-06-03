# Desktop Trading-Bot Agent

Control and monitor your **custom Python trading bot** from Telegram — from
anywhere, on your phone — while it keeps running on your home desktop.

This agent runs **on your desktop**. It connects *outbound* to Telegram by
long-polling, so:

- ✅ No port forwarding, no firewall changes, nothing exposed on your network.
- ✅ Works behind any home router.
- ✅ Only Telegram user IDs you whitelist can control the machine.

## What you can do from Telegram

| Command | What it does |
|---|---|
| `/status` or `/pnl` | Show the trading bot's status & P&L (from a status file) |
| `/screenshot` | Capture the desktop and send it as a photo |
| `/start_bot` | Launch your trading bot |
| `/stop_bot` | Stop your trading bot |
| `/run <command>` | Run a shell command and return the output |
| `/help` | List commands |

> ⚠️ **This is a powerful agent** — `/run` executes arbitrary shell commands and
> `/screenshot` can capture sensitive on-screen info. Keep `ALLOWED_USER_IDS`
> tight, never share the bot token, and set `ENABLE_SHELL=0` if you don't want
> remote shell access.

---

## Setup (5 minutes)

### 1. Create a dedicated Telegram bot

In Telegram, message **@BotFather** → `/newbot` → pick a name/username. Copy the
token it gives you (looks like `123456789:ABCdef...`).

> Use a **separate** bot from your Family-Bot. Telegram only lets one program
> receive a given bot's messages at a time, and Family-Bot already uses its
> token via webhook.

### 2. Find your Telegram user ID

Message **@userinfobot** in Telegram. It replies with your numeric `Id`. That's
what goes in `ALLOWED_USER_IDS`.

### 3. Install on your desktop

```bash
cd desktop_agent
pip install -r requirements.txt
cp .env.example .env      # Windows: copy .env.example .env
```

Then edit `.env`:

```ini
DESKTOP_BOT_TOKEN=123456789:ABCdef...      # from BotFather
ALLOWED_USER_IDS=11111111                  # your @userinfobot Id (comma-separate for more)
TRADING_BOT_CMD=python C:/trading/bot.py   # how to launch your bot
STATUS_FILE=status.json                    # where your bot writes status
ENABLE_SHELL=1                             # set 0 to disable /run
```

### 4. Run the agent

```bash
python agent.py
```

You should see `Desktop agent online.` Now message your bot `/help` in Telegram.

---

## Wiring up `/status` and `/pnl`

The agent reads a small JSON file your trading bot writes. Add one call to your
bot using the included helper (`status_writer_example.py`):

```python
from desktop_agent.status_writer_example import write_status

# call this in your trading loop / after each trade
write_status(
    status="running",
    pnl=123.45,
    pnl_today=12.30,
    balance=10250.00,
    open_trades=2,
    positions="BTC long 0.5, ETH short 2",
)
```

Any fields you pass are displayed. If you don't add this, `/status` still
reports whether the process is running.

---

## Keeping it running

- **Windows:** create a shortcut to `pythonw agent.py`, or use Task Scheduler
  ("At log on").
- **macOS:** a `launchd` plist, or just `nohup python3 agent.py &`.
- **Linux:** a `systemd --user` service, or `nohup python3 agent.py &`.

The agent drops any backlog of messages on startup, so restarting it won't
replay old commands.

---

## Security notes

- Commands from any user not in `ALLOWED_USER_IDS` are logged and ignored.
- The bot token is the only secret that matters — anyone with it can message
  the bot, but they still can't run anything unless their user ID is whitelisted.
- Consider `ENABLE_SHELL=0` and relying on `/start_bot` / `/stop_bot` /
  `/status` only, if you don't need full shell access.
