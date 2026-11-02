# Deploy to Render — Step by Step

## 1. Push to GitHub
Create a private GitHub repo and push all these files.

## 2. Create Render Web Service
- Go to dashboard.render.com → New → Web Service
- Connect your GitHub repo
- Render auto-detects `render.yaml`

## 3. Set Environment Variables
In Render dashboard → Environment:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Your bot token from @BotFather |
| `WEBHOOK_URL` | `https://YOUR-APP-NAME.onrender.com` |
| `CAPTURED_BY` | `@YourUsername` |
| `ADMIN_IDS` | Your Telegram user ID |

## 4. Get your Telegram ID
Message @userinfobot on Telegram — it replies with your ID.

## 5. Deploy
Click Deploy. Render builds and starts the bot.
Webhook sets automatically on startup.

## 6. Test
Send `/start` to your bot on Telegram.

---
## Config you can tune (in render.yaml envVars or config.py)

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_THREADS` | 100 | How many concurrent checkers |
| `MAX_COMBO_MB` | 500 | Max combo file size in MB |

---
## Upgrading plan
- **Starter ($7/mo)** — good for 50-100 threads
- **Standard ($25/mo)** — 150-200 threads, faster CPU
