import asyncio
import re
import sqlite3
import discord
from datetime import datetime, timedelta, timezone
from discord import app_commands
from google import genai
from google.genai import types

PREFIX = ";"  # change this to , or ! or . if you want a different symbol

# Bot status: shows as "Listening to ..." and rotates through this list.
# {servers} is replaced by the server count if you ever want to use it.
STATUSES = [
    ".gg/noxity",
    ";help",
]
STATUS_INTERVAL = 60  # seconds between changes (60 = 1 minute)

# ---------------------------------------------------------------------------
# Warning system settings
# ---------------------------------------------------------------------------
MOD_LOG_CHANNEL_ID = 0  # paste a channel ID here to log every warning/timeout there
DB_FILE = "warnings.db"
AUTO_BAN = True  # False = the bot won't ban on the 5th offense, it just tells you to review

# What each offense does, per category. Numbers are minutes of timeout.
#   None  = reminder only (no action)
#   "ban" = ban
# After the last step, any further offense repeats the last step.
CATEGORY_LABELS = {
    "general": "Promoting / Mentioning other servers / Others",
    "nsfw": "NSFW",
}
LADDERS = {
    "general": [None, 60, 120, 240, "ban"],         # reminder, 1h, 2h, 4h, ban
    "nsfw": [None, 600, 1440, 10080, "ban"],        # reminder, 10h, 1d, 1 week, ban
}

MAX_TIMEOUT_MINUTES = 28 * 24 * 60  # Discord's limit: 28 days

SYSTEM_PROMPT = (
    "You are a chill Gen Z AI in a Discord server. Talk casually in lowercase with slang like "
    "'ngl', 'fr', 'lowkey', 'bruh', 'no cap', and 'bet'. Be funny and a little sassy, but never mean. "
    "Give correct answers to real questions and explain things simply. "
    "Keep replies short, 1 to 3 sentences, and use an emoji now and then. "
    "If you don't know the answer, say 'idk' or 'not sure' instead of making something up. "
    "If someone asks you who the owner of the server is, say 'eylogy is the owner of this server' and nothing else. "
    "Don't use unnecessary emojis, and don't use emojis in every sentence. Don't use emojis in the middle of words."
)

MODEL = "gemini-3.5-flash-lite"

ai = genai.Client(api_key="GEMINi_API_KEY") 
chats = {}  # channel id -> chat session (remembers history)
afk = {}    # user id -> (reason, time they went afk)

# Embed colors
COLOR_AFK = discord.Color(0x4F545C)  # gray, like your screenshot
COLOR_WARN = discord.Color.orange()

# Words to watch for -> emoji to react with
REACTIONS = {
    "eyl": "🫃",
    "kori": "🫃",
    "rowi": "🫃",
    "mayi": "🫃",
    "alex": "🫃",
    "ezy": "🫃",
    "heda": "🫃",
    "max": "🫃",
    "eli": "🫃",
    "rune": "🫃",
    "ria": "🫃",
    "jah": "🫃",
    "ryry": "🫃",
    "hori": "🫃",
    "kino": "🫃",
}

# ---------------------------------------------------------------------------
# Warning database (a small file saved next to main.py)
# ---------------------------------------------------------------------------
db = sqlite3.connect(DB_FILE)
db.execute(
    """CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        mod_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'general'
    )"""
)
# Upgrade older database files that don't have the category column yet
_columns = [row[1] for row in db.execute("PRAGMA table_info(warnings)")]
if "category" not in _columns:
    db.execute("ALTER TABLE warnings ADD COLUMN category TEXT NOT NULL DEFAULT 'general'")
db.commit()


def add_warning(guild_id, user_id, mod_id, reason, category):
    cur = db.execute(
        "INSERT INTO warnings (guild_id, user_id, mod_id, reason, created_at, category) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (guild_id, user_id, mod_id, reason, datetime.now(timezone.utc).isoformat(), category),
    )
    db.commit()
    return cur.lastrowid


def get_warnings(guild_id, user_id):
    return db.execute(
        "SELECT id, mod_id, reason, created_at, category FROM warnings "
        "WHERE guild_id = ? AND user_id = ? ORDER BY id",
        (guild_id, user_id),
    ).fetchall()


