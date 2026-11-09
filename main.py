import asyncio
import re
import sqlite3
import discord
from datetime import datetime, timedelta, timezone
from discord import app_commands
from google import genai
from google.genai import types

PREFIX = "?"

STATUSES = [
    ".gg/noxity",
    "house of owls",
    "lurking @ noxity",
    "?help",
]
STATUS_INTERVAL = 60

# ---------------------------------------------------------------------------
# Warning system settings
# ---------------------------------------------------------------------------
MOD_LOG_CHANNEL_ID = 0
DB_FILE = "warnings.db"
AUTO_BAN = True

#   None  = reminder only (no action)
#   "ban" = ban
CATEGORY_LABELS = {
    "general": "Promoting / Mentioning other servers / Others",
    "nsfw": "NSFW",
}
LADDERS = {
    "general": [None, 60, 120, 240, "ban"],
    "nsfw": [None, 600, 1440, 10080, "ban"],
}

MAX_TIMEOUT_MINUTES = 28 * 24 * 60

# Roles with any of these permissions can't be removed with the role command
PROTECTED_PERMS = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "ban_members",
    "kick_members",
    "moderate_members",
    "manage_channels",
    "manage_webhooks",
)

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

ai = genai.Client(api_key="GEMINI_API_KEY")
chats = {}
afk = {}

# Embed colors (all black)
COLOR_AFK = discord.Color(0x010101)
COLOR_WARN = discord.Color(0x010101)

# Auto reacts
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
    "hori": "🫃",
    "kino": "🫃",
}

# ---------------------------------------------------------------------------
# Warning database
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
    """Deletes one warning by its database id."""
    db.execute("DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warn_id, guild_id))
    db.commit()


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
DURATION_HELP = "Time examples: `10m` (minutes), `2h` (hours), `3d` (days), `1w` (weeks)"
ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
}


def ordinal_word(n):
    """1 -> 'first', 2 -> 'second', 12 -> '12th'."""
    if n in ORDINAL_WORDS:
        return ORDINAL_WORDS[n]
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def is_mod(member):
    """True if the member is an admin or has Manage Messages. False in DMs."""
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_messages))


def can_manage_roles(member):
    """True if the member is an admin or has Manage Roles. False in DMs."""
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_roles))


def is_protected_role(role):
    """True if the role has any permission in PROTECTED_PERMS."""
    return any(getattr(role.permissions, perm, False) for perm in PROTECTED_PERMS)


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


def validate(guild, author, target=None):
    """Returns a problem message if the author can't do a mod action, else None."""
    if guild is None:
        return "That only works inside a server."
    if not is_mod(author):
        return "Only **mods** and **admins** can use that."
    if target is not None:
        if target.bot or target.id == author.id or target.id == guild.owner_id:
            return "You can't do that to that person."
        if author.id != guild.owner_id and target.top_role >= author.top_role:
            return "That member's top role is the same as or higher than yours."
    return None


def validate_role(guild, author, target):
    """Checks for the role command. Self and higher-ranked members are allowed."""
    if guild is None:
        return "That only works inside a server."
    if not can_manage_roles(author):
        return "Only members with **Manage Roles** can use that."
    if target.bot:
        return "You can't change roles on a bot."
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


def parse_user_id(text):
    """Returns the user ID as an int if the text is a plain Discord ID, else None."""
    text = text.strip()
    return int(text) if re.fullmatch(r"\d{15,20}", text) else None


def find_role(guild, text):
    """Finds a role from a mention, an ID, or a name (exact or partial, case-insensitive)."""
    text = text.strip()
    found = re.fullmatch(r"<@&(\d{15,20})>|(\d{15,20})", text)
    if found:
        return guild.get_role(int(found.group(1) or found.group(2)))

    name = text.lstrip("@").strip().lower()
    if not name:
        return None
    roles = [r for r in guild.roles if not r.is_default()]
    exact = [r for r in roles if r.name.lower() == name]
    if exact:
        return exact[0]
    partial = [r for r in roles if name in r.name.lower()]
    return partial[0] if len(partial) == 1 else None


