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
        
        # Store pending verifications
        self.pending_verifications = {}
        
    def setup_database(self):
        """Setup MongoDB connection"""
        try:
            from pymongo import MongoClient
            self.db_client = MongoClient(Config.MONGODB_URI, serverSelectionTimeoutMS=5000)
            self.db_client.server_info()  # Test connection
            self.db = self.db_client[Config.DATABASE_NAME]
            print("✅ Bot connected to MongoDB")
        except Exception as e:
            print(f"⚠️  Bot MongoDB connection failed: {e}")
            self.db = None
    
    async def on_ready(self):
        print(f'✅ Bot logged in as {self.user}')
        print(f'✅ Connected to {len(self.guilds)} guild(s)')
        
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
        
        # Sync commands
        try:
            synced = await self.tree.sync()
            print(f"✅ Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"❌ Command sync failed: {e}")
    
    @tasks.loop(seconds=10)  # Check every 10 seconds
    async def check_verifications(self):
        """Check for new verified users and give them roles"""
        if self.db is None or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            # Find users who are verified but don't have role_added flag
            unprocessed_users = self.db.users.find({
                "verified_at": {"$exists": True},
                "role_added": {"$ne": True},
                "is_banned": {"$ne": True}
            })
            
            for user in unprocessed_users:
                discord_id = user.get('discord_id')
                username = user.get('username')
                
                if not discord_id:
                    continue
                
                print(f"🎯 Processing verification for {username} ({discord_id})")
                
                # Try to give role in all guilds the bot is in
                role_given = False
                for guild in self.guilds:
                    try:
                        member = await guild.fetch_member(int(discord_id))
                        if member:
                            # Get verified role
                            verified_role = guild.get_role(int(Config.VERIFIED_ROLE_ID))
                            if verified_role:
                                # Check if member already has role
                                if verified_role not in member.roles:
                                    await member.add_roles(verified_role)
                                    print(f"✅ Added role to {member.name} in {guild.name}")
                                    
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
                        print(f"❌ No permission to add role in {guild.name}")
                        continue
                    except Exception as e:
                        print(f"❌ Error giving role in {guild.name}: {e}")
                        continue
                
                if not role_given:
                    print(f"⚠️  Could not find member {username} ({discord_id}) in any server")
                    # Add to pending for retry later
                    self.pending_verifications[discord_id] = {
                        "username": username,
                        "attempts": self.pending_verifications.get(discord_id, {}).get("attempts", 0) + 1,
                        "last_attempt": time.time()
                    }
        
        except Exception as e:
            print(f"❌ Error in check_verifications: {e}")
    
    @tasks.loop(minutes=5)  # Cleanup every 5 minutes
    async def cleanup_pending(self):
        """Cleanup old pending verifications"""
        current_time = time.time()
        expired = []
        
        for discord_id, data in self.pending_verifications.items():
            # Remove if too many attempts or too old
            if data["attempts"] > 10 or (current_time - data["last_attempt"]) > 3600:  # 1 hour
                expired.append(discord_id)
        
        for discord_id in expired:
            del self.pending_verifications[discord_id]
            print(f"🗑️  Removed expired pending verification: {discord_id}")
    
    @check_verifications.before_loop
    @cleanup_pending.before_loop
    async def before_tasks(self):
        """Wait until bot is ready before starting tasks"""
        await self.wait_until_ready()
    
    async def on_member_join(self, member):
        """Check if new member is already verified and give role"""
        if self.db is None or not Config.VERIFIED_ROLE_ID:
            return
        
        try:
            # Check if user is in database
            user_data = self.db.users.find_one({
                "discord_id": str(member.id),
                "role_added": {"$ne": True},
                "is_banned": {"$ne": True}
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
                    
                    print(f"✅ Auto-gave role to rejoining member: {member.name}")
                    
                    await self.send_webhook(
                        "🔄 AUTO-ROLE ON JOIN",
                        f"**User:** {member.mention}\n**ID:** {member.id}\n**Role:** {verified_role.name}\n**Action:** Automatic role assignment on server join",
                        0x00ff00
                    )
        
        except Exception as e:
            print(f"❌ Error in on_member_join: {e}")
    
    async def setup_hook(self):
        """Setup all slash commands"""
        
        # ============ /setup COMMAND ============
        @self.tree.command(name="setup", description="Setup verification system")
        @app_commands.checks.has_permissions(administrator=True)
        async def setup_verification(interaction: discord.Interaction):
            """Setup verification embed"""
            embed = discord.Embed(
                title="🔐 SERVER VERIFICATION",
                description="**SERVER VERIFICATION**",
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
            
            await interaction.response.send_message(embed=embed, view=view)
            await self.log_action(f"Verification setup by {interaction.user}")
        
        # ============ /ban COMMAND ============
        @self.tree.command(name="ban", description="Ban a user and their IP")
        @app_commands.checks.has_permissions(ban_members=True)
        @app_commands.describe(
            user="User to ban",
            reason="Reason for ban",
            delete_messages="Delete messages from this user (days, 0-7)"
        )
        async def ban_user(
            interaction: discord.Interaction, 
            user: discord.Member, 
            reason: str = "No reason provided",
            delete_messages: app_commands.Range[int, 0, 7] = 0
        ):
            """Ban user and their IP"""
            try:
                if user.id == interaction.user.id:
                    await interaction.response.send_message("❌ You cannot ban yourself!", ephemeral=True)
                    return
                
                if user.guild_permissions.administrator:
                    await interaction.response.send_message("❌ You cannot ban an administrator!", ephemeral=True)
                    return
                
                # Check if user is already in database to get their IP
                user_ip = "Unknown"
                if self.db is not None:
                    user_data = self.db.users.find_one({"discord_id": str(user.id)})
                    if user_data and user_data.get('ip_address'):
                        user_ip = user_data['ip_address']
                        
                        # Save IP to banned_ips collection
                        self.db.banned_ips.insert_one({
                            "discord_id": str(user.id),
                            "username": str(user),
                            "ip_address": user_ip,
                            "reason": reason,
                            "banned_by": str(interaction.user),
                            "banned_at": datetime.utcnow()
                        })
                
                # Ban from Discord
                await user.ban(reason=reason, delete_message_days=delete_messages)
                
                # Remove verified role if they have it
                if Config.VERIFIED_ROLE_ID:
                    try:
                        verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                        if verified_role and verified_role in user.roles:
                            # Remove from verified users in database
                            if self.db is not None:
                                self.db.users.delete_one({"discord_id": str(user.id)})
                    except:
                        pass
                
                # Send webhook notification
                await self.send_webhook(
                    "🚨 USER BANNED",
                    f"**User:** {user.mention}\n**ID:** {user.id}\n**Reason:** {reason}\n**Banned by:** {interaction.user.mention}\n**IP:** ||{user_ip}||\n**Messages Deleted:** {delete_messages} days",
                    0xff0000
                )
                
                embed = discord.Embed(
                    title="✅ User Banned",
                    description=f"{user.mention} has been banned and their IP has been recorded.\n\n**Reason:** {reason}\n**IP:** ||{user_ip}||",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"Banned by {interaction.user}")
                
                await interaction.response.send_message(embed=embed)
                
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to ban this user!", ephemeral=True)
            except Exception as e:
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
            try:
                if not Config.VERIFIED_ROLE_ID:
                    await interaction.response.send_message("❌ Verified role ID not configured in .env file!", ephemeral=True)
                    return
                
                # Get verified role
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
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
                                "is_banned": False
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
                
                embed = discord.Embed(
                    title="✅ Force Verification Complete",
                    description=f"{user.mention} has been force verified!\n\n**Role Added:** {verified_role.mention}\n**IP Saved:** ||{ip_address}||",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Verified by {interaction.user}")
                
                await interaction.response.send_message(embed=embed)
                
            except Exception as e:
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /remove_all_verify COMMAND ============
        @self.tree.command(name="remove_all_verify", description="Remove verify role from ALL users")
        @app_commands.checks.has_permissions(administrator=True)
        async def remove_all_verify(interaction: discord.Interaction):
            """Remove verify role from all users"""
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
                await interaction.response.send_message(embed=embed, view=view)
                
            except Exception as e:
                await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        
        # ============ /verifyinfo COMMAND ============
        @self.tree.command(name="verifyinfo", description="Get verification info for a user")
        @app_commands.checks.has_permissions(manage_roles=True)
        @app_commands.describe(user="User to check (optional)")
        async def verify_info(interaction: discord.Interaction, user: Optional[discord.Member] = None):
            """Get verification info"""
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
                    
                    last_seen = user_data.get('last_seen')
                    if last_seen:
                        if isinstance(last_seen, datetime):
                            last_seen_str = last_seen.strftime("%Y-%m-d %H:%M")
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
            
            await interaction.response.send_message(embed=embed)
        
        # ============ /fix_roles COMMAND (NEW) ============
        @self.tree.command(name="fix_roles", description="Fix missing roles for verified users")
        @app_commands.checks.has_permissions(administrator=True)
        async def fix_roles(interaction: discord.Interaction):
            """Fix missing roles for verified users"""
            try:
                if not Config.VERIFIED_ROLE_ID:
                    await interaction.response.send_message("❌ Verified role ID not configured!", ephemeral=True)
                    return
                
                verified_role = interaction.guild.get_role(int(Config.VERIFIED_ROLE_ID))
                if not verified_role:
                    await interaction.response.send_message("❌ Verified role not found!", ephemeral=True)
                    return
                
                # Show working message
                await interaction.response.defer()
                
                # Get all verified users from database
                fixed_count = 0
                if self.db is not None:
                    verified_users = self.db.users.find({
                        "verified_at": {"$exists": True},
                        "is_banned": {"$ne": True}
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
                
                embed = discord.Embed(
                    title="✅ Roles Fixed",
                    description=f"Fixed roles for **{fixed_count}** users.\n\nThe bot will now automatically assign roles to newly verified users.",
                    color=discord.Color.green()
                )
                
                await interaction.followup.send(embed=embed)
                
            except Exception as e:
                await interaction.followup.send(f"❌ Error: {str(e)}")
        
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
                    await interaction.response.send_message(embed=embed)
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
                
                embed = discord.Embed(
                    title="✅ IP Banned",
                    description=f"IP `{ip_address}` has been added to the ban list.\n**Reason:** {reason}",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"Banned by {interaction.user}")
                
                await interaction.response.send_message(embed=embed)
                    
            except Exception as e:
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
                        print(f"Webhook failed: {response.status}")
        except Exception as e:
            print(f"Webhook error: {e}")
    
    async def log_action(self, action):
        """Log action to webhook"""
        if Config.LOGS_WEBHOOK:
            await self.send_webhook("📝 Action Log", action, 0x00ff00)
    
    async def close(self):
        """Cleanup when bot shuts down"""
        self.check_verifications.cancel()
        self.cleanup_pending.cancel()
        await super().close()

def run_discord_bot():
    """Run Discord bot"""
    if not Config.DISCORD_TOKEN:
        print("❌ No Discord token configured")
        return
    
    bot = VerificationBot()
    bot.run(Config.DISCORD_TOKEN)