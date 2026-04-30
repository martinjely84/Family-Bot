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
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
import anthropic
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import database as db
from duckduckgo_search import DDGS
from datetime import datetime, date, timedelta
import dateparser
import calendar_helper as cal

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── App & Clients ─────────────────────────────────────────────────────────────
app = Flask(__name__)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
}

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

    if re.match(r"^(search|google|find|look up)\b", lower):
        return handle_search(text)

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


# ── Web Search Handler ────────────────────────────────────────────────────────
def handle_search(text: str) -> str:
    query = re.sub(r"^(search|google|find|look up)\s+", "", text, flags=re.IGNORECASE).strip()
    if not query:
        return "What would you like me to search for?"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=4))
        if not results:
            return f"No results found for: _{query}_"
        lines = [f"🔍 *Search results for: {query}*\n"]
        for r in results:
            lines.append(f"*{r['title']}*")
            lines.append(r['body'])
            lines.append(f"_{r['href']}_\n")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return "Sorry, search isn't working right now. Try again shortly."


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
        resp = requests.post(
            ANTHROPIC_URL,
            headers=ANTHROPIC_HEADERS,
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 800,
                "system": SYSTEM_PROMPT,
                "messages": history,
            },
            timeout=30,
        )
        resp.raise_for_status()
        reply = resp.json()["content"][0]["text"]
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
        "*🔍 Web Search*\n"
        "`search Braintopia Houston reviews`\n"
        "`google autism friendly restaurants near me`\n\n"
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


