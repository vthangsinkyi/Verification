import os
import sys
import threading
import time
import signal
import atexit

# Check Python version
PYTHON_VERSION = sys.version_info
if PYTHON_VERSION.major == 3 and PYTHON_VERSION.minor >= 13:
    print(f"⚠️  Python {PYTHON_VERSION.major}.{PYTHON_VERSION.minor} detected")
    print("⚠️  Creating audioop mock for compatibility...")
    
    # Mock audioop module if not present
    try:
        import audioop
    except ImportError:
        print("⚠️  Creating mock audioop module")
        import types
        audioop = types.ModuleType('audioop')
        # Add dummy functions
        audioop.add = lambda *args: b''
        audioop.adpcm2lin = lambda *args: (b'', 0)
        audioop.alaw2lin = lambda *args: b''
        audioop.avg = lambda *args: 0
        audioop.avgpp = lambda *args: 0
        audioop.bias = lambda *args: b''
        audioop.cross = lambda *args: 0
        audioop.findfactor = lambda *args: 0.0
        audioop.findfit = lambda *args: (0, 0)
        audioop.findmax = lambda *args: 0
        audioop.getsample = lambda *args: 0
        audioop.lin2adpcm = lambda *args: (b'', 0)
        audioop.lin2alaw = lambda *args: b''
        audioop.lin2lin = lambda *args: b''
        audioop.lin2ulaw = lambda *args: b''
        audioop.max = lambda *args: 0
        audioop.maxpp = lambda *args: 0
        audioop.minmax = lambda *args: (0, 0)
        audioop.mul = lambda *args: b''
        audioop.ratecv = lambda *args: (b'', 0, 0)
        audioop.reverse = lambda *args: b''
        audioop.rms = lambda *args: 0
        audioop.tomono = lambda *args: b''
        audioop.tostereo = lambda *args: b''
        audioop.ulaw2lin = lambda *args: b''
        sys.modules['audioop'] = audioop

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import after path setup
try:
    from website.app import create_app
    from bot.bot import run_discord_bot
    from config import Config
    from utils.logger import logger
    from monitor import HealthMonitor
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Current sys.path:", sys.path)
    raise

# Global variables
bot_thread = None
health_monitor = None
health_monitor_thread = None

def cleanup():
    """Cleanup resources on exit"""
    logger.info("🧹 Cleaning up resources...")
    global bot_thread, health_monitor_thread
    
    if health_monitor_thread and health_monitor_thread.is_alive():
        logger.info("🛑 Stopping health monitor...")
    
    if bot_thread and bot_thread.is_alive():
        logger.info("🛑 Bot thread will terminate with main process...")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    logger.info(f"🛑 Received signal {signum}, shutting down...")
    cleanup()
    sys.exit(0)

def run_bot():
    """Run Discord bot"""
    logger.info("🤖 Starting Discord bot...")
    try:
        run_discord_bot()
    except Exception as e:
        logger.error(f"❌ Bot error: {e}")
        import traceback
        traceback.print_exc()

def run_health_monitor():
    """Run health monitor in background"""
    logger.info("📊 Starting health monitor...")
    global health_monitor
    
    health_monitor = HealthMonitor()
    
    while True:
        try:
            health_monitor.run_check()
            time.sleep(300)  # Check every 5 minutes
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Health monitor error: {e}")
            time.sleep(60)  # Wait 1 minute on error

def run_website():
    """Run Flask website"""
    logger.info("🌐 Starting website...")
    
    # Create app
    app = create_app()
    
    # Get port from environment
    port = int(os.environ.get("PORT", 10000))
    host = os.environ.get("HOST", "0.0.0.0")
    
    logger.info(f"🌍 Server will run on: http://{host}:{port}")
    logger.info(f"🔗 Verification: http://{host}:{port}/verify")
    logger.info(f"👑 Admin: http://{host}:{port}/admin/login")
    logger.info(f"🆘 Feedback: http://{host}:{port}/feedback")
    logger.info("=" * 50)
    
    # Run Flask app
    app.run(host=host, port=port, debug=False, use_reloader=False)

def main():
    """Main entry point"""
    print("=" * 60)
    print("🚀 DISCORD VERIFICATION SYSTEM")
    print("=" * 60)
    logger.info(f"✅ Python {PYTHON_VERSION.major}.{PYTHON_VERSION.minor}.{PYTHON_VERSION.micro}")
    
    # Register cleanup handlers
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Validate configuration
    if not Config.DISCORD_TOKEN:
        logger.error("❌ ERROR: DISCORD_TOKEN not configured!")
        print("Please set DISCORD_TOKEN environment variable")
        sys.exit(1)
    
    if not Config.VERIFIED_ROLE_ID:
        logger.warning("⚠️  WARNING: VERIFIED_ROLE_ID not configured")
        print("Force verify command won't work without it")
    
    # Set website URL
    website_url = os.environ.get("WEBSITE_URL", f"http://localhost:{os.environ.get('PORT', 10000)}")
    os.environ["WEBSITE_URL"] = website_url
    logger.info(f"🌐 Website URL: {website_url}")
    
    # Start Discord bot in separate thread
    global bot_thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Start health monitor in separate thread
    global health_monitor_thread
    health_monitor_thread = threading.Thread(target=run_health_monitor, daemon=True)
    health_monitor_thread.start()
    
    # Wait for bot to initialize
    logger.info("⏳ Initializing bot (3 seconds)...")
    time.sleep(3)
    
    # Start Flask website (this will block)
    run_website()

if __name__ == "__main__":
    main()