def delete_warning(guild_id, warn_id):
    """Deletes one warning. Returns the warned user's id, or None if not found."""
    row = db.execute(
        "SELECT user_id FROM warnings WHERE id = ? AND guild_id = ?",
        (warn_id, guild_id),
    ).fetchone()
    if row is None:
        return None
    db.execute("DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warn_id, guild_id))
    db.commit()
    return row[0]


def clear_warnings(guild_id, user_id):
    """Deletes all of a member's warnings. Returns how many were removed."""
    cur = db.execute(
        "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    db.commit()
    return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
USER_ID_RE = re.compile(r"\d{15,20}")
DURATION_RE = re.compile(r"^(\d{1,6})\s*([a-z]+)$", re.IGNORECASE)
UNIT_MINUTES = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
    "w": 10080, "wk": 10080, "wks": 10080, "week": 10080, "weeks": 10080,
    "mo": 43200, "month": 43200, "months": 43200,  # a month counts as 30 days
}


def is_mod(member):
    """True if the member is an admin or has Manage Messages. False in DMs."""
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_messages))


def time_ago(since):
    """Turns a time into text like '8 seconds ago' or '5 minutes ago'."""
    seconds = int((datetime.now(timezone.utc) - since).total_seconds())
    if seconds < 60:
        n, unit = seconds, "second"
    elif seconds < 3600:
        n, unit = seconds // 60, "minute"
    elif seconds < 86400:
        n, unit = seconds // 3600, "hour"
    else:
        n, unit = seconds // 86400, "day"
    return f"{n} {unit}{'' if n == 1 else 's'} ago"


def parse_duration(text):
    """Turns '10m', '2h', '3d', '1w' or '1mo' into minutes. Returns None if invalid."""
    found = DURATION_RE.match(text.strip())
    if not found:
        return None
    unit = UNIT_MINUTES.get(found.group(2).lower())
    amount = int(found.group(1))
    if unit is None or amount <= 0:
        return None
    return amount * unit


def format_duration(total_minutes):
    """Turns minutes into text like '1 day 2 hours'."""
    parts = []
    for name, size in (("week", 10080), ("day", 1440), ("hour", 60), ("minute", 1)):
        count, total_minutes = divmod(total_minutes, size)
        if count:
            parts.append(f"{count} {name}{'' if count == 1 else 's'}")
    return " ".join(parts) or "0 minutes"


def ladder_step(category, offense):
    """What happens on this offense number: None (reminder), minutes, or 'ban'."""
    ladder = LADDERS[category]
    return ladder[min(offense, len(ladder)) - 1]


def step_label(step):
    if step is None:
        return "Reminder (no action)"
    if step == "ban":
        return "Ban"
    return f"{format_duration(step)} timeout"


def check_target(message, target):
    """Returns a problem message if the author can't act on the target, else None."""
    guild = message.guild
    if target.bot or target.id == message.author.id or target.id == guild.owner_id:
        return "You can't do that to that person."
    if message.author.id != guild.owner_id and target.top_role >= message.author.top_role:
        return "That member's top role is the same as or higher than yours."
    return None


async def find_member(guild, text):
    """Finds a server member from a mention (<@123>) or a plain user ID."""
    found = USER_ID_RE.search(text)
    if not found:
        return None
    user_id = int(found.group())
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None
    return member


async def try_dm(user, embed):
    try:
        await user.send(embed=embed)
    except discord.HTTPException:
        pass  # their DMs are closed


async def send_mod_log(guild, embed, current_channel_id):
    """Posts the embed in your mod log channel (if you set one)."""
    if not MOD_LOG_CHANNEL_ID:
        return
    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel is None or channel.id == current_channel_id:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------------------
