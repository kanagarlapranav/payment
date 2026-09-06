# 💰 Payment Tracker Bot - Complete Reference & Documentation

## 🔗 Live Links & Credentials Reference
- **GitHub Repository**: [https://github.com/kanagarlapranav/payment](https://github.com/kanagarlapranav/payment)
- **Vercel Live URL**: [https://payment-p9riol44n-payment9.vercel.app](https://payment-p9riol44n-payment9.vercel.app)
- **Vercel Dashboard**: [https://vercel.com/payment9/payment](https://vercel.com/payment9/payment/2D9Thx4XUWEqfRno2nicWYMLhvRn)
- **Telegram Webhook**: `https://payment-p9riol44n-payment9.vercel.app/api/index`

---

## 🤖 Bot Features & Commands

Both forward slashes (`/command`) and backslashes (`\command`) are fully supported:

| Command | Action |
| :--- | :--- |
| **Natural Text / Voice** | Add transaction via plain conversational text (e.g. `Paid to balaji icic admin paid to him 5000 on yesterday` or `Received 2000 from Ramesh today`) |
| **Screenshot (OCR)** | Upload screenshot of UPI / Google Pay / PhonePe / Paytm to extract transactions |
| `/history` | View recent transactions (clean view with amounts, dates, accounts, and categories) |
| `/edit` | Interactive 1-tap button menu to select transaction and edit amount, category, or note |
| `/delete` | Interactive 1-tap confirmation button menu to delete transactions |
| `/sort` | Sort transactions by highest amount, lowest amount, newest, or oldest |
| `/monthly` | Monthly financial analytics (total spend, income, savings, breakdown) |
| `/filter` | Filter by Income, Expense, or Category |
| `/date <YYYY-MM-DD>` | Filter all transactions on a specific date (e.g. `/date 2026-09-05` or `/date today` or `/date yesterday`) |
| `/search <query>` | Search transactions by person, note, bank, or category |
| `/details` / `/ids` | Show technical Transaction IDs on demand |
| `/export` | Export all data to Excel sheet |
| `/help` | Complete help guide |

---

## ☁️ Architecture & Serverless Operation

- **Vercel Serverless (`api/index.py`)**: Runs without needing a PC on 24/7. When a message is sent to Telegram, Telegram invokes the Vercel serverless function via webhook.
- **Webhook Switch Script (`set_webhook.py`)**:
  - To activate Vercel: `python set_webhook.py https://payment-p9riol44n-payment9.vercel.app`
  - To switch back to local testing: `python set_webhook.py delete` then `python app.py`

---

## 💡 How to Resume / Access this Chat in Antigravity

1. **Automatic Persistence**: Antigravity automatically saves all chat history, code changes, and logs in the local session storage.
2. **Left Sidebar Chat History**: Click the history/session icon in the Antigravity left sidebar to open or continue this chat anytime.
3. **`/learn` command**: You can type `/learn` in chat to instruct Antigravity to memorize this project's structure and deployment setup across all future sessions.
