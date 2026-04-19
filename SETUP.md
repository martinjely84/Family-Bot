# Family Telegram Bot — Setup Guide

A shared group chat bot for you and your wife. Everyone sees all messages in one place!

---

## What You'll Need (all free)

| Account | Purpose | Link |
|---|---|---|
| **Telegram** | The chat app | https://telegram.org |
| **GitHub** | Store your code | https://github.com |
| **Railway** | Host the bot | https://railway.app |
| **Anthropic** | AI brain (Claude) | https://console.anthropic.com |

---

## Step 1 — Get Your Anthropic API Key

1. Go to https://console.anthropic.com/settings/keys
2. Click **Create Key**, give it a name
3. Copy the key (starts with `sk-ant-...`) — save it for later

---

## Step 2 — Create Your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send: `/newbot`
3. Choose a name, e.g. `Ely Family Bot`
4. Choose a username ending in `bot`, e.g. `ElyFamilyBot`
5. BotFather will give you a **token** — looks like `123456789:ABCdef...`
6. Save that token — you'll need it in Step 5

---

## Step 3 — Put the Code on GitHub

1. Go to https://github.com and create a **private** repository called `family-bot`
2. Upload all the files from the `ely-family-bot` folder:
   - `app.py`, `database.py`, `calendar_helper.py`
   - `requirements.txt`, `Procfile`, `railway.toml`, `start.sh`

---

## Step 4 — Deploy on Railway

1. Go to https://railway.app → **New Project → Deploy from GitHub repo**
2. Select your `family-bot` repository
3. Once deployed, go to **Settings → Networking → Generate Domain**
4. Copy your public URL, e.g. `https://family-bot-production-xxxx.up.railway.app`

---

## Step 5 — Set Environment Variables on Railway

Go to **Variables** and add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Claude key from Step 1 |
| `TELEGRAM_BOT_TOKEN` | Your token from BotFather |
| `BOT_USERNAME` | Your bot's username without @, e.g. `ElyFamilyBot` |
| `TIMEZONE` | Your timezone, e.g. `Europe/London` |

For iCloud Calendar (optional — add later):

| Variable | Value |
|---|---|
| `ICLOUD_USERNAME` | Your Apple ID email |
| `ICLOUD_APP_PASSWORD` | App-specific password from appleid.apple.com |
| `ICLOUD_CALENDAR_NAME` | Name of your shared calendar, e.g. `Family` |

---

## Step 6 — Connect Telegram to Your Bot

Open your browser and paste this URL (replace both values):

```
https://api.telegram.org/bot{YOUR_TOKEN}/setWebhook?url=https://family-bot-production-xxxx.up.railway.app/webhook
```

You should see: `{"ok":true,"result":true}`

---

## Step 7 — Create the Group & Test

1. In Telegram, create a **new group**
2. Add your wife and search for your bot by username (e.g. `@ElyFamilyBot`) and add it too
3. Send in the group: `help`

The bot should reply with its guide within a few seconds!

---

## How to Use

### 💬 Chat & Advice
Just type anything in the group — the bot responds to everyone.

### 📅 Apple Calendar
```
cal today
cal tomorrow / cal week
cal add dentist on Friday at 3pm
cal add school play tomorrow at 2pm for 2 hours
what's on this weekend?
```

### 📝 Shared To-Do List
```
todo add buy birthday cake
todo list
todo done 2
todo delete 3
todo clear
```

### ⏰ Reminders (sent to the group)
```
remind me in 2 hours to take medicine
remind us tomorrow at 9am to call the school
remind me on Friday at 6pm to pay rent
reminders
```

---

## Troubleshooting

**Bot doesn't respond** — Check Railway logs. Make sure the webhook URL was set correctly in Step 6.

**"Sorry, I'm having a moment"** — Check `ANTHROPIC_API_KEY` in Railway variables.

**Reminders not arriving** — Check Railway logs for errors. Make sure `TIMEZONE` is set correctly.
