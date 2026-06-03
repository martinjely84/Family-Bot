"""
Desktop Trading-Bot Agent
-------------------------
Runs on YOUR desktop, right next to your trading bot. It connects *outbound*
to Telegram (long-polling) so you can control and monitor the bot from your
phone — no port forwarding, nothing exposed on your home network.

What it can do from Telegram:
  - 📸 /screenshot       — capture the desktop and send it as a photo
  - 📊 /status, /pnl     — show the trading bot's status & P&L
  - ▶️  /start_bot        — launch your trading bot
  - ⏹  /stop_bot         — stop your trading bot
  - 💻 /run <command>    — run a shell command and return the output
  - ❓ /help             — list commands

Security: only Telegram user IDs listed in ALLOWED_USER_IDS may use it.
Everyone else is ignored. This is a deliberately powerful agent (it can run
shell commands on your machine), so keep that whitelist tight and never share
the bot token.

Setup lives in README.md in this folder.
"""

import os
import io
import sys
import time
import shlex
import logging
import subprocess
from datetime import datetime

import requests

# Load a local .env file if present (so you don't have to export vars by hand).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except Exception:
    pass  # dotenv is optional; real env vars still work.

# Screenshot capture (cross-platform: Windows / macOS / Linux with a display)
try:
    import mss
    import mss.tools
    _HAVE_MSS = True
except Exception:  # pragma: no cover - optional until first screenshot
    _HAVE_MSS = False


# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger("desktop-agent")


# ── Config (from environment) ─────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["DESKTOP_BOT_TOKEN"]            # from @BotFather
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Comma-separated Telegram numeric user IDs allowed to control this machine.
# Find yours by messaging @userinfobot on Telegram.
ALLOWED_USER_IDS = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
}

# Command used to launch your trading bot, e.g. "python C:/trading/bot.py".
TRADING_BOT_CMD = os.environ.get("TRADING_BOT_CMD", "")

# JSON file your trading bot writes its status to (see README for the format).
STATUS_FILE = os.environ.get("STATUS_FILE", "status.json")

# Allow /run shell commands? Powerful — disable by setting ENABLE_SHELL=0.
ENABLE_SHELL = os.environ.get("ENABLE_SHELL", "1") not in ("0", "false", "False")

# Max seconds a /run command may take before we kill it.
SHELL_TIMEOUT = int(os.environ.get("SHELL_TIMEOUT", "30"))

# Telegram messages cap at ~4096 chars; leave headroom for formatting.
MAX_MSG = 3500

# Handle to the trading bot process we launched (if any).
_bot_proc: "subprocess.Popen | None" = None


# ── Telegram helpers ──────────────────────────────────────────────────────────
def send_message(chat_id: int, text: str):
    """Send a plain-text message, chunked if it's over Telegram's limit."""
    for i in range(0, len(text) or 1, MAX_MSG):
        chunk = text[i:i + MAX_MSG] or " "
        try:
            resp = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=15,
            )
            if not resp.ok:
                logger.warning("sendMessage failed: %s", resp.text)
        except requests.RequestException as e:
            logger.warning("sendMessage error: %s", e)


def send_photo(chat_id: int, image_bytes: bytes, caption: str = ""):
    """Send a PNG image (used for screenshots)."""
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("screenshot.png", image_bytes, "image/png")},
            timeout=30,
        )
        if not resp.ok:
            logger.warning("sendPhoto failed: %s", resp.text)
            send_message(chat_id, f"⚠️ Couldn't send screenshot: {resp.text}")
    except requests.RequestException as e:
        logger.warning("sendPhoto error: %s", e)
        send_message(chat_id, f"⚠️ Couldn't send screenshot: {e}")


# ── Command handlers ──────────────────────────────────────────────────────────
def cmd_help(chat_id: int):
    send_message(chat_id, (
        "🖥 Desktop Trading Agent\n\n"
        "/status — trading bot status & P&L\n"
        "/pnl — same as /status\n"
        "/screenshot — capture the desktop\n"
        "/start_bot — launch the trading bot\n"
        "/stop_bot — stop the trading bot\n"
        + ("/run <command> — run a shell command\n" if ENABLE_SHELL else "")
        + "/help — this message"
    ))


def cmd_screenshot(chat_id: int):
    if not _HAVE_MSS:
        send_message(chat_id, (
            "⚠️ Screenshot support not installed.\n"
            "Run: pip install mss Pillow"
        ))
        return
    try:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])  # monitors[0] = all screens combined
            png = mss.tools.to_png(shot.rgb, shot.size)
        caption = datetime.now().strftime("Desktop · %Y-%m-%d %H:%M:%S")
        send_photo(chat_id, png, caption)
    except Exception as e:
        logger.exception("screenshot failed")
        send_message(chat_id, f"⚠️ Screenshot failed: {e}")


