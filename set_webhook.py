"""
Helper script to register or unregister the Telegram Webhook for Vercel.
Usage:
  python set_webhook.py https://your-project.vercel.app
  python set_webhook.py delete
"""
import sys
import os
import json
import urllib.request
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def set_webhook(url: str):
    if not TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set in .env")
        return
    webhook_url = f"{url.rstrip('/')}/api/index"
    api_url = f"https://api.telegram.org/bot{TOKEN}/setWebhook?url={webhook_url}"
    print(f"Setting Telegram Webhook to: {webhook_url} ...")
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print("Response:", data)
    except Exception as e:
        print("[ERROR] Failed to set webhook:", e)

def delete_webhook():
    if not TOKEN:
        print("[ERROR] TELEGRAM_BOT_TOKEN is not set in .env")
        return
    api_url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook"
    print("Removing Telegram Webhook (switching back to local polling)...")
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            print("Response:", data)
    except Exception as e:
        print("[ERROR] Failed to delete webhook:", e)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python set_webhook.py https://your-project.vercel.app")
        print("  python set_webhook.py delete")
    elif sys.argv[1].lower() == "delete":
        delete_webhook()
    else:
        set_webhook(sys.argv[1])

