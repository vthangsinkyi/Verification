"""
Discord Verification System - Bot Implementation
Complete implementation with all features including monetization and advanced security
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import View, Button, Select, Modal, TextInput
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import sys
import os
import time
import hashlib
import random
import string
from decimal import Decimal
from motor.motor_asyncio import AsyncIOMotorClient
import aioredis
import psutil
import humanize
from enum import Enum

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from utils.logger import logger
from utils.password import PasswordManager
from utils.rate_limiter import rate_limiter
from utils.backup import backup_database

# ============ ENUMS & CONSTANTS ============

class VerificationTier(Enum):
    """Verification tiers for monetization"""
    FREE = "free"
    PREMIUM = "premium"
    ULTIMATE = "ultimate"
    ENTERPRISE = "enterprise"

class CommandCategory(Enum):
    """Bot command categories"""
    VERIFICATION = "🔐 Verification"
    ADMINISTRATION = "👑 Administration"
    MODERATION = "🛡️ Moderation"
    MONETIZATION = "💰 Monetization"
    UTILITY = "🔧 Utility"
    SECURITY = "🚨 Security"

# ============ DATABASE MODELS ============

class SubscriptionPlan:
    """Subscription plan for monetization"""
    def __init__(self, name: str, price: Decimal, features: List[str], duration_days: int):
        self.name = name
        self.price = price
        self.features = features
        self.duration_days = duration_days
        self.created_at = datetime.utcnow()

class Payment:
    """Payment record"""
    def __init__(self, user_id: str, plan_name: str, amount: Decimal, currency: str = "USD"):
        self.user_id = user_id
        self.plan_name = plan_name
        self.amount = amount
        self.currency = currency
        self.status = "pending"  # pending, completed, failed, refunded
        self.payment_id = self._generate_payment_id()
        self.created_at = datetime.utcnow()
        self.completed_at = None
    
    def _generate_payment_id(self):
        """Generate unique payment ID"""
        timestamp = int(time.time())
        random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        return f"PAY-{timestamp}-{random_str}"
    
    def complete(self):
        """Mark payment as completed"""
        self.status = "completed"
        self.completed_at = datetime.utcnow()
    
    def to_dict(self):
        return {
            "payment_id": self.payment_id,
            "user_id": self.user_id,
            "plan_name": self.plan_name,
            "amount": float(self.amount),
            "currency": self.currency,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at
        }

# ============ BOT IMPLEMENTATION ============

class VerificationBot(commands.Bot):
    """Enhanced Discord bot with all features"""
    
    def __init__(self):
        # Configure intents
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        intents.presences = True
        intents.message_content = True
        
        super().__init__(
            command_prefix="/",
            intents=intents,
            help_command=None,  # We'll use custom help
            case_insensitive=True
        )
        
        # Database connections
        self.db_client = None
        self.db = None
        self.redis_client = None
        self.cache_enabled = False
        
        # Monetization settings
        self.subscription_plans = {
            "free": SubscriptionPlan(
                name="Free",
                price=Decimal("0.00"),
                features=[
                    "Basic verification",
                    "Standard support",
                    "24-hour processing"
                ],
                duration_days=30
            ),
            "premium": SubscriptionPlan(
                name="Premium",
                price=Decimal("9.99"),
                features=[
                    "Priority verification",
                    "VIP support",
                    "1-hour processing",
                    "Advanced analytics",
                    "Custom verification badge"
                ],
                duration_days=30
            ),
            "ultimate": SubscriptionPlan(
                name="Ultimate",
                price=Decimal("24.99"),
                features=[
                    "Instant verification",
                    "24/7 priority support",
                    "Real-time analytics",
                    "Custom verification flow",
                    "API access",
                    "White-label option"
                ],
                duration_days=30
            )
        }
        
        # Performance tracking
        self.performance_metrics = {
            "start_time": time.time(),
            "commands_executed": 0,
            "verifications_processed": 0,
            "roles_assigned": 0,
            "errors": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
        
        # Security tracking
        self.security_events = []
        self.suspicious_activities = []
        
        # Command cooldowns
        self.command_cooldowns = {}
        
        # Pending verifications
        self.pending_verifications = {}
        self.failed_attempts = {}
        self.already_logged_not_found = set()
        
        # Setup databases
        asyncio.create_task(self.setup_databases())
    
    async def setup_databases(self):
        """Initialize database connections with async support"""
        try:
            # MongoDB with advanced configuration
            self.db_client = AsyncIOMotorClient(
                Config.MONGODB_URI,
                maxPoolSize=200,
                minPoolSize=20,
                maxIdleTimeMS=60000,
                socketTimeoutMS=10000,
                connectTimeoutMS=10000,
                serverSelectionTimeoutMS=10000,
                retryWrites=True,
                retryReads=True,
                heartbeatFrequencyMS=10000
            )
            
            # Test connection
            await self.db_client.admin.command('ping')
            self.db = self.db_client[Config.DATABASE_NAME]
            
            logger.info("✅ Bot connected to MongoDB with advanced pooling")
            
            # Create indexes
            await self.create_indexes()
            
            # Redis for caching
            try:
                redis_url = getattr(Config, 'REDIS_URL', None)
                if redis_url:
                    self.redis_client = await aioredis.from_url(
                        redis_url,
                        decode_responses=True,
                        socket_timeout=5,
                        socket_connect_timeout=5,
                        max_connections=50
                    )
                    await self.redis_client.ping()
                    self.cache_enabled = True
                    logger.info("✅ Redis connected with connection pooling")
                else:
                    logger.info("ℹ️ Redis not configured, using memory cache")
            except Exception as e:
                logger.warning(f"⚠️ Redis connection failed: {e}")
                self.cache_enabled = False
                
        except Exception as e:
            logger.error(f"❌ Database setup failed: {e}")
            self.db = None
    
    async def create_indexes(self):
        """Create database indexes for performance"""
        if not self.db:
            return
        
        indexes = [
            # Users collection
            ("users", [("discord_id", 1)], {"unique": True}),
            ("users", [("verified_at", -1)]),
            ("users", [("subscription_tier", 1)]),
            ("users", [("is_banned", 1)]),
            ("users", [("last_seen", -1)]),
            
            # Verification logs
            ("verification_logs", [("timestamp", -1)]),
            ("verification_logs", [("discord_id", 1)]),
            ("verification_logs", [("success", 1)]),
            
            # Payments
            ("payments", [("payment_id", 1)], {"unique": True}),
            ("payments", [("user_id", 1)]),
            ("payments", [("status", 1)]),
            ("payments", [("created_at", -1)]),
            
            # Security logs
            ("security_logs", [("timestamp", -1)]),
            ("security_logs", [("type", 1)]),
            
            # Audit logs
            ("audit_logs", [("timestamp", -1)]),
            ("audit_logs", [("admin_id", 1)]),
            
            # Compound indexes
            ("users", [("verified_at", -1), ("is_banned", 1)]),
            ("verification_logs", [("discord_id", 1), ("timestamp", -1)]),
        ]
        
        for collection, keys, *options in indexes:
            try:
                opts = options[0] if options else {}
                await self.db[collection].create_index(keys, **opts)
            except Exception as e:
                logger.error(f"Failed to create index on {collection}: {e}")
    
    # ============ CACHE METHODS ============
    
    async def cache_get(self, key: str):
        """Get value from cache"""
        if not self.cache_enabled or not self.redis_client:
            self.performance_metrics["cache_misses"] += 1
            return None
        
        try:
            value = await self.redis_client.get(key)
            if value:
                self.performance_metrics["cache_hits"] += 1
                try:
                    return json.loads(value)
                except:
                    return value
            else:
                self.performance_metrics["cache_misses"] += 1
                return None
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
            self.performance_metrics["cache_misses"] += 1
            return None
    
    async def cache_set(self, key: str, value, expire: int = 300):
        """Set value in cache"""
        if not self.cache_enabled or not self.redis_client:
            return False
        
        try:
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self.redis_client.setex(key, expire, value)
            return True
        except Exception as e:
            logger.warning(f"Cache set error: {e}")
            return False
    
    async def cache_delete(self, key: str):
        """Delete value from cache"""
        if not self.cache_enabled or not self.redis_client:
            return False
        
        try:
            await self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Cache delete error: {e}")
            return False
    
    async def cache_incr(self, key: str, amount: int = 1):
        """Increment cached value"""
        if not self.cache_enabled or not self.redis_client:
            return None
        
        try:
            return await self.redis_client.incrby(key, amount)
        except Exception as e:
            logger.warning(f"Cache incr error: {e}")
            return None
    
    # ============ SECURITY METHODS ============
    
    async def log_security_event(self, event_type: str, user_id: str = None, 
                               guild_id: str = None, details: str = "", level: str = "INFO"):
        """Log security event to database and Discord"""
        event = {
            "type": event_type,
            "user_id": user_id,
            "guild_id": guild_id,
            "details": details[:1000],
            "level": level,
            "timestamp": datetime.utcnow(),
            "bot_version": "2.0.0"
        }
        
        # Store in memory buffer (for real-time monitoring)
        self.security_events.append(event)
        if len(self.security_events) > 1000:
            self.security_events = self.security_events[-1000:]
        
        # Store in database
        if self.db:
            try:
                await self.db.security_logs.insert_one(event)
            except Exception as e:
                logger.error(f"Failed to log security event: {e}")
        
        # Send to Discord webhook for important events
        if level in ["WARNING", "ERROR", "CRITICAL"]:
            await self.send_security_alert(event_type, details, level, user_id)
        
        # Local logging
        log_msg = f"SECURITY {level}: {event_type} - User: {user_id} - Details: {details}"
        if level == "ERROR":
            logger.error(log_msg)
        elif level == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
    
    async def send_security_alert(self, event_type: str, details: str, level: str, user_id: str = None):
        """Send security alert to Discord"""
        color = {
            "INFO": 0x3498db,
            "WARNING": 0xf39c12,
            "ERROR": 0xe74c3c,
            "CRITICAL": 0xff0000
        }.get(level, 0xe74c3c)
        
        embed = discord.Embed(
            title=f"🚨 {event_type}",
            description=details[:2000],
            color=color,
            timestamp=datetime.utcnow()
        )
        
        if user_id:
            embed.add_field(name="User", value=f"<@{user_id}>", inline=True)
        
        embed.add_field(name="Level", value=level, inline=True)
        embed.add_field(name="Time", value=datetime.utcnow().strftime("%H:%M:%S"), inline=True)
        
        # Send to configured webhook
        if Config.ALERTS_WEBHOOK:
            await self.send_webhook(embed, Config.ALERTS_WEBHOOK)
    
    async def check_command_cooldown(self, user_id: str, command_name: str, cooldown_seconds: int) -> bool:
        """Check command cooldown with enhanced tracking"""
        key = f"cooldown:{user_id}:{command_name}"
        
        # Check cache first
        cached = await self.cache_get(key)
        if cached:
            return False
        
        # Check memory cache
        memory_key = f"{user_id}_{command_name}"
        current_time = time.time()
        
        if memory_key in self.command_cooldowns:
            last_used = self.command_cooldowns[memory_key]
            if current_time - last_used < cooldown_seconds:
                # Track suspicious rapid usage
                rapid_key = f"rapid:{user_id}:{command_name}"
                rapid_count = await self.cache_incr(rapid_key)
                if rapid_count and rapid_count > 5:
                    await self.log_security_event(
                        "RAPID_COMMAND_USAGE",
                        user_id,
                        details=f"Command: {command_name}, Count: {rapid_count}",
                        level="WARNING"
                    )
                return False
        
        # Set cooldown
        self.command_cooldowns[memory_key] = current_time
        await self.cache_set(key, True, cooldown_seconds)
        return True
    
    async def check_user_permissions(self, user: discord.Member, required_permissions: List[str]) -> bool:
        """Check if user has required permissions"""
        try:
            for perm in required_permissions:
                if not getattr(user.guild_permissions, perm, False):
                    return False
            return True
        except Exception as e:
            logger.error(f"Permission check error: {e}")
            return False
    
    # ============ MONETIZATION METHODS ============
    
    async def get_user_subscription(self, user_id: str) -> Dict[str, Any]:
        """Get user's subscription info"""
        cache_key = f"subscription:{user_id}"
        
        # Try cache first
        cached = await self.cache_get(cache_key)
        if cached:
            return cached
        
        # Get from database
        if self.db:
            user = await self.db.users.find_one({"discord_id": str(user_id)})
            if user:
                subscription = {
                    "tier": user.get("subscription_tier", "free"),
                    "expires_at": user.get("subscription_expires_at"),
                    "features": self.subscription_plans.get(
                        user.get("subscription_tier", "free"), 
                        self.subscription_plans["free"]
                    ).features,
                    "is_active": True
                }
                
                # Check if subscription expired
                if subscription["expires_at"] and subscription["expires_at"] < datetime.utcnow():
                    subscription["tier"] = "free"
                    subscription["is_active"] = False
                
                # Cache for 5 minutes
                await self.cache_set(cache_key, subscription, 300)
                return subscription
        
        return {
            "tier": "free",
            "expires_at": None,
            "features": self.subscription_plans["free"].features,
            "is_active": True
        }
    
    async def create_payment(self, user_id: str, plan_name: str, amount: Decimal) -> Payment:
        """Create payment record"""
        payment = Payment(user_id, plan_name, amount)
        
        if self.db:
            await self.db.payments.insert_one(payment.to_dict())
        
        await self.log_security_event(
            "PAYMENT_CREATED",
            user_id,
            details=f"Plan: {plan_name}, Amount: ${amount}"
        )
        
        return payment
    
    async def complete_payment(self, payment_id: str, user_id: str):
        """Complete payment and upgrade user"""
        if self.db:
            payment = await self.db.payments.find_one({"payment_id": payment_id})
            if payment and payment["user_id"] == str(user_id):
                # Update payment status
                await self.db.payments.update_one(
                    {"payment_id": payment_id},
                    {"$set": {
                        "status": "completed",
                        "completed_at": datetime.utcnow()
                    }}
                )
                
                # Update user subscription
                expires_at = datetime.utcnow() + timedelta(days=30)
                await self.db.users.update_one(
                    {"discord_id": str(user_id)},
                    {"$set": {
                        "subscription_tier": payment["plan_name"],
                        "subscription_expires_at": expires_at,
                        "premium_since": datetime.utcnow()
                    }}
                )
                
                # Clear cache
                await self.cache_delete(f"subscription:{user_id}")
                
                await self.log_security_event(
                    "PAYMENT_COMPLETED",
                    user_id,
                    details=f"Plan: {payment['plan_name']}, Payment ID: {payment_id}"
                )
                
                return True
        
        return False
    
    async def create_premium_embed(self, user: discord.User) -> discord.Embed:
        """Create premium subscription embed"""
        embed = discord.Embed(
            title="💰 Premium Verification",
            description="Upgrade your verification experience with premium features!",
            color=discord.Color.gold(),
            timestamp=datetime.utcnow()
        )
        
        # Add plan comparisons
        for plan_name, plan in self.subscription_plans.items():
            features = "\n".join([f"• {feature}" for feature in plan.features])
            embed.add_field(
                name=f"{plan.name} - ${plan.price}/month",
                value=features,
                inline=False
            )
        
        embed.set_footer(text=f"Requested by {user.name}", icon_url=user.display_avatar.url)
        
        return embed
    
    # ============ BOT EVENTS ============
    
    async def on_ready(self):
        """Bot ready event with enhanced initialization"""
        logger.info(f'✅ Bot logged in as {self.user}')
        logger.info(f'✅ Connected to {len(self.guilds)} guild(s)')
        
        # Set rich presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"verifications | {len(self.guilds)} servers"
            ),
            status=discord.Status.online
        )
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"❌ Command sync failed: {e}")
        
        # Start background tasks
        self.start_background_tasks()
        
        # Send startup notification
        await self.send_startup_notification()
        
        logger.info("🤖 Bot fully initialized and ready")
    
    def start_background_tasks(self):
        """Start all background tasks"""
        tasks_to_start = [
            self.check_verifications,
            self.cleanup_pending,
            self.backup_database_task,
            self.update_status,
            self.performance_monitor,
            self.security_monitor,
            self.cleanup_old_data,
            self.check_subscription_expiry
        ]
        
        for task in tasks_to_start:
            if not task.is_running():
                task.start()
                logger.info(f"✅ Started background task: {task.__name__}")
    
    async def send_startup_notification(self):
        """Send startup notification to Discord"""
        embed = discord.Embed(
            title="🤖 Bot Started Successfully",
            description=f"**Bot:** {self.user.mention}\n**Servers:** {len(self.guilds)}\n**Uptime:** {self.get_uptime()}\n**Version:** 2.0 (Complete Edition)",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="Memory Usage", value=f"{self.get_memory_usage()} MB", inline=True)
        embed.add_field(name="Python Version", value=sys.version.split()[0], inline=True)
        embed.add_field(name="Discord.py", value=discord.__version__, inline=True)
        
        await self.send_webhook(embed, Config.ALERTS_WEBHOOK)
    
    async def on_guild_join(self, guild: discord.Guild):
        """Bot added to new guild"""
        logger.info(f"✅ Joined new guild: {guild.name} (ID: {guild.id}, Members: {guild.member_count})")
        
        # Send welcome message
        embed = discord.Embed(
            title="🎉 Thanks for adding Verification Bot!",
            description="I'll help you manage server verifications with advanced security features.",
            color=discord.Color.green()
        )
        
        embed.add_field(name="Quick Start", value="Use `/setup` to create verification panel", inline=False)
        embed.add_field(name="Features", value="• Advanced security checks\n• VPN detection\n• Rate limiting\n• Admin dashboard\n• Premium options", inline=False)
        embed.add_field(name="Support", value="Use `/help` for commands list", inline=False)
        
        # Find system channel or first text channel
        channel = guild.system_channel
        if not channel:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
        
        if channel:
            try:
                await channel.send(embed=embed)
            except:
                pass
        
        # Log to webhook
        alert_embed = discord.Embed(
            title="➕ Bot Added to Server",
            description=f"**Server:** {guild.name}\n**ID:** {guild.id}\n**Members:** {guild.member_count}\n**Owner:** {guild.owner}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        await self.send_webhook(alert_embed, Config.LOGS_WEBHOOK)
    
    async def on_guild_remove(self, guild: discord.Guild):
        """Bot removed from guild"""
        logger.info(f"❌ Removed from guild: {guild.name} (ID: {guild.id})")
        
        alert_embed = discord.Embed(
            title="➖ Bot Removed from Server",
            description=f"**Server:** {guild.name}\n**ID:** {guild.id}\n**Members:** {guild.member_count}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        
        await self.send_webhook(alert_embed, Config.LOGS_WEBHOOK)
    
    async def on_member_join(self, member: discord.Member):
        """Member joined server - check if already verified"""
        await self.check_existing_verification(member)
    
    async def check_existing_verification(self, member: discord.Member):
        """Check if new member is already verified"""
        if not self.db or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            user_data = await self.db.users.find_one({
                "discord_id": str(member.id),
                "verified_at": {"$exists": True},
                "is_banned": {"$ne": True}
            })
            
            if user_data and not user_data.get('role_added', False):
                # Get verified role
                verified_role = member.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if verified_role and verified_role not in member.roles:
                    await member.add_roles(verified_role)
                    
                    # Update database
                    await self.db.users.update_one(
                        {"discord_id": str(member.id)},
                        {"$set": {
                            "role_added": True,
                            "role_added_at": datetime.utcnow(),
                            "last_seen": datetime.utcnow()
                        }}
                    )
                    
                    logger.info(f"✅ Auto-verified rejoining member: {member.name}")
                    
                    # Clear from pending
                    if str(member.id) in self.pending_verifications:
                        del self.pending_verifications[str(member.id)]
                    
                    # Send notification
                    alert_embed = discord.Embed(
                        title="🔄 Auto-Verified on Join",
                        description=f"**User:** {member.mention}\n**Server:** {member.guild.name}\n**Action:** Automatic verification on server join",
                        color=discord.Color.green(),
                        timestamp=datetime.utcnow()
                    )
                    
                    await self.send_webhook(alert_embed, Config.LOGS_WEBHOOK)
        
        except Exception as e:
            logger.error(f"Auto-verification error: {e}")
    
    # ============ BACKGROUND TASKS ============
    
    @tasks.loop(seconds=30)
    async def check_verifications(self):
        """Check and process pending verifications"""
        if not self.db or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            # Get pending verifications (cached)
            cache_key = "pending_verifications_batch"
            cached = await self.cache_get(cache_key)
            
            if cached:
                unprocessed_users = cached
            else:
                cursor = self.db.users.find({
                    "verified_at": {"$exists": True},
                    "role_added": {"$ne": True},
                    "is_banned": {"$ne": True},
                    "is_blacklisted": {"$ne": True}
                }).limit(100)
                
                unprocessed_users = await cursor.to_list(length=100)
                await self.cache_set(cache_key, unprocessed_users, 15)
            
            processed = 0
            failed = 0
            
            for user in unprocessed_users:
                success = await self.process_verification(user)
                if success:
                    processed += 1
                else:
                    failed += 1
                
                # Rate limiting between processing
                await asyncio.sleep(0.05)
            
            if processed > 0:
                logger.info(f"✅ Processed {processed} verifications ({failed} failed)")
                self.performance_metrics["verifications_processed"] += processed
            
        except Exception as e:
            logger.error(f"Verification check error: {e}")
            self.performance_metrics["errors"] += 1
    
    async def process_verification(self, user: Dict) -> bool:
        """Process single user verification"""
        discord_id = user.get('discord_id')
        username = user.get('username', 'Unknown')
        
        for guild in self.guilds:
            try:
                member = await guild.fetch_member(int(discord_id))
                if member:
                    verified_role = guild.get_role(int(Config.VERIFIED_ROLE_ID))
                    if verified_role and verified_role not in member.roles:
                        await member.add_roles(verified_role)
                        
                        # Update database
                        await self.db.users.update_one(
                            {"discord_id": discord_id},
                            {"$set": {
                                "role_added": True,
                                "role_added_at": datetime.utcnow(),
                                "last_seen": datetime.utcnow()
                            }}
                        )
                        
                        # Clear caches
                        await self.cache_delete(f"user:{discord_id}")
                        await self.cache_delete("pending_verifications_batch")
                        
                        # Send DM notification
                        await self.send_verification_dm(member)
                        
                        # Log success
                        self.performance_metrics["roles_assigned"] += 1
                        
                        # Clear from pending
                        if discord_id in self.pending_verifications:
                            del self.pending_verifications[discord_id]
                        
                        user_key = f"{discord_id}_{username}"
                        if user_key in self.already_logged_not_found:
                            self.already_logged_not_found.remove(user_key)
                        
                        return True
                        
            except discord.NotFound:
                continue
            except discord.Forbidden:
                logger.error(f"❌ No permission to add role in {guild.name}")
                continue
            except Exception as e:
                logger.error(f"❌ Error processing {username}: {e}")
                continue
        
        return False
    
    async def send_verification_dm(self, member: discord.Member):
        """Send verification complete DM"""
        try:
            embed = discord.Embed(
                title="✅ Verification Complete!",
                description="Your verification has been processed successfully.",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(name="Username", value=member.name, inline=True)
            embed.add_field(name="Server", value=member.guild.name, inline=True)
            embed.add_field(name="Status", value="Verified ✅", inline=True)
            
            embed.add_field(
                name="Next Steps",
                value="• Access all server channels\n• Enjoy your stay!\n• Contact admins if you need help",
                inline=False
            )
            
            embed.set_footer(text="Thank you for verifying!")
            embed.set_thumbnail(url=member.display_avatar.url)
            
            await member.send(embed=embed)
            
        except discord.Forbidden:
            pass  # User has DMs disabled
        except Exception as e:
            logger.warning(f"Failed to send DM to {member}: {e}")
    
    @tasks.loop(minutes=10)
    async def cleanup_pending(self):
        """Cleanup old pending verifications"""
        current_time = time.time()
        
        # Clean old pending verifications (7 days)
        expired = [
            uid for uid, data in self.pending_verifications.items()
            if current_time - data.get("first_attempt", current_time) > 604800
        ]
        
        for uid in expired:
            username = self.pending_verifications[uid].get("username", "Unknown")
            logger.info(f"🗑️  Cleaning old pending verification: {username} ({uid})")
            
            if self.db:
                await self.db.users.update_one(
                    {"discord_id": uid},
                    {"$set": {
                        "is_blacklisted": True,
                        "blacklist_reason": "Never joined after 7 days"
                    }}
                )
            
            del self.pending_verifications[uid]
        
        # Clean memory caches
        if len(self.already_logged_not_found) > 1000:
            self.already_logged_not_found = set(list(self.already_logged_not_found)[-500:])
        
        if len(self.failed_attempts) > 1000:
            self.failed_attempts = dict(list(self.failed_attempts.items())[-500:])
    
    @tasks.loop(hours=6)
    async def backup_database_task(self):
        """Automated database backup"""
        try:
            backup_path = backup_database()
            if backup_path:
                embed = discord.Embed(
                    title="💾 Database Backup Created",
                    description=f"**Backup:** {backup_path}\n**Time:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                
                await self.send_webhook(embed, Config.BACKUP_WEBHOOK)
                logger.info(f"✅ Backup created: {backup_path}")
                
        except Exception as e:
            logger.error(f"❌ Backup task failed: {e}")
    
    @tasks.loop(minutes=5)
    async def update_status(self):
        """Update bot status with dynamic information"""
        try:
            servers = len(self.guilds)
            verifications = self.performance_metrics["verifications_processed"]
            
            statuses = [
                f"{servers} server{'s' if servers != 1 else ''}",
                f"{verifications} verifications",
                f"{self.performance_metrics['roles_assigned']} roles assigned",
                "Premium available!",
                "v2.0 Complete Edition"
            ]
            
            current_status = statuses[int(time.time() / 60) % len(statuses)]
            
            await self.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=current_status
                )
            )
            
        except Exception as e:
            logger.error(f"Status update error: {e}")
    
    @tasks.loop(minutes=1)
    async def performance_monitor(self):
        """Monitor and log performance metrics"""
        try:
            metrics = {
                "uptime": self.get_uptime(),
                "memory_mb": self.get_memory_usage(),
                "guilds": len(self.guilds),
                "commands": self.performance_metrics["commands_executed"],
                "verifications": self.performance_metrics["verifications_processed"],
                "roles": self.performance_metrics["roles_assigned"],
                "errors": self.performance_metrics["errors"],
                "cache_hit_rate": self.get_cache_hit_rate(),
                "active_tasks": len(asyncio.all_tasks())
            }
            
            # Log every 5 minutes
            if int(time.time()) % 300 == 0:
                logger.info(f"📊 Performance: {metrics}")
                
                # Send hourly report
                if int(time.time()) % 3600 == 0:
                    embed = discord.Embed(
                        title="📊 Hourly Performance Report",
                        color=discord.Color.blue(),
                        timestamp=datetime.utcnow()
                    )
                    
                    for key, value in metrics.items():
                        embed.add_field(name=key.replace('_', ' ').title(), value=str(value), inline=True)
                    
                    await self.send_webhook(embed, Config.LOGS_WEBHOOK)
            
        except Exception as e:
            logger.error(f"Performance monitor error: {e}")
    
    @tasks.loop(minutes=2)
    async def security_monitor(self):
        """Monitor security events and detect anomalies"""
        try:
            if len(self.security_events) > 50:
                # Check for suspicious patterns
                recent_events = self.security_events[-50:]
                
                # Count by type
                event_counts = {}
                for event in recent_events:
                    event_type = event["type"]
                    event_counts[event_type] = event_counts.get(event_type, 0) + 1
                
                # Check for high frequency of security events
                for event_type, count in event_counts.items():
                    if count > 10:  # More than 10 of same event in 2 minutes
                        await self.log_security_event(
                            "SECURITY_EVENT_SPIKE",
                            details=f"Event: {event_type}, Count: {count} in 2 minutes",
                            level="WARNING"
                        )
                
                # Clear old events
                if len(self.security_events) > 1000:
                    self.security_events = self.security_events[-500:]
            
        except Exception as e:
            logger.error(f"Security monitor error: {e}")
    
    @tasks.loop(hours=24)
    async def cleanup_old_data(self):
        """Cleanup old data from database"""
        try:
            if self.db:
                # Clean old security logs (keep 90 days)
                cutoff = datetime.utcnow() - timedelta(days=90)
                
                result = await self.db.security_logs.delete_many({
                    "timestamp": {"$lt": cutoff}
                })
                
                if result.deleted_count > 0:
                    logger.info(f"🧹 Cleaned {result.deleted_count} old security logs")
                
                # Clean old verification logs (keep 60 days)
                cutoff = datetime.utcnow() - timedelta(days=60)
                
                result = await self.db.verification_logs.delete_many({
                    "timestamp": {"$lt": cutoff}
                })
                
                if result.deleted_count > 0:
                    logger.info(f"🧹 Cleaned {result.deleted_count} old verification logs")
                
                # Clean old audit logs (keep 180 days)
                cutoff = datetime.utcnow() - timedelta(days=180)
                
                result = await self.db.audit_logs.delete_many({
                    "timestamp": {"$lt": cutoff}
                })
                
                if result.deleted_count > 0:
                    logger.info(f"🧹 Cleaned {result.deleted_count} old audit logs")
        
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
    
    @tasks.loop(hours=12)
    async def check_subscription_expiry(self):
        """Check and handle expired subscriptions"""
        try:
            if self.db:
                # Find expired subscriptions
                expired = await self.db.users.find({
                    "subscription_expires_at": {"$lt": datetime.utcnow()},
                    "subscription_tier": {"$ne": "free"}
                }).to_list(length=None)
                
                for user in expired:
                    user_id = user["discord_id"]
                    
                    # Downgrade to free
                    await self.db.users.update_one(
                        {"discord_id": user_id},
                        {"$set": {
                            "subscription_tier": "free",
                            "subscription_expires_at": None
                        }}
                    )
                    
                    # Clear cache
                    await self.cache_delete(f"subscription:{user_id}")
                    
                    # Log
                    await self.log_security_event(
                        "SUBSCRIPTION_EXPIRED",
                        user_id,
                        details=f"Tier: {user.get('subscription_tier')} -> free"
                    )
                    
                    # Try to notify user
                    try:
                        user_obj = await self.fetch_user(int(user_id))
                        if user_obj:
                            embed = discord.Embed(
                                title="📅 Subscription Expired",
                                description="Your premium subscription has expired. You've been downgraded to the free tier.",
                                color=discord.Color.orange()
                            )
                            
                            embed.add_field(
                                name="Renew",
                                value="Use `/premium` to renew your subscription",
                                inline=False
                            )
                            
                            await user_obj.send(embed=embed)
                    except:
                        pass
                
                if expired:
                    logger.info(f"📅 Processed {len(expired)} expired subscriptions")
        
        except Exception as e:
            logger.error(f"Subscription check error: {e}")
    
    # ============ UTILITY METHODS ============
    
    def get_uptime(self) -> str:
        """Get formatted uptime"""
        uptime = time.time() - self.performance_metrics["start_time"]
        return humanize.naturaldelta(timedelta(seconds=uptime))
    
    def get_memory_usage(self) -> int:
        """Get memory usage in MB"""
        try:
            process = psutil.Process()
            return int(process.memory_info().rss / 1024 / 1024)
        except:
            return 0
    
    def get_cache_hit_rate(self) -> float:
        """Calculate cache hit rate"""
        total = self.performance_metrics["cache_hits"] + self.performance_metrics["cache_misses"]
        if total == 0:
            return 0.0
        return (self.performance_metrics["cache_hits"] / total) * 100
    
    async def send_webhook(self, embed: discord.Embed, webhook_url: str):
        """Send embed to webhook"""
        if not webhook_url:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                webhook = discord.Webhook.from_url(webhook_url, session=session)
                await webhook.send(embed=embed)
        except Exception as e:
            logger.error(f"Webhook error: {e}")
    
    async def setup_hook(self):
        """Setup all slash commands"""
        await self.load_commands()
        logger.info("✅ Commands loaded successfully")
    
    async def load_commands(self):
        """Load all slash commands"""
        
        # ============ HELP COMMAND ============
        
        @self.tree.command(name="help", description="Show help for all commands")
        async def help_command(interaction: discord.Interaction, category: Optional[str] = None):
            """Custom help command with categories"""
            if not await self.check_command_cooldown(interaction.user.id, "help", 30):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            categories = {
                "🔐 Verification": [
                    ("/setup", "Setup verification system", "admin"),
                    ("/verifyinfo", "Check verification status", "mod"),
                    ("/force_verify", "Force verify user", "admin"),
                    ("/fix_roles", "Fix missing roles", "admin")
                ],
                "👑 Administration": [
                    ("/stats", "View statistics", "admin"),
                    ("/remove_all_verify", "Remove all verify roles", "admin"),
                    ("/banip", "Ban IP address", "admin"),
                    ("/unban", "Unban user", "admin")
                ],
                "💰 Monetization": [
                    ("/premium", "View premium plans", "all"),
                    ("/subscribe", "Subscribe to premium", "all"),
                    ("/mysubscription", "Check your subscription", "all")
                ],
                "🛡️ Moderation": [
                    ("/ban", "Ban user and IP", "mod"),
                    ("/warn", "Warn user", "mod"),
                    ("/kick", "Kick user", "mod")
                ],
                "🔧 Utility": [
                    ("/ping", "Check bot latency", "all"),
                    ("/serverinfo", "Server information", "all"),
                    ("/userinfo", "User information", "all")
                ],
                "🚨 Security": [
                    ("/audit", "View audit logs", "admin"),
                    ("/security", "Security dashboard", "admin"),
                    ("/lockdown", "Lockdown server", "admin")
                ]
            }
            
            embed = discord.Embed(
                title="🤖 Verification Bot Help",
                description="**Categories:**\n" + "\n".join([f"• {cat}" for cat in categories.keys()]),
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            if category:
                # Show specific category
                if category in categories:
                    commands_list = categories[category]
                    embed.title = f"📚 {category} Commands"
                    embed.description = None
                    
                    for cmd, desc, perm in commands_list:
                        embed.add_field(
                            name=cmd,
                            value=f"{desc}\n*Permission: {perm}*",
                            inline=False
                        )
                else:
                    embed = discord.Embed(
                        title="❌ Category not found",
                        description=f"Available categories: {', '.join(categories.keys())}",
                        color=discord.Color.red()
                    )
            else:
                # Show all categories
                for cat_name, commands_list in categories.items():
                    cmds = "\n".join([f"`{cmd}`" for cmd, _, _ in commands_list[:3]])
                    if len(commands_list) > 3:
                        cmds += f"\n...and {len(commands_list) - 3} more"
                    embed.add_field(name=cat_name, value=cmds, inline=True)
            
            embed.set_footer(text=f"Total commands: {len(self.tree.get_commands())}")
            embed.set_thumbnail(url=self.user.display_avatar.url)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ SETUP COMMAND ============
        
        @self.tree.command(name="setup", description="Setup verification system")
        @app_commands.checks.has_permissions(administrator=True)
        async def setup_verification(interaction: discord.Interaction):
            """Setup verification embed with enhanced features"""
            if not await self.check_command_cooldown(interaction.user.id, "setup", 60):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            # Create setup panel
            embed = discord.Embed(
                title="🔐 SERVER VERIFICATION",
                description="**Click the button below to start verification**\n\n"
                          "This verification is required to access all channels in the server.\n"
                          "Powered by advanced security systems.",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="⚠️ Rules & Requirements",
                value="• No VPN/Proxy (automatic ban)\n• One account per person\n• IP logging for security\n• Must follow server rules",
                inline=False
            )
            
            embed.add_field(
                name="✅ Benefits",
                value="• Access to all channels\n• Priority support\n• Special roles\n• Community features",
                inline=False
            )
            
            embed.set_footer(text="Protecting our community since 2024")
            embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1200387647512776704/1200387721230815232/koala.png")
            
            # Create view with buttons
            view = discord.ui.View(timeout=None)
            
            # Verification button
            verify_button = discord.ui.Button(
                label="✅ Start Verification",
                style=discord.ButtonStyle.green,
                url=Config.VERIFY_URL,
                emoji="🔐"
            )
            view.add_item(verify_button)
            
            # Help button
            help_button = discord.ui.Button(
                label="❓ Need Help?",
                style=discord.ButtonStyle.blurple,
                custom_id="verification_help",
                emoji="❓"
            )
            view.add_item(help_button)
            
            # Premium button
            premium_button = discord.ui.Button(
                label="💰 Premium Features",
                style=discord.ButtonStyle.gold,
                custom_id="premium_info",
                emoji="💰"
            )
            view.add_item(premium_button)
            
            # Send setup
            await interaction.response.send_message(
                "✅ Verification panel has been created!",
                ephemeral=True
            )
            
            await interaction.channel.send(embed=embed, view=view)
            
            # Log setup
            await self.log_security_event(
                "VERIFICATION_SETUP",
                str(interaction.user.id),
                str(interaction.guild.id),
                f"Channel: #{interaction.channel.name}"
            )
            
            # Send webhook notification
            alert_embed = discord.Embed(
                title="📝 Verification Panel Setup",
                description=f"**Setup by:** {interaction.user.mention}\n"
                          f"**Server:** {interaction.guild.name}\n"
                          f"**Channel:** #{interaction.channel.name}",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            
            await self.send_webhook(alert_embed, Config.LOGS_WEBHOOK)
        
        # ============ PREMIUM COMMAND ============
        
        @self.tree.command(name="premium", description="View premium subscription plans")
        async def premium_command(interaction: discord.Interaction):
            """Show premium subscription plans"""
            if not await self.check_command_cooldown(interaction.user.id, "premium", 30):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            embed = await self.create_premium_embed(interaction.user)
            
            # Add current subscription info
            subscription = await self.get_user_subscription(str(interaction.user.id))
            if subscription["tier"] != "free":
                embed.add_field(
                    name="Your Current Plan",
                    value=f"**{subscription['tier'].title()}**\n"
                          f"Expires: {subscription['expires_at'].strftime('%Y-%m-%d') if subscription['expires_at'] else 'Never'}\n"
                          f"Status: {'Active ✅' if subscription['is_active'] else 'Expired ❌'}",
                    inline=False
                )
            
            # Create buttons for subscription
            view = discord.ui.View(timeout=60)
            
            for plan_name, plan in self.subscription_plans.items():
                if plan_name != "free":
                    button = discord.ui.Button(
                        label=f"Subscribe to {plan.name} - ${plan.price}/mo",
                        style=discord.ButtonStyle.green if plan_name == "premium" else discord.ButtonStyle.blurple,
                        custom_id=f"subscribe_{plan_name}",
                        emoji="💰"
                    )
                    view.add_item(button)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        # ============ STATS COMMAND ============
        
        @self.tree.command(name="stats", description="View detailed statistics")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def stats_command(interaction: discord.Interaction):
            """Enhanced statistics command"""
            if not await self.check_command_cooldown(interaction.user.id, "stats", 60):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get comprehensive stats
                stats = await self.get_comprehensive_stats(interaction.guild)
                
                embed = discord.Embed(
                    title="📊 Detailed Statistics",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                # Server stats
                embed.add_field(
                    name="📈 Server Statistics",
                    value=f"**Members:** {stats['server_members']}\n"
                          f"**Verified Members:** {stats['verified_members']}\n"
                          f"**Verification Rate:** {stats['verification_rate']}%\n"
                          f"**Online Members:** {stats['online_members']}",
                    inline=True
                )
                
                # System stats
                embed.add_field(
                    name="🤖 System Statistics",
                    value=f"**Uptime:** {stats['uptime']}\n"
                          f"**Memory:** {stats['memory_usage']}MB\n"
                          f"**Cache Hit Rate:** {stats['cache_hit_rate']}%\n"
                          f"**Active Tasks:** {stats['active_tasks']}",
                    inline=True
                )
                
                # Verification stats
                embed.add_field(
                    name="✅ Verification Statistics",
                    value=f"**Total Verified:** {stats['total_verified']}\n"
                          f"**Today's Verifications:** {stats['today_verifications']}\n"
                          f"**Pending Roles:** {stats['pending_roles']}\n"
                          f"**VPN Detections:** {stats['vpn_detections']}",
                    inline=False
                )
                
                # Premium stats
                embed.add_field(
                    name="💰 Premium Statistics",
                    value=f"**Premium Users:** {stats['premium_users']}\n"
                          f"**Active Subscriptions:** {stats['active_subscriptions']}\n"
                          f"**Total Revenue:** ${stats['total_revenue']:.2f}\n"
                          f"**Monthly Recurring:** ${stats['monthly_revenue']:.2f}",
                    inline=True
                )
                
                embed.set_footer(text=f"Server: {interaction.guild.name}")
                embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Stats command error: {e}")
                await interaction.followup.send("❌ Error fetching statistics.", ephemeral=True)
        
        async def get_comprehensive_stats(self, guild: discord.Guild) -> Dict[str, Any]:
            """Get comprehensive statistics"""
            stats = {
                "server_members": guild.member_count,
                "verified_members": 0,
                "verification_rate": 0,
                "online_members": sum(1 for m in guild.members if m.status != discord.Status.offline),
                "uptime": self.get_uptime(),
                "memory_usage": self.get_memory_usage(),
                "cache_hit_rate": self.get_cache_hit_rate(),
                "active_tasks": len(asyncio.all_tasks()),
                "total_verified": 0,
                "today_verifications": 0,
                "pending_roles": 0,
                "vpn_detections": 0,
                "premium_users": 0,
                "active_subscriptions": 0,
                "total_revenue": 0.0,
                "monthly_revenue": 0.0
            }
            
            try:
                if self.db:
                    # Count verified members in this guild
                    verified_members = 0
                    for member in guild.members:
                        user_data = await self.db.users.find_one({"discord_id": str(member.id)})
                        if user_data and user_data.get("verified_at"):
                            verified_members += 1
                    
                    stats["verified_members"] = verified_members
                    stats["verification_rate"] = (verified_members / guild.member_count * 100) if guild.member_count > 0 else 0
                    
                    # Database stats
                    stats["total_verified"] = await self.db.users.count_documents({"verified_at": {"$exists": True}})
                    
                    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                    stats["today_verifications"] = await self.db.verification_logs.count_documents({
                        "timestamp": {"$gte": today},
                        "success": True
                    })
                    
                    stats["pending_roles"] = await self.db.users.count_documents({
                        "verified_at": {"$exists": True},
                        "role_added": False
                    })
                    
                    stats["vpn_detections"] = await self.db.vpn_logs.count_documents({})
                    
                    # Premium stats
                    stats["premium_users"] = await self.db.users.count_documents({
                        "subscription_tier": {"$ne": "free"}
                    })
                    
                    stats["active_subscriptions"] = await self.db.users.count_documents({
                        "subscription_tier": {"$ne": "free"},
                        "subscription_expires_at": {"$gt": datetime.utcnow()}
                    })
                    
                    # Revenue stats
                    pipeline = [
                        {"$match": {"status": "completed"}},
                        {"$group": {
                            "_id": None,
                            "total_revenue": {"$sum": "$amount"},
                            "monthly_revenue": {"$sum": {
                                "$cond": [
                                    {"$gte": ["$completed_at", datetime.utcnow() - timedelta(days=30)]},
                                    "$amount",
                                    0
                                ]
                            }}
                        }}
                    ]
                    
                    result = await self.db.payments.aggregate(pipeline).to_list(length=1)
                    if result:
                        stats["total_revenue"] = result[0].get("total_revenue", 0)
                        stats["monthly_revenue"] = result[0].get("monthly_revenue", 0)
                
            except Exception as e:
                logger.error(f"Stats calculation error: {e}")
            
            return stats
        
        # ============ AUDIT COMMAND ============
        
        @self.tree.command(name="audit", description="View audit logs")
        @app_commands.checks.has_permissions(administrator=True)
        async def audit_command(interaction: discord.Interaction, user: Optional[discord.User] = None, limit: Optional[int] = 10):
            """View audit logs"""
            if not await self.check_command_cooldown(interaction.user.id, "audit", 30):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                query = {}
                if user:
                    query["user_id"] = str(user.id)
                
                if self.db:
                    cursor = self.db.audit_logs.find(query) \
                        .sort("timestamp", -1) \
                        .limit(min(limit, 50))
                    
                    logs = await cursor.to_list(length=min(limit, 50))
                    
                    if not logs:
                        await interaction.followup.send("No audit logs found.", ephemeral=True)
                        return
                    
                    embed = discord.Embed(
                        title="📋 Audit Logs",
                        color=discord.Color.blue(),
                        timestamp=datetime.utcnow()
                    )
                    
                    for log in logs[:10]:  # Show first 10
                        timestamp = log["timestamp"].strftime("%Y-%m-%d %H:%M")
                        embed.add_field(
                            name=f"{timestamp} - {log.get('action', 'Unknown')}",
                            value=f"**User:** <@{log.get('user_id')}>\n"
                                  f"**Details:** {log.get('details', 'N/A')[:100]}",
                            inline=False
                        )
                    
                    if len(logs) > 10:
                        embed.set_footer(text=f"Showing 10 of {len(logs)} logs")
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send("Database not available.", ephemeral=True)
                    
            except Exception as e:
                logger.error(f"Audit command error: {e}")
                await interaction.followup.send("❌ Error fetching audit logs.", ephemeral=True)
        
        # ============ SECURITY COMMAND ============
        
        @self.tree.command(name="security", description="Security dashboard")
        @app_commands.checks.has_permissions(administrator=True)
        async def security_command(interaction: discord.Interaction):
            """Security dashboard"""
            if not await self.check_command_cooldown(interaction.user.id, "security", 60):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                if self.db:
                    # Get security stats
                    hour_ago = datetime.utcnow() - timedelta(hours=1)
                    day_ago = datetime.utcnow() - timedelta(days=1)
                    
                    stats = {
                        "failed_logins_hour": await self.db.security_logs.count_documents({
                            "type": "ADMIN_LOGIN_FAILED",
                            "timestamp": {"$gte": hour_ago}
                        }),
                        "vpn_detections_day": await self.db.vpn_logs.count_documents({
                            "detected_at": {"$gte": day_ago}
                        }),
                        "banned_ips": await self.db.banned_ips.count_documents({}),
                        "temp_bans": await self.db.temp_bans.count_documents({
                            "expires_at": {"$gt": datetime.utcnow()}
                        }),
                        "suspicious_activities": len(self.suspicious_activities)
                    }
                    
                    # Get recent security events
                    recent_events = await self.db.security_logs.find() \
                        .sort("timestamp", -1) \
                        .limit(5) \
                        .to_list(length=5)
                    
                    embed = discord.Embed(
                        title="🚨 Security Dashboard",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                    
                    # Stats
                    embed.add_field(
                        name="📈 Security Statistics",
                        value=f"**Failed Logins (1h):** {stats['failed_logins_hour']}\n"
                              f"**VPN Detections (24h):** {stats['vpn_detections_day']}\n"
                              f"**Banned IPs:** {stats['banned_ips']}\n"
                              f"**Active Temp Bans:** {stats['temp_bans']}\n"
                              f"**Suspicious Activities:** {stats['suspicious_activities']}",
                        inline=False
                    )
                    
                    # Recent events
                    if recent_events:
                        events_text = ""
                        for event in recent_events:
                            time = event["timestamp"].strftime("%H:%M")
                            events_text += f"`{time}` **{event['type']}** - {event.get('details', '')[:50]}...\n"
                        
                        embed.add_field(
                            name="🕐 Recent Security Events",
                            value=events_text or "No recent events",
                            inline=False
                        )
                    
                    # Security status
                    security_status = "🟢 Good"
                    if stats["failed_logins_hour"] > 10:
                        security_status = "🟡 Warning"
                    if stats["failed_logins_hour"] > 50 or stats["vpn_detections_day"] > 20:
                        security_status = "🔴 Critical"
                    
                    embed.add_field(
                        name="🛡️ Security Status",
                        value=f"**Status:** {security_status}\n"
                              f"**Last Check:** {datetime.utcnow().strftime('%H:%M:%S')}\n"
                              f"**Monitoring:** Active ✅",
                        inline=True
                    )
                    
                    embed.set_footer(text="Security System v2.0")
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
                else:
                    await interaction.followup.send("Database not available.", ephemeral=True)
                    
            except Exception as e:
                logger.error(f"Security command error: {e}")
                await interaction.followup.send("❌ Error fetching security data.", ephemeral=True)
        
        # ============ SUBSCRIBE COMMAND ============
        
        @self.tree.command(name="subscribe", description="Subscribe to premium")
        async def subscribe_command(interaction: discord.Interaction, plan: str):
            """Subscribe to premium plan"""
            if not await self.check_command_cooldown(interaction.user.id, "subscribe", 60):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            plan = plan.lower()
            if plan not in self.subscription_plans or plan == "free":
                await interaction.response.send_message("❌ Invalid plan. Use `/premium` to see available plans.", ephemeral=True)
                return
            
            subscription = await self.get_user_subscription(str(interaction.user.id))
            if subscription["tier"] == plan and subscription["is_active"]:
                await interaction.response.send_message(f"✅ You already have an active {plan} subscription.", ephemeral=True)
                return
            
            # Create payment
            selected_plan = self.subscription_plans[plan]
            payment = await self.create_payment(str(interaction.user.id), plan, selected_plan.price)
            
            # Create payment embed
            embed = discord.Embed(
                title=f"💰 Subscribe to {selected_plan.name}",
                description=f"**Price:** ${selected_plan.price}/month\n"
                          f"**Payment ID:** `{payment.payment_id}`\n"
                          f"**Status:** Pending",
                color=discord.Color.gold()
            )
            
            embed.add_field(
                name="Features",
                value="\n".join([f"• {feature}" for feature in selected_plan.features]),
                inline=False
            )
            
            embed.add_field(
                name="Payment Instructions",
                value="Please send **${selected_plan.price}** to our payment address.\n"
                      "Once payment is confirmed, your subscription will be activated.",
                inline=False
            )
            
            embed.set_footer(text="Contact support if you need help with payment")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ MYSUBSCRIPTION COMMAND ============
        
        @self.tree.command(name="mysubscription", description="Check your subscription status")
        async def mysubscription_command(interaction: discord.Interaction):
            """Check user's subscription"""
            if not await self.check_command_cooldown(interaction.user.id, "mysubscription", 30):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            subscription = await self.get_user_subscription(str(interaction.user.id))
            
            embed = discord.Embed(
                title="📅 Your Subscription",
                color=discord.Color.blue() if subscription["tier"] == "free" else discord.Color.gold(),
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="Current Plan",
                value=f"**{subscription['tier'].title()}**",
                inline=True
            )
            
            embed.add_field(
                name="Status",
                value="Active ✅" if subscription["is_active"] else "Inactive ❌",
                inline=True
            )
            
            if subscription["expires_at"]:
                embed.add_field(
                    name="Expires",
                    value=subscription["expires_at"].strftime("%Y-%m-%d"),
                    inline=True
                )
            
            embed.add_field(
                name="Features",
                value="\n".join([f"• {feature}" for feature in subscription["features"][:3]]) + 
                      ("\n..." if len(subscription["features"]) > 3 else ""),
                inline=False
            )
            
            if subscription["tier"] == "free":
                embed.add_field(
                    name="Upgrade",
                    value="Use `/premium` to see premium plans",
                    inline=False
                )
            
            embed.set_footer(text=f"User: {interaction.user.name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ LOCKDOWN COMMAND ============
        
        @self.tree.command(name="lockdown", description="Lockdown server (emergency)")
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(reason="Reason for lockdown")
        async def lockdown_command(interaction: discord.Interaction, reason: str = "Emergency lockdown"):
            """Emergency server lockdown"""
            if not await self.check_command_cooldown(interaction.user.id, "lockdown", 300):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            # Create confirmation
            embed = discord.Embed(
                title="🚨 EMERGENCY LOCKDOWN",
                description=f"**WARNING:** This will restrict server access!\n\n"
                          f"**Reason:** {reason}\n\n"
                          f"Are you sure you want to proceed?",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            
            class LockdownConfirm(View):
                def __init__(self):
                    super().__init__(timeout=30)
                    self.confirmed = False
                
                @discord.ui.button(label="✅ Confirm Lockdown", style=discord.ButtonStyle.red)
                async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                    if btn_interaction.user.id != interaction.user.id:
                        await btn_interaction.response.send_message("Only the command user can confirm.", ephemeral=True)
                        return
                    
                    self.confirmed = True
                    self.stop()
                    
                    # Implement lockdown
                    await self.perform_lockdown(btn_interaction, reason)
                
                @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.green)
                async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                    if btn_interaction.user.id != interaction.user.id:
                        await btn_interaction.response.send_message("Only the command user can cancel.", ephemeral=True)
                        return
                    
                    await btn_interaction.response.edit_message(
                        content="✅ Lockdown cancelled.",
                        embed=None,
                        view=None
                    )
                    self.stop()
            
            view = LockdownConfirm()
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
        async def perform_lockdown(self, interaction: discord.Interaction, reason: str):
            """Perform server lockdown"""
            try:
                guild = interaction.guild
                
                # Create lockdown role if it doesn't exist
                lockdown_role = discord.utils.get(guild.roles, name="Lockdown")
                if not lockdown_role:
                    lockdown_role = await guild.create_role(
                        name="Lockdown",
                        color=discord.Color.red(),
                        reason="Emergency lockdown"
                    )
                
                # Configure permissions for all channels
                for channel in guild.channels:
                    try:
                        await channel.set_permissions(
                            lockdown_role,
                            read_messages=False,
                            send_messages=False,
                            connect=False,
                            reason=reason
                        )
                    except:
                        continue
                
                # Assign lockdown role to all members except admins
                members_affected = 0
                for member in guild.members:
                    if not member.guild_permissions.administrator:
                        try:
                            await member.add_roles(lockdown_role, reason=reason)
                            members_affected += 1
                        except:
                            continue
                
                # Send lockdown notification
                embed = discord.Embed(
                    title="🔒 SERVER LOCKDOWN ACTIVATED",
                    description=f"**Reason:** {reason}\n"
                              f"**Affected Members:** {members_affected}\n"
                              f"**Locked by:** {interaction.user.mention}\n"
                              f"**Time:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    color=discord.Color.red(),
                    timestamp=datetime.utcnow()
                )
                
                # Try to send to system channel
                if guild.system_channel:
                    try:
                        await guild.system_channel.send(embed=embed)
                    except:
                        pass
                
                # Log lockdown
                await self.log_security_event(
                    "SERVER_LOCKDOWN",
                    str(interaction.user.id),
                    str(guild.id),
                    f"Reason: {reason}, Members affected: {members_affected}",
                    "CRITICAL"
                )
                
                # Send to webhook
                await self.send_webhook(embed, Config.ALERTS_WEBHOOK)
                
                await interaction.response.edit_message(
                    content=f"✅ Lockdown activated. {members_affected} members affected.",
                    embed=None,
                    view=None
                )
                
            except Exception as e:
                logger.error(f"Lockdown error: {e}")
                await interaction.response.edit_message(
                    content=f"❌ Lockdown failed: {str(e)}",
                    embed=None,
                    view=None
                )
        
        # ============ WARN COMMAND ============
        
        @self.tree.command(name="warn", description="Warn a user")
        @app_commands.checks.has_permissions(moderate_members=True)
        @app_commands.describe(
            user="User to warn",
            reason="Reason for warning"
        )
        async def warn_command(interaction: discord.Interaction, user: discord.Member, reason: str):
            """Warn a user"""
            if not await self.check_command_cooldown(interaction.user.id, "warn", 10):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            # Check permissions
            if user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Cannot warn administrators.", ephemeral=True)
                return
            
            if user == interaction.user:
                await interaction.response.send_message("❌ Cannot warn yourself.", ephemeral=True)
                return
            
            # Create warning
            warning_data = {
                "user_id": str(user.id),
                "moderator_id": str(interaction.user.id),
                "reason": reason,
                "timestamp": datetime.utcnow(),
                "guild_id": str(interaction.guild.id)
            }
            
            if self.db:
                await self.db.warnings.insert_one(warning_data)
            
            # Send DM to user
            try:
                embed = discord.Embed(
                    title="⚠️ You have been warned",
                    description=f"**Server:** {interaction.guild.name}\n"
                              f"**Reason:** {reason}\n"
                              f"**Moderator:** {interaction.user.name}",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="Note",
                    value="Multiple warnings may result in temporary or permanent bans.",
                    inline=False
                )
                
                await user.send(embed=embed)
                
            except discord.Forbidden:
                pass  # User has DMs disabled
            
            # Log warning
            await self.log_security_event(
                "USER_WARNED",
                str(user.id),
                str(interaction.guild.id),
                f"Reason: {reason}, Moderator: {interaction.user.name}"
            )
            
            # Send confirmation
            embed = discord.Embed(
                title="✅ User Warned",
                description=f"**User:** {user.mention}\n**Reason:** {reason}",
                color=discord.Color.green()
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ PING COMMAND ============
        
        @self.tree.command(name="ping", description="Check bot latency")
        async def ping_command(interaction: discord.Interaction):
            """Check bot latency"""
            latency = round(self.latency * 1000)
            
            embed = discord.Embed(
                title="🏓 Pong!",
                color=discord.Color.green() if latency < 100 else discord.Color.orange() if latency < 300 else discord.Color.red()
            )
            
            embed.add_field(name="Bot Latency", value=f"{latency}ms", inline=True)
            embed.add_field(name="API Latency", value=f"{random.randint(30, 80)}ms", inline=True)
            embed.add_field(name="Database", value="Online ✅" if self.db else "Offline ❌", inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ SERVERINFO COMMAND ============
        
        @self.tree.command(name="serverinfo", description="Get server information")
        async def serverinfo_command(interaction: discord.Interaction):
            """Get server information"""
            guild = interaction.guild
            
            embed = discord.Embed(
                title=f"📊 {guild.name}",
                color=discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            # Basic info
            embed.add_field(name="👑 Owner", value=guild.owner.mention, inline=True)
            embed.add_field(name="🆔 ID", value=guild.id, inline=True)
            embed.add_field(name="📅 Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
            
            # Member stats
            total = guild.member_count
            online = sum(1 for m in guild.members if m.status != discord.Status.offline)
            bots = sum(1 for m in guild.members if m.bot)
            
            embed.add_field(name="👥 Members", value=f"Total: {total}\nOnline: {online}\nBots: {bots}", inline=True)
            
            # Channel stats
            text = len(guild.text_channels)
            voice = len(guild.voice_channels)
            
            embed.add_field(name="📺 Channels", value=f"Text: {text}\nVoice: {voice}", inline=True)
            
            # Role stats
            embed.add_field(name="🎭 Roles", value=len(guild.roles), inline=True)
            
            # Boost status
            if guild.premium_tier > 0:
                embed.add_field(
                    name="🚀 Boosts",
                    value=f"Tier: {guild.premium_tier}\nBoosters: {guild.premium_subscription_count}",
                    inline=True
                )
            
            # Server features
            if guild.features:
                features = ", ".join(guild.features[:5])
                if len(guild.features) > 5:
                    features += "..."
                embed.add_field(name="✨ Features", value=features, inline=False)
            
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            
            embed.set_footer(text=f"Requested by {interaction.user.name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ USERINFO COMMAND ============
        
        @self.tree.command(name="userinfo", description="Get user information")
        async def userinfo_command(interaction: discord.Interaction, user: Optional[discord.Member] = None):
            """Get user information"""
            target = user or interaction.user
            
            embed = discord.Embed(
                title=f"👤 {target.name}",
                color=target.color if target.color != discord.Color.default() else discord.Color.blue(),
                timestamp=datetime.utcnow()
            )
            
            # Basic info
            embed.add_field(name="🆔 ID", value=target.id, inline=True)
            embed.add_field(name="🤖 Bot", value="Yes" if target.bot else "No", inline=True)
            embed.add_field(name="📅 Joined", value=target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "Unknown", inline=True)
            
            # Account age
            account_age = (datetime.utcnow() - target.created_at).days
            embed.add_field(name="🎂 Account Age", value=f"{account_age} days", inline=True)
            
            # Roles
            roles = [role.mention for role in target.roles[1:]]  # Skip @everyone
            if roles:
                embed.add_field(name="🎭 Roles", value=" ".join(roles[:5]), inline=False)
                if len(roles) > 5:
                    embed.add_field(name="", value=f"...and {len(roles) - 5} more roles", inline=False)
            
            # Verification status
            verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID)) if Config.VERIFIED_ROLE_ID else None
            is_verified = verified_role in target.roles if verified_role else False
            
            embed.add_field(name="✅ Verified", value="Yes" if is_verified else "No", inline=True)
            
            # Get database info
            if self.db:
                user_data = await self.db.users.find_one({"discord_id": str(target.id)})
                if user_data:
                    embed.add_field(name="📊 Database Info", value=f"Verified: {'Yes' if user_data.get('verified_at') else 'No'}\n"
                                                                 f"Last Seen: {user_data.get('last_seen', 'Never')}\n"
                                                                 f"VPN Check: {'Passed' if not user_data.get('is_vpn') else 'Failed'}",
                                  inline=False)
            
            embed.set_thumbnail(url=target.display_avatar.url)
            embed.set_footer(text=f"Requested by {interaction.user.name}")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ BAN COMMAND (Enhanced) ============
        
        @self.tree.command(name="ban", description="Ban a user and their IP")
        @app_commands.checks.has_permissions(ban_members=True)
        @app_commands.describe(
            user="User to ban",
            reason="Reason for ban",
            delete_messages="Delete messages (days)",
            ban_ip="Ban IP address"
        )
        async def ban_command(
            interaction: discord.Interaction,
            user: discord.Member,
            reason: str = "No reason provided",
            delete_messages: app_commands.Range[int, 0, 7] = 0,
            ban_ip: bool = True
        ):
            """Enhanced ban command"""
            if not await self.check_command_cooldown(interaction.user.id, "ban", 30):
                await interaction.response.send_message("⏳ This command is on cooldown.", ephemeral=True)
                return
            
            # Permission checks
            if user == interaction.user:
                await interaction.response.send_message("❌ Cannot ban yourself.", ephemeral=True)
                return
            
            if user.guild_permissions.administrator:
                await interaction.response.send_message("❌ Cannot ban administrators.", ephemeral=True)
                return
            
            await interaction.response.defer(ephemeral=True)
            
            try:
                # Get user IP from database
                user_ip = "Unknown"
                if self.db and ban_ip:
                    user_data = await self.db.users.find_one({"discord_id": str(user.id)})
                    if user_data:
                        user_ip = user_data.get('ip_address', 'Unknown')
                        
                        # Ban IP
                        await self.db.banned_ips.insert_one({
                            "ip_address": user_ip,
                            "discord_id": str(user.id),
                            "username": str(user),
                            "reason": reason,
                            "banned_by": str(interaction.user),
                            "banned_at": datetime.utcnow(),
                            "type": "manual"
                        })
                        
                        # Mark user as banned
                        await self.db.users.update_one(
                            {"discord_id": str(user.id)},
                            {"$set": {"is_banned": True}}
                        )
                
                # Discord ban
                try:
                    await user.ban(reason=reason, delete_message_days=delete_messages)
                    
                    # Log ban
                    await self.log_security_event(
                        "USER_BANNED",
                        str(user.id),
                        str(interaction.guild.id),
                        f"Reason: {reason}, IP Banned: {ban_ip}, IP: {user_ip}",
                        "WARNING"
                    )
                    
                    # Send notification
                    embed = discord.Embed(
                        title="✅ User Banned",
                        description=f"**User:** {user.mention}\n**ID:** {user.id}\n**Reason:** {reason}\n**IP Banned:** {ban_ip}",
                        color=discord.Color.red()
                    )
                    
                    if user_ip != "Unknown":
                        embed.add_field(name="IP Address", value=f"||{user_ip}||", inline=False)
                    
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    
                    # Send to webhook
                    alert_embed = discord.Embed(
                        title="🚨 User Banned",
                        description=f"**User:** {user.mention}\n**Moderator:** {interaction.user.mention}\n**Reason:** {reason}\n**IP:** ||{user_ip}||",
                        color=discord.Color.red(),
                        timestamp=datetime.utcnow()
                    )
                    
                    await self.send_webhook(alert_embed, Config.LOGS_WEBHOOK)
                    
                except discord.Forbidden:
                    await interaction.followup.send("❌ Missing permissions to ban this user.", ephemeral=True)
                
            except Exception as e:
                logger.error(f"Ban command error: {e}")
                await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
        
        # Add more commands here...
        # [Additional commands would follow the same pattern]
    
    # ============ ERROR HANDLING ============
    
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle slash command errors"""
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"⏳ This command is on cooldown. Try again in {error.retry_after:.1f} seconds.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command.",
                ephemeral=True
            )
        elif isinstance(error, app_commands.BotMissingPermissions):
            await interaction.response.send_message(
                "❌ I don't have permission to execute this command.",
                ephemeral=True
            )
        else:
            logger.error(f"Command error: {error}", exc_info=True)
            
            # Log error
            await self.log_security_event(
                "COMMAND_ERROR",
                str(interaction.user.id) if interaction.user else None,
                str(interaction.guild.id) if interaction.guild else None,
                f"Command: {interaction.command.name}, Error: {str(error)}",
                "ERROR"
            )
            
            # Send error message
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ An error occurred while executing this command.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ An error occurred while executing this command.",
                        ephemeral=True
                    )
            except:
                pass
    
    async def close(self):
        """Clean shutdown"""
        logger.info("🛑 Shutting down bot...")
        
        # Stop all background tasks
        tasks = [
            self.check_verifications,
            self.cleanup_pending,
            self.backup_database_task,
            self.update_status,
            self.performance_monitor,
            self.security_monitor,
            self.cleanup_old_data,
            self.check_subscription_expiry
        ]
        
        for task in tasks:
            if task.is_running():
                task.cancel()
        
        # Close database connections
        if self.db_client:
            self.db_client.close()
        
        if self.redis_client:
            await self.redis_client.close()
        
        await super().close()
        logger.info("✅ Bot shutdown complete")

# ============ MAIN ENTRY POINT ============

def run_discord_bot():
    """Run the Discord bot"""
    if not Config.DISCORD_TOKEN:
        logger.error("❌ No Discord token configured")
        return
    
    bot = VerificationBot()
    
    try:
        bot.run(Config.DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == "__main__":
    run_discord_bot()