def cmd_status(chat_id: int):
    """Report bot status from the JSON status file plus our process info."""
    running = _bot_proc is not None and _bot_proc.poll() is None
    lines = [f"🤖 Process: {'🟢 running' if running else '🔴 stopped'}"]
    if running:
        lines[0] += f" (pid {_bot_proc.pid})"

    if os.path.exists(STATUS_FILE):
        try:
            import json
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            lines.append("")
            # Highlight common fields first if present, then dump the rest.
            for key in ("status", "pnl", "pnl_today", "balance", "positions",
                        "open_trades", "last_update"):
                if key in data:
                    lines.append(f"{key}: {data.pop(key)}")
            for key, val in data.items():
                lines.append(f"{key}: {val}")
        except Exception as e:
            lines.append(f"\n⚠️ Couldn't read {STATUS_FILE}: {e}")
    else:
        lines.append(f"\n(no status file at {STATUS_FILE} yet)")

    send_message(chat_id, "\n".join(lines))


def cmd_start_bot(chat_id: int):
    global _bot_proc
    if not TRADING_BOT_CMD:
        send_message(chat_id, "⚠️ TRADING_BOT_CMD is not set — can't start the bot.")
        return
    if _bot_proc is not None and _bot_proc.poll() is None:
        send_message(chat_id, f"ℹ️ Already running (pid {_bot_proc.pid}).")
        return
    try:
        args = shlex.split(TRADING_BOT_CMD, posix=(os.name != "nt"))
        _bot_proc = subprocess.Popen(args)
        send_message(chat_id, f"▶️ Started trading bot (pid {_bot_proc.pid}).")
    except Exception as e:
        logger.exception("start_bot failed")
        send_message(chat_id, f"⚠️ Couldn't start: {e}")


def cmd_stop_bot(chat_id: int):
    global _bot_proc
    if _bot_proc is None or _bot_proc.poll() is not None:
        send_message(chat_id, "ℹ️ Trading bot isn't running (at least not one I started).")
        return
    try:
        _bot_proc.terminate()
        try:
            _bot_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _bot_proc.kill()
            _bot_proc.wait(timeout=5)
        send_message(chat_id, "⏹ Trading bot stopped.")
    except Exception as e:
        logger.exception("stop_bot failed")
        send_message(chat_id, f"⚠️ Couldn't stop cleanly: {e}")
    finally:
        _bot_proc = None


def cmd_run(chat_id: int, command: str):
    if not ENABLE_SHELL:
        send_message(chat_id, "⚠️ Shell commands are disabled (ENABLE_SHELL=0).")
        return
    if not command.strip():
        send_message(chat_id, "Usage: /run <command>")
        return
    send_message(chat_id, f"💻 Running: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT,
        )
        out = (result.stdout or "") + (result.stderr or "")
        out = out.strip() or "(no output)"
        send_message(chat_id, f"exit {result.returncode}:\n{out}")
    except subprocess.TimeoutExpired:
        send_message(chat_id, f"⚠️ Timed out after {SHELL_TIMEOUT}s.")
    except Exception as e:
        logger.exception("run failed")
        send_message(chat_id, f"⚠️ Error: {e}")


# ── Dispatch ──────────────────────────────────────────────────────────────────
def handle_message(message: dict):
    user_id = message.get("from", {}).get("id")
    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()

    if chat_id is None:
        return

    # Authorization gate — ignore anyone not on the whitelist.
    if user_id not in ALLOWED_USER_IDS:
        logger.warning("Ignored message from unauthorized user %s", user_id)
        return

    if not text:
        return

    # Normalize: strip a leading slash and any @BotName suffix.
    parts = text.split(maxsplit=1)
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd in ("help", "start"):
        cmd_help(chat_id)
    elif cmd == "screenshot":
        cmd_screenshot(chat_id)
    elif cmd in ("status", "pnl"):
        cmd_status(chat_id)
    elif cmd == "start_bot":
        cmd_start_bot(chat_id)
    elif cmd == "stop_bot":
        cmd_stop_bot(chat_id)
    elif cmd == "run":
        cmd_run(chat_id, arg)
    else:
        send_message(chat_id, "Unknown command. Send /help.")


# ── Main long-polling loop ──────────────────────────────────────────────────────
def main():
    if not ALLOWED_USER_IDS:
        logger.error("ALLOWED_USER_IDS is empty — refusing to start. "
                     "Set it to your Telegram user ID(s) so only you can control this machine.")
        sys.exit(1)

    logger.info("Desktop agent online. Authorized users: %s", sorted(ALLOWED_USER_IDS))
    offset = None
    # Drop any backlog so we don't replay old commands on restart.
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params={"offset": -1}, timeout=15)
        if r.ok and r.json().get("result"):
            offset = r.json()["result"][-1]["update_id"] + 1
    except requests.RequestException:
        pass

    while True:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": offset, "timeout": 30},
                timeout=40,
            )
            if not resp.ok:
                logger.warning("getUpdates failed: %s", resp.text)
                time.sleep(3)
                continue
            for update in resp.json().get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if msg:
                    try:
                        handle_message(msg)
                    except Exception:
                        logger.exception("handler crashed on update %s", update.get("update_id"))
        except requests.RequestException as e:
            logger.warning("polling error: %s", e)
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            break


if __name__ == "__main__":
    main()