async def get_display_name(guild, user_id):
    """Gets a user's display name, even if they're not in the bot's cache or have left the server."""
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            member = None
    if member is not None:
        return member.display_name
    try:
        user = await clients.fetch_user(user_id)
        return user.display_name
    except discord.HTTPException:
        return f"User {user_id}"


async def try_dm(user, embed):
    try:
        await user.send(embed=embed)
    except discord.HTTPException:
        pass  # their DMs are closed


async def send_mod_log(guild, content, current_channel_id):
    """Posts to your mod log channel (if you set one). Accepts plain text or an embed."""
    if not MOD_LOG_CHANNEL_ID:
        return
    channel = guild.get_channel(MOD_LOG_CHANNEL_ID)
    if channel is None or channel.id == current_channel_id:
        return
    try:
        if isinstance(content, str):
            await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
        else:
            await channel.send(embed=content)
    except discord.HTTPException:
        pass


async def do_warn(guild, author, target, category, reason, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    warn_id = add_warning(guild.id, target.id, author.id, reason, category)
    rows = get_warnings(guild.id, target.id)
    offense = sum(1 for row in rows if row[4] == category)
    total = len(rows)
    ladder = LADDERS[category]
    step = ladder_step(category, offense)

    # DM for the member
    dm = discord.Embed(title=f"You were warned in {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Rule", value=CATEGORY_LABELS[category], inline=False)
    dm.add_field(name="Reason", value=reason, inline=False)
    dm.add_field(name="Offense", value=f"{min(offense, len(ladder))} of {len(ladder)}")
    dm.add_field(name="Consequence", value=step_label(step))

    # Carry out the action for this offense
    extra = ""
    if step == "ban":
        await try_dm(target, dm)  # before the ban, since they can't be messaged after
        if AUTO_BAN:
            try:
                await target.ban(
                    reason=f"Offense {offense} ({CATEGORY_LABELS[category]}): {reason}",
                    delete_message_seconds=0,
                )
                extra = " they hit the ban stage, so they got banned."
            except discord.HTTPException:
                extra = " they hit the ban stage, but i couldn't ban them (check my Ban Members permission)."
        else:
            extra = " they hit the ban stage, but AUTO_BAN is off, so a mod needs to decide."
    elif step is not None:
        try:
            await target.timeout(
                timedelta(minutes=step),
                reason=f"Offense {offense} ({CATEGORY_LABELS[category]}): {reason}",
            )
        except discord.HTTPException:
            extra = " i couldn't time them out (check my Moderate Members permission)."
        await try_dm(target, dm)
    else:
        await try_dm(target, dm)

    text = f"**{target.display_name}** has been warned, this is their {ordinal_word(total)} warning.{extra}"
    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_timeout(guild, author, target, minutes, reason, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None
    if minutes > MAX_TIMEOUT_MINUTES:
        return (
            "Discord only allows timeouts up to **28 days** (`4w`), so months aren't possible "
            "for a timeout. Use `4w` or less, or ban the member for something longer."
        ), None

    try:
        await target.timeout(
            timedelta(minutes=minutes),
            reason=f"{reason} (by {author})",
        )
    except discord.HTTPException:
        return (
            "I couldn't time them out. Check that i have the **Moderate Members** permission "
            "and that my role is above theirs."
        ), None

    dm = discord.Embed(title=f"You were timed out in {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Duration", value=format_duration(minutes))
    dm.add_field(name="Reason", value=reason, inline=False)
    await try_dm(target, dm)

    text = f"**{target.display_name}** has been timed out for **{format_duration(minutes)}**."
    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_untimeout(guild, author, target, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    try:
        await target.timeout(None, reason=f"Timeout removed by {author}")
    except discord.HTTPException:
        return "I couldn't remove their timeout. Check my **Moderate Members** permission and role position.", None

    text = f"**{target.display_name}** has had their timeout removed."
    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_kick(guild, author, target, reason, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    # DM first, since they can't be messaged after they're kicked
    dm = discord.Embed(title=f"You were kicked from {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Reason", value=reason, inline=False)
    await try_dm(target, dm)

    try:
        await target.kick(reason=f"{reason} (by {author})")
    except discord.HTTPException:
        return (
            "I couldn't kick them. Check that i have the **Kick Members** permission "
            "and that my role is above theirs."
        ), None

    embed = discord.Embed(title="👢 Member kicked", color=COLOR_WARN)
    embed.add_field(name="Member", value=f"{target.mention} ({target})")
    embed.add_field(name="By", value=author.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    await send_mod_log(guild, embed, channel_id)
    return None, embed


async def do_ban(guild, author, target, reason, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    # DM first, since they can't be messaged after they're banned
    dm = discord.Embed(title=f"You were banned from {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Reason", value=reason, inline=False)
    await try_dm(target, dm)

    try:
        await target.ban(reason=f"{reason} (by {author})", delete_message_seconds=0)
    except discord.HTTPException:
        return (
            "I couldn't ban them. Check that i have the **Ban Members** permission "
            "and that my role is above theirs."
        ), None

    text = f"**{target.display_name}** got banned LOOOL"
    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_unban(guild, author, user_id, channel_id):
    problem = validate(guild, author)
    if problem:
        return problem, None

    try:
        ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
    except discord.NotFound:
        return "That user isn't banned.", None
    except discord.HTTPException:
        return "I couldn't check the ban list. Make sure i have the **Ban Members** permission.", None

    user = ban_entry.user
    try:
        await guild.unban(user, reason=f"Unbanned by {author}")
    except discord.HTTPException:
        return "I couldn't unban them. Check that i have the **Ban Members** permission.", None

    text = f"**{user.display_name}** has been unbanned."
    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_role(guild, author, target, role, channel_id):
    """Gives the role if they don't have it, removes it if they do. Returns (error, text)."""
    problem = validate_role(guild, author, target)
    if problem:
        return problem, None
    if role.is_default() or role.managed:
        return "I can't give or remove that role.", None
    if role >= guild.me.top_role:
        return "That role is at or above my top role, so i can't manage it.", None

    has_role = role in target.roles
    if has_role and is_protected_role(role):
        return "That role has admin-level permissions, so it can't be removed with this command.", None

    reason = f"Role toggled by {author}"
    try:
        if has_role:
            await target.remove_roles(role, reason=reason)
            text = f"**{role.name}** removed from **{target.mention}** by **{author.display_name}**"
        else:
            await target.add_roles(role, reason=reason)
            text = f"**{role.name}** given to **{target.mention}** by **{author.display_name}**"
    except discord.HTTPException:
        return "I couldn't change their roles. Check my **Manage Roles** permission and role position.", None

    await send_mod_log(guild, text, channel_id)
    return None, text


async def do_warnings(guild, author, user_id):
    """Members can see their own warnings, mods can see anyone's."""
    if guild is None:
        return "That only works inside a server.", None
    if user_id != author.id and not is_mod(author):
        return "You can only view your own warnings.", None

    rows = get_warnings(guild.id, user_id)
    name = await get_display_name(guild, user_id)

    if not rows:
        return None, discord.Embed(description=f"**{name}** has no warnings.", color=COLOR_AFK)

    lines = []
    start = max(0, len(rows) - 10)  # show the latest 10, but keep their real numbers
    for number in range(start, len(rows)):
        _, mod_id, reason, created_at, category = rows[number]
        when = discord.utils.format_dt(datetime.fromisoformat(created_at), "R")
        tag = "NSFW" if category == "nsfw" else "General"
        lines.append(f"**{number + 1}.** `{tag}` - {reason[:150]} - by <@{mod_id}> {when}")
    embed = discord.Embed(
        title=f"⚠️ Warnings for {name}",
        description="\n".join(lines),
        color=COLOR_WARN,
    )
    general = sum(1 for row in rows if row[4] != "nsfw")
    nsfw = len(rows) - general
    footer = f"General: {general} | NSFW: {nsfw} | Remove one with ;delwarn @user <number>"
    if len(rows) > 10:
        footer += " | showing the latest 10"
    embed.set_footer(text=footer)
    return None, embed


async def do_delwarn(guild, author, target, number, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    rows = get_warnings(guild.id, target.id)
    if not rows:
        return f"{target.display_name} doesn't have any warnings.", None
    if number < 1 or number > len(rows):
        return (
            f"{target.display_name} has {len(rows)} warning{'' if len(rows) == 1 else 's'}. "
            f"Pick a number from 1 to {len(rows)} (see `{PREFIX}warns @user`)."
        ), None

    warn_id = rows[number - 1][0]
    delete_warning(guild.id, warn_id)

    remaining = len(rows) - 1
    embed = discord.Embed(
        description=(
            f"Removed warning **#{number}** from {target.mention}. "
            f"They now have **{remaining}** warning{'' if remaining == 1 else 's'}."
        ),
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"Removed by {author.display_name}")
    await send_mod_log(guild, embed, channel_id)
    return None, embed


async def do_clearwarns(guild, author, user_id, channel_id):
    problem = validate(guild, author)
    if problem:
        return problem, None

    removed = clear_warnings(guild.id, user_id)
    embed = discord.Embed(
        description=f"Cleared **{removed}** warning{'' if removed == 1 else 's'} for <@{user_id}>.",
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"Cleared by {author.display_name}")
    await send_mod_log(guild, embed, channel_id)
    return None, embed


# ---------------------------------------------------------------------------
# ;prefix command handlers (they read the message, then call the logic above)
# ---------------------------------------------------------------------------
async def reply_result(message, error, result):
    """Replies with the error, the plain text result, or the embed result."""
    no_mentions = discord.AllowedMentions.none()
    if error:
        await message.reply(error, allowed_mentions=no_mentions)
    elif isinstance(result, str):
        await message.reply(result, allowed_mentions=no_mentions)
    else:
        await message.reply(embed=result, allowed_mentions=no_mentions)


async def prefix_warn(message):
    """;warn @user [nsfw] <reason>"""
    guild = message.guild
    problem = validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(
            f"Usage: `{PREFIX}warn @user <reason>` for Promoting / Other servers / Others\n"
            f"or `{PREFIX}warn @user nsfw <reason>` for NSFW"
        )
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

    error, result = await do_warn(guild, message.author, target, category, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_to(message):
    """;to @user <time> [reason]"""
    guild = message.guild
    problem = validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=3)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    minutes = parse_duration(args[2]) if len(args) > 2 else None
    if target is None or minutes is None:
        await message.reply(f"Usage: `{PREFIX}to @user <time> [reason]`\n{DURATION_HELP}")
        return

    reason = args[3].strip()[:300] if len(args) > 3 else "No reason given"
    error, result = await do_timeout(guild, message.author, target, minutes, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_rto(message):
    """;rto @user - removes a timeout"""
    guild = message.guild
    problem = validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}rto @user`")
        return

    error, result = await do_untimeout(guild, message.author, target, message.channel.id)
    await reply_result(message, error, result)


async def prefix_kick(message):
    """;kick @user [reason]"""
    guild = message.guild
    problem = validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}kick @user [reason]`")
        return

    reason = args[2].strip()[:300] if len(args) > 2 else "No reason given"
    error, result = await do_kick(guild, message.author, target, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_ban(message):
    """;ban @user [reason]"""
    guild = message.guild
    problem = validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}ban @user [reason]`")
        return

    reason = args[2].strip()[:300] if len(args) > 2 else "No reason given"
    error, result = await do_ban(guild, message.author, target, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_unban(message):
    """;unban <user id>"""
    problem = validate(message.guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    user_id = parse_user_id(args[1]) if len(args) > 1 else None
    if user_id is None:
        await message.reply(f"Usage: `{PREFIX}unban <user id>` (right-click the user's ID to copy it)")
        return

    error, result = await do_unban(message.guild, message.author, user_id, message.channel.id)
    await reply_result(message, error, result)


async def prefix_role(message):
    """;role @user <role name, mention, or ID>"""
    problem = validate_role(message.guild, message.author, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(message.guild, args[1]) if len(args) > 1 else None
    role = find_role(message.guild, args[2]) if len(args) > 2 else None
    if target is None or role is None:
        await message.reply(
            f"Usage: `{PREFIX}role @user <role name>` (a mention, ID, or name works)"
        )
        return

    error, text = await do_role(message.guild, message.author, target, role, message.channel.id)
    if error:
        await message.reply(error)
        return
    await message.reply(
        text,
        allowed_mentions=discord.AllowedMentions(users=[target], roles=False, everyone=False, replied_user=False),
    )


async def prefix_warns(message):
    """;warns [@user]"""
    args = message.content.strip().split()
    if len(args) > 1:
        found = USER_ID_RE.search(args[1])
        if not found:
            await message.reply(f"Usage: `{PREFIX}warns [@user]`")
            return
        user_id = int(found.group())
    else:
        user_id = message.author.id

    error, result = await do_warnings(message.guild, message.author, user_id)
    await reply_result(message, error, result)


async def prefix_delwarn(message):
    """;delwarn @user <number>"""
    problem = validate(message.guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    target = await find_member(message.guild, args[1]) if len(args) > 1 else None
    number = int(args[2]) if len(args) > 2 and args[2].isdigit() else None
    if target is None or number is None:
        await message.reply(
            f"Usage: `{PREFIX}delwarn @user <number>`\n"
            f"The number is the warning's position in `{PREFIX}warns @user`."
        )
        return

    error, result = await do_delwarn(message.guild, message.author, target, number, message.channel.id)
    await reply_result(message, error, result)


async def prefix_clearwarns(message):
    """;clearwarns @user"""
    problem = validate(message.guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    found = USER_ID_RE.search(args[1]) if len(args) > 1 else None
    if not found:
        await message.reply(f"Usage: `{PREFIX}clearwarns @user`")
        return

    error, result = await do_clearwarns(message.guild, message.author, int(found.group()), message.channel.id)
    await reply_result(message, error, result)


# ---------------------------------------------------------------------------
# Help command: front page with COMMANDS / MODERATIONS buttons
# ---------------------------------------------------------------------------
def _help_embed(bot_user, description):
    embed = discord.Embed(description=description, color=COLOR_AFK)
    if bot_user is not None:
        embed.set_author(name=bot_user.name, icon_url=bot_user.display_avatar.url)
    embed.set_footer(text="discord.gg/noxity")
    return embed


def build_help_main_embed(bot_user):
    """Front page of ;help."""
    return _help_embed(bot_user, "Click any button to view.")


def build_help_commands_embed(bot_user):
    """The COMMANDS page of ;help."""
    description = (
        "__**Commands:**__\n"
        f"**{PREFIX}help** - Shows this help message.\n"
        f"**{PREFIX}afk <reason>** - Marks you as away. The bot tells anyone who pings you.\n"
        f"**{PREFIX}summarize** - Summarizes the last 50 messages in the channel.\n"
        f"**{PREFIX}reset** - Clears my memory in this channel.\n"
        f"**{PREFIX}warns [@user]** - Shows your warnings. Mods can check anyone.\n"
        "\n"
        "__**Chat:**__\n"
        "**@mention** - Mention me with a question and i'll answer.\n"
    )
    return _help_embed(bot_user, description)


def build_help_moderation_embed(bot_user):
    """The MODERATIONS page of ;help."""
    description = (
        "__**Moderation:**__\n"
        f"**{PREFIX}warn @user <reason>** - Warns for Promoting / Other servers / Others.\n"
        f"**{PREFIX}warn @user nsfw <reason>** - Warns for NSFW.\n"
        f"**{PREFIX}to @user <time> [reason]** - Times someone out (`10m`, `2h`, `3d`, `1w`).\n"
        f"**{PREFIX}rto @user** - Removes a timeout.\n"
        f"**{PREFIX}kick @user [reason]** - Kicks a member from the server.\n"
        f"**{PREFIX}ban @user [reason]** - Bans a member from the server.\n"
        f"**{PREFIX}unban <user id>** - Unbans someone using their user ID.\n"
        f"**{PREFIX}role @user <role name>** - Gives a role, or removes it if they already have it. Needs Manage Roles.\n"
        f"**{PREFIX}delwarn @user <number>** - Removes one of their warnings (number from `{PREFIX}warns`).\n"
        f"**{PREFIX}clearwarns @user** - Clears all of a member's warnings.\n"
    )
    return _help_embed(bot_user, description)


class HelpView(discord.ui.View):
    """Two gray buttons that switch the help embed between sections."""

    def __init__(self, bot_user):
        super().__init__(timeout=300)
        self.bot_user = bot_user

    @discord.ui.button(label="COMMANDS", style=discord.ButtonStyle.secondary)
    async def commands_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_help_commands_embed(self.bot_user), view=self
        )

    @discord.ui.button(label="MODERATIONS", style=discord.ButtonStyle.secondary)
    async def moderation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=build_help_moderation_embed(self.bot_user), view=self
        )


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
        await self.tree.sync()
        asyncio.create_task(self.rotate_status())

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
                return
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
        no_pings = discord.AllowedMentions.none()

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

        if message.author.id in afk:
            _, since = afk.pop(message.author.id)
            await message.reply(
                f"Welcome back {message.author.mention}. You were AFK {time_ago(since)}.",
                allowed_mentions=no_pings,
                delete_after=2,
            )

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
            await prefix_warn(message)
            return

        if command == f"{PREFIX}to":
            await prefix_to(message)
            return

        if command == f"{PREFIX}rto":
            await prefix_rto(message)
            return

        if command == f"{PREFIX}kick":
            await prefix_kick(message)
            return

        if command == f"{PREFIX}ban":
            await prefix_ban(message)
            return

        if command == f"{PREFIX}unban":
            await prefix_unban(message)
            return

        if command == f"{PREFIX}role":
            await prefix_role(message)
            return

        if command == f"{PREFIX}warns":
            await prefix_warns(message)
            return

        if command == f"{PREFIX}delwarn":
            await prefix_delwarn(message)
            return

        if command == f"{PREFIX}clearwarns":
            await prefix_clearwarns(message)
            return

        # Prefix commands like ?reset and ?summarize
        if command == f"{PREFIX}reset":
            chats.pop(message.channel.id, None)
            await message.reply("Memory cleared! Starting over...")
            return

        if command == f"{PREFIX}summarize":
            async with message.channel.typing():
                summary = await get_summary(message.channel)
            await message.reply(summary)
            return

        if command == f"{PREFIX}help":
            await message.reply(
                embed=build_help_main_embed(self.user),
                view=HelpView(self.user),
                allowed_mentions=no_pings,
            )
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

        for i in range(0, len(reply), 2000):
            await message.reply(reply[i:i + 2000])


intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

clients = Client(intents=intents)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
RULE_CHOICES = [
    app_commands.Choice(name=label, value=key)
    for key, label in CATEGORY_LABELS.items()
]


async def slash_finish(interaction, error, result):
    """Sends the result of a slash command that already used defer()."""
    no_mentions = discord.AllowedMentions.none()
    if error:
        await interaction.followup.send(error, allowed_mentions=no_mentions)
    elif isinstance(result, str):
        await interaction.followup.send(result, allowed_mentions=no_mentions)
    else:
        await interaction.followup.send(embed=result)


@clients.tree.command(name="reset", description="Clear the AI's memory for this channel")
@app_commands.guild_only()
async def reset(interaction: discord.Interaction):
    chats.pop(interaction.channel_id, None)
    await interaction.response.send_message("Memory cleared! Starting over...")


@clients.tree.command(name="summarize", description="Summarize the last 50 messages in this channel")
async def summarize(interaction: discord.Interaction):
    await interaction.response.defer()
    summary = await get_summary(interaction.channel)
    await interaction.followup.send(summary)


@clients.tree.command(name="warn", description="Warn a member (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    member="Who to warn",
    reason="Why they're being warned",
    rule="Which rule they broke (leave empty for Promoting / Other servers / Others)",
)
@app_commands.choices(rule=RULE_CHOICES)
async def slash_warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    rule: app_commands.Choice[str] = None,
):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    category = rule.value if rule else "general"
    error, result = await do_warn(
        interaction.guild, interaction.user, member, category, reason[:300], interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="to", description="Time a member out (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    member="Who to time out",
    duration="How long: 10m, 2h, 3d or 1w (max 28 days)",
    reason="Why they're being timed out",
)
async def slash_to(
    interaction: discord.Interaction,
    member: discord.Member,
    duration: str,
    reason: str = "No reason given",
):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    minutes = parse_duration(duration)
    if minutes is None:
        await interaction.response.send_message(DURATION_HELP, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await do_timeout(
        interaction.guild, interaction.user, member, minutes, reason[:300], interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="rto", description="Remove a member's timeout (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="Whose timeout to remove")
async def slash_rto(interaction: discord.Interaction, member: discord.Member):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await do_untimeout(
        interaction.guild, interaction.user, member, interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="kick", description="Kick a member from the server (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    member="Who to kick",
    reason="Why they're being kicked",
)
async def slash_kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason given",
):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await do_kick(
        interaction.guild, interaction.user, member, reason[:300], interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="ban", description="Ban a member from the server (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    member="Who to ban",
    reason="Why they're being banned",
)
async def slash_ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason given",
):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await do_ban(
        interaction.guild, interaction.user, member, reason[:300], interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="unban", description="Unban a user by their ID (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(user_id="The ID of the banned user")
async def slash_unban(interaction: discord.Interaction, user_id: str):
    parsed = parse_user_id(user_id)
    if parsed is None:
        await interaction.response.send_message("That isn't a valid user ID.", ephemeral=True)
        return
    if not is_mod(interaction.user):
        await interaction.response.send_message("Only **mods** and **admins** can use that.", ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await do_unban(interaction.guild, interaction.user, parsed, interaction.channel_id)
    await slash_finish(interaction, error, result)


@clients.tree.command(name="role", description="Give or remove a role from a member (Manage Roles only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(member="Who to give or remove the role from", role="The role name to toggle")
async def slash_role(interaction: discord.Interaction, member: discord.Member, role: str):
    problem = validate_role(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    found_role = find_role(interaction.guild, role)
    if found_role is None:
        await interaction.response.send_message(
            f"I couldn't find a role matching `{role}`.", ephemeral=True
        )
        return
    await interaction.response.defer()
    error, text = await do_role(interaction.guild, interaction.user, member, found_role, interaction.channel_id)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    await interaction.followup.send(
        text,
        allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
    )


@slash_role.autocomplete("role")
async def role_autocomplete(interaction: discord.Interaction, current: str):
    cur = current.lower().lstrip("@")
    return [
        app_commands.Choice(name=r.name, value=r.name)
        for r in interaction.guild.roles
        if not r.is_default() and cur in r.name.lower()
    ][:25]


@clients.tree.command(name="warns", description="Show warnings (yours, or anyone's if you're a mod)")
@app_commands.guild_only()
@app_commands.describe(user="Whose warnings to show (leave empty for yours)")
async def slash_warns(interaction: discord.Interaction, user: discord.User = None):
    target_id = user.id if user else interaction.user.id
    error, embed = await do_warnings(interaction.guild, interaction.user, target_id)
    # Only visible to the person who asked, so warnings stay private
    if error:
        await interaction.response.send_message(error, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


@clients.tree.command(name="delwarn", description="Remove one of a member's warnings by its number (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(
    member="Whose warning to remove",
    number="The warning's number, as shown in /warns",
)
async def slash_delwarn(interaction: discord.Interaction, member: discord.Member, number: int):
    problem = validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, embed = await do_delwarn(
        interaction.guild, interaction.user, member, number, interaction.channel_id
    )
    await slash_finish(interaction, error, embed)


@clients.tree.command(name="clearwarns", description="Clear all of a member's warnings (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(user="Whose warnings to clear")
async def slash_clearwarns(interaction: discord.Interaction, user: discord.User):
    problem = validate(interaction.guild, interaction.user)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, embed = await do_clearwarns(
        interaction.guild, interaction.user, user.id, interaction.channel_id
    )
    await slash_finish(interaction, error, embed)


clients.run("BOT_TOKEN")
