from datetime import datetime, timedelta, timezone

import discord

from config import (
    AUTO_BAN, CATEGORY_LABELS, COLOR_AFK, COLOR_WARN, LADDERS,
    MAX_TIMEOUT_MINUTES, PREFIX,
)
from db import db
from helpers import (
    format_duration, is_mod, is_protected_role, ordinal_word,
    can_manage_roles, send_mod_log, try_dm,
)


# --- Database -------------------------------------------------------------
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
    db.execute("DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warn_id, guild_id))
    db.commit()


def clear_warnings(guild_id, user_id):
    cur = db.execute(
        "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
    )
    db.commit()
    return cur.rowcount


# --- Permission checks ----------------------------------------------------
def ladder_step(category, offense):
    ladder = LADDERS[category]
    return ladder[min(offense, len(ladder)) - 1]


def step_label(step):
    if step is None:
        return "Reminder (no action)"
    if step == "ban":
        return "Ban"
    return f"{format_duration(step)} timeout"


def validate(guild, author, target=None):
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
    if guild is None:
        return "That only works inside a server."
    if not can_manage_roles(author):
        return "Only members with **Manage Roles** can use that."
    if target.bot:
        return "You can't change roles on a bot."
    return None


def role_above_problem(guild, author, role):
    if author.id == guild.owner_id:
        return None
    if role >= author.top_role:
        return (
            f"The role '{role.name}' (pos {role.position}) is above your highest role "
            f"'{author.top_role.name}' (pos {author.top_role.position}) and cannot be handed "
            f"out by you normally. Ask a higher-up mod to be dragged up."
        )
    return None


# --- Actions --------------------------------------------------------------
async def do_warn(guild, author, target, category, reason, channel_id):
    problem = validate(guild, author, target)
    if problem:
        return problem, None

    add_warning(guild.id, target.id, author.id, reason, category)
    rows = get_warnings(guild.id, target.id)
    offense = sum(1 for row in rows if row[4] == category)
    total = len(rows)
    ladder = LADDERS[category]
    step = ladder_step(category, offense)

    dm = discord.Embed(title=f"You were warned in {guild.name}", color=COLOR_WARN)
    dm.add_field(name="Rule", value=CATEGORY_LABELS[category], inline=False)
    dm.add_field(name="Reason", value=reason, inline=False)
    dm.add_field(name="Offense", value=f"{min(offense, len(ladder))} of {len(ladder)}")
    dm.add_field(name="Consequence", value=step_label(step))

    extra = ""
    if step == "ban":
        await try_dm(target, dm)
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
        await target.timeout(timedelta(minutes=minutes), reason=f"{reason} (by {author})")
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
    problem = validate_role(guild, author, target)
    if problem:
        return problem, None
    if role.is_default() or role.managed:
        return "I can't give or remove that role.", None

    problem = role_above_problem(guild, author, role)
    if problem:
        return problem, None

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


async def do_warnings(guild, author, user_id, get_name):
    if guild is None:
        return "That only works inside a server.", None
    if user_id != author.id and not is_mod(author):
        return "You can only view your own warnings.", None

    rows = get_warnings(guild.id, user_id)
    name = await get_name(guild, user_id)

    if not rows:
        return None, discord.Embed(description=f"**{name}** has no warnings.", color=COLOR_AFK)

    lines = []
    start = max(0, len(rows) - 10)
    for number in range(start, len(rows)):
        _, mod_id, reason, created_at, category = rows[number]
        when = discord.utils.format_dt(datetime.fromisoformat(created_at), "R")
        tag = "NSFW" if category == "nsfw" else "General"
        lines.append(f"**{number + 1}.** `{tag}` - {reason[:150]} - by <@{mod_id}> {when}")
    embed = discord.Embed(title=f"⚠️ Warnings for {name}", description="\n".join(lines), color=COLOR_WARN)
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

    delete_warning(guild.id, rows[number - 1][0])
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