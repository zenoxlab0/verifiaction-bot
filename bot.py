import os
import sqlite3
import asyncio
from urllib.parse import urlencode

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web


# ============================================================
# CONFIGURATION
# ============================================================

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
BOT_TOKEN = os.getenv("BOT_TOKEN")

PUBLIC_URL = os.getenv(
    "PUBLIC_URL",
    "http://localhost:8080"
).rstrip("/")

PORT = int(os.getenv("PORT", "8080"))

REDIRECT_URI = f"{PUBLIC_URL}/callback"


# ============================================================
# CONFIG VALIDATION
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

if not CLIENT_ID:
    raise RuntimeError("CLIENT_ID environment variable is missing.")

if not CLIENT_SECRET:
    raise RuntimeError("CLIENT_SECRET environment variable is missing.")


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "members.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            discriminator TEXT,
            verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def save_verified_user(
    user_id: str,
    username: str,
    discriminator: str
):
    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO members
        (user_id, username, discriminator)
        VALUES (?, ?, ?)
    """, (
        user_id,
        username,
        discriminator
    ))

    conn.commit()
    conn.close()


# ============================================================
# PENDING OAUTH REQUESTS
# ============================================================

pending_auths = {}


# ============================================================
# VERIFICATION VIEW
# ============================================================

class BackupView(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Account",
        style=discord.ButtonStyle.success,
        custom_id="backup_button"
    )
    async def backup_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):

        user_id = str(interaction.user.id)

        pending_auths[user_id] = interaction.user

        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify",
            "state": user_id
        }

        auth_url = (
            "https://discord.com/oauth2/authorize?"
            + urlencode(params)
        )

        embed = discord.Embed(
            title="🔐 Account Verification",
            description=(
                "**Verify your Discord account to continue.**\n\n"
                "Click the button below to open Discord's "
                "official authorization page."
            ),
            color=0x5865F2
        )

        embed.add_field(
            name="📋 What happens next?",
            value=(
                "• Open Discord authorization\n"
                "• Approve the application\n"
                "• Return to Discord\n"
                "• Your account will be verified"
            ),
            inline=False
        )

        embed.set_footer(
            text="🔒 Discord OAuth2 • Secure verification"
        )

        verify_button = discord.ui.Button(
            label="Verify with Discord",
            style=discord.ButtonStyle.link,
            url=auth_url
        )

        view = discord.ui.View()
        view.add_item(verify_button)

        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )


# ============================================================
# BOT READY
# ============================================================

@bot.event
async def on_ready():

    print("=" * 50)
    print(f"✅ Logged in as {bot.user}")
    print(f"🆔 Bot ID: {bot.user.id}")
    print("=" * 50)

    init_db()

    # Register persistent button
    try:
        bot.add_view(BackupView())
    except Exception as e:
        print(f"⚠️ View registration warning: {e}")

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Command sync failed: {e}")

    # Start OAuth server only once
    if not hasattr(bot, "oauth_started"):

        bot.oauth_started = True

        asyncio.create_task(
            start_oauth_server()
        )

        print(
            f"🌐 OAuth2 server running at {PUBLIC_URL}"
        )


# ============================================================
# /SETUP
# ============================================================

@bot.tree.command(
    name="setup",
    description="Set up the account verification panel"
)
@app_commands.checks.has_permissions(administrator=True)
async def setup(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used inside a server.",
            ephemeral=True
        )
        return

    server_icon = (
        guild.icon.url
        if guild.icon
        else None
    )

    embed = discord.Embed(
        title=f"🛡️ {guild.name}",
        description=(
            "**Account Verification System**\n\n"
            "Click the button below to verify your Discord "
            "account through Discord OAuth2."
        ),
        color=0x5865F2
    )

    if server_icon:
        embed.set_thumbnail(
            url=server_icon
        )

    embed.add_field(
        name="🎯 Features",
        value=(
            "• **Secure OAuth2**\n"
            "• **Discord verification**\n"
            "• **Simple verification process**\n"
            "• **No password required**"
        ),
        inline=False
    )

    embed.add_field(
        name="📊 Server Stats",
        value=(
            f"**Members:** {guild.member_count}\n"
            f"**Server ID:** `{guild.id}`"
        ),
        inline=False
    )

    embed.set_footer(
        text="🔐 Powered by Discord OAuth2"
    )

    view = BackupView()

    await interaction.response.send_message(
        embed=embed,
        view=view
    )


# ============================================================
# /STATS
# ============================================================

@bot.tree.command(
    name="stats",
    description="Show verification statistics"
)
@app_commands.checks.has_permissions(administrator=True)
async def stats(
    interaction: discord.Interaction
):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute(
        "SELECT COUNT(*) FROM members"
    )

    count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT username
        FROM members
        ORDER BY verified_at DESC
        LIMIT 5
    """)

    recent = cursor.fetchall()

    conn.close()

    embed = discord.Embed(
        title="📊 Verification Statistics",
        description="Current verification database",
        color=0x5865F2
    )

    embed.add_field(
        name="👥 Verified Accounts",
        value=f"**{count}**",
        inline=False
    )

    if recent:

        users = "\n".join(
            f"• {user[0]}"
            for user in recent
        )

        embed.add_field(
            name="🔹 Recent Verifications",
            value=users,
            inline=False
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# OAUTH CALLBACK
# ============================================================

async def handle_callback(
    request: web.Request
):

    code = request.query.get("code")
    state = request.query.get("state")

    if not code or not state:

        return web.Response(
            text=verification_failed_page(
                "Missing authorization code."
            ),
            content_type="text/html"
        )

    # Prevent arbitrary state values
    user = pending_auths.get(state)

    if user is None:

        return web.Response(
            text=verification_failed_page(
                "This verification session is invalid or expired."
            ),
            content_type="text/html"
        )

    token_data = None

    try:

        async with aiohttp.ClientSession() as session:

            # Exchange authorization code
            data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI
            }

            headers = {
                "Content-Type":
                    "application/x-www-form-urlencoded"
            }

            async with session.post(
                "https://discord.com/api/v10/oauth2/token",
                data=data,
                headers=headers
            ) as response:

                if response.status != 200:

                    print(
                        f"❌ OAuth token exchange failed: "
                        f"{response.status}"
                    )

                    return web.Response(
                        text=verification_failed_page(
                            "Discord authorization could not be completed."
                        ),
                        content_type="text/html"
                    )

                token_data = await response.json()

            access_token = token_data.get(
                "access_token"
            )

            if not access_token:

                return web.Response(
                    text=verification_failed_page(
                        "Discord did not return an authorization token."
                    ),
                    content_type="text/html"
                )

            # Get Discord user information
            user_headers = {
                "Authorization":
                    f"Bearer {access_token}"
            }

            async with session.get(
                "https://discord.com/api/v10/users/@me",
                headers=user_headers
            ) as response:

                if response.status != 200:

                    print(
                        f"❌ Failed to get Discord user: "
                        f"{response.status}"
                    )

                    return web.Response(
                        text=verification_failed_page(
                            "Unable to retrieve your Discord account."
                        ),
                        content_type="text/html"
                    )

                user_data = await response.json()

        # ====================================================
        # SAVE ONLY BASIC VERIFICATION DATA
        # ====================================================

        user_id = user_data["id"]
        username = user_data["username"]
        discriminator = user_data.get(
            "discriminator",
            "0"
        )

        save_verified_user(
            user_id,
            username,
            discriminator
        )

        # Remove pending session
        pending_auths.pop(state, None)

        print(
            f"✅ Verified: {username} ({user_id})"
        )

        return web.Response(
            text=verification_success_page(
                username
            ),
            content_type="text/html"
        )

    except Exception as e:

        print(
            f"❌ OAuth callback error: {e}"
        )

        return web.Response(
            text=verification_failed_page(
                "An unexpected error occurred."
            ),
            content_type="text/html"
        )


