"""
Helper script to discover Telegram User ID and Group/Chat ID.
Reads TELEGRAM_BOT_TOKEN from environment (.env) or prompts for it.
This script ONLY prints the detected IDs and NEVER writes to or overwrites .env.
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

def main():
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token and len(sys.argv) > 1:
        token = sys.argv[1]

    if not token:
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        token = input("Enter your Telegram Bot Token from @BotFather: ").strip()

    if not token:
        print("Error: No bot token provided. Exiting.")
        sys.exit(1)

    url = f"https://api.telegram.org/bot{token}/getUpdates"

    print(f"\nListening for Telegram updates... (Polling {url.split('/bot')[0]}/bot[TOKEN]/getUpdates)")
    print("Please send any message (e.g. 'hello') to your bot or in your payment group now!\n")

    offset = 0
    attempts = 0
    max_attempts = 30

    while attempts < max_attempts:
        try:
            req_url = f"{url}?offset={offset}&timeout=5"
            with urllib.request.urlopen(req_url) as response:
                data = json.loads(response.read().decode('utf-8'))

            if data.get("ok") and data.get("result"):
                for update in data["result"]:
                    offset = update["update_id"] + 1

                    msg = update.get("message") or update.get("edited_message") or update.get("my_chat_member")
                    if msg:
                        chat = msg.get("chat", {})
                        sender = msg.get("from", {})

                        chat_id = chat.get("id")
                        chat_type = chat.get("type", "unknown")
                        chat_title = chat.get("title") or f"{sender.get('first_name', '')} {sender.get('last_name', '')}".strip() or "Private"

                        user_id = sender.get("id")
                        username = sender.get("username", "no_username")

                        print("=" * 60)
                        print(f"📩 Detected message from @{username} (Name: {sender.get('first_name', '')})")
                        print(f"   User ID:     {user_id}")
                        print(f"   Chat ID:     {chat_id} (Type: {chat_type}, Title: {chat_title})")
                        print("=" * 60)
                        print("\nCopy the required values into your .env or Render Environment Variables:")
                        print(f"TELEGRAM_USER_ID={user_id}")
                        if chat_type in ("group", "supergroup"):
                            print(f"TELEGRAM_GROUP_ID={chat_id}")
                        print("=" * 60 + "\n")

        except urllib.error.HTTPError as e:
            print(f"HTTP Error: {e.code} - {e.reason}")
            if e.code == 401:
                print("Invalid Bot Token! Please verify the token from @BotFather.")
                break
        except Exception as e:
            print(f"Error checking updates: {e}")

        attempts += 1
        time.sleep(2)

    print("Finished listening.")

if __name__ == "__main__":
    main()
