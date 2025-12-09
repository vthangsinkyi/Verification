import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Discord
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '')
    GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '0'))
    CLIENT_ID = os.getenv('CLIENT_ID', '')
    CLIENT_SECRET = os.getenv('CLIENT_SECRET', '')
    REDIRECT_URI = os.getenv('REDIRECT_URI', '')
    
    # Webhooks
    WEBHOOK_URL = os.getenv('DISCORD_WEBHOOK_URL', '')
    LOGS_WEBHOOK = os.getenv('DISCORD_LOGS_WEBHOOK', '')
    
    # MongoDB
    MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
    DATABASE_NAME = os.getenv('DATABASE_NAME', 'discord_verification')
    
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'production-secret-key-change-this')
    FLASK_ENV = os.getenv('FLASK_ENV', 'production')
    PORT = int(os.getenv('PORT', '10000'))  # Render uses 10000
    
    # Admin
    ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
    
    # Verification Role
    VERIFIED_ROLE_NAME = "Verified"
    VERIFIED_ROLE_ID = os.getenv('VERIFIED_ROLE_ID', '')
    VERIFIED_ROLE_COLOR = 0x00ff00
    
    # API Services
    IPINFO_TOKEN = os.getenv('IPINFO_TOKEN', '')
    VPN_API_KEY = os.getenv('VPN_API_KEY', '')
    
    # Website URL (Set by Render)
    WEBSITE_URL = os.getenv('WEBSITE_URL', '')
    VERIFY_URL = f"{WEBSITE_URL}/verify" if WEBSITE_URL else "https://koalahub.onrender.com/verify"