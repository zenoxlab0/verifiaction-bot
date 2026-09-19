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
intents.guilds = True
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
    c.execute('''CREATE TABLE IF NOT EXISTS guild_settings
                 (guild_id TEXT PRIMARY KEY,
                  verified_role_id TEXT)''')
    conn.commit()
    conn.close()

# OAuth2 Configuration
CLIENT_ID = os.getenv("CLIENT_ID", "YOUR_CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "YOUR_CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8080")
REDIRECT_URI = f"{PUBLIC_URL}/callback"
PORT = int(os.getenv("PORT", 8080))

# Store pending authorizations with guild info
pending_auths = {}

class BackupView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
    
    @discord.ui.button(label="Verify Account", style=discord.ButtonStyle.green, custom_id="backup_button", emoji="✅")
    async def backup_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        pending_auths[user_id] = {
            'user': interaction.user,
            'guild_id': str(interaction.guild.id)
        }
        
        auth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join&state={user_id}"
        
        embed = discord.Embed(
            description="Click the button below to verify your account.",
            color=0x5865F2
        )
        
        verify_button = discord.ui.Button(
            label="Verify with Discord",
            style=discord.ButtonStyle.link,
            url=auth_url,
            emoji="🔐"
        )
        view = discord.ui.View()
        view.add_item(verify_button)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    init_db()
    
    # Register persistent views for all guilds
    for guild in bot.guilds:
        bot.add_view(BackupView(str(guild.id)))
    
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
    embed = discord.Embed(
        description="Click the button below to verify your account.",
        color=0x5865F2
    )
    
    view = BackupView(str(interaction.guild.id))
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="setrole", description="Set the verified role for this server")
@app_commands.checks.has_permissions(administrator=True)
async def setrole(interaction: discord.Interaction, role: discord.Role):
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO guild_settings (guild_id, verified_role_id) VALUES (?, ?)",
              (str(interaction.guild.id), str(role.id)))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(
        title="✅ Verified Role Set",
        description=f"Users will now receive {role.mention} after verification.",
        color=0x57F287
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="pull", description="Pull all backed up members to this server")
@app_commands.checks.has_permissions(administrator=True)
async def pull(interaction: discord.Interaction):
    await interaction.response.defer()
    
    guild_id = str(interaction.guild.id)
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("SELECT user_id, access_token, username FROM members")
    members = c.fetchall()
    
    # Get verified role
    c.execute("SELECT verified_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
    role_result = c.fetchone()
    conn.close()
    
    if not members:
        embed = discord.Embed(
            title="❌ No Backup Data",
            description="No members have verified yet.",
            color=0xED4245
        )
        await interaction.followup.send(embed=embed)
        return
    
    success_count = 0
    fail_count = 0
    already_in = 0
    
    # Get verified role if set
    verified_role = None
    if role_result and role_result[0]:
        verified_role = interaction.guild.get_role(int(role_result[0]))
    
    status_embed = discord.Embed(
        title="⏳ Transferring Members...",
        description=f"Processing {len(members)} verified accounts...",
        color=0xFEE75C
    )
    await interaction.followup.send(embed=status_embed)
    
    async with aiohttp.ClientSession() as session:
        for user_id, access_token, username in members:
            # Check if already in guild
            member = interaction.guild.get_member(int(user_id))
            if member:
                already_in += 1
                # Give role if not already has it
                if verified_role and verified_role not in member.roles:
                    try:
                        await member.add_roles(verified_role)
                    except:
                        pass
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
            
            # Add role to payload if exists
            if verified_role:
                payload["roles"] = [str(verified_role.id)]
            
            try:
                async with session.put(url, headers=headers, json=payload) as resp:
                    if resp.status == 201 or resp.status == 204:
                        success_count += 1
                    else:
                        fail_count += 1
                        print(f"Failed to add {username}: {resp.status}")
                
                await asyncio.sleep(1)
            except Exception as e:
                fail_count += 1
                print(f"Error adding {username}: {e}")
    
    result_embed = discord.Embed(
        title="✅ Transfer Complete",
        color=0x57F287
    )
    result_embed.add_field(name="✅ Added", value=f"**{success_count}**", inline=True)
    result_embed.add_field(name="👥 Already Here", value=f"**{already_in}**", inline=True)
    result_embed.add_field(name="❌ Failed", value=f"**{fail_count}**", inline=True)
    
    await interaction.followup.send(embed=result_embed)

@bot.tree.command(name="stats", description="Show backup statistics")
@app_commands.checks.has_permissions(administrator=True)
async def stats(interaction: discord.Interaction):
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM members")
    count = c.fetchone()[0]
    
    c.execute("SELECT verified_role_id FROM guild_settings WHERE guild_id = ?", (str(interaction.guild.id),))
    role_result = c.fetchone()
    conn.close()
    
    embed = discord.Embed(
        title="📊 Backup Statistics",
        color=0x5865F2
    )
    embed.add_field(name="Total Verified Members", value=f"**{count}**", inline=False)
    
    if role_result and role_result[0]:
        role = interaction.guild.get_role(int(role_result[0]))
        if role:
            embed.add_field(name="Verified Role", value=role.mention, inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove", description="Remove a member from backup database")
@app_commands.checks.has_permissions(administrator=True)
async def remove(interaction: discord.Interaction, user_id: str):
    conn = sqlite3.connect('members.db')
    c = conn.cursor()
    c.execute("DELETE FROM members WHERE user_id = ?", (user_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    if deleted > 0:
        embed = discord.Embed(
            title="✅ Removed",
            description=f"User `{user_id}` removed from database.",
            color=0x57F287
        )
    else:
        embed = discord.Embed(
            title="❌ Not Found",
            description=f"User `{user_id}` not in database.",
            color=0xED4245
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

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
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                * { margin: 0; padding: 0; box-sizing: border-box; }
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                    color: #fff; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    min-height: 100vh; 
                    padding: 20px;
                }
                .container { 
                    text-align: center; 
                    background: rgba(255, 255, 255, 0.1); 
                    backdrop-filter: blur(10px);
                    padding: 50px 40px; 
                    border-radius: 20px; 
                    box-shadow: 0 8px 32px rgba(0,0,0,0.3); 
                    max-width: 500px;
                    border: 1px solid rgba(255,255,255,0.2);
                }
                .icon { font-size: 80px; margin-bottom: 20px; }
                h1 { font-size: 32px; margin-bottom: 15px; font-weight: 600; }
                p { color: rgba(255,255,255,0.9); font-size: 16px; line-height: 1.6; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="icon">❌</div>
                <h1>Verification Failed</h1>
                <p>Missing authorization code. Please try again from Discord.</p>
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
                        
                        # Get guild info and assign role
                        guild_id = None
                        if state in pending_auths:
                            guild_id = pending_auths[state]['guild_id']
                        
                        # Assign verified role if configured
                        if guild_id:
                            c.execute("SELECT verified_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
                            role_result = c.fetchone()
                            
                            if role_result and role_result[0]:
                                guild = bot.get_guild(int(guild_id))
                                if guild:
                                    member = guild.get_member(int(user_id))
                                    if member:
                                        role = guild.get_role(int(role_result[0]))
                                        if role:
                                            try:
                                                await member.add_roles(role)
                                                print(f"✅ Assigned {role.name} to {username}")
                                            except Exception as e:
                                                print(f"❌ Failed to assign role: {e}")
                        
                        conn.close()
                        
                        html = f"""
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <title>Verification Successful</title>
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
                            <style>
                                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                                body {{ 
                                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                                    color: #fff; 
                                    display: flex; 
                                    justify-content: center; 
                                    align-items: center; 
                                    min-height: 100vh; 
                                    padding: 20px;
                                }}
                                .container {{ 
                                    text-align: center; 
                                    background: rgba(255, 255, 255, 0.1); 
                                    backdrop-filter: blur(10px);
                                    padding: 50px 40px; 
                                    border-radius: 20px; 
                                    box-shadow: 0 8px 32px rgba(0,0,0,0.3); 
                                    max-width: 500px;
                                    border: 1px solid rgba(255,255,255,0.2);
                                }}
                                .icon {{ font-size: 80px; margin-bottom: 20px; animation: bounce 1s ease; }}
                                h1 {{ font-size: 32px; margin-bottom: 15px; font-weight: 600; }}
                                p {{ color: rgba(255,255,255,0.9); font-size: 16px; line-height: 1.6; margin-bottom: 10px; }}
                                .username {{ 
                                    color: #FFD93D; 
                                    font-weight: 600; 
                                    font-size: 20px;
                                    display: block;
                                    margin: 20px 0;
                                }}
                                @keyframes bounce {{
                                    0%, 100% {{ transform: translateY(0); }}
                                    50% {{ transform: translateY(-10px); }}
                                }}
                            </style>
                        </head>
                        <body>
                            <div class="container">
                                <div class="icon">✅</div>
                                <h1>Verification Successful!</h1>
                                <span class="username">{username}</span>
                                <p>Your account has been verified.</p>
                                <p>You can now close this window.</p>
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
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    * { margin: 0; padding: 0; box-sizing: border-box; }
                    body { 
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                        color: #fff; 
                        display: flex; 
                        justify-content: center; 
                        align-items: center; 
                        min-height: 100vh; 
                        padding: 20px;
                    }
                    .container { 
                        text-align: center; 
                        background: rgba(255, 255, 255, 0.1); 
                        backdrop-filter: blur(10px);
                        padding: 50px 40px; 
                        border-radius: 20px; 
                        box-shadow: 0 8px 32px rgba(0,0,0,0.3); 
                        max-width: 500px;
                        border: 1px solid rgba(255,255,255,0.2);
                    }
                    .icon { font-size: 80px; margin-bottom: 20px; }
                    h1 { font-size: 32px; margin-bottom: 15px; font-weight: 600; }
                    p { color: rgba(255,255,255,0.9); font-size: 16px; line-height: 1.6; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="icon">❌</div>
                    <h1>Verification Failed</h1>
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
