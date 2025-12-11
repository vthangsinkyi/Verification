import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional
import sys
import os
import time

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from utils.logger import logger
from utils.backup import backup_database

class VerificationBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        
        super().__init__(command_prefix="/", intents=intents)
        
        # Setup database connection
        self.db_client = None
        self.db = None
        self.setup_database()
        
        # Store pending verifications with attempt tracking
        self.pending_verifications = {}
        
        # Store failed attempts (for blacklisting)
        self.failed_attempts = {}
        
        # Command cooldown tracking
        self.command_cooldowns = {}
        
        # Track which users we've already logged "not found" for
        self.already_logged_not_found = set()
        
    def setup_database(self):
        """Setup MongoDB connection"""
        try:
            from pymongo import MongoClient
            self.db_client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
            self.db_client.server_info()  # Test connection
            self.db = self.db_client[Config.DATABASE_NAME]
            logger.info("✅ Bot connected to MongoDB")
        except Exception as e:
            logger.error(f"⚠️  Bot MongoDB connection failed: {e}")
            self.db = None
    
    async def on_ready(self):
        logger.info(f'✅ Bot logged in as {self.user}')
        logger.info(f'✅ Connected to {len(self.guilds)} guild(s)')
        
        # Set status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for verifications"
            )
        )
        
        # Start background tasks
        self.check_verifications.start()
        self.cleanup_pending.start()
        self.backup_database_task.start()
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            logger.error(f"❌ Command sync failed: {e}")
    
    @tasks.loop(seconds=30)  # Changed from 10 to 30 seconds to reduce spam
    async def check_verifications(self):
        """Check for new verified users and give them roles"""
        if self.db is None or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            # Find users who are verified but don't have role_added flag
            unprocessed_users = self.db.users.find({
                "verified_at": {"$exists": True},
                "role_added": {"$ne": True},
                "is_banned": {"$ne": True},
                "is_blacklisted": {"$ne": True}  # Skip blacklisted users
            })
            
            for user in unprocessed_users:
                discord_id = user.get('discord_id')
                username = user.get('username', 'Unknown')
                
                if not discord_id:
                    continue
                
                # Check if we should skip logging (already logged recently)
                user_key = f"{discord_id}_{username}"
                
                # Try to give role in all guilds the bot is in
                role_given = False
                member_found = False
                
                for guild in self.guilds:
                    try:
                        member = await guild.fetch_member(int(discord_id))
                        if member:
                            member_found = True
                            
                            # Get verified role
                            verified_role = guild.get_role(int(Config.VERIFIED_ROLE_ID))
                            if verified_role:
                                # Check if member already has role
                                if verified_role not in member.roles:
                                    await member.add_roles(verified_role)
                                    logger.info(f"✅ Added role to {member.name} in {guild.name}")
                                    
                                    # Send DM to user
                                    try:
                                        embed = discord.Embed(
                                            title="✅ Verification Complete!",
                                            description="You have been successfully verified!\n\nYou can now access all channels in the server.",
                                            color=discord.Color.green()
                                        )
                                        embed.set_footer(text="Thank you for verifying!")
                                        await member.send(embed=embed)
                                    except:
                                        pass  # User might have DMs closed
                                
                                # Mark as role added
                                self.db.users.update_one(
                                    {"discord_id": discord_id},
                                    {"$set": {"role_added": True, "role_added_at": datetime.utcnow()}}
                                )
                                
                                role_given = True
                                
                                # Clear from pending and failed attempts
                                if discord_id in self.pending_verifications:
                                    del self.pending_verifications[discord_id]
                                if user_key in self.already_logged_not_found:
                                    self.already_logged_not_found.remove(user_key)
                                
                                # Send webhook notification
                                await self.send_webhook(
                                    "✅ ROLE ASSIGNED",
                                    f"**User:** {member.mention}\n**ID:** {member.id}\n**Role:** {verified_role.name}\n**Server:** {guild.name}",
                                    0x00ff00
                                )
                                break  # Role given, break out of guild loop
                    
                    except discord.NotFound:
                        continue  # Member not in this guild
                    except discord.Forbidden:
                        logger.error(f"❌ No permission to add role in {guild.name}")
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error giving role in {guild.name}: {e}")
                        continue
                
                if not member_found:
                    # User not in any server
                    if user_key not in self.already_logged_not_found:
                        # First time we can't find them
                        logger.info(f"⏳ User verified but not in server yet: {username} ({discord_id})")
                        self.already_logged_not_found.add(user_key)
                        
                        # Add to pending for tracking
                        self.pending_verifications[discord_id] = {
                            "username": username,
                            "attempts": 1,
                            "first_attempt": time.time(),
                            "last_attempt": time.time()
                        }
                    else:
                        # Update pending verification attempts
                        if discord_id in self.pending_verifications:
                            self.pending_verifications[discord_id]["attempts"] += 1
                            self.pending_verifications[discord_id]["last_attempt"] = time.time()
                            
                            # Check if should blacklist (too many attempts)
                            attempts = self.pending_verifications[discord_id]["attempts"]
                            first_attempt = self.pending_verifications[discord_id]["first_attempt"]
                            time_since_first = time.time() - first_attempt
                            
                            # Blacklist if: more than 10 attempts AND more than 24 hours passed
                            if attempts > 10 and time_since_first > 86400:  # 24 hours
                                logger.warning(f"🚫 Blacklisting user {username} ({discord_id}) - Too many attempts without joining")
                                
                                # Mark as blacklisted in database
                                self.db.users.update_one(
                                    {"discord_id": discord_id},
                                    {"$set": {"is_blacklisted": True, "blacklist_reason": "Too many verification attempts without joining server"}}
                                )
                                
                                # Remove from pending
                                del self.pending_verifications[discord_id]
                                
                                # Send webhook notification
                                await self.send_webhook(
                                    "🚫 USER BLACKLISTED",
                                    f"**User:** {username}\n**ID:** {discord_id}\n**Reason:** Too many verification attempts without joining server\n**Attempts:** {attempts}\n**Time since first attempt:** {int(time_since_first/3600)} hours",
                                    0xff9900
                                )
        
        except Exception as e:
            logger.error(f"❌ Error in check_verifications: {e}")
    
    @tasks.loop(minutes=10)  # Cleanup every 10 minutes
    async def cleanup_pending(self):
        """Cleanup old pending verifications and clear logs"""
        current_time = time.time()
        expired = []
        cleaned_logs = 0
        
        # Clean pending verifications (older than 7 days)
        for discord_id, data in self.pending_verifications.items():
            first_attempt = data.get("first_attempt", current_time)
            if (current_time - first_attempt) > 604800:  # 7 days
                expired.append(discord_id)
                
                # Also mark as blacklisted if they never joined
                username = data.get("username", "Unknown")
                logger.info(f"🗑️  Removing old pending verification: {username} ({discord_id}) - Never joined after 7 days")
                
                if self.db is not None:
                    self.db.users.update_one(
                        {"discord_id": discord_id},
                        {"$set": {"is_blacklisted": True, "blacklist_reason": "Never joined server after 7 days"}}
                    )
        
        for discord_id in expired:
            del self.pending_verifications[discord_id]
        
        # Clean already_logged_not_found set (remove entries older than 1 hour)
        to_remove = []
        for user_key in list(self.already_logged_not_found):
            # Extract timestamp from key or use default
            # We'll remove entries that have been there too long to keep memory usage low
            if len(self.already_logged_not_found) > 1000:  # If set gets too big
                to_remove.append(user_key)
                cleaned_logs += 1
        
        for user_key in to_remove:
            self.already_logged_not_found.remove(user_key)
        
        if cleaned_logs > 0:
            logger.info(f"🧹 Cleaned {cleaned_logs} old log entries from memory")
        
        # Clean failed attempts (older than 1 hour)
        to_remove_failed = []
        for user_key, attempt_data in list(self.failed_attempts.items()):
            if (current_time - attempt_data.get("last_attempt", 0)) > 3600:  # 1 hour
                to_remove_failed.append(user_key)
        
        for user_key in to_remove_failed:
            del self.failed_attempts[user_key]
    
    @tasks.loop(hours=6)  # Backup every 6 hours
    async def backup_database_task(self):
        """Automatically backup database"""
        try:
            backup_path = backup_database()
            if backup_path:
                await self.send_webhook(
                    "💾 DATABASE BACKUP",
                    f"**Backup created:** {backup_path}\n**Time:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}",
                    0x00ff00
                )
                logger.info(f"✅ Database backup created: {backup_path}")
        except Exception as e:
            logger.error(f"❌ Backup task failed: {e}")
    
    @check_verifications.before_loop
    @cleanup_pending.before_loop
    @backup_database_task.before_loop
    async def before_tasks(self):
        """Wait until bot is ready before starting tasks"""
        await self.wait_until_ready()
    
    async def check_command_cooldown(self, user_id, command_name, cooldown_seconds):
        """Check if command is on cooldown for user"""
        key = f"{user_id}_{command_name}"
        current_time = time.time()
        
        if key in self.command_cooldowns:
            last_used = self.command_cooldowns[key]
            if current_time - last_used < cooldown_seconds:
                return False
        
        self.command_cooldowns[key] = current_time
        return True
    
    async def on_member_join(self, member):
        """Check if new member is already verified and give role"""
        if self.db is None or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            # Check if user is in database
            user_data = self.db.users.find_one({
                "discord_id": str(member.id),
                "role_added": {"$ne": True},
                "is_banned": {"$ne": True},
                "is_blacklisted": {"$ne": True}
            })
            
            if user_data:
                # Get verified role
                verified_role = member.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if verified_role:
                    # Give role
                    await member.add_roles(verified_role)
                    
                    # Update database
                    self.db.users.update_one(
                        {"discord_id": str(member.id)},
                        {"$set": {"role_added": True, "role_added_at": datetime.utcnow()}}
                    )
                    
                    logger.info(f"✅ Auto-gave role to rejoining member: {member.name}")
                    
                    # Remove from pending if exists
                    if str(member.id) in self.pending_verifications:
                        del self.pending_verifications[str(member.id)]
                    
                    # Remove from already_logged_not_found
                    user_key = f"{member.id}_{member.name}"
                    if user_key in self.already_logged_not_found:
                        self.already_logged_not_found.remove(user_key)
                    
                    await self.send_webhook(
                        "🔄 AUTO-ROLE ON JOIN",
                        f"**User:** {member.mention}\n**ID:** {member.id}\n**Role:** {verified_role.name}\n**Action:** Automatic role assignment on server join",
                        0x00ff00
                    )
        
        except Exception as e:
            logger.error(f"❌ Error in on_member_join: {e}")
    
    async def setup_hook(self):
        """Setup all slash commands"""
        
        # ============ /setup COMMAND ============
        @self.tree.command(name="setup", description="Setup verification system")
        @app_commands.checks.has_permissions(administrator=True)
        async def setup_verification(interaction: discord.Interaction):
            """Setup verification embed"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "setup", 30):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 30 seconds.", ephemeral=True)
                return
            
            # 1. Send ephemeral confirmation to command user
            await interaction.response.send_message(
                "✅ Verification panel has been created in this channel!",
                ephemeral=True
            )
            
            # 2. Send public verification panel in the channel
            embed = discord.Embed(
                title="🔐 SERVER VERIFICATION",
                description="**Click the button below to start verification**\n\nThis verification is required to access all channels in the server.\n\n⚠️ **Rules:**\n• VPN/Proxy users will be banned\n• One account per person only\n• IP address will be recorded for security",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Protecting our community from scammers")
            
            view = discord.ui.View()
            button = discord.ui.Button(
                label="✅ Start Verification",
                style=discord.ButtonStyle.green,
                url=Config.VERIFY_URL
            )
            view.add_item(button)
            
            # Send the public panel
            await interaction.channel.send(embed=embed, view=view)
            
            logger.info(f"Verification panel setup by {interaction.user} in #{interaction.channel.name}")
            await self.send_webhook(
                "📝 VERIFICATION PANEL SETUP",
                f"**Setup by:** {interaction.user.mention}\n**Channel:** #{interaction.channel.name}\n**Server:** {interaction.guild.name}",
                0x00ff00
            )
        
        # ============ /ban COMMAND ============
        @self.tree.command(name="ban", description="Ban a user and their IP")
        @app_commands.checks.has_permissions(ban_members=True)
        @app_commands.describe(
            user_id="User ID or mention to ban",
            reason="Reason for ban",
            delete_messages="Delete messages from this user (days, 0-7)"
        )
        async def ban_user(
            interaction: discord.Interaction, 
            user_id: str,
            reason: str = "No reason provided",
            delete_messages: app_commands.Range[int, 0, 7] = 0
        ):
            """Ban user by ID and their IP"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "ban", 10):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 10 seconds.", ephemeral=True)
                return
            
            try:
                # Clean user ID (remove <@ and > if mention)
                user_id = user_id.replace('<@', '').replace('>', '').replace('!', '')
                
                if not user_id.isdigit():
                    await interaction.response.send_message("❌ Invalid user ID. Please provide a valid Discord ID or mention.", ephemeral=True)
                    return
                
                user_id_int = int(user_id)
                
                # Check if trying to ban self
                if user_id_int == interaction.user.id:
                    await interaction.response.send_message("❌ You cannot ban yourself!", ephemeral=True)
                    return
                
                # Try to find user in guild
                user = None
                try:
                    user = await interaction.guild.fetch_member(user_id_int)
                except discord.NotFound:
                    # User not in server, but we can still ban by ID
                    pass
                
                if user and user.guild_permissions.administrator:
                    await interaction.response.send_message("❌ You cannot ban an administrator!", ephemeral=True)
                    return
                
                # Get IP from database if exists
                user_ip = "Unknown"
                username = "Unknown"
                if self.db is not None:
                    user_data = self.db.users.find_one({"discord_id": user_id})
                    if user_data:
                        user_ip = user_data.get('ip_address', 'Unknown')
                        username = user_data.get('username', 'Unknown')
                        
                        # Save IP to banned_ips collection
                        self.db.banned_ips.insert_one({
                            "discord_id": user_id,
                            "username": username,
                            "ip_address": user_ip,
                            "reason": reason,
                            "banned_by": str(interaction.user),
                            "banned_at": datetime.utcnow()
                        })
                        
                        # Mark user as banned in users collection
                        self.db.users.update_one(
                            {"discord_id": user_id},
                            {"$set": {"is_banned": True}}
                        )
                
                # Ban from Discord (works even if user not in server)
                try:
                    await interaction.guild.ban(
                        discord.Object(id=user_id_int),
                        reason=reason,
                        delete_message_days=delete_messages
                    )
                except discord.NotFound:
                    # User not in server, but we still log the ban
                    pass
                except discord.Forbidden:
                    await interaction.response.send_message("❌ I don't have permission to ban this user!", ephemeral=True)
                    return
                
                # Send webhook notification
                await self.send_webhook(
                    "🚨 USER BANNED",
                    f"**User:** <@{user_id}>\n**ID:** {user_id}\n**Username:** {username}\n**Reason:** {reason}\n**Banned by:** {interaction.user.mention}\n**IP:** ||{user_ip}||\n**Messages Deleted:** {delete_messages} days",
                    0xff0000
                )
                
                logger.info(f"User banned: {username} ({user_id}) by {interaction.user}")
                
                embed = discord.Embed(
                    title="✅ User Banned Successfully",
                    description=f"**User:** <@{user_id}>\n**ID:** {user_id}\n**IP:** ||{user_ip}||\n**Reason:** {reason}",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"Banned by {interaction.user}")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Ban command error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /unban COMMAND ============
        @self.tree.command(name="unban", description="Unban a user by ID")
        @app_commands.checks.has_permissions(ban_members=True)
        @app_commands.describe(
            user_id="User ID to unban",
            reason="Reason for unban"
        )
        async def unban_user(
            interaction: discord.Interaction, 
            user_id: str,
            reason: str = "Manual unban"
        ):
            """Unban a user"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "unban", 10):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 10 seconds.", ephemeral=True)
                return
            
            try:
                # Clean user ID
                user_id = user_id.replace('<@', '').replace('>', '').replace('!', '')
                
                if not user_id.isdigit():
                    await interaction.response.send_message("❌ Invalid user ID", ephemeral=True)
                    return
                
                # Unban from Discord
                try:
                    await interaction.guild.unban(discord.Object(id=int(user_id)), reason=reason)
                except discord.NotFound:
                    # Not banned in Discord, but we still process database
                    pass
                except discord.Forbidden:
                    await interaction.response.send_message("❌ I don't have permission to unban this user!", ephemeral=True)
                    return
                
                # Remove from database
                if self.db is not None:
                    self.db.users.update_one(
                        {"discord_id": user_id},
                        {"$set": {"is_banned": False}}
                    )
                    
                    # Remove from banned_ips
                    self.db.banned_ips.delete_many({"discord_id": user_id})
                
                await self.send_webhook(
                    "✅ USER UNBANNED",
                    f"**User:** <@{user_id}>\n**ID:** {user_id}\n**Reason:** {reason}\n**Unbanned by:** {interaction.user.mention}",
                    0x00ff00
                )
                
                logger.info(f"User unbanned: {user_id} by {interaction.user}")
                
                embed = discord.Embed(
                    title="✅ User Unbanned",
                    description=f"**User:** <@{user_id}>\n**Reason:** {reason}",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Unbanned by {interaction.user}")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Unban command error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /force_verify COMMAND ============
        @self.tree.command(name="force_verify", description="Force verify a user (add verified role)")
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(
            user="User to force verify",
            ip_address="IP address to save (optional)"
        )
        async def force_verify(
            interaction: discord.Interaction, 
            user: discord.Member,
            ip_address: str = "Manual verification"
        ):
            """Force verify a user"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "force_verify", 10):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 10 seconds.", ephemeral=True)
                return
            
            try:
                if not Config.VERIFIED_ROLE_ID:
                    await interaction.response.send_message("❌ Verified role ID not configured!", ephemeral=True)
                    return
                
                # Check if already verified
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if verified_role and verified_role in user.roles:
                    await interaction.response.send_message(
                        f"❌ {user.mention} is already verified!", 
                        ephemeral=True
                    )
                    return
                
                # Get verified role
                if not verified_role:
                    await interaction.response.send_message(f"❌ Verified role not found! Check if role ID {Config.VERIFIED_ROLE_ID} exists.", ephemeral=True)
                    return
                
                # Add verified role to user
                await user.add_roles(verified_role)
                
                # Save to database
                if self.db is not None:
                    self.db.users.update_one(
                        {"discord_id": str(user.id)},
                        {
                            "$set": {
                                "username": str(user),
                                "ip_address": ip_address,
                                "user_agent": "Manual verification",
                                "is_vpn": False,
                                "last_seen": datetime.utcnow(),
                                "force_verified_by": str(interaction.user),
                                "verified_at": datetime.utcnow(),
                                "role_added": True,
                                "role_added_at": datetime.utcnow()
                            },
                            "$setOnInsert": {
                                "is_banned": False,
                                "is_blacklisted": False
                            }
                        },
                        upsert=True
                    )
                
                # Send webhook notification
                await self.send_webhook(
                    "✅ FORCE VERIFIED",
                    f"**User:** {user.mention}\n**ID:** {user.id}\n**Verified by:** {interaction.user.mention}\n**IP:** ||{ip_address}||",
                    0x00ff00
                )
                
                logger.info(f"Force verified: {user} by {interaction.user}")
                
                embed = discord.Embed(
                    title="✅ Force Verification Complete",
                    description=f"{user.mention} has been force verified!\n\n**Role Added:** {verified_role.mention}\n**IP Saved:** ||{ip_address}||",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Verified by {interaction.user}")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Force verify error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /stats COMMAND ============
        @self.tree.command(name="stats", description="Show verification statistics")
        @app_commands.checks.has_permissions(manage_guild=True)
        async def stats_command(interaction: discord.Interaction):
            """Show verification statistics"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "stats", 30):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 30 seconds.", ephemeral=True)
                return
            
            try:
                if self.db is None:
                    await interaction.response.send_message("❌ Database not available", ephemeral=True)
                    return
                
                # Get stats from database
                total_users = self.db.users.count_documents({})
                verified_users = self.db.users.count_documents({"verified_at": {"$exists": True}})
                banned_users = self.db.users.count_documents({"is_banned": True})
                blacklisted_users = self.db.users.count_documents({"is_blacklisted": True})
                banned_ips = self.db.banned_ips.count_documents({})
                
                # Today's stats
                today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                today_verifications = self.db.users.count_documents({
                    "verified_at": {"$gte": today}
                })
                
                # Pending verifications
                pending = len(self.pending_verifications)
                
                embed = discord.Embed(
                    title="📊 Verification Statistics",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(name="Total Users", value=f"👥 {total_users}", inline=True)
                embed.add_field(name="Verified Users", value=f"✅ {verified_users}", inline=True)
                embed.add_field(name="Banned Users", value=f"🚫 {banned_users}", inline=True)
                embed.add_field(name="Blacklisted Users", value=f"⛔ {blacklisted_users}", inline=True)
                embed.add_field(name="Banned IPs", value=f"🔒 {banned_ips}", inline=True)
                embed.add_field(name="Today's Verifications", value=f"📈 {today_verifications}", inline=True)
                embed.add_field(name="Pending Verifications", value=f"⏳ {pending}", inline=True)
                
                # Server info
                embed.add_field(name="Server Members", value=f"👤 {len(interaction.guild.members)}", inline=True)
                
                embed.set_footer(text=f"Requested by {interaction.user}")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Stats command error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /remove_all_verify COMMAND ============
        @self.tree.command(name="remove_all_verify", description="Remove verify role from ALL users")
        @app_commands.checks.has_permissions(administrator=True)
        async def remove_all_verify(interaction: discord.Interaction):
            """Remove verify role from all users"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "remove_all_verify", 60):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 60 seconds.", ephemeral=True)
                return
            
            try:
                if not Config.VERIFIED_ROLE_ID:
                    await interaction.response.send_message("❌ Verified role ID not configured in .env file!", ephemeral=True)
                    return
                
                # Get verified role
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if not verified_role:
                    await interaction.response.send_message(f"❌ Verified role not found! Check if role ID {Config.VERIFIED_ROLE_ID} exists.", ephemeral=True)
                    return
                
                # Create confirmation embed
                embed = discord.Embed(
                    title="⚠️ CONFIRM REMOVE ALL VERIFY",
                    description=f"This will remove the {verified_role.mention} role from **ALL** users in the server.\n\n**This action cannot be undone!**",
                    color=discord.Color.orange()
                )
                embed.set_footer(text="Click Confirm to proceed or Cancel to abort")
                
                # Create view with buttons
                class ConfirmView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)
                        self.confirmed = False
                    
                    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.green)
                    async def confirm_button(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                        if interaction_btn.user.id != interaction.user.id:
                            await interaction_btn.response.send_message("Only the command user can confirm this!", ephemeral=True)
                            return
                        
                        self.confirmed = True
                        self.stop()
                        
                        # Remove role from all members
                        removed_count = 0
                        failed_count = 0
                        
                        for member in interaction.guild.members:
                            try:
                                if verified_role in member.roles:
                                    await member.remove_roles(verified_role)
                                    removed_count += 1
                                    
                                    # Remove from database
                                    if self.db is not None:
                                        self.db.users.delete_one({"discord_id": str(member.id)})
                            except:
                                failed_count += 1
                        
                        # Send webhook notification
                        await self.send_webhook(
                            "🔄 ALL VERIFY REMOVED",
                            f"**Action by:** {interaction.user.mention}\n**Removed from:** {removed_count} users\n**Failed:** {failed_count} users",
                            0xff9900
                        )
                        
                        logger.info(f"All verify removed: {removed_count} users by {interaction.user}")
                        
                        embed = discord.Embed(
                            title="✅ All Verify Removed",
                            description=f"Removed {verified_role.mention} from **{removed_count}** users.\nFailed: **{failed_count}** users.",
                            color=discord.Color.green()
                        )
                        
                        await interaction_btn.response.edit_message(embed=embed, view=None)
                    
                    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.red)
                    async def cancel_button(self, interaction_btn: discord.Interaction, button: discord.ui.Button):
                        if interaction_btn.user.id != interaction.user.id:
                            await interaction_btn.response.send_message("Only the command user can cancel this!", ephemeral=True)
                            return
                        
                        embed = discord.Embed(
                            title="❌ Action Cancelled",
                            description="Verify role removal cancelled.",
                            color=discord.Color.red()
                        )
                        
                        await interaction_btn.response.edit_message(embed=embed, view=None)
                        self.stop()
                
                view = ConfirmView()
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Remove all verify error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /verifyinfo COMMAND ============
        @self.tree.command(name="verifyinfo", description="Get verification info for a user")
        @app_commands.checks.has_permissions(manage_roles=True)
        @app_commands.describe(user="User to check (optional)")
        async def verify_info(interaction: discord.Interaction, user: Optional[discord.Member] = None):
            """Get verification info"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "verifyinfo", 5):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 5 seconds.", ephemeral=True)
                return
            
            target = user or interaction.user
            
            embed = discord.Embed(
                title=f"📊 Verification Info - {target.name}",
                color=discord.Color.green()
            )
            
            # Basic info
            embed.add_field(name="Discord ID", value=target.id, inline=True)
            embed.add_field(name="Joined Server", 
                          value=target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "Unknown", 
                          inline=True)
            
            # Check verified role
            verified_role = None
            is_verified = False
            if Config.VERIFIED_ROLE_ID:
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if verified_role:
                    is_verified = verified_role in target.roles
            
            embed.add_field(name="Verified Status", 
                           value=f"✅ Verified ({verified_role.mention})" if is_verified else "❌ Not Verified", 
                           inline=True)
            
            embed.set_thumbnail(url=target.display_avatar.url)
            
            # Database info
            if self.db is not None:
                user_data = self.db.users.find_one({"discord_id": str(target.id)})
                if user_data:
                    embed.add_field(name="IP Address", 
                                   value=f"||{user_data.get('ip_address', 'Not recorded')}||", 
                                   inline=False)
                    embed.add_field(name="VPN Detected", 
                                   value="✅ Yes" if user_data.get('is_vpn') else "❌ No", 
                                   inline=True)
                    embed.add_field(name="Blacklisted", 
                                   value="✅ Yes" if user_data.get('is_blacklisted') else "❌ No", 
                                   inline=True)
                    
                    last_seen = user_data.get('last_seen')
                    if last_seen:
                        if isinstance(last_seen, datetime):
                            last_seen_str = last_seen.strftime("%Y-%m-%d %H:%M")
                        else:
                            last_seen_str = str(last_seen)
                        embed.add_field(name="Last Seen", value=last_seen_str, inline=True)
                    
                    if user_data.get('force_verified_by'):
                        embed.add_field(name="Force Verified By", 
                                       value=user_data.get('force_verified_by'), 
                                       inline=False)
                    
                    if user_data.get('role_added'):
                        embed.add_field(name="Role Status", 
                                       value="✅ Role assigned" if user_data.get('role_added') else "❌ Role pending",
                                       inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # ============ /fix_roles COMMAND ============
        @self.tree.command(name="fix_roles", description="Fix missing roles for verified users")
        @app_commands.checks.has_permissions(administrator=True)
        async def fix_roles(interaction: discord.Interaction):
            """Fix missing roles for verified users"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "fix_roles", 60):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 60 seconds.", ephemeral=True)
                return
            
            try:
                if not Config.VERIFIED_ROLE_ID:
                    await interaction.response.send_message("❌ Verified role ID not configured!", ephemeral=True)
                    return
                
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if not verified_role:
                    await interaction.response.send_message("❌ Verified role not found!", ephemeral=True)
                    return
                
                # Show working message
                await interaction.response.defer(ephemeral=True)
                
                # Get all verified users from database
                fixed_count = 0
                if self.db is not None:
                    verified_users = self.db.users.find({
                        "verified_at": {"$exists": True},
                        "is_banned": {"$ne": True},
                        "is_blacklisted": {"$ne": True}
                    })
                    
                    for user in verified_users:
                        discord_id = user.get('discord_id')
                        if not discord_id:
                            continue
                        
                        try:
                            member = await interaction.guild.fetch_member(int(discord_id))
                            if member and verified_role not in member.roles:
                                await member.add_roles(verified_role)
                                fixed_count += 1
                                
                                # Update database
                                self.db.users.update_one(
                                    {"discord_id": discord_id},
                                    {"$set": {"role_added": True, "role_added_at": datetime.utcnow()}}
                                )
                        except:
                            continue
                
                logger.info(f"Fixed roles for {fixed_count} users by {interaction.user}")
                
                embed = discord.Embed(
                    title="✅ Roles Fixed",
                    description=f"Fixed roles for **{fixed_count}** users.\n\nThe bot will now automatically assign roles to newly verified users.",
                    color=discord.Color.green()
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"Fix roles error: {e}")
                await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /banip COMMAND ============
        @self.tree.command(name="banip", description="Ban an IP address manually")
        @app_commands.checks.has_permissions(administrator=True)
        @app_commands.describe(
            ip_address="IP address to ban",
            reason="Reason for ban"
        )
        async def ban_ip(
            interaction: discord.Interaction,
            ip_address: str,
            reason: str = "Manual ban"
        ):
            """Ban an IP address manually"""
            # Check cooldown
            if not await self.check_command_cooldown(interaction.user.id, "banip", 10):
                await interaction.response.send_message("⏳ This command is on cooldown. Please wait 10 seconds.", ephemeral=True)
                return
            
            try:
                if self.db is None:
                    await interaction.response.send_message("❌ Database not available", ephemeral=True)
                    return
                
                # Check if IP already banned
                existing = self.db.banned_ips.find_one({"ip_address": ip_address})
                if existing:
                    embed = discord.Embed(
                        title="❌ IP Already Banned",
                        description=f"IP `{ip_address}` is already banned.\n**Reason:** {existing.get('reason')}\n**Banned by:** {existing.get('banned_by')}",
                        color=discord.Color.orange()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                
                # Add to banned IPs
                self.db.banned_ips.insert_one({
                    "ip_address": ip_address,
                    "discord_id": "Manual",
                    "username": "Manual ban",
                    "reason": reason,
                    "banned_by": str(interaction.user),
                    "banned_at": datetime.utcnow()
                })
                
                # Send webhook
                await self.send_webhook(
                    "🚨 IP MANUALLY BANNED",
                    f"**IP:** ||{ip_address}||\n**Reason:** {reason}\n**Banned by:** {interaction.user.mention}",
                    0xff0000
                )
                
                logger.info(f"IP banned: {ip_address} by {interaction.user}")
                
                embed = discord.Embed(
                    title="✅ IP Banned",
                    description=f"IP `{ip_address}` has been added to the ban list.\n**Reason:** {reason}",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"Banned by {interaction.user}")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                    
            except Exception as e:
                logger.error(f"Ban IP error: {e}")
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
    
    async def send_webhook(self, title, description, color):
        """Send webhook to Discord"""
        if not Config.WEBHOOK_URL:
            return
            
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(Config.WEBHOOK_URL, json={"embeds": [embed]}) as response:
                    if response.status != 204:
                        logger.error(f"Webhook failed: {response.status}")
        except Exception as e:
            logger.error(f"Webhook error: {e}")
    
    async def close(self):
        """Cleanup when bot shuts down"""
        self.check_verifications.cancel()
        self.cleanup_pending.cancel()
        self.backup_database_task.cancel()
        await super().close()

def run_discord_bot():
    """Run Discord bot"""
    if not Config.DISCORD_TOKEN:
        logger.error("❌ No Discord token configured")
        return
    
    bot = VerificationBot()
    bot.run(Config.DISCORD_TOKEN)