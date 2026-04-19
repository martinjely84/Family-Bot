"""
Family Telegram Bot
--------------------
A group chat bot for you and your wife with:
  - AI-powered advice & chat (via Claude)
  - Shared to-do list
  - Reminders sent to the group
  - Apple Shared Calendar (read & add events)

Add the bot to your Telegram group and everyone chats together!
"""

import os
import re
import logging
import requests
from flask import Flask, request, jsonify
from anthropic import Anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import database as db
from datetime import datetime, date, timedelta
import dateparser
import calendar_helper as cal

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App & Clients ─────────────────────────────────────────────────────────────
app = Flask(__name__)

anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BOT_USERNAME    = os.environ.get("BOT_USERNAME", "")  # e.g. FamilyElyBot (without @)

# In-memory conversation history per chat
conversation_history: dict[str, list] = {}


# ── Telegram Helpers ──────────────────────────────────────────────────────────
def send_message(chat_id: str, text: str):
    """Send a Markdown message to a Telegram chat."""
    try:
        resp = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not resp.ok:
            logger.warning(f"Telegram sendMessage failed: {resp.text}")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if not data or "message" not in data:
        return jsonify({"ok": True})

    message   = data["message"]
    chat_id   = str(message["chat"]["id"])
    text      = message.get("text", "").strip()
    from_user = message["from"].get("first_name", "Someone")

    if not text:
        return jsonify({"ok": True})

    # Save chat_id so the reminder scheduler knows where to send messages
    db.save_chat_id(chat_id)

    # Strip bot @mention from group messages
    raw_text = text
    if BOT_USERNAME:
        text = text.replace(f"@{BOT_USERNAME}", "").strip()

    # In group chats, respond to everything (it's a private family group)
    # To only respond when @mentioned, uncomment the block below:
    # chat_type = message["chat"]["type"]
    # if chat_type in ("group", "supergroup"):
    #     is_mention = BOT_USERNAME and f"@{BOT_USERNAME}" in raw_text
    #     is_command = text.startswith("/")
    #     if not (is_mention or is_command):
    #         return jsonify({"ok": True})

    reply = process_message(text, chat_id, from_user)
    if reply:
        send_message(chat_id, reply)

    return jsonify({"ok": True})


# ── Message Router ────────────────────────────────────────────────────────────
def process_message(text: str, chat_id: str, from_user: str) -> str:
    lower = re.sub(r"^/", "", text.lower().strip())

    if lower in ("help", "?", "start"):
        return get_help_text()

    if lower.startswith("todo"):
        return handle_todo(text, chat_id, from_user)

    if re.search(r"\bremind\b", lower):
        return handle_reminder(text, chat_id, from_user)

    if lower.startswith("reminders"):
        return list_reminders(chat_id)

    if re.match(r"^(cal|calendar)\b", lower):
        return handle_calendar(text, chat_id)

    if re.search(r"\b(what('?s| is) on|what do (we|i) have|any events?|schedule)\b", lower):
        return handle_calendar(text, chat_id)

    return handle_ai_chat(text, chat_id, from_user)


# ── To-Do Handler ─────────────────────────────────────────────────────────────
def handle_todo(text: str, chat_id: str, from_user: str) -> str:
    text  = re.sub(r"^/", "", text.strip())
    parts = text.split(None, 2)
    action = parts[1].lower() if len(parts) > 1 else "list"

    if action == "list":
        todos = db.get_todos()
        if not todos:
            return "Your family to-do list is empty! 🎉"
        lines = ["📝 *Family To-Do List*\n"]
        for i, row in enumerate(todos, 1):
            tick = "✅" if row["done"] else "⬜"
            lines.append(f"{tick} {i}. {row['item']}")
        return "\n".join(lines)

    if action == "add":
        if len(parts) < 3:
            return "What should I add? e.g. _todo add buy milk_"
        db.add_todo(parts[2], from_user)
        return f"✅ Added: _{parts[2]}_"

    if action in ("done", "complete", "tick"):
        if len(parts) < 3:
            return "Which item? e.g. _todo done 2_"
        try:
            item = db.complete_todo(int(parts[2]))
            return f"🎉 Done: _{item}_" if item else "Item not found. Check _todo list_."
        except ValueError:
            return "Please give the item number, e.g. _todo done 2_"

    if action in ("delete", "remove", "del"):
        if len(parts) < 3:
            return "Which item? e.g. _todo delete 2_"
        try:
            item = db.delete_todo(int(parts[2]))
            return f"🗑️ Deleted: _{item}_" if item else "Item not found."
        except ValueError:
            return "Please give the item number, e.g. _todo delete 2_"

    if action == "clear":
        db.clear_completed()
        return "🧹 Cleared all completed items!"

    return "Todo commands: add, list, done [#], delete [#], clear"


