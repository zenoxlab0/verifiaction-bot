import os
import sqlite3
import asyncio
from urllib.parse import urlencode
from html import escape

import aiohttp
import discord
from discord.ext import commands
from discord import app_commands
from aiohttp import web


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")

PUBLIC_URL = os.getenv(
    "PUBLIC_URL",
    "http://localhost:8080"
).rstrip("/")

PORT = int(os.getenv("PORT", "8080"))

VERIFIED_ROLE_ID = os.getenv("VERIFIED_ROLE_ID")

REDIRECT_URI = f"{PUBLIC_URL}/callback"


# ============================================================
# CONFIG CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN environment variable is missing."
    )

if not CLIENT_ID:
    raise RuntimeError(
        "CLIENT_ID environment variable is missing."
    )

if not CLIENT_SECRET:
    raise RuntimeError(
        "CLIENT_SECRET environment variable is missing."
    )

if not VERIFIED_ROLE_ID:
    raise RuntimeError(
        "VERIFIED_ROLE_ID environment variable is missing."
    )

try:
    VERIFIED_ROLE_ID = int(VERIFIED_ROLE_ID)
except ValueError:
    raise RuntimeError(
        "VERIFIED_ROLE_ID must be a Discord role ID."
    )


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
        (
            user_id,
            username,
            discriminator,
            verified_at
        )
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        user_id,
        username,
        discriminator
    ))

    conn.commit()
    conn.close()


