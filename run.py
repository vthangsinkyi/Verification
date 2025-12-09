import os
import sys
import threading
import time
import signal
import atexit

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import after path setup
try:
    from website.app import create_app
    from bot.bot import run_discord_bot
    from config import Config
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Current sys.path:", sys.path)
    raise

# Global variables for cleanup
bot_process = None
app = None
bot_thread = None

def cleanup():
    """Cleanup resources on exit"""
    print("🧹 Cleaning up resources...")
    global bot_thread
    if bot_thread and bot_thread.is_alive():
        print("🛑 Stopping bot thread...")
        # We can't easily stop the bot thread, but we'll try
        pass

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"🛑 Received signal {signum}, shutting down...")
    cleanup()
    sys.exit(0)

def run_bot():
    """Run Discord bot"""
    print("🤖 Starting Discord bot...")
    try:
        run_discord_bot()
    except Exception as e:
        print(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

def run_website():
    """Run Flask website"""
    global app
    print("🌐 Starting website...")
    
    # Create app
    app = create_app()
    
    # Get port from environment
    port = int(os.environ.get("PORT", 10000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    print(f"🌍 Server will run on: http://{host}:{port}")
    print(f"🔗 Verification: http://{host}:{port}/verify")
    print(f"👑 Admin: http://{host}:{port}/admin/login")
    
    # Run Flask app
    app.run(host=host, port=port, debug=False, use_reloader=False)

def main():
    """Main entry point"""
    print("=" * 60)
    print("🚀 DISCORD VERIFICATION SYSTEM - RENDER DEPLOYMENT")
    print("=" * 60)
    
    # Register cleanup handlers
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Validate configuration
    if not Config.DISCORD_TOKEN:
        print("❌ ERROR: DISCORD_TOKEN not configured!")
        print("Please set DISCORD_TOKEN environment variable")
        sys.exit(1)
    
    if not Config.VERIFIED_ROLE_ID:
        print("⚠️  WARNING: VERIFIED_ROLE_ID not configured")
        print("Force verify command won't work without it")
    
    # Set website URL for production
    website_url = os.environ.get("WEBSITE_URL", f"https://discord-verification.onrender.com")
    os.environ["WEBSITE_URL"] = website_url
    print(f"🌐 Production URL: {website_url}")
    
    # Start Discord bot in separate thread
    global bot_thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Wait for bot to initialize
    print("⏳ Initializing bot (5 seconds)...")
    time.sleep(5)
    
    # Start Flask website (this will block)
    run_website()

if __name__ == "__main__":
    main()