from datetime import date, datetime, timedelta, timezone

import discord

from config import COLOR_AFK, PREFIX, STREAK_TZ
from db import db
from helpers import is_mod


# --- Date helpers ---------------------------------------------------------
def today_local():
    return datetime.now(STREAK_TZ).date()


def pair_key(a, b):
    return (min(a, b), max(a, b))


def iso_week(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def streak_alive(last_day_str, today):
    if not last_day_str:
        return False
    return date.fromisoformat(last_day_str) >= today - timedelta(days=1)


# --- Database -------------------------------------------------------------
def has_sent(guild_id, sender_id, receiver_id, day):
    row = db.execute(
        "SELECT 1 FROM streak_sends WHERE guild_id = ? AND sender_id = ? AND receiver_id = ? AND day = ?",
        (guild_id, sender_id, receiver_id, day.isoformat()),
    ).fetchone()
    return row is not None


def record_send(guild_id, sender_id, receiver_id, day):
    db.execute(
        "INSERT OR IGNORE INTO streak_sends (guild_id, sender_id, receiver_id, day) VALUES (?, ?, ?, ?)",
        (guild_id, sender_id, receiver_id, day.isoformat()),
    )
    db.commit()


def get_pair(guild_id, a, b):
    low, high = pair_key(a, b)
    row = db.execute(
        "SELECT streak, last_day, grace_week FROM streak_pairs "
        "WHERE guild_id = ? AND user_low = ? AND user_high = ?",
        (guild_id, low, high),
    ).fetchone()
    return row if row else (0, None, None)


def save_pair(guild_id, a, b, streak, last_day, grace_week):
    low, high = pair_key(a, b)
    db.execute(
        "INSERT INTO streak_pairs (guild_id, user_low, user_high, streak, last_day, grace_week) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(guild_id, user_low, user_high) DO UPDATE SET "
        "streak = excluded.streak, last_day = excluded.last_day, grace_week = excluded.grace_week",
        (guild_id, low, high, streak, last_day, grace_week),
    )
    db.commit()


def get_user_streaks(guild_id, user_id):
    return db.execute(
        "SELECT user_low, user_high, streak, last_day FROM streak_pairs "
        "WHERE guild_id = ? AND (user_low = ? OR user_high = ?) AND streak > 0 "
        "ORDER BY streak DESC",
        (guild_id, user_id, user_id),
    ).fetchall()


# --- Streak rules ---------------------------------------------------------
def complete_streak(guild_id, a, b, today):
    """Runs when both sides have sent today. Returns (new_streak, used_grace_day, broke_from)."""
    streak, last_day_str, grace_week = get_pair(guild_id, a, b)
    last_day = date.fromisoformat(last_day_str) if last_day_str else None
    this_week = iso_week(today)
    used_grace = False
    broke_from = None

    if last_day == today:
        return streak, False, None

    if last_day is None:
        streak = 1
    elif last_day == today - timedelta(days=1):
        streak += 1
    elif last_day == today - timedelta(days=2) and grace_week != this_week:
        streak += 1
        grace_week = this_week
        used_grace = True
    else:
        if streak > 0:
            broke_from = (streak, last_day)
        streak = 1

    save_pair(guild_id, a, b, streak, today.isoformat(), grace_week)
    return streak, used_grace, broke_from


# --- Commands -------------------------------------------------------------
async def do_streak(guild, author, target, channel_id):
    """Returns (error, embed, ping_text)."""
    if guild is None:
        return "That only works inside a server.", None, None
    if target.bot:
        return "bots can't do streaks, bruh", None, None
    if target.id == author.id:
        return "you can't streak yourself lol", None, None

    today = today_local()
    if has_sent(guild.id, author.id, target.id, today):
        return (
            f"you already sent **{target.display_name}** their streak today. "
            f"come back after 12am PH time 🔥"
        ), None, None

    record_send(guild.id, author.id, target.id, today)

    if has_sent(guild.id, target.id, author.id, today):
        streak, used_grace, broke_from = complete_streak(guild.id, author.id, target.id, today)

        embed = discord.Embed(
            title="🔥 Streak kept alive",
            description=f"**{author.display_name}** and **{target.display_name}** both sent today.",
            color=COLOR_AFK,
        )
        embed.add_field(name="Streak", value=f"🔥 {streak} day{'' if streak == 1 else 's'}", inline=True)
        if used_grace:
            embed.add_field(name="Grace day", value="Used this week's grace day for the missed day.", inline=False)
        if broke_from:
            old_streak, old_day = broke_from
            embed.add_field(
                name="Streak reset",
                value=(
                    f"The old streak was **{old_streak}** day{'' if old_streak == 1 else 's'}. "
                    f"Last counted on **{old_day.strftime('%b %d')}**. "
                    f"It reset because a full day was missed and the grace day was already used this week."
                ),
                inline=False,
            )
        embed.set_footer(text="Streak days reset at 12am PH time")

        dm = discord.Embed(
            title="🔥 Streak kept alive",
            description=(
                f"Your streak with **{author.display_name}** in **{guild.name}** "
                f"is now **{streak}** day{'' if streak == 1 else 's'}."
            ),
            color=COLOR_AFK,
        )
        try:
            await target.send(embed=dm)
        except discord.HTTPException:
            pass
        return None, embed, None

    embed = discord.Embed(
        title="🔥 Streak sent",
        description=(
            f"**{author.display_name}** sent **{target.display_name}** their streak.\n"
            f"Waiting on **{target.display_name}** to send one back with `{PREFIX}streak @{author.name}`."
        ),
        color=COLOR_AFK,
    )
    embed.set_footer(text="Both of you need to send today (12am PH time reset)")

    dm = discord.Embed(
        title="🔥 Streak request",
        description=(
            f"**{author.display_name}** sent you their streak in **{guild.name}**.\n"
            f"Send one back with `{PREFIX}streak @{author.name}` today to keep it going."
        ),
        color=COLOR_AFK,
    )
    try:
        await target.send(embed=dm)
        ping = None
    except discord.HTTPException:
        ping = target.mention

    return None, embed, ping


async def do_streaks_board(guild, limit=10):
    if guild is None:
        return "That only works inside a server.", None

    today = today_local()
    rows = db.execute(
        "SELECT user_low, user_high, streak, last_day FROM streak_pairs "
        "WHERE guild_id = ? AND streak > 0 ORDER BY streak DESC",
        (guild.id,),
    ).fetchall()
    active = [(low, high, streak) for low, high, streak, last in rows if streak_alive(last, today)][:limit]

    if not active:
        return None, discord.Embed(
            title="🔥 Streak leaderboard",
            description="No active streaks yet. Start one with `?streak @user`",
            color=COLOR_AFK,
        )

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [
        f"{medals.get(i, f'**{i}.**')} <@{low}> + <@{high}> · 🔥 **{streak}** day{'' if streak == 1 else 's'}"
        for i, (low, high, streak) in enumerate(active, 1)
    ]
    embed = discord.Embed(title="🔥 Streak leaderboard", description="\n".join(lines), color=COLOR_AFK)
    embed.set_footer(text="Both members send each day · resets at 12am PH time · 1 grace day per pair per week")
    return None, embed


async def do_slist(guild, user):
    if guild is None:
        return "That only works inside a server.", None

    today = today_local()
    rows = get_user_streaks(guild.id, user.id)
    active = [(low, high, streak, last) for low, high, streak, last in rows if streak_alive(last, today)]

    if not active:
        return None, discord.Embed(
            title=f"🔥 {user.display_name}'s streaks",
            description="No active streaks yet. Start one with `?streak @user`",
            color=COLOR_AFK,
        )

    lines = []
    for low, high, streak, last in active:
        other = high if low == user.id else low
        if date.fromisoformat(last) == today:
            status = "✅ done for today"
        elif has_sent(guild.id, user.id, other, today):
            status = "⏳ you sent today, waiting on them"
        else:
            status = "⚠️ send one today to keep it going"
        lines.append(f"<@{other}> · 🔥 **{streak}** day{'' if streak == 1 else 's'}\n{status}")

    embed = discord.Embed(
        title=f"🔥 {user.display_name}'s streaks",
        description="\n\n".join(lines),
        color=COLOR_AFK,
    )
    embed.set_footer(text=f"{len(active)} active · resets at 12am PH time")
    return None, embed