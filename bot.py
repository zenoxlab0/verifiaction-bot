import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import asyncio
from aiohttp import web
import json
import os

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Database setup
def init_db():
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS members
                 (user_id TEXT PRIMARY KEY, 
                  access_token TEXT,
                  refresh_token TEXT,
                  username TEXT,
                  discriminator TEXT)''')
    conn.commit()
    conn.close()

# OAuth2 Configuration - Environment variables for Railway
CLIENT_ID = os.getenv("CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "YOUR_CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8080")
REDIRECT_URI = f"{PUBLIC_URL}/callback"
PORT = int(os.getenv("PORT", 8080))

# Store pending authorizations
pending_auths = {}

class BackupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="✓ Verify Account", style=discord.ButtonStyle.green, custom_id="backup_button")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        pending_auths[user_id] = interaction.user
        
        auth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join&state={user_id}"
        
        embed = discord.Embed(
            title="🔐 Account Verification",
            description="**Complete verification to enable server transfers**\n\nClick the button below to verify your account with our secure OAuth2 system.",
            color=0x5865F2  # Discord Blurple
        )
        embed.add_field(
            name="📋 What happens next?",
            value="• You'll be redirected to Discord\n• Authorize the application\n• Return here automatically\n• You're all set!",
            inline=False
        )
        embed.set_footer(text="🔒 Secure Discord OAuth2 • Expires in 10 minutes")
        
        verify_button = discord.ui.Button(
            label="Verify with Discord",
            style=discord.ButtonStyle.link,
            url=auth_url,
            emoji="✓"
        )
        view = discord.ui.View()
        view.add_item(verify_button)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    init_db()
    
    # Register persistent view
    bot.add_view(BackupView())
    
    # Sync commands
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    
    # Start OAuth2 web server
    asyncio.create_task(start_oauth_server())
    print(f"🌐 OAuth2 server running on {PUBLIC_URL}")

@bot.tree.command(name="setup", description="Set up the member backup system in this channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    # Get server icon
    server_icon = interaction.guild.icon.url if interaction.guild.icon else None
    
    embed = discord.Embed(
        title=f"🛡️ {interaction.guild.name}",
        description="**Member Backup & Transfer System**\n\nVerify your account to enable seamless server transfers. Your data is secure and you can revoke access anytime.",
        color=0x5865F2
    )
    
    if server_icon:
        embed.set_thumbnail(url=server_icon)
    
    embed.add_field(
        name="🎯 Features",
        value="• **Instant Transfers** - Move between servers instantly\n• **Secure OAuth2** - Industry-standard authentication\n• **One-Time Setup** - Verify once, use everywhere\n• **Full Control** - Revoke access anytime",
        inline=False
    )
    
    embed.add_field(
        name="📊 Server Stats",
        value=f"**Members:** {interaction.guild.member_count}\n**Server ID:** {interaction.guild.id}",
        inline=False
    )
    
    embed.set_footer(text="🔐 Powered by Discord OAuth2 • Click below to get started")
    
    view = BackupView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="pull", description="Pull all backed up members to this server")
@app_commands.checks.has_permissions(administrator=True)
async def pull(interaction: discord.Interaction):
    await interaction.response.defer()
    
    guild_id = str(interaction.guild.id)
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("SELECT user_id, access_token, username FROM members")
    members = c.fetchall()
    conn.close()
    
    if not members:
        embed = discord.Embed(
            title="❌ No Backup Data",
            description="No members have verified yet. Use `/setup` to create a verification panel.",
            color=0xED4245
        )
        await interaction.followup.send(embed=embed)
        return
    
    success_count = 0
    fail_count = 0
    already_in = 0
    
    status_embed = discord.Embed(
        title="⏳ Transferring Members...",
        description=f"Processing {len(members)} verified accounts...",
        color=0xFEE75C
    )
    status_msg = await interaction.followup.send(embed=status_embed)
    
    async with aiohttp.ClientSession() as session:
        for user_id, access_token, username in members:
            # Check if already in guild
            member = interaction.guild.get_member(int(user_id))
            if member:
                already_in += 1
                continue
            
            # Try to add member
            url = f"https://discord.com/api/v10/guilds/{guild_id}/members/{user_id}"
            headers = {
                "Authorization": f"Bot {BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            payload = {
                "access_token": access_token
            }
            
            try:
                async with session.put(url, headers=headers, json=payload) as resp:
                    if resp.status == 201 or resp.status == 204:
                        success_count += 1
                    else:
                        fail_count += 1
                        print(f"Failed to add {username}: {resp.status}")
                
                # Rate limit handling
                await asyncio.sleep(1)
            except Exception as e:
                fail_count += 1
                print(f"Error adding {username}: {e}")
    
    result_embed = discord.Embed(
        title="✅ Transfer Complete",
        description=f"Member transfer operation finished for **{interaction.guild.name}**",
        color=0x57F287
    )
    result_embed.add_field(name="✅ Successfully Added", value=f"**{success_count}** members", inline=True)
    result_embed.add_field(name="👥 Already in Server", value=f"**{already_in}** members", inline=True)
    result_embed.add_field(name="❌ Failed", value=f"**{fail_count}** members", inline=True)
    result_embed.set_footer(text=f"Total Processed: {len(members)} members")
    
    await interaction.followup.send(embed=result_embed)

@bot.tree.command(name="stats", description="Show backup statistics")
@app_commands.checks.has_permissions(administrator=True)
async def stats(interaction: discord.Interaction):
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM members")
    count = c.fetchone()[0]
    
    c.execute("SELECT username FROM members LIMIT 5")
    recent = c.fetchall()
    conn.close()
    
    embed = discord.Embed(
        title="📊 Backup System Statistics",
        description="Current backup database status",
        color=0x5865F2
    )
    embed.add_field(name="👥 Total Verified Members", value=f"**{count}** accounts", inline=False)
    
    if recent:
        recent_users = "\n".join([f"• {user[0]}" for user in recent[:5]])
        embed.add_field(name="🔹 Recent Verifications", value=recent_users, inline=False)
    
    embed.set_footer(text="Use /pull to transfer members to this server")
    
    await interaction.response.send_message(embed=embed)

# OAuth2 Web Server
async def handle_callback(request):
    code = request.query.get('code')
    state = request.query.get('state')
    
    if not code or not state:
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Verification Failed</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #23272A; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                .container { text-align: center; background: #2C2F33; padding: 40px; border-radius: 8px; box-shadow: 0 8px 16px rgba(0,0,0,0.3); }
                h1 { color: #ED4245; margin-bottom: 10px; }
                p { color: #B9BBBE; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>❌ Verification Failed</h1>
                <p>Missing authorization code. Please try again.</p>
            </div>
        </body>
        </html>
        """
        return web.Response(text=html, content_type='text/html')
    
    # Exchange code for access token
    async with aiohttp.ClientSession() as session:
        data = {
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI
        }
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        async with session.post('https://discord.com/api/v10/oauth2/token', data=data, headers=headers) as resp:
            if resp.status == 200:
                token_data = await resp.json()
                access_token = token_data['access_token']
                refresh_token = token_data.get('refresh_token', '')
                
                # Get user info
                headers = {'Authorization': f'Bearer {access_token}'}
                async with session.get('https://discord.com/api/v10/users/@me', headers=headers) as user_resp:
                    if user_resp.status == 200:
                        user_data = await user_resp.json()
                        user_id = user_data['id']
                        username = user_data['username']
                        discriminator = user_data.get('discriminator', '0')
                        
                        # Store in database
                        conn = sqlite3.connect('members.db')
                        c = conn.cursor()
                        c.execute("""INSERT OR REPLACE INTO members 
                                    (user_id, access_token, refresh_token, username, discriminator) 
                                    VALUES (?, ?, ?, ?, ?)""",
                                 (user_id, access_token, refresh_token, username, discriminator))
                        conn.commit()
                        conn.close()
                        
                        html = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <title>Verification Successful</title>
                            <style>
                                body {{ font-family: 'Segoe UI', sans-serif; background: #23272A; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
                                .container {{ text-align: center; background: #2C2F33; padding: 40px; border-radius: 8px; box-shadow: 0 8px 16px rgba(0,0,0,0.3); max-width: 500px; }}
                                h1 {{ color: #57F287; margin-bottom: 10px; }}
                                p {{ color: #B9BBBE; line-height: 1.6; }}
                                .username {{ color: #5865F2; font-weight: bold; }}
                                .checkmark {{ font-size: 64px; margin-bottom: 20px; }}
                            </style>
                        </head>
                        <body>
                            <div class="container">
                                <div class="checkmark">✓</div>
                                <h1>Verification Successful!</h1>
                                <p><span class="username">{username}</span> has been successfully verified and backed up.</p>
                                <p>You can now close this window and return to Discord.</p>
                            </div>
                        </body>
                        </html>
                        """
                        return web.Response(text=html, content_type='text/html')
            
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Verification Failed</title>
                <style>
                    body { font-family: 'Segoe UI', sans-serif; background: #23272A; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                    .container { text-align: center; background: #2C2F33; padding: 40px; border-radius: 8px; box-shadow: 0 8px 16px rgba(0,0,0,0.3); }
                    h1 { color: #ED4245; margin-bottom: 10px; }
                    p { color: #B9BBBE; }
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>❌ Verification Failed</h1>
                    <p>Failed to exchange authorization code. Please try again.</p>
                </div>
            </body>
            </html>
            """
            return web.Response(text=html, content_type='text/html')

async def start_oauth_server():
    app = web.Application()
    app.router.add_get('/callback', handle_callback)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# Run the bot
if __name__ == "__main__":
    bot.run(BOT_TOKEN)