# ============================================================
# SUCCESS PAGE
# ============================================================

def verification_success_page(
    username: str
):

    safe_username = (
        username
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    return f"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Verification Successful</title>

<style>

* {{
    box-sizing: border-box;
}}

body {{

    margin: 0;

    min-height: 100vh;

    display: flex;

    align-items: center;

    justify-content: center;

    font-family:
        "Segoe UI",
        Arial,
        sans-serif;

    background:
        radial-gradient(
            circle at top,
            #303b70,
            #11131c 55%,
            #090a0f
        );

    color: white;

}}

.container {{

    width: min(90%, 500px);

    padding: 45px 35px;

    text-align: center;

    background:
        rgba(255,255,255,.06);

    border:
        1px solid rgba(255,255,255,.12);

    border-radius: 24px;

    backdrop-filter: blur(20px);

    box-shadow:
        0 25px 80px
        rgba(0,0,0,.45);

}}

.check {{

    width: 90px;

    height: 90px;

    margin: auto auto 25px;

    display: flex;

    align-items: center;

    justify-content: center;

    border-radius: 50%;

    background: #57F287;

    color: #111;

    font-size: 48px;

    font-weight: bold;

}}

h1 {{

    margin: 0 0 12px;

}}

p {{

    color: #b9bbbe;

    line-height: 1.7;

}}

.username {{

    color: #8ea1ff;

    font-weight: 700;

}}

</style>

</head>

<body>

<div class="container">

<div class="check">✓</div>

<h1>Verification Successful!</h1>

<p>

<span class="username">
{safe_username}
</span>

has been successfully verified.

</p>

<p>
You can close this window and return to Discord.
</p>

</div>

</body>

</html>
"""


# ============================================================
# FAILED PAGE
# ============================================================

def verification_failed_page(
    message: str
):

    safe_message = (
        message
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )

    return f"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Verification Failed</title>

<style>

body {{

    margin: 0;

    min-height: 100vh;

    display: flex;

    align-items: center;

    justify-content: center;

    font-family:
        "Segoe UI",
        Arial,
        sans-serif;

    background:
        radial-gradient(
            circle at top,
            #47252b,
            #11131c 55%,
            #090a0f
        );

    color: white;

}}

.container {{

    width: min(90%, 500px);

    padding: 45px 35px;

    text-align: center;

    background:
        rgba(255,255,255,.06);

    border:
        1px solid rgba(255,255,255,.12);

    border-radius: 24px;

    backdrop-filter: blur(20px);

}}

.icon {{

    font-size: 70px;

    color: #ED4245;

    margin-bottom: 15px;

}}

p {{

    color: #b9bbbe;

    line-height: 1.6;

}}

</style>

</head>

<body>

<div class="container">

<div class="icon">✕</div>

<h1>Verification Failed</h1>

<p>{safe_message}</p>

<p>
Please return to Discord and try again.
</p>

</div>

</body>

</html>
"""


# ============================================================
# OAUTH WEB SERVER
# ============================================================

async def start_oauth_server():

    app = web.Application()

    app.router.add_get(
        "/",
        lambda request: web.Response(
            text="Verification bot is online."
        )
    )

    app.router.add_get(
        "/callback",
        handle_callback
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        f"🌐 Web server listening on port {PORT}"
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):

    if isinstance(
        error,
        app_commands.MissingPermissions
    ):

        message = (
            "❌ You need administrator permissions "
            "to use this command."
        )

    else:

        print(
            f"❌ Command error: {error}"
        )

        message = (
            "❌ Something went wrong while "
            "executing the command."
        )

    if interaction.response.is_done():

        await interaction.followup.send(
            message,
            ephemeral=True
        )

    else:

        await interaction.response.send_message(
            message,
            ephemeral=True
        )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    print("🚀 Starting verification bot...")

    bot.run(BOT_TOKEN)