# Moderation commands
# ---------------------------------------------------------------------------
async def handle_warn(message):
    """;warn @user [nsfw] <reason>"""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Warnings only work inside a server.")
        return
    if not is_mod(message.author):
        await message.reply("Only **mods** and **admins** can warn people.")
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(
            f"Usage: `{PREFIX}warn @user <reason>` for Promoting / Other servers / Others\n"
            f"or `{PREFIX}warn @user nsfw <reason>` for NSFW"
        )
        return
    problem = check_target(message, target)
    if problem:
        await message.reply(problem)
        return

    # Category: "nsfw" or "general" can be the first word after the user.
    # With no category word, it counts as Promoting / Other servers / Others.
    rest = args[2].strip() if len(args) > 2 else ""
    category = "general"
    first, _, remainder = rest.partition(" ")
    if first.lower() in CATEGORY_LABELS:
        category = first.lower()
        rest = remainder.strip()
    reason = rest[:300] or "No reason given"

    warn_id = add_warning(guild.id, target.id, message.author.id, reason, category)
    rows = get_warnings(guild.id, target.id)
    offense = sum(1 for row in rows if row[4] == category)
    ladder = LADDERS[category]
    step = ladder_step(category, offense)

    # DM for the member
    dm = discord.Embed(title=f"You were warned in {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Rule", value=CATEGORY_LABELS[category], inline=False)
    dm.add_field(name="Reason", value=reason, inline=False)
    dm.add_field(name="Offense", value=f"{min(offense, len(ladder))} of {len(ladder)}")
    dm.add_field(name="Consequence", value=step_label(step))

    # Carry out the action for this offense
    note = None
    if step == "ban":
        await try_dm(target, dm)  # before the ban, since they can't be messaged after
        if AUTO_BAN:
            try:
                await target.ban(
                    reason=f"Offense {offense} ({CATEGORY_LABELS[category]}): {reason}",
                    delete_message_seconds=0,
                )
                note = "Reached the ban stage, so they were banned."
            except discord.HTTPException:
                note = "Reached the ban stage, but i couldn't ban them (check my **Ban Members** permission and role position)."
        else:
            note = "Reached the ban stage. AUTO_BAN is off, so a mod needs to decide."
    elif step is not None:
        try:
            await target.timeout(
                timedelta(minutes=step),
                reason=f"Offense {offense} ({CATEGORY_LABELS[category]}): {reason}",
            )
            note = f"Timed out for {format_duration(step)}."
        except discord.HTTPException:
            note = "I couldn't time them out (check my **Moderate Members** permission and role position)."
        await try_dm(target, dm)
    else:
        await try_dm(target, dm)

    embed = discord.Embed(title="⚠️ Member warned", color=COLOR_WARN)
    embed.add_field(name="Member", value=target.mention)
    embed.add_field(name="Warned by", value=message.author.mention)
    embed.add_field(name="Offense", value=f"{min(offense, len(ladder))} of {len(ladder)}")
    embed.add_field(name="Rule", value=CATEGORY_LABELS[category], inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Action", value=step_label(step), inline=False)
    if note:
        embed.add_field(name="Result", value=note, inline=False)
    embed.set_footer(text=f"Warning ID: {warn_id}")
    await message.reply(embed=embed, allowed_mentions=no_pings)
    await send_mod_log(guild, embed, message.channel.id)


async def handle_timeout(message):
    """;timeout @user <time> [reason]"""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Timeouts only work inside a server.")
        return
    if not is_mod(message.author):
        await message.reply("Only **mods** and **admins** can time people out.")
        return

    args = message.content.strip().split(maxsplit=3)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    minutes = parse_duration(args[2]) if len(args) > 2 else None
    if target is None or minutes is None:
        await message.reply(
            f"Usage: `{PREFIX}timeout @user <time> [reason]`\n"
            f"Time examples: `10m` (minutes), `2h` (hours), `3d` (days), `1w` (weeks)"
        )
        return
    problem = check_target(message, target)
    if problem:
        await message.reply(problem)
        return
    if minutes > MAX_TIMEOUT_MINUTES:
        await message.reply(
            "Discord only allows timeouts up to **28 days** (`4w`), so months aren't possible "
            "for a timeout. Use `4w` or less, or ban the member for something longer."
        )
        return

    reason = args[3].strip()[:300] if len(args) > 3 else "No reason given"
    try:
        await target.timeout(
            timedelta(minutes=minutes),
            reason=f"{reason} (by {message.author})",
        )
    except discord.HTTPException:
        await message.reply(
            "I couldn't time them out. Check that i have the **Moderate Members** permission "
            "and that my role is above theirs."
        )
        return

    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    dm = discord.Embed(title=f"You were timed out in {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Duration", value=format_duration(minutes))
    dm.add_field(name="Reason", value=reason, inline=False)
    await try_dm(target, dm)

    embed = discord.Embed(title="⏳ Member timed out", color=COLOR_WARN)
    embed.add_field(name="Member", value=target.mention)
    embed.add_field(name="By", value=message.author.mention)
    embed.add_field(name="Duration", value=format_duration(minutes))
    embed.add_field(name="Ends", value=discord.utils.format_dt(until, "R"))
    embed.add_field(name="Reason", value=reason, inline=False)
    await message.reply(embed=embed, allowed_mentions=no_pings)
    await send_mod_log(guild, embed, message.channel.id)


async def handle_untimeout(message):
    """;untimeout @user"""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Timeouts only work inside a server.")
        return
    if not is_mod(message.author):
        await message.reply("Only **mods** and **admins** can remove timeouts.")
        return

    args = message.content.strip().split()
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}untimeout @user`")
        return

    try:
        await target.timeout(None, reason=f"Timeout removed by {message.author}")
    except discord.HTTPException:
        await message.reply("I couldn't remove their timeout. Check my **Moderate Members** permission and role position.")
        return

    embed = discord.Embed(
        description=f"Removed the timeout from {target.mention}.",
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"Removed by {message.author.display_name}")
    await message.reply(embed=embed, allowed_mentions=no_pings)
    await send_mod_log(guild, embed, message.channel.id)


async def handle_warnings(message):
    """;warnings [@user] - members can see their own, mods can see anyone's."""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Warnings only work inside a server.")
        return

    args = message.content.strip().split()
    if len(args) > 1:
        found = USER_ID_RE.search(args[1])
        if not found:
            await message.reply(f"Usage: `{PREFIX}warnings [@user]`")
            return
        user_id = int(found.group())
    else:
        user_id = message.author.id

    if user_id != message.author.id and not is_mod(message.author):
        await message.reply("You can only view your own warnings.")
        return

    rows = get_warnings(guild.id, user_id)
    member = guild.get_member(user_id)
    name = member.display_name if member else f"User {user_id}"

    if not rows:
        embed = discord.Embed(description=f"**{name}** has no warnings.", color=COLOR_AFK)
    else:
        lines = []
        for warn_id, mod_id, reason, created_at, category in rows[-10:]:
            when = discord.utils.format_dt(datetime.fromisoformat(created_at), "R")
            tag = "NSFW" if category == "nsfw" else "General"
            lines.append(f"**#{warn_id}** `{tag}` - {reason[:150]} - by <@{mod_id}> {when}")
        embed = discord.Embed(
            title=f"⚠️ Warnings for {name}",
            description="\n".join(lines),
            color=COLOR_WARN,
        )
        general = sum(1 for row in rows if row[4] != "nsfw")
        nsfw = len(rows) - general
        footer = f"General: {general} | NSFW: {nsfw}"
        if len(rows) > 10:
            footer += " (showing the latest 10)"
        embed.set_footer(text=footer)
    await message.reply(embed=embed, allowed_mentions=no_pings)


async def handle_delwarn(message):
    """;delwarn <warning id>"""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Warnings only work inside a server.")
        return
    if not is_mod(message.author):
        await message.reply("Only **mods** and **admins** can remove warnings.")
        return

    args = message.content.strip().split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply(f"Usage: `{PREFIX}delwarn <warning id>` (the ID is shown in `{PREFIX}warnings`)")
        return

    warn_id = int(args[1])
    user_id = delete_warning(guild.id, warn_id)
    if user_id is None:
        await message.reply("I couldn't find a warning with that ID in this server.")
        return

    remaining = len(get_warnings(guild.id, user_id))
    embed = discord.Embed(
        description=(
            f"Removed warning **#{warn_id}** from <@{user_id}>. "
            f"They now have **{remaining}** warning{'' if remaining == 1 else 's'}."
        ),
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"Removed by {message.author.display_name}")
    await message.reply(embed=embed, allowed_mentions=no_pings)
    await send_mod_log(guild, embed, message.channel.id)


async def handle_clearwarns(message):
    """;clearwarns @user"""
    no_pings = discord.AllowedMentions.none()
    guild = message.guild
    if guild is None:
        await message.reply("Warnings only work inside a server.")
        return
    if not is_mod(message.author):
        await message.reply("Only **mods** and **admins** can clear warnings.")
        return

    args = message.content.strip().split()
    found = USER_ID_RE.search(args[1]) if len(args) > 1 else None
    if not found:
        await message.reply(f"Usage: `{PREFIX}clearwarns @user`")
        return

    user_id = int(found.group())
    removed = clear_warnings(guild.id, user_id)
    embed = discord.Embed(
        description=f"Cleared **{removed}** warning{'' if removed == 1 else 's'} for <@{user_id}>.",
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"Cleared by {message.author.display_name}")
    await message.reply(embed=embed, allowed_mentions=no_pings)
    await send_mod_log(guild, embed, message.channel.id)


# ---------------------------------------------------------------------------
# Help and summary
# ---------------------------------------------------------------------------
def build_help_embed(bot_user):
    """Builds the ;help embed."""
    description = (
        "**📌 Commands:**\n"
        f"`{PREFIX}help` - Shows this help message.\n"
        f"`{PREFIX}afk <reason>` - Marks you as away. The bot tells anyone who pings you.\n"
        f"`{PREFIX}summarize` - Summarizes the last 50 messages in the channel.\n"
        f"`{PREFIX}reset` - Clears my memory in this channel (mods/admins only).\n"
        f"`{PREFIX}warnings [@user]` - Shows your warnings. Mods can check anyone.\n"
        "\n"
        "**🛡️ Moderation (mods/admins only):**\n"
        f"`{PREFIX}warn @user <reason>` - Warns for Promoting / Other servers / Others.\n"
        f"`{PREFIX}warn @user nsfw <reason>` - Warns for NSFW.\n"
        f"`{PREFIX}timeout @user <time> [reason]` - Times someone out (`10m`, `2h`, `3d`, `1w`).\n"
        f"`{PREFIX}untimeout @user` - Removes a timeout.\n"
        f"`{PREFIX}delwarn <id>` - Removes one warning.\n"
        f"`{PREFIX}clearwarns @user` - Clears all of a member's warnings.\n"
        "\n"
        "**🤖 Chat:**\n"
        "`@mention` - Mention me with a question and i'll answer.\n"
    )
    embed = discord.Embed(description=description, color=COLOR_AFK)
    if bot_user is not None:
        embed.set_author(
            name=bot_user.name,
            icon_url=bot_user.display_avatar.url,
        )
    embed.set_footer(text="discord.gg/noxity")
    return embed


async def get_summary(channel):
    """Reads the last 50 messages in a channel and returns a Gemini summary."""
    lines = []
    try:
        async for msg in channel.history(limit=50):
            # skip bots, empty messages, and command messages like ;summarize
            if msg.author.bot or not msg.content.strip() or msg.content.startswith(PREFIX):
                continue
            lines.append(f"{msg.author.display_name}: {msg.content}")
    except discord.Forbidden:
        return "I don't have permission to read this channel's history."

    if not lines:
        return "There's nothing to summarize here yet."

    lines.reverse()  # oldest first, so it reads in order
    conversation = "\n".join(lines)

    try:
        response = await ai.aio.models.generate_content(
            model=MODEL,
            contents=(
                "Summarize this Discord conversation in a few short bullet points. "
                "Mention who said what when it matters.\n\n" + conversation
            ),
            config=types.GenerateContentConfig(max_output_tokens=600),
        )
        return (response.text or "I couldn't make a summary.")[:2000]
    except Exception as e:
        print("API error:", e)
        return "Sorry, something went wrong (maybe a rate limit). Try again in a minute."


# ---------------------------------------------------------------------------
# The bot
# ---------------------------------------------------------------------------
class Client(discord.Client):
    def __init__(self, *, intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()  # registers the slash commands with Discord
        asyncio.create_task(self.rotate_status())  # starts the status

    async def rotate_status(self):
        """Sets 'Listening to ...' and rotates it if there is more than one status."""
        await self.wait_until_ready()
        i = 0
        while not self.is_closed():
            name = STATUSES[i % len(STATUSES)].format(servers=len(self.guilds))
            await self.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(type=discord.ActivityType.listening, name=name),
            )
            if len(STATUSES) == 1:
                return  # only one status, so no need to keep changing it
            i += 1
            await asyncio.sleep(STATUS_INTERVAL)

    async def on_ready(self):
        print(f'Logged in as {self.user}')

    async def on_message(self, message):
        if message.author.bot:
            return

        # Auto-react
        content = message.content.lower()
        for word, emoji in REACTIONS.items():
            if word in content:
                try:
                    await message.add_reaction(emoji)
                except discord.HTTPException:
                    pass

        parts = content.strip().split()
        command = parts[0] if parts else ""
        no_pings = discord.AllowedMentions.none()  # replies never ping anyone

        # ;afk <reason> - mark yourself as away
        if command == f"{PREFIX}afk":
            pieces = message.content.strip().split(maxsplit=1)
            reason = pieces[1][:100] if len(pieces) > 1 else "AFK"
            afk[message.author.id] = (reason, datetime.now(timezone.utc))

            embed = discord.Embed(
                description=f"You are now AFK: `{reason}`",
                color=COLOR_AFK,
            )
            await message.reply(embed=embed, allowed_mentions=no_pings)
            return

        # If an afk person sends any other message, they're back.
        # The welcome back message deletes itself after 2 seconds.
        if message.author.id in afk:
            _, since = afk.pop(message.author.id)
            await message.reply(
                f"Welcome back {message.author.mention}. You were AFK {time_ago(since)}.",
                allowed_mentions=no_pings,
                delete_after=2,
            )

        # If someone pinged an afk person, tell them
        notes = []
        for user in message.mentions:
            if user.bot or user.id == message.author.id or user.id not in afk:
                continue
            reason, since = afk[user.id]
            notes.append(f"**{user.display_name}** is AFK: `{reason}` - {time_ago(since)}")
        if notes:
            embed = discord.Embed(description="\n".join(notes), color=COLOR_AFK)
            await message.reply(embed=embed, allowed_mentions=no_pings)

        # Moderation commands
        if command == f"{PREFIX}warn":
            await handle_warn(message)
            return

        if command == f"{PREFIX}timeout":
            await handle_timeout(message)
            return

        if command == f"{PREFIX}untimeout":
            await handle_untimeout(message)
            return

        if command == f"{PREFIX}warnings":
            await handle_warnings(message)
            return

        if command == f"{PREFIX}delwarn":
            await handle_delwarn(message)
            return

        if command == f"{PREFIX}clearwarns":
            await handle_clearwarns(message)
            return

        # Prefix commands like ;reset and ;summarize
        if command == f"{PREFIX}reset":
            if not is_mod(message.author):
                await message.reply("Only **mods** and **admins** can reset my memory.")
                return
            chats.pop(message.channel.id, None)
            await message.reply("Memory cleared! Starting over...")
            return

        if command == f"{PREFIX}summarize":
            async with message.channel.typing():
                summary = await get_summary(message.channel)
            await message.reply(summary)
            return

        if command == f"{PREFIX}help":
            await message.reply(embed=build_help_embed(self.user), allowed_mentions=no_pings)
            return

        # AI reply only when the bot is @mentioned
        if self.user not in message.mentions:
            return

        # Remove the @mention from the text
        text = message.content
        for tag in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            text = text.replace(tag, "")
        text = text.strip()
        if not text:
            await message.reply("Hi! Ask me something.")
            return

        chat = chats.get(message.channel.id)
        if chat is None:
            chat = ai.aio.chats.create(
                model=MODEL,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=800,
                ),
            )
            chats[message.channel.id] = chat

        async with message.channel.typing():
            try:
                response = await chat.send_message(text)
                reply = response.text or "I couldn't come up with a reply."
            except Exception as e:
                print("API error:", e)
                await message.reply("Sorry, something went wrong (maybe a rate limit). Try again in a minute.")
                return

        # Discord's limit is 2000 characters per message
        for i in range(0, len(reply), 2000):
            await message.reply(reply[i:i + 2000])


intents = discord.Intents.default()
intents.messages = True
intents.message_content = True  # needed so the bot can read message text

clients = Client(intents=intents)


# Slash command versions
@clients.tree.command(name="reset", description="Clear the AI's memory for this channel (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
async def reset(interaction: discord.Interaction):
    if not is_mod(interaction.user):
        await interaction.response.send_message(
            "Only mods and admins can reset my memory.", ephemeral=True
        )
        return
    chats.pop(interaction.channel_id, None)
    await interaction.response.send_message("Memory cleared! Starting over...")


@clients.tree.command(name="summarize", description="Summarize the last 50 messages in this channel")
async def summarize(interaction: discord.Interaction):
    await interaction.response.defer()  # Gemini takes a few seconds
    summary = await get_summary(interaction.channel)
    await interaction.followup.send(summary)


clients.run("BOT_TOKEN") 