# ── Reminder Handler ──────────────────────────────────────────────────────────
def handle_reminder(text: str, chat_id: str, from_user: str) -> str:
    parsed = parse_reminder(text)
    if not parsed:
        return (
            "I couldn't understand that reminder. Try:\n"
            "• _remind me in 2 hours to take medicine_\n"
            "• _remind us tomorrow at 9am to call the school_\n"
            "• _remind me on Friday to pay the electric bill_"
        )
    remind_at, message, _ = parsed
    db.add_reminder(message, remind_at, chat_id, from_user)
    time_str = remind_at.strftime("%A, %d %b at %-I:%M %p")
    return f"⏰ Reminder set for {time_str}:\n_{message}_"


def parse_reminder(text: str):
    send_to_both = bool(re.search(r"\bremind\s+us\b", text, re.IGNORECASE))
    clean = re.sub(r"^remind\s+(me|us)\s+", "", text, flags=re.IGNORECASE).strip()
    to_match = re.search(r"\bto\s+(.+)$", clean, re.IGNORECASE)
    if not to_match:
        return None
    message  = to_match.group(1).strip()
    time_str = clean[: to_match.start()].strip()
    parsed_time = dateparser.parse(
        time_str,
        settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False},
    )
    if not parsed_time:
        return None
    return parsed_time, message, send_to_both


def list_reminders(chat_id: str) -> str:
    reminders = db.get_upcoming_reminders(chat_id)
    if not reminders:
        return "No upcoming reminders."
    lines = ["⏰ *Upcoming Reminders*\n"]
    for r in reminders:
        dt = datetime.fromisoformat(r["remind_at"])
        time_str = dt.strftime("%a %d %b, %-I:%M %p")
        lines.append(f"• {time_str}: _{r['message']}_")
    return "\n".join(lines)


# ── Calendar Handler ──────────────────────────────────────────────────────────
def handle_calendar(text: str, chat_id: str) -> str:
    if not cal.is_configured():
        return (
            "📅 Calendar isn't set up yet.\n"
            "Add your iCloud credentials in Railway variables to get started."
        )

    lower = text.lower().strip()
    body  = re.sub(r"^(cal|calendar)\s*", "", lower).strip()
    today = date.today()

    if body in ("list calendars", "calendars", "show calendars"):
        names = cal.list_calendars()
        return ("📅 Your iCloud calendars:\n" + "\n".join(f"• {n}" for n in names)) if names else "No calendars found."

    add_match = re.match(
        r"^add\s+(.+?)\s+(?:on\s+)?(.+?)(?:\s+for\s+(\d+)\s*(hour|hr|min|minute)s?)?$",
        body, re.IGNORECASE,
    )
    if add_match:
        title, time_text = add_match.group(1).strip(), add_match.group(2).strip()
        dur_num, dur_unit = add_match.group(3), add_match.group(4)
        duration_mins = 60
        if dur_num and dur_unit:
            n = int(dur_num)
            duration_mins = n * 60 if dur_unit.startswith("hour") or dur_unit == "hr" else n
        parsed_dt = dateparser.parse(time_text, settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False})
        if not parsed_dt:
            return f"I couldn't understand '{time_text}'.\nTry: _cal add dentist on Friday at 3pm_"
        all_day = "am" not in time_text.lower() and "pm" not in time_text.lower() and ":" not in time_text
        if cal.add_event(title, parsed_dt, duration_minutes=duration_mins, all_day=all_day):
            label = parsed_dt.strftime("%A %-d %b") if all_day else parsed_dt.strftime("%A %-d %b at %-I:%M %p")
            return f"📅 Added: _{title}_ on {label}"
        return "Sorry, couldn't add that event. Check your iCloud credentials."

    if "tomorrow" in body:
        target = today + timedelta(days=1)
        events = cal.get_events_for_day(target)
        label  = _day_label(target)
        return (f"📅 *{label}*\n" + "\n".join(events)) if events else f"📅 Nothing on the calendar for {label}."
    elif "weekend" in body:
        days_ahead = 5 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        target = today + timedelta(days=days_ahead)
        return _format_range(cal.get_events_for_range(target, target + timedelta(days=2)), "Weekend")
    elif any(w in body for w in ("week", "this week", "next 7 days")):
        return _format_range(cal.get_events_for_range(today, today + timedelta(days=7)), "This week")
    elif body in ("", "show", "upcoming", "today"):
        target = today
        if "today" in body or body == "":
            events = cal.get_events_for_day(today)
            label  = _day_label(today)
            return (f"📅 *{label}*\n" + "\n".join(events)) if events else f"📅 Nothing on the calendar for {label}."
        return _format_range(cal.get_events_for_range(today, today + timedelta(days=7)), "Upcoming (next 7 days)")
    else:
        parsed_dt = dateparser.parse(body, settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": False})
        target = parsed_dt.date() if parsed_dt else today
        events = cal.get_events_for_day(target)
        label  = _day_label(target)
        return (f"📅 *{label}*\n" + "\n".join(events)) if events else f"📅 Nothing on the calendar for {label}."


def _day_label(d: date) -> str:
    today = date.today()
    if d == today:
        return "Today, " + d.strftime("%-d %b")
    if d == today + timedelta(days=1):
        return "Tomorrow, " + d.strftime("%-d %b")
    return d.strftime("%A %-d %b")


def _format_range(grouped: dict, heading: str) -> str:
    if not grouped:
        return f"📅 Nothing on the calendar for {heading.lower()}."
    lines = [f"📅 *{heading}*\n"]
    for day_label, events in grouped.items():
        lines.append(f"*{day_label}*")
        lines.extend(events)
        lines.append("")
    return "\n".join(lines).strip()


# ── AI Chat Handler ───────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a warm, practical family assistant for Martin and his wife, in their shared Telegram group.
You help with family organisation, life and relationship advice, parenting tips, household management, and general questions.
Keep responses concise and friendly. Use emojis sparingly. Be empathetic first when someone seems stressed.
You manage a shared to-do list (todo add/list/done), reminders (remind me/us...), and their Apple Calendar (cal today/week/add).
Mention these features when relevant."""


def handle_ai_chat(text: str, chat_id: str, from_user: str) -> str:
    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": f"{from_user}: {text}"})
    if len(history) > 20:
        history = history[-20:]
        conversation_history[chat_id] = history
    try:
        response = anthropic_client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            system=SYSTEM_PROMPT,
            messages=history,
        )
        reply = response.content[0].text
        history.append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Sorry, I'm having a moment! Please try again shortly."


# ── Help Text ─────────────────────────────────────────────────────────────────
def get_help_text() -> str:
    return (
        "👋 *Family Bot — Quick Guide*\n\n"
        "*💬 Chat & Advice*\n"
        "Just type anything!\n\n"
        "*📅 Apple Calendar*\n"
        "`cal today` / `cal tomorrow` / `cal week`\n"
        "`cal add dentist on Friday at 3pm`\n"
        "`what's on this weekend?`\n\n"
        "*📝 To-Do List*\n"
        "`todo add buy milk`\n"
        "`todo list`\n"
        "`todo done 2` / `todo delete 3` / `todo clear`\n\n"
        "*⏰ Reminders*\n"
        "`remind me in 2 hours to take medicine`\n"
        "`remind us tomorrow at 9am to call school`\n"
        "`reminders` — see upcoming ones\n\n"
        "Type `help` any time 😊"
    )


# ── Reminder Scheduler ────────────────────────────────────────────────────────
def send_due_reminders():
    due = db.get_due_reminders()
    for r in due:
        try:
            send_message(r["chat_id"], f"⏰ *Reminder*: {r['message']}")
            db.mark_reminder_sent(r["id"])
        except Exception as e:
            logger.error(f"Failed to send reminder {r['id']}: {e}")


scheduler = BackgroundScheduler()
scheduler.add_job(send_due_reminders, IntervalTrigger(minutes=1))
scheduler.start()


# ── Startup ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    db.init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