def get_verified_users():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            user_id,
            username,
            discriminator,
            verified_at
        FROM members
        ORDER BY verified_at DESC
    """)

    users = cursor.fetchall()

    conn.close()

    return users


# ============================================================
# PENDING VERIFICATIONS
# ============================================================

# state/user ID -> verification information
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

        if interaction.guild is None:

            await interaction.response.send_message(
                "❌ This button can only be used inside a server.",
                ephemeral=True
            )

            return

        user_id = str(interaction.user.id)

        # Store which server started verification.
        pending_auths[user_id] = {
            "guild_id": interaction.guild.id
        }

        # OAuth parameters
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

        # Main embed
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
                "• Receive the Verified role automatically"
            ),
            inline=False
        )

        embed.add_field(
            name="🔒 Security",
            value=(
                "Your Discord password is never requested. "
                "Authorization happens directly through Discord."
            ),
            inline=False
        )

        embed.set_footer(
            text="Discord OAuth2 Verification"
        )

        # OAuth button
        verify_button = discord.ui.Button(
            label="Verify with Discord",
            style=discord.ButtonStyle.link,
            url=auth_url
        )

        view = discord.ui.View()

        view.add_item(
            verify_button
        )

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

    print("=" * 60)
    print(f"✅ Logged in as {bot.user}")
    print(f"🆔 Bot ID: {bot.user.id}")
    print("=" * 60)

    init_db()

    # Persistent button
    if not getattr(bot, "view_registered", False):

        try:

            bot.add_view(
                BackupView()
            )

            bot.view_registered = True

            print(
                "✅ Verification button registered"
            )

        except Exception as e:

            print(
                f"❌ Failed to register button: {e}"
            )

    # Sync commands
    if not getattr(bot, "commands_synced", False):

        try:

            synced = await bot.tree.sync()

            bot.commands_synced = True

            print(
                f"✅ Synced {len(synced)} command(s)"
            )

        except Exception as e:

            print(
                f"❌ Command sync failed: {e}"
            )

    # Start web server once
    if not getattr(bot, "oauth_started", False):

        bot.oauth_started = True

        asyncio.create_task(
            start_oauth_server()
        )

        print(
            f"🌐 OAuth server: {PUBLIC_URL}"
        )

        print(
            f"🔗 Callback URL: {REDIRECT_URI}"
        )


# ============================================================
# /SETUP
# ============================================================

@bot.tree.command(
    name="setup",
    description="Set up the verification panel"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def setup(
    interaction: discord.Interaction
):

    guild = interaction.guild

    if guild is None:

        await interaction.response.send_message(
            "❌ This command must be used inside a server.",
            ephemeral=True
        )

        return

    server_icon = None

    if guild.icon:

        server_icon = guild.icon.url

    embed = discord.Embed(
        title=f"🛡️ {guild.name}",
        description=(
            "**Discord Account Verification**\n\n"
            "Verify your Discord account using the "
            "official Discord OAuth2 authorization system."
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
            "• Secure Discord OAuth2\n"
            "• Automatic verification\n"
            "• Automatic Verified role\n"
            "• Simple one-click process"
        ),
        inline=False
    )

    embed.add_field(
        name="👥 Server",
        value=(
            f"**Members:** {guild.member_count}\n"
            f"**Server ID:** `{guild.id}`"
        ),
        inline=False
    )

    embed.set_footer(
        text="Click Verify Account to continue"
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
@app_commands.checks.has_permissions(
    administrator=True
)
async def stats(
    interaction: discord.Interaction
):

    users = get_verified_users()

    embed = discord.Embed(
        title="📊 Verification Statistics",
        description="Current verification database.",
        color=0x5865F2
    )

    embed.add_field(
        name="👥 Verified Accounts",
        value=f"**{len(users)}**",
        inline=False
    )

    if users:

        recent = users[:5]

        lines = []

        for user_id, username, discriminator, verified_at in recent:

            lines.append(
                f"• **{username}**"
            )

        embed.add_field(
            name="🔹 Recent Verifications",
            value="\n".join(lines),
            inline=False
        )

    embed.set_footer(
        text="Use /pull to view verified members"
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# /PULL
# ============================================================

@bot.tree.command(
    name="pull",
    description="Show verified members"
)
@app_commands.checks.has_permissions(
    administrator=True
)
async def pull(
    interaction: discord.Interaction
):

    users = get_verified_users()

    if not users:

        embed = discord.Embed(
            title="📭 No Verified Members",
            description=(
                "Nobody has completed verification yet."
            ),
            color=0xED4245
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

        return

    lines = []

    for user_id, username, discriminator, verified_at in users[:25]:

        lines.append(
            f"• **{username}**\n"
            f"  ID: `{user_id}`"
        )

    embed = discord.Embed(
        title="👥 Verified Members",
        description="\n\n".join(lines),
        color=0x5865F2
    )

    embed.add_field(
        name="📊 Total Verified",
        value=f"**{len(users)}**",
        inline=False
    )

    if len(users) > 25:

        embed.set_footer(
            text=(
                f"Showing 25 of "
                f"{len(users)} verified members"
            )
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# GIVE VERIFIED ROLE
# ============================================================

async def give_verified_role(
    guild_id: int,
    user_id: int,
    username: str
):

    guild = bot.get_guild(
        guild_id
    )

    if guild is None:

        print(
            f"❌ Guild {guild_id} not found."
        )

        return False, "Server not found."

    # Find member
    try:

        member = guild.get_member(
            user_id
        )

        if member is None:

            member = await guild.fetch_member(
                user_id
            )

    except discord.NotFound:

        print(
            f"❌ {username} is not a member of {guild.name}."
        )

        return False, (
            "You must already be a member of this server "
            "to receive the Verified role."
        )

    except discord.HTTPException as e:

        print(
            f"❌ Failed to fetch member: {e}"
        )

        return False, "Could not find your server membership."

    # Find role
    role = guild.get_role(
        VERIFIED_ROLE_ID
    )

    if role is None:

        print(
            f"❌ Role {VERIFIED_ROLE_ID} not found "
            f"in {guild.name}"
        )

        return False, (
            "The Verified role could not be found."
        )

    # Check bot's highest role
    me = guild.me

    if me is None:

        return False, (
            "Bot member information is unavailable."
        )

    if role >= me.top_role:

        print(
            f"❌ Verified role is above the bot's role."
        )

        return False, (
            "The Verified role must be below "
            "the bot's highest role."
        )

    # Already has role
    if role in member.roles:

        print(
            f"ℹ️ {username} already has Verified role."
        )

        return True, "already"

    # Give role
    try:

        await member.add_roles(
            role,
            reason="Discord OAuth2 account verification"
        )

        print(
            f"✅ Verified role added to {username}"
        )

        return True, "added"

    except discord.Forbidden:

        print(
            "❌ Missing Manage Roles permission."
        )

        return False, (
            "The bot does not have permission "
            "to assign the Verified role."
        )

    except discord.HTTPException as e:

        print(
            f"❌ Role assignment failed: {e}"
        )

        return False, (
            "Discord rejected the role assignment."
        )


# ============================================================
# OAUTH CALLBACK
# ============================================================

async def handle_callback(
    request: web.Request
):

    code = request.query.get(
        "code"
    )

    state = request.query.get(
        "state"
    )

    if not code or not state:

        return web.Response(
            text=verification_failed_page(
                "Missing authorization code."
            ),
            content_type="text/html"
        )

    # Find pending verification
    session = pending_auths.get(
        state
    )

    if not session:

        return web.Response(
            text=verification_failed_page(
                "This verification session is invalid or expired."
            ),
            content_type="text/html"
        )

    guild_id = session["guild_id"]

    try:

        # ====================================================
        # EXCHANGE CODE FOR ACCESS TOKEN
        # ====================================================

        async with aiohttp.ClientSession() as http:

            token_data = {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI
            }

            token_headers = {
                "Content-Type":
                    "application/x-www-form-urlencoded"
            }

            async with http.post(
                "https://discord.com/api/v10/oauth2/token",
                data=token_data,
                headers=token_headers
            ) as response:

                if response.status != 200:

                    error_text = await response.text()

                    print(
                        "❌ OAuth token exchange failed:"
                    )

                    print(
                        error_text
                    )

                    return web.Response(
                        text=verification_failed_page(
                            "Discord authorization failed. "
                            "Please try again."
                        ),
                        content_type="text/html"
                    )

                oauth_data = await response.json()

            access_token = oauth_data.get(
                "access_token"
            )

            if not access_token:

                return web.Response(
                    text=verification_failed_page(
                        "Discord did not return an access token."
                    ),
                    content_type="text/html"
                )

            # ====================================================
            # GET USER INFORMATION
            # ====================================================

            user_headers = {
                "Authorization":
                    f"Bearer {access_token}"
            }

            async with http.get(
                "https://discord.com/api/v10/users/@me",
                headers=user_headers
            ) as response:

                if response.status != 200:

                    print(
                        f"❌ User API returned "
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
        # USER INFORMATION
        # ====================================================

        user_id = int(
            user_data["id"]
        )

        username = user_data.get(
            "username",
            "Unknown"
        )

        discriminator = user_data.get(
            "discriminator",
            "0"
        )

        # ====================================================
        # SAVE BASIC VERIFICATION
        # ====================================================

        save_verified_user(
            str(user_id),
            username,
            discriminator
        )

        print(
            f"✅ OAuth verification completed: "
            f"{username} ({user_id})"
        )

        # ====================================================
        # GIVE VERIFIED ROLE
        # ====================================================

        role_success, role_status = await give_verified_role(
            guild_id,
            user_id,
            username
        )

        # Delete pending session
        pending_auths.pop(
            state,
            None
        )

        # ====================================================
        # SUCCESS PAGE
        # ====================================================

        if role_success:

            if role_status == "already":

                message = (
                    "You are already verified and "
                    "already have the Verified role."
                )

            else:

                message = (
                    "Your Discord account has been verified "
                    "and the Verified role has been added."
                )

            return web.Response(
                text=verification_success_page(
                    username,
                    message
                ),
                content_type="text/html"
            )

        # OAuth succeeded but role failed
        return web.Response(
            text=verification_success_page(
                username,
                (
                    "Your Discord account was verified, "
                    "but the Verified role could not be "
                    f"assigned: {role_status}"
                )
            ),
            content_type="text/html"
        )

    except Exception as e:

        print(
            f"❌ OAuth callback error: {e}"
        )

        pending_auths.pop(
            state,
            None
        )

        return web.Response(
            text=verification_failed_page(
                "An unexpected error occurred. "
                "Please try again."
            ),
            content_type="text/html"
        )


# ============================================================
# SUCCESS PAGE
# ============================================================

def verification_success_page(
    username: str,
    message: str
):

    username = escape(
        username
    )

    message = escape(
        message
    )

    return f"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

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

    width: min(90%, 520px);

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

    margin:
        0 auto 25px;

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

    margin:
        0 0 15px;

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
<span class="username">{username}</span>
</p>

<p>
{message}
</p>

<p>
You can now close this window and return to Discord.
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

    message = escape(
        message
    )

    return f"""
<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Verification Failed</title>

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
            #47252b,
            #11131c 55%,
            #090a0f
        );

    color: white;

}}

.container {{

    width: min(90%, 520px);

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

    line-height: 1.7;

}}

</style>

</head>

<body>

<div class="container">

<div class="icon">✕</div>

<h1>Verification Failed</h1>

<p>{message}</p>

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

    async def home(request):

        return web.Response(
            text="✅ Verification bot is online."
        )

    app.router.add_get(
        "/",
        home
    )

    app.router.add_get(
        "/callback",
        handle_callback
    )

    runner = web.AppRunner(
        app
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        f"🌐 HTTP server listening on 0.0.0.0:{PORT}"
    )


# ============================================================
# COMMAND ERROR HANDLER
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
            "❌ You need Administrator permission "
            "to use this command."
        )

    else:

        print(
            f"❌ Command error: {error}"
        )

        message = (
            "❌ An error occurred while "
            "executing this command."
        )

    try:

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

    except Exception as e:

        print(
            f"❌ Could not send error message: {e}"
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "🚀 Starting verification bot..."
    )

    print(
        f"🔗 OAuth Redirect: {REDIRECT_URI}"
    )

    bot.run(
        BOT_TOKEN
    )
