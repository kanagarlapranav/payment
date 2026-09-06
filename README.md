# Personal Payment Tracker Bot

A Telegram bot designed to automatically track personal payments by extracting details from payment screenshots using OCR.

## Features
- Automatically processes screenshots from PhonePe, Google Pay, Paytm, etc.
- Extracts Type (SENT/RECEIVED), Amount, Date, Time, Person, and Reference.
- Maintains a running account balance.
- Prevents duplicate entries.
- Fallback manual confirmation if OCR confidence is low.
- Export all transactions to Excel directly in Telegram.

## Architecture
- **OCR Engine**: Tesseract OCR (via `pytesseract`) + OpenCV (for preprocessing).
- **Database**: SQLite3.
- **Bot Framework**: `python-telegram-bot`.

The code is heavily modularized:
- `parsers/`: Contains generic and app-specific logic for mapping OCR text to structured data.
- `services/`: Contains business logic for checking balances, duplicates, and saving.
- `bot/`: Contains Telegram-specific routing.
- `database/`: Contains queries and schema.

## Requirements
- Python 3.9+
- A Telegram Bot Token.

## Setup & Installation (Windows)

### 1. Install Tesseract OCR
1. Download the Windows installer from [UB-Mannheim's Tesseract GitHub](https://github.com/UB-Mannheim/tesseract/wiki).
2. Install it. By default, it installs to `C:\Program Files\Tesseract-OCR`.
3. Note the installation path. If it differs, update the `TESSERACT_CMD` variable in your `.env` file.

### 2. Setup Telegram Bot
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`, choose a name and a username.
3. BotFather will give you an **HTTP API Token**. Keep it secret.
4. Search for `@userinfobot` or similar to find your personal **Telegram User ID** (a long number).

### 3. Clone / Setup Project
Open Command Prompt or PowerShell in this folder:

```powershell
# Create a virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 4. Configuration
1. Copy `.env.example` and rename it to `.env`.
2. Open `.env` and fill in your details:
   ```env
   TELEGRAM_BOT_TOKEN=your_token_from_botfather
   TELEGRAM_USER_ID=your_user_id
   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
   ```

### 5. Running the Bot
```powershell
python app.py
```

Now, go to Telegram, find your bot, and type `/start`. Send it a screenshot!

## Testing
Run the unit tests using `pytest`:
```powershell
pytest tests/
```

## Adding a New Payment App Parser
1. Create a new file in `parsers/` (e.g., `bharatpe.py`).
2. Create a class `BharatPeParser(GenericParser)`.
3. Override `can_parse()` to return True if specific BharatPe keywords exist.
4. Override `parse()` to add specific regex logic, then return the `Transaction`.
5. Import and add the new parser to the list in `parsers/__init__.py`.
