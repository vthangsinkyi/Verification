from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
import requests
import json
from datetime import datetime, timedelta
import secrets
import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config['SECRET_KEY'] = Config.SECRET_KEY
    app.config['SESSION_TYPE'] = 'filesystem'
    CORS(app)
    
    # Global database variable
    db_client = None
    db = None
    
    try:
        # Try to connect to MongoDB
        from pymongo import MongoClient
        db_client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
        db_client.server_info()  # Test connection
        db = db_client[Config.DATABASE_NAME]
        print("✅ Website connected to MongoDB")
    except Exception as e:
        print(f"⚠️  MongoDB connection failed: {e}")
        print("⚠️  Using fallback memory storage (data will be lost on restart)")
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
            print(f"VPN check error: {e}")
            
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
            print(f"Webhook error: {e}")
            return False
    
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
        """Verification page"""
        return render_template('verify.html')
    
    @app.route('/api/verify', methods=['POST'])
    def api_verify():
        """API endpoint for verification"""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"success": False, "error": "No data provided"}), 400
            
            discord_id = data.get('discord_id')
            username = data.get('username')
            
            if not discord_id or not username:
                return jsonify({"success": False, "error": "Missing Discord ID or username"}), 400
            
            # Get client IP
            ip_address = get_client_ip()
            user_agent = request.headers.get('User-Agent', 'Unknown')
            
            print(f"🔍 Verification attempt: {username} ({discord_id}) from IP: {ip_address}")
            
            # Check if IP is banned
            is_banned = False
            if db is not None:  # FIXED: Changed from 'if db:'
                # Check MongoDB
                banned_ip = db.banned_ips.find_one({"ip_address": ip_address})
                if banned_ip:
                    is_banned = True
            elif 'memory_storage' in locals():
                # Check memory storage
                is_banned = any(ban['ip_address'] == ip_address for ban in memory_storage['banned_ips'])
            
            if is_banned:
                send_discord_webhook(
                    "🚨 BLOCKED - Banned IP Attempt",
                    f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**Reason:** IP is banned",
                    0xff0000
                )
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
                
                if db is not None:  # FIXED: Changed from 'if db:'
                    db.banned_ips.insert_one(ban_data)
                elif 'memory_storage' in locals():
                    memory_storage['banned_ips'].append(ban_data)
                
                send_discord_webhook(
                    "🚨 VPN DETECTED & BANNED",
                    f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**Action:** IP banned from system",
                    0xff0000
                )
                
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
            
            if db is not None:  # FIXED: Changed from 'if db:'
                db.users.update_one(
                    {"discord_id": str(discord_id)},
                    {"$set": user_data},
                    upsert=True
                )
            elif 'memory_storage' in locals():
                # Update or add to memory storage
                existing = next((u for u in memory_storage['users'] if u['discord_id'] == str(discord_id)), None)
                if existing:
                    memory_storage['users'].remove(existing)
                memory_storage['users'].append(user_data)
            
            # Send success notification
            send_discord_webhook(
                "VERIFICATION SUCCESS",
                f"**User:** {username}\n**ID:** {discord_id}\n**IP:** ||{ip_address}||\n**VPN Check:** Passed ✅",
                0x00ff00
            )
            
            return jsonify({
                "success": True,
                "message": "Verification successful! You can now access the server.",
                "data": {
                    "username": username,
                    "ip_masked": ip_address[:7] + "***" if len(ip_address) > 7 else "***",
                    "vpn_check": "passed"
                }
            })
            
        except Exception as e:
            print(f"❌ Verification error: {e}")
            return jsonify({"success": False, "error": "Internal server error. Please try again later."}), 500
    
    # Admin Routes
    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        """Admin login page"""
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            
            if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
                session['admin_logged_in'] = True
                session['admin_username'] = username
                return redirect(url_for('admin_dashboard'))
            
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
            if db is not None:  # FIXED: Changed from 'if db:'
                stats["total_users"] = db.users.count_documents({})
                stats["banned_users"] = db.banned_ips.count_documents({})
                stats["total_bans"] = db.banned_ips.count_documents({})
                today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                stats["today_verifications"] = db.users.count_documents({"verified_at": {"$gte": today}})
            elif 'memory_storage' in locals():
                stats["total_users"] = len(memory_storage['users'])
                stats["banned_users"] = len(memory_storage['banned_ips'])
                stats["total_bans"] = len(memory_storage['banned_ips'])
                today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                stats["today_verifications"] = len([u for u in memory_storage['users'] 
                                                  if (isinstance(u['verified_at'], str) and datetime.fromisoformat(u['verified_at'].replace('Z', '+00:00')) >= today)
                                                  or (not isinstance(u['verified_at'], str) and u['verified_at'] >= today)])
        except Exception as e:
            print(f"Stats calculation error: {e}")
        
        return render_template('admin/dashboard.html', stats=stats)
    
    @app.route('/admin/banned')
    @admin_required
    def admin_banned():
        """Banned users list"""
        banned_list = []
        
        try:
            if db is not None:  # FIXED: Changed from 'if db:'
                banned_list = list(db.banned_ips.find().sort("banned_at", -1).limit(100))
                # Convert ObjectId to string for template
                for item in banned_list:
                    if '_id' in item:
                        item['_id'] = str(item['_id'])
            elif 'memory_storage' in locals():
                banned_list = memory_storage['banned_ips'][-100:]  # Get last 100
        except Exception as e:
            print(f"Banned list error: {e}")
        
        return render_template('admin/banned.html', banned_list=banned_list)
    
    @app.route('/admin/verified')
    @admin_required
    def admin_verified():
        """Verified users list"""
        verified_list = []
        
        try:
            if db is not None:  # FIXED: Changed from 'if db:'
                verified_list = list(db.users.find().sort("verified_at", -1).limit(100))
                # Convert ObjectId to string for template
                for item in verified_list:
                    if '_id' in item:
                        item['_id'] = str(item['_id'])
            elif 'memory_storage' in locals():
                verified_list = sorted(memory_storage['users'], 
                                     key=lambda x: x.get('verified_at', datetime.min), 
                                     reverse=True)[:100]
        except Exception as e:
            print(f"Verified list error: {e}")
        
        return render_template('admin/verified.html', verified_list=verified_list)
    
    @app.route('/admin/unban/<ip_address>')
    @admin_required
    def admin_unban(ip_address):
        """Unban IP address"""
        try:
            if db is not None:  # FIXED: Changed from 'if db:'
                db.banned_ips.delete_one({"ip_address": ip_address})
            elif 'memory_storage' in locals():
                memory_storage['banned_ips'] = [b for b in memory_storage['banned_ips'] if b['ip_address'] != ip_address]
            
            send_discord_webhook(
                "🔓 IP UNBANNED",
                f"**IP:** ||{ip_address}||\n**Unbanned by:** {session.get('admin_username', 'Admin')}",
                0x00ff00
            )
            
        except Exception as e:
            print(f"Unban error: {e}")
        
        return redirect(url_for('admin_banned'))
    
    @app.route('/admin/logout')
    def admin_logout():
        """Admin logout"""
        session.clear()
        return redirect(url_for('admin_login'))
    
    @app.route('/callback')
    def discord_callback():
        """Discord OAuth2 callback placeholder"""
        return redirect(url_for('verify_page'))
    
    @app.route('/health')
    def health_check():
        """Health check endpoint"""
        status = {
            "status": "online",
            "database": "connected" if db is not None else "disconnected (memory mode)",
            "bot": "connected" if hasattr(app, 'bot_connected') else "unknown",
            "timestamp": datetime.utcnow().isoformat()
        }
        return jsonify(status)
    
    # Error handlers
    @app.errorhandler(404)
    def not_found(error):
        return render_template('error.html', error="Page not found"), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return render_template('error.html', error="Internal server error"), 500
    
    return app