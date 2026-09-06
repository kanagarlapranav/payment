import time
import urllib.request
import json
import urllib.error

BOT_TOKEN = "8863268724:AAFcDfpdgTXas2E6OnNIQj9mRIwRzQ8WV94"
URL = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"

offset = 0
attempts = 0

print("Checking for messages...")

while attempts < 15:
    try:
        req_url = f"{URL}?offset={offset}&timeout=5"
        with urllib.request.urlopen(req_url) as response:
            data = json.loads(response.read().decode())
            
        if data.get("ok") and data["result"]:
            for update in data["result"]:
                offset = update["update_id"] + 1
                
                msg = update.get("message") or update.get("my_chat_member")
                if msg:
                    chat = msg.get("chat")
                    if chat:
                        chat_id = chat.get("id")
                        user_id = msg.get("from", {}).get("id")
                        
                        print(f"Detected message from User ID: {user_id} in Chat ID: {chat_id}")
                        
                        with open(".env", "w") as f:
                            f.write(f"TELEGRAM_BOT_TOKEN={BOT_TOKEN}\n")
                            f.write(f"TELEGRAM_USER_ID={user_id}\n")
                            f.write(f"TELEGRAM_GROUP_ID={chat_id}\n")
                            f.write(r"TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe" + "\n")
                            
                        print("Successfully created .env file!")
                        exit(0)
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.reason}")
    except Exception as e:
        print(f"Error: {e}")
        
    attempts += 1
    time.sleep(2)

print("No messages found.")