# ── Life Assessment Dashboard ─────────────────────────────────────────────────
LIFE_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Life Assessment — Bar's View</title>
<style>
  :root {
    --bg: #f7f5f1; --card: #ffffff; --ink: #2a2a2a; --muted: #777;
    --accent: #c8956d; --accent-soft: #f3e7d9; --border: #e6e1d8;
    --good: #6fa77a; --warn: #d49a4a; --bad: #c46a6a;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
         background: var(--bg); color: var(--ink); line-height: 1.5; }
  header { padding: 32px 24px 8px; max-width: 980px; margin: 0 auto; }
  h1 { margin: 0 0 4px; font-size: 28px; font-weight: 600; }
  .sub { color: var(--muted); font-size: 14px; }
  main { max-width: 980px; margin: 0 auto; padding: 16px 24px 64px; display: grid; gap: 20px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px 22px; }
  .card h2 { margin: 0 0 14px; font-size: 18px; font-weight: 600; }
  .row { display: flex; align-items: center; gap: 10px; padding: 10px 0; border-bottom: 1px dashed var(--border); }
  .row:last-child { border-bottom: 0; }
  .row .title { flex: 1; }
  .row .meta { color: var(--muted); font-size: 12px; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px;
          background: var(--accent-soft); color: var(--accent); margin-right: 6px; }
  .rating { font-weight: 600; min-width: 36px; text-align: right; }
  .r-good { color: var(--good); } .r-warn { color: var(--warn); } .r-bad { color: var(--bad); }
  form.inline { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  input, select, textarea, button {
    font: inherit; padding: 8px 10px; border-radius: 8px;
    border: 1px solid var(--border); background: #fff; color: var(--ink);
  }
  input[type=text], textarea { flex: 1; min-width: 180px; }
  textarea { width: 100%; min-height: 60px; resize: vertical; }
  button { background: var(--accent); color: white; border: 0; cursor: pointer; }
  button.secondary { background: transparent; color: var(--muted); border: 1px solid var(--border); }
  button:hover { filter: brightness(0.96); }
  .empty { color: var(--muted); font-style: italic; padding: 8px 0; }
  .assess-block { margin-top: 8px; padding: 10px; background: var(--accent-soft);
                  border-radius: 10px; font-size: 13px; color: #5a4a3a; }
  .assess-block .stamp { color: var(--muted); font-size: 11px; margin-top: 4px; }
  details summary { cursor: pointer; color: var(--accent); font-size: 13px; padding: 4px 0; }
  .grid { display: grid; gap: 10px; }
  @media (min-width: 720px) { .grid-2 { grid-template-columns: 1fr 1fr; } }
  .toolbar { display: flex; gap: 8px; align-items: center; }
  .toolbar form { display: inline; }
  .strike { text-decoration: line-through; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>🌿 Life Assessment</h1>
  <div class="sub">A space for Bar to weigh in on goals, todos, and aspirations. Today: {{ today }}</div>
</header>
<main>

  {# ── Overall ── #}
  <section class="card">
    <h2>Overall Life Score</h2>
    {% if overall %}
      <div class="row">
        <div class="title">
          Latest from <strong>{{ overall.assessor }}</strong>
          {% if overall.note %}<div class="meta">"{{ overall.note }}"</div>{% endif %}
        </div>
        <div class="rating {{ rating_class(overall.rating) }}">{{ overall.rating }}/10</div>
      </div>
      <div class="meta">{{ overall.created_at }}</div>
    {% else %}
      <div class="empty">No overall assessment yet.</div>
    {% endif %}
    <details>
      <summary>+ Add overall assessment</summary>
      <form class="inline" method="post" action="{{ url_for('life_assess') }}">
        <input type="hidden" name="kind" value="overall">
        <select name="rating" required>
          {% for n in range(1, 11) %}<option value="{{ n }}">{{ n }}/10</option>{% endfor %}
        </select>
        <input type="text" name="note" placeholder="Bar's note (optional)">
        <input type="hidden" name="assessor" value="Bar">
        <button>Save</button>
      </form>
    </details>
  </section>

  {# ── Life Areas ── #}
  <section class="card">
    <h2>Life Areas</h2>
    <div class="grid grid-2">
      {% for a in areas %}
        {% set last = area_assess.get(a.id) %}
        <div>
          <div class="row">
            <div class="title"><strong>{{ a.name }}</strong></div>
            {% if last %}
              <div class="rating {{ rating_class(last.rating) }}">{{ last.rating }}/10</div>
            {% else %}
              <div class="meta">unrated</div>
            {% endif %}
          </div>
          {% if last and last.note %}
            <div class="assess-block">"{{ last.note }}"<div class="stamp">{{ last.created_at }}</div></div>
          {% endif %}
          <details>
            <summary>+ Rate this area</summary>
            <form class="inline" method="post" action="{{ url_for('life_assess') }}">
              <input type="hidden" name="kind" value="area">
              <input type="hidden" name="ref_id" value="{{ a.id }}">
              <select name="rating" required>
                {% for n in range(1, 11) %}<option value="{{ n }}">{{ n }}/10</option>{% endfor %}
              </select>
              <input type="text" name="note" placeholder="Note (optional)">
              <button>Save</button>
            </form>
          </details>
        </div>
      {% endfor %}
    </div>
  </section>

  {# ── Goals ── #}
  <section class="card">
    <h2>Goals</h2>
    {% if goals %}
      {% for g in goals %}
        {% set last = goal_assess.get(g.id) %}
        <div class="row">
          <div class="title">
            <span class="pill">{{ g.area_name or 'Unassigned' }}</span>
            <span class="{{ 'strike' if g.status == 'done' else '' }}">{{ g.title }}</span>
            {% if g.target_date %}<div class="meta">Target: {{ g.target_date }}</div>{% endif %}
            {% if g.description %}<div class="meta">{{ g.description }}</div>{% endif %}
            {% if last %}
              <div class="assess-block">
                <strong>{{ last.assessor }}:</strong> {{ last.rating }}/10
                {% if last.note %} — "{{ last.note }}"{% endif %}
                <div class="stamp">{{ last.created_at }}</div>
              </div>
            {% endif %}
          </div>
          <div class="toolbar">
            <form method="post" action="{{ url_for('life_goal_update') }}">
              <input type="hidden" name="goal_id" value="{{ g.id }}">
              <input type="hidden" name="status" value="{{ 'active' if g.status == 'done' else 'done' }}">
              <button class="secondary">{{ '↺' if g.status == 'done' else '✓' }}</button>
            </form>
            <form method="post" action="{{ url_for('life_goal_delete') }}"
                  onsubmit="return confirm('Delete this goal?');">
              <input type="hidden" name="goal_id" value="{{ g.id }}">
              <button class="secondary">✕</button>
            </form>
          </div>
        </div>
        <details>
          <summary>+ Bar rates this goal</summary>
          <form class="inline" method="post" action="{{ url_for('life_assess') }}">
            <input type="hidden" name="kind" value="goal">
            <input type="hidden" name="ref_id" value="{{ g.id }}">
            <select name="rating" required>
              {% for n in range(1, 11) %}<option value="{{ n }}">{{ n }}/10</option>{% endfor %}
            </select>
            <input type="text" name="note" placeholder="Bar's thoughts">
            <button>Save</button>
          </form>
        </details>
      {% endfor %}
    {% else %}
      <div class="empty">No goals yet — add your first below.</div>
    {% endif %}
    <form class="inline" method="post" action="{{ url_for('life_goal_add') }}">
      <input type="text" name="title" placeholder="New goal title" required>
      <select name="area_id">
        <option value="">— Area —</option>
        {% for a in areas %}<option value="{{ a.id }}">{{ a.name }}</option>{% endfor %}
      </select>
      <input type="text" name="target_date" placeholder="Target (e.g. Dec 2026)">
      <button>Add goal</button>
    </form>
  </section>

  {# ── Aspirations ── #}
  <section class="card">
    <h2>Aspirations</h2>
    {% if aspirations %}
      {% for asp in aspirations %}
        {% set last = asp_assess.get(asp.id) %}
        <div class="row">
          <div class="title">
            <strong>{{ asp.title }}</strong>
            {% if asp.description %}<div class="meta">{{ asp.description }}</div>{% endif %}
            {% if last %}
              <div class="assess-block">
                <strong>{{ last.assessor }}:</strong> {{ last.rating }}/10
                {% if last.note %} — "{{ last.note }}"{% endif %}
                <div class="stamp">{{ last.created_at }}</div>
              </div>
            {% endif %}
          </div>
          <form method="post" action="{{ url_for('life_aspiration_delete') }}"
                onsubmit="return confirm('Delete this aspiration?');">
            <input type="hidden" name="asp_id" value="{{ asp.id }}">
            <button class="secondary">✕</button>
          </form>
        </div>
        <details>
          <summary>+ Bar rates this aspiration</summary>
          <form class="inline" method="post" action="{{ url_for('life_assess') }}">
            <input type="hidden" name="kind" value="aspiration">
            <input type="hidden" name="ref_id" value="{{ asp.id }}">
            <select name="rating" required>
              {% for n in range(1, 11) %}<option value="{{ n }}">{{ n }}/10</option>{% endfor %}
            </select>
            <input type="text" name="note" placeholder="Bar's thoughts">
            <button>Save</button>
          </form>
        </details>
      {% endfor %}
    {% else %}
      <div class="empty">No aspirations yet — dream big below.</div>
    {% endif %}
    <form class="inline" method="post" action="{{ url_for('life_aspiration_add') }}">
      <input type="text" name="title" placeholder="A dream / aspiration" required>
      <input type="text" name="description" placeholder="Why it matters (optional)">
      <button>Add</button>
    </form>
  </section>

  {# ── Things To Do (synced from Telegram bot) ── #}
  <section class="card">
    <h2>Things To Do <span class="meta">(shared with Telegram bot)</span></h2>
    {% if todos %}
      {% for t in todos %}
        <div class="row">
          <div class="title {{ 'strike' if t.done else '' }}">
            {{ t.item }}
            <div class="meta">added by {{ t.added_by or '—' }} · {{ t.created_at }}</div>
          </div>
          <div>{{ '✅' if t.done else '⬜' }}</div>
        </div>
      {% endfor %}
    {% else %}
      <div class="empty">To-do list is empty.</div>
    {% endif %}
  </section>

  {# ── Recent Assessment History ── #}
  <section class="card">
    <h2>Recent Assessments</h2>
    {% if history %}
      {% for h in history %}
        <div class="row">
          <div class="title">
            <span class="pill">{{ h.kind }}</span>
            <strong>{{ h.assessor }}:</strong> {{ h.rating }}/10
            {% if h.note %} — "{{ h.note }}"{% endif %}
            <div class="meta">{{ h.created_at }}</div>
          </div>
        </div>
      {% endfor %}
    {% else %}
      <div class="empty">No assessments yet — Bar, take it away!</div>
    {% endif %}
  </section>

</main>
</body>
</html>
"""


def _rating_class(r: int) -> str:
    if r is None:
        return ""
    if r >= 7:
        return "r-good"
    if r >= 4:
        return "r-warn"
    return "r-bad"


@app.route("/life", methods=["GET"])
def life_dashboard():
    db.init_db()
    areas = db.get_life_areas()
    goals = db.get_goals()
    aspirations = db.get_aspirations()
    todos = db.get_todos()
    history = db.assessment_history(limit=15)

    area_assess = {a["id"]: db.latest_assessment("area", a["id"]) for a in areas}
    area_assess = {k: v for k, v in area_assess.items() if v}
    goal_assess = {g["id"]: db.latest_assessment("goal", g["id"]) for g in goals}
    goal_assess = {k: v for k, v in goal_assess.items() if v}
    asp_assess = {a["id"]: db.latest_assessment("aspiration", a["id"]) for a in aspirations}
    asp_assess = {k: v for k, v in asp_assess.items() if v}
    overall = db.latest_assessment("overall", None)

    return render_template_string(
        LIFE_DASHBOARD_HTML,
        today=date.today().strftime("%A %d %b %Y"),
        areas=areas,
        goals=goals,
        aspirations=aspirations,
        todos=todos,
        history=history,
        area_assess=area_assess,
        goal_assess=goal_assess,
        asp_assess=asp_assess,
        overall=overall,
        rating_class=_rating_class,
    )


@app.route("/life/goals/add", methods=["POST"])
def life_goal_add():
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("life_dashboard"))
    area_id = request.form.get("area_id") or None
    area_id = int(area_id) if area_id else None
    target = (request.form.get("target_date") or "").strip()
    description = (request.form.get("description") or "").strip()
    db.add_goal(title, area_id=area_id, description=description, target_date=target)
    return redirect(url_for("life_dashboard"))


@app.route("/life/goals/update", methods=["POST"])
def life_goal_update():
    goal_id = int(request.form["goal_id"])
    status = request.form.get("status", "active")
    if status not in ("active", "done", "paused"):
        status = "active"
    db.update_goal_status(goal_id, status)
    return redirect(url_for("life_dashboard"))


@app.route("/life/goals/delete", methods=["POST"])
def life_goal_delete():
    db.delete_goal(int(request.form["goal_id"]))
    return redirect(url_for("life_dashboard"))


@app.route("/life/aspirations/add", methods=["POST"])
def life_aspiration_add():
    title = (request.form.get("title") or "").strip()
    if not title:
        return redirect(url_for("life_dashboard"))
    description = (request.form.get("description") or "").strip()
    db.add_aspiration(title, description=description)
    return redirect(url_for("life_dashboard"))


@app.route("/life/aspirations/delete", methods=["POST"])
def life_aspiration_delete():
    db.delete_aspiration(int(request.form["asp_id"]))
    return redirect(url_for("life_dashboard"))


@app.route("/life/assess", methods=["POST"])
def life_assess():
    kind = request.form.get("kind", "overall")
    if kind not in ("goal", "aspiration", "area", "overall"):
        return redirect(url_for("life_dashboard"))
    ref_id_raw = request.form.get("ref_id")
    ref_id = int(ref_id_raw) if ref_id_raw else None
    try:
        rating = max(1, min(10, int(request.form.get("rating", "5"))))
    except ValueError:
        rating = 5
    note = (request.form.get("note") or "").strip()
    assessor = (request.form.get("assessor") or "Bar").strip() or "Bar"
    db.add_assessment(kind, ref_id, rating, note=note, assessor=assessor)
    return redirect(url_for("life_dashboard"))


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
