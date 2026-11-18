import re
from datetime import datetime, timezone

import discord

from config import PROTECTED_PERMS

USER_ID_RE = re.compile(r"\d{15,20}")
DURATION_RE = re.compile(r"^(\d{1,6})\s*([a-z]+)$", re.IGNORECASE)
UNIT_MINUTES = {
    "m": 1, "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "d": 1440, "day": 1440, "days": 1440,
    "w": 10080, "wk": 10080, "wks": 10080, "week": 10080, "weeks": 10080,
    "mo": 43200, "month": 43200, "months": 43200,
}
DURATION_HELP = "Time examples: `10m` (minutes), `2h` (hours), `3d` (days), `1w` (weeks)"
ORDINAL_WORDS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
}


def ordinal_word(n):
    if n in ORDINAL_WORDS:
        return ORDINAL_WORDS[n]
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def is_mod(member):
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_messages))


def can_manage_roles(member):
    perms = getattr(member, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_roles))


def is_protected_role(role):
    return any(getattr(role.permissions, perm, False) for perm in PROTECTED_PERMS)


def time_ago(since):
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
    found = DURATION_RE.match(text.strip())
    if not found:
        return None
    unit = UNIT_MINUTES.get(found.group(2).lower())
    amount = int(found.group(1))
    if unit is None or amount <= 0:
        return None
    return amount * unit


def format_duration(total_minutes):
    parts = []
    for name, size in (("week", 10080), ("day", 1440), ("hour", 60), ("minute", 1)):
        count, total_minutes = divmod(total_minutes, size)
        if count:
            parts.append(f"{count} {name}{'' if count == 1 else 's'}")
    return " ".join(parts) or "0 minutes"


def parse_user_id(text):
    text = text.strip()
    return int(text) if re.fullmatch(r"\d{15,20}", text) else None


def find_role(guild, text):
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


async def find_member(guild, text):
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


async def send_mod_log(guild, content, current_channel_id):
    from config import MOD_LOG_CHANNEL_ID

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


async def try_dm(user, embed):
    try:
        await user.send(embed=embed)
    except discord.HTTPException:
        pass