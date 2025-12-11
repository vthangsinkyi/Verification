from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta
import secrets
import os
import sys
import urllib.parse
import time

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from utils.logger import logger

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['SESSION_TYPE'] = 'filesystem'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True
    CORS(app)
    
    # Simple rate limiting dictionary
    rate_limits = {}
    
    def check_rate_limit(ip, limit=5, window=60):
        """Simple rate limiting"""
        current_time = time.time()
        
        if ip not in rate_limits:
            rate_limits[ip] = []
        
        # Remove old entries
        rate_limits[ip] = [t for t in rate_limits[ip] if current_time - t < window]
        
        # Check if over limit
        if len(rate_limits[ip]) >= limit:
            return False
        
        # Add current request
        rate_limits[ip].append(current_time)
        return True
    
    # Global database variable
    db_client = None
    db = None
    
    try:
        # Try to connect to MongoDB
        from pymongo import MongoClient
        db_client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
        db_client.server_info()  # Test connection
        db = db_client[Config.DATABASE_NAME]
        logger.info("✅ Website connected to MongoDB")
    except Exception as e:
        logger.error(f"⚠️  MongoDB connection failed: {e}")
        logger.warning("⚠️  Using fallback memory storage (data will be lost on restart)")
        # Fallback in-memory storage
        db = None
        memory_storage = {
            'users': [],
            'banned_ips': [],
            'verification_logs': []
        }
    
    # Helper functions
    def get_client_ip():
        """Get client IP address"""
        if request.headers.get('X-Forwarded-For'):
            ip = request.headers['X-Forwarded-For'].split(',')[0].strip()
        elif request.headers.get('X-Real-IP'):
            ip = request.headers['X-Real-IP']
        else:
            ip = request.remote_addr
            
        # Remove port if present (IPv6 addresses)
        if ':' in ip and '.' in ip:  # IPv4-mapped IPv6
            ip = ip.split(':')[-1]
        return ip
    
    def check_vpn(ip):
        """Check if IP is VPN using APIs"""
        try:
            # Check with ipinfo.io (if token provided)
            if Config.IPINFO_TOKEN:
                response = requests.get(
                    f'https://ipinfo.io/{ip}/json?token={Config.IPINFO_TOKEN}',
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('vpn') or data.get('proxy') or data.get('tor'):
                        return True
            
            # Check with ipqualityscore (if API key provided)
            if Config.VPN_API_KEY:
                response = requests.get(
                    f'https://www.ipqualityscore.com/api/json/ip/{Config.VPN_API_KEY}/{ip}',
                    params={'strictness': 1},
                    timeout=5
                )
                if response.status_code == 200:
                    data = response.json()
                    if data.get('vpn') or data.get('proxy') or data.get('tor'):
                        return True
            
            # Check with free API as fallback
            try:
                response = requests.get(f'https://ipapi.co/{ip}/json/', timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    # Check for VPN hosting services
                    vpn_keywords = ['vpn', 'proxy', 'hosting', 'datacenter']
                    if any(keyword in str(data.get('org', '')).lower() for keyword in vpn_keywords):
                        return True
            except:
                pass
                
        except Exception as e:
            logger.error(f"VPN check error: {e}")
            
        return False
    
    def send_discord_webhook(title, description, color):
        """Send webhook to Discord"""
        if not Config.WEBHOOK_URL:
            return False
            
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            response = requests.post(Config.WEBHOOK_URL, json={"embeds": [embed]}, timeout=5)
            return response.status_code in [200, 204]
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return False
    
    def exchange_code(code):
        """Exchange OAuth2 code for access token"""
        data = {
            'client_id': Config.CLIENT_ID,
            'client_secret': Config.CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': Config.REDIRECT_URI,
            'scope': 'identify'
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        try:
            response = requests.post(
                'https://discord.com/api/oauth2/token',
                data=data,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"OAuth2 token exchange error: {e}")
            return None
    
    def get_user_info(access_token):
        """Get user info from Discord API"""
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        
        try:
            response = requests.get(
                'https://discord.com/api/users/@me',
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Discord API error: {e}")
            return None
    
    # Middleware for admin routes
    def admin_required(f):
        from functools import wraps
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('admin_logged_in'):
                return redirect(url_for('admin_login'))
            return f(*args, **kwargs)
        return decorated_function
    
    # Routes
    @app.route('/')
    def home():
        return redirect(url_for('verify_page'))
    
    @app.route('/verify')
    def verify_page():
        """Verification page with Discord OAuth2"""
        # Check if user already has Discord info in session
        discord_user = session.get('discord_user')
        
        return render_template('verify.html', 
                             discord_user=discord_user,
                             client_id=Config.CLIENT_ID,
                             redirect_uri=urllib.parse.quote(Config.REDIRECT_URI))
    
    @app.route('/auth/discord')
    def auth_discord():
        """Redirect to Discord OAuth2"""
        discord_auth_url = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={Config.CLIENT_ID}"
            f"&redirect_uri={urllib.parse.quote(Config.REDIRECT_URI)}"
            f"&response_type=code"
            f"&scope=identify"
            f"&prompt=none"
        )
        return redirect(discord_auth_url)
    
    @app.route('/auth/callback')
    def auth_callback():
        """Discord OAuth2 callback"""
        code = request.args.get('code')
        
        if not code:
            logger.warning("No code provided in OAuth2 callback")
            return redirect(url_for('verify_page'))
        
        # Exchange code for access token
        token_data = exchange_code(code)
        if not token_data:
            logger.error("Failed to exchange OAuth2 code for token")
            return redirect(url_for('verify_page'))
        
        # Get user info
        user_info = get_user_info(token_data.get('access_token'))
        if not user_info:
            logger.error("Failed to get user info from Discord API")
            return redirect(url_for('verify_page'))
        
        # Store user info in session
        session['discord_user'] = {
            'id': user_info['id'],
            'username': user_info['username'],
            'discriminator': user_info.get('discriminator', '0'),
            'avatar': user_info.get('avatar'),
            'full_username': f"{user_info['username']}#{user_info.get('discriminator', '0')}"
        }
        
        logger.info(f"User logged in: {session['discord_user']['full_username']}")
        
        return redirect(url_for('verify_page'))
    
    @app.route('/auth/logout')
    def auth_logout():
        """Logout from Discord OAuth2"""
        username = session.get('discord_user', {}).get('full_username', 'Unknown')
        session.pop('discord_user', None)
        logger.info(f"User logged out: {username}")
        return redirect(url_for('verify_page'))
    
    @app.route('/api/verify', methods=['POST'])
    def api_verify():
        """API endpoint for verification"""
        client_ip = get_client_ip()
        logger.info(f"Verification attempt from IP: {client_ip}")
        
        # Simple rate limiting
        if not check_rate_limit(client_ip, limit=5, window=60):
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return jsonify({
                "success": False,
                "error": "Rate limit exceeded. Please try again later."
            }), 429
        
        try:
            # Check if user is logged in via Discord OAuth2
            discord_user = session.get('discord_user')
            
            if not discord_user:
                logger.warning(f"Unauthorized verification attempt from IP: {client_ip}")
                return jsonify({
                    "success": False, 
                    "error": "Please connect your Discord account first.",
                    "requires_oauth": True
                }), 401
            
            discord_id = discord_user['id']
            username = discord_user['full_username']
            
            # Check if already verified
            is_verified = False
            if db is not None:
                user_data = db.users.find_one({"discord_id": str(discord_id)})
                if user_data and user_data.get('verified_at'):
                    # Already verified
                    logger.info(f"Already verified attempt: {username} ({discord_id})")
                    return jsonify({
                        "success": False, 
                        "error": "You are already verified! Please return to the server."
                    }), 400
            
            # Get client IP
            ip_address = get_client_ip()
            user_agent = request.headers.get('User-Agent', 'Unknown')
            
            logger.info(f"Verification attempt: {username} ({discord_id}) from IP: {ip_address}")
            
            # Check if IP is banned
            is_banned = False
            if db is not None:
                banned_ip = db.banned_ips.find_one({"ip_address": ip_address})
                if banned_ip:
                    is_banned = True
            
            if is_banned:
                send_discord_webhook(
                    "🚨 BLOCKED - Banned IP Attempt",
                    f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**Reason:** IP is banned",
                    0xff0000
                )
                logger.warning(f"Banned IP attempt: {username} from {ip_address}")
                return jsonify({
                    "success": False, 
                    "error": "Access denied. Your IP is banned from this server."
                }), 403
            
            # Check for VPN
            is_vpn = check_vpn(ip_address)
            
            if is_vpn:
                # Ban IP
                ban_data = {
                    "ip_address": ip_address,
                    "discord_id": discord_id,
                    "username": username,
                    "reason": "VPN/Proxy detected",
                    "banned_by": "System",
                    "banned_at": datetime.utcnow()
                }
                
                if db is not None:
                    db.banned_ips.insert_one(ban_data)
                
                send_discord_webhook(
                    "🚨 VPN DETECTED & BANNED",
                    f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**Action:** IP banned from system",
                    0xff0000
                )
                
                logger.warning(f"VPN detected and banned: {username} from {ip_address}")
                return jsonify({
                    "success": False, 
                    "error": "VPN/Proxy detected. Your IP has been banned from our system."
                }), 403
            
            # Save verification to database
            user_data = {
                "discord_id": str(discord_id),
                "username": username,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "is_vpn": is_vpn,
                "last_seen": datetime.utcnow(),
                "verified_at": datetime.utcnow(),
                "is_banned": False,
                "role_added": False
            }
            
            if db is not None:
                db.users.update_one(
                    {"discord_id": str(discord_id)},
                    {"$set": user_data},
                    upsert=True
                )
            
            # Send success notification
            send_discord_webhook(
                "✅ VERIFICATION SUCCESS",
                f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**VPN Check:** Passed ✅",
                0x00ff00
            )
            
            logger.info(f"Verification successful: {username} ({discord_id})")
            
            # Clear session after successful verification
            session.pop('discord_user', None)
            
            return jsonify({
                "success": True,
                "message": "✅ Verification successful! You can now return to the server and access all channels.",
                "data": {
                    "username": username,
                    "ip_masked": ip_address[:7] + "***" if len(ip_address) > 7 else "***",
                    "vpn_check": "passed"
                }
            })
            
        except Exception as e:
            logger.error(f"Verification error: {e}")
            return jsonify({"success": False, "error": "Internal server error. Please try again later."}), 500
    
    # Admin Routes
    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        """Admin login page"""
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            client_ip = get_client_ip()
            
            logger.info(f"Admin login attempt from IP: {client_ip}, username: {username}")
            
            # Rate limiting for admin login
            if not check_rate_limit(client_ip, limit=10, window=3600):  # 10 attempts per hour
                logger.warning(f"Admin login rate limit exceeded for IP: {client_ip}")
                return render_template('admin/login.html', error="Too many login attempts. Please try again later.")
            
            if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
                session['admin_logged_in'] = True
                session['admin_username'] = username
                logger.info(f"Admin login successful: {username} from {client_ip}")
                return redirect(url_for('admin_dashboard'))
            
            logger.warning(f"Admin login failed: {username} from {client_ip}")
            return render_template('admin/login.html', error="Invalid credentials")
        
        return render_template('admin/login.html')
    
    @app.route('/admin/dashboard')
    @admin_required
    def admin_dashboard():
        """Admin dashboard"""
        stats = {
            "total_users": 0,
            "banned_users": 0,
            "today_verifications": 0,
            "total_bans": 0
        }
        
        # Calculate stats
        try:
            if db is not None:
                stats["total_users"] = db.users.count_documents({})
                stats["banned_users"] = db.banned_ips.count_documents({})
                stats["total_bans"] = db.banned_ips.count_documents({})
                today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                stats["today_verifications"] = db.users.count_documents({"verified_at": {"$gte": today}})
        except Exception as e:
            logger.error(f"Stats calculation error: {e}")
        
        return render_template('admin/dashboard.html', stats=stats)
    
    @app.route('/admin/banned')
    @admin_required
    def admin_banned():
        """Banned users list"""
        banned_list = []
        
        try:
            if db is not None:
                banned_list = list(db.banned_ips.find().sort("banned_at", -1).limit(100))
                for item in banned_list:
                    if '_id' in item:
                        item['_id'] = str(item['_id'])
        except Exception as e:
            logger.error(f"Banned list error: {e}")
        
        return render_template('admin/banned.html', banned_list=banned_list)
    
    @app.route('/admin/verified')
    @admin_required
    def admin_verified():
        """Verified users list"""
        verified_list = []
        
        try:
            if db is not None:
                verified_list = list(db.users.find().sort("verified_at", -1).limit(100))
                for item in verified_list:
                    if '_id' in item:
                        item['_id'] = str(item['_id'])
        except Exception as e:
            logger.error(f"Verified list error: {e}")
        
        return render_template('admin/verified.html', verified_list=verified_list)
    
    @app.route('/admin/unban/<ip_address>')
    @admin_required
    def admin_unban(ip_address):
        """Unban IP address"""
        try:
            if db is not None:
                result = db.banned_ips.delete_one({"ip_address": ip_address})
                
                if result.deleted_count > 0:
                    send_discord_webhook(
                        "🔓 IP UNBANNED",
                        f"**IP:** ||{ip_address}||\n**Unbanned by:** {session.get('admin_username', 'Admin')}",
                        0x00ff00
                    )
                    logger.info(f"IP unbanned: {ip_address} by {session.get('admin_username')}")
                else:
                    logger.warning(f"IP not found for unban: {ip_address}")
            
        except Exception as e:
            logger.error(f"Unban error: {e}")
        
        return redirect(url_for('admin_banned'))
    
    @app.route('/admin/logout')
    def admin_logout():
        """Admin logout"""
        username = session.get('admin_username', 'Unknown')
        session.clear()
        logger.info(f"Admin logged out: {username}")
        return redirect(url_for('admin_login'))
    
    @app.route('/healthz')
    @app.route('/health')
    def health_check():
        """Health check endpoint"""
        status = {
            "status": "online",
            "service": "discord-verification",
            "database": "connected" if db is not None else "memory_mode",
            "timestamp": datetime.utcnow().isoformat(),
            "version": "1.0.0"
        }
        return jsonify(status)
    
    @app.route('/feedback')
    def feedback():
        """Feedback and support page"""
        return render_template('feedback.html')
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        logger.warning(f"404 Not Found: {request.path}")
        return render_template('error.html', error="Page not found"), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        logger.error(f"500 Internal Server Error: {error}")
        return render_template('error.html', error="Internal server error"), 500
    
    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.error(f"Unhandled exception: {e}")
        return render_template('error.html', error="An unexpected error occurred"), 500
    
    return app