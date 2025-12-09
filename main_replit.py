import os
import sys
import threading
import time
from website.app import create_app
from bot.bot import run_discord_bot
from config import Config

# Set Replit-specific config
os.environ['PORT'] = '8080'
os.environ['FLASK_ENV'] = 'production'
os.environ['WEBSITE_URL'] = f'https://{os.environ.get("REPL_SLUG")}.{os.environ.get("REPL_OWNER")}.repl.co'

def run_bot():
    print("🤖 Starting Discord bot...")
    run_discord_bot()

def run_website():
    print("🌐 Starting website...")
    app = create_app()
    app.run(host='0.0.0.0', port=8080, debug=False)

if __name__ == "__main__":
    print("🚀 Starting Discord Verification System on Replit...")
    
    # Start bot in thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait a bit
    time.sleep(3)
    
    # Run website
    run_website()