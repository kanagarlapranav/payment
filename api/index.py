import json
import asyncio
from http.server import BaseHTTPRequestHandler
from telegram import Update
from app import build_application
from config import TELEGRAM_BOT_TOKEN, logger

# Global application instance for serverless container reuse
application = None

def get_app():
    global application
    if application is None:
        application = build_application()
    return application

async def process_telegram_update(update_json: dict):
    """Initializes app if needed and processes incoming webhook update."""
    app = get_app()
    if not app.running and not getattr(app, '_initialized', False):
        await app.initialize()
        app._initialized = True
    update = Update.de_json(update_json, app.bot)
    await app.process_update(update)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        response = {
            "status": "online",
            "service": "Payment Tracker Telegram Bot",
            "mode": "Vercel Serverless Webhook"
        }
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def do_POST(self):
        """Telegram Webhook update receiver."""
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            update_dict = json.loads(post_data.decode('utf-8'))
            asyncio.run(process_telegram_update(update_dict))
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode('utf-8'))
        except Exception as e:
            logger.error(f"Error processing webhook update: {e}", exc_info=True)
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
