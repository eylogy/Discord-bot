import asyncio
import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    COLOR_AFK, MODEL, PREFIX, REACTIONS, STATUS_INTERVAL, STATUSES,
    SYSTEM_PROMPT, CATEGORY_LABELS,
)
from db import db  # noqa: F401  (opens the shared database)
from helpers import (
    DURATION_HELP, USER_ID_RE, can_manage_roles, find_member, find_role,
    parse_duration, parse_user_id, time_ago,
)
import warnings_system as ws
import streaks_system as ss

load_dotenv()

ai = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
chats = {}
afk = {}


async def get_summary(channel):
    lines = []
    try:
        async for msg in channel.history(limit=50):
            if msg.author.bot or not msg.content.strip() or msg.content.startswith(PREFIX):
                continue
            lines.append(f"{msg.author.display_name}: {msg.content}")
    except discord.Forbidden:
        return "I don't have permission to read this channel's history."

    if not lines:
        return "There's nothing to summarize here yet."

    lines.reverse()
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


async def get_display_name(guild, user_id):
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


async def reply_result(message, error, result):
    no_mentions = discord.AllowedMentions.none()
    if error:
        await message.reply(error, allowed_mentions=no_mentions)
    elif isinstance(result, str):
        await message.reply(result, allowed_mentions=no_mentions)
    else:
        await message.reply(embed=result, allowed_mentions=no_mentions)


def _help_embed(bot_user, description):
    embed = discord.Embed(description=description, color=COLOR_AFK)
    if bot_user is not None:
        embed.set_author(name=bot_user.name, icon_url=bot_user.display_avatar.url)
    embed.set_footer(text="discord.gg/noxity")
    return embed


def build_help_main_embed(bot_user):
    return _help_embed(bot_user, "Click any button to view.")


def build_help_commands_embed(bot_user):
    description = (
        "__**Commands:**__\n"
        f"**{PREFIX}help** - Shows this help message.\n"
        f"**{PREFIX}afk <reason>** - Marks you as away. The bot tells anyone who pings you.\n"
        f"**{PREFIX}summarize** - Summarizes the last 50 messages in the channel.\n"
        f"**{PREFIX}reset** - Clears my memory in this channel.\n"
        f"**{PREFIX}warns [@user]** - Shows your warnings. Mods can check anyone.\n"
        f"**{PREFIX}streak @user** - Sends your daily streak. Both of you need to send each day (resets 12am PH time).\n"
        f"**{PREFIX}streaks** - Shows the top active streaks in the server.\n"
        f"**{PREFIX}slist** - Shows all of your active streaks and who still needs to send today.\n"
        "\n"
        "__**Chat:**__\n"
        "**@mention** - Mention me with a question and i'll answer.\n"
    )
    return _help_embed(bot_user, description)


def build_help_moderation_embed(bot_user):
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
    def __init__(self, bot_user):
        super().__init__(timeout=300)
        self.bot_user = bot_user

    @discord.ui.button(label="COMMANDS", style=discord.ButtonStyle.secondary)
    async def commands_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_help_commands_embed(self.bot_user), view=self)

    @discord.ui.button(label="MODERATIONS", style=discord.ButtonStyle.secondary)
    async def moderation_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=build_help_moderation_embed(self.bot_user), view=self)


class Client(discord.Client):
    def __init__(self, *, intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        asyncio.create_task(self.rotate_status())

    async def rotate_status(self):
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
        print(f"Logged in as {self.user}")

    async def on_message(self, message):
        if message.author.bot:
            return

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

        if command == f"{PREFIX}afk":
            pieces = message.content.strip().split(maxsplit=1)
            reason = pieces[1][:100] if len(pieces) > 1 else "AFK"
            afk[message.author.id] = (reason, datetime.now(timezone.utc))
            embed = discord.Embed(description=f"You are now AFK: `{reason}`", color=COLOR_AFK)
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

        if command == f"{PREFIX}streak":
            await prefix_streak(message)
            return
        if command == f"{PREFIX}streaks":
            error, embed = await ss.do_streaks_board(message.guild)
            await reply_result(message, error, embed)
            return
        if command == f"{PREFIX}slist":
            error, embed = await ss.do_slist(message.guild, message.author)
            await reply_result(message, error, embed)
            return

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

        if self.user not in message.mentions:
            return

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
                config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT, max_output_tokens=800),
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


# --- Prefix handlers ------------------------------------------------------
async def prefix_warn(message):
    guild = message.guild
    problem = ws.validate(guild, message.author)
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

    rest = args[2].strip() if len(args) > 2 else ""
    category = "general"
    first, _, remainder = rest.partition(" ")
    if first.lower() in CATEGORY_LABELS:
        category = first.lower()
        rest = remainder.strip()
    reason = rest[:300] or "No reason given"

    error, result = await ws.do_warn(guild, message.author, target, category, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_to(message):
    guild = message.guild
    problem = ws.validate(guild, message.author)
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
    error, result = await ws.do_timeout(guild, message.author, target, minutes, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_rto(message):
    guild = message.guild
    problem = ws.validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}rto @user`")
        return

    error, result = await ws.do_untimeout(guild, message.author, target, message.channel.id)
    await reply_result(message, error, result)


async def prefix_kick(message):
    guild = message.guild
    problem = ws.validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}kick @user [reason]`")
        return

    reason = args[2].strip()[:300] if len(args) > 2 else "No reason given"
    error, result = await ws.do_kick(guild, message.author, target, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_ban(message):
    guild = message.guild
    problem = ws.validate(guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}ban @user [reason]`")
        return

    reason = args[2].strip()[:300] if len(args) > 2 else "No reason given"
    error, result = await ws.do_ban(guild, message.author, target, reason, message.channel.id)
    await reply_result(message, error, result)


async def prefix_unban(message):
    problem = ws.validate(message.guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    user_id = parse_user_id(args[1]) if len(args) > 1 else None
    if user_id is None:
        await message.reply(f"Usage: `{PREFIX}unban <user id>` (right-click the user's ID to copy it)")
        return

    error, result = await ws.do_unban(message.guild, message.author, user_id, message.channel.id)
    await reply_result(message, error, result)


async def prefix_role(message):
    problem = ws.validate_role(message.guild, message.author, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split(maxsplit=2)
    target = await find_member(message.guild, args[1]) if len(args) > 1 else None
    role = find_role(message.guild, args[2]) if len(args) > 2 else None
    if target is None or role is None:
        await message.reply(f"Usage: `{PREFIX}role @user <role name>` (a mention, ID, or name works)")
        return

    error, text = await ws.do_role(message.guild, message.author, target, role, message.channel.id)
    if error:
        await message.reply(error)
        return
    await message.reply(
        text,
        allowed_mentions=discord.AllowedMentions(users=[target], roles=False, everyone=False, replied_user=False),
    )


async def prefix_warns(message):
    args = message.content.strip().split()
    if len(args) > 1:
        found = USER_ID_RE.search(args[1])
        if not found:
            await message.reply(f"Usage: `{PREFIX}warns [@user]`")
            return
        user_id = int(found.group())
    else:
        user_id = message.author.id

    error, result = await ws.do_warnings(message.guild, message.author, user_id, get_display_name)
    await reply_result(message, error, result)


async def prefix_delwarn(message):
    problem = ws.validate(message.guild, message.author)
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

    error, result = await ws.do_delwarn(message.guild, message.author, target, number, message.channel.id)
    await reply_result(message, error, result)


async def prefix_clearwarns(message):
    problem = ws.validate(message.guild, message.author)
    if problem:
        await message.reply(problem)
        return

    args = message.content.strip().split()
    found = USER_ID_RE.search(args[1]) if len(args) > 1 else None
    if not found:
        await message.reply(f"Usage: `{PREFIX}clearwarns @user`")
        return

    error, result = await ws.do_clearwarns(message.guild, message.author, int(found.group()), message.channel.id)
    await reply_result(message, error, result)


async def prefix_streak(message):
    args = message.content.strip().split()
    target = await find_member(message.guild, args[1]) if len(args) > 1 else None
    if target is None:
        await message.reply(f"Usage: `{PREFIX}streak @user`")
        return

    error, embed, ping = await ss.do_streak(message.guild, message.author, target, message.channel.id)
    if error:
        await message.reply(error, allowed_mentions=discord.AllowedMentions.none())
        return
    await message.reply(
        content=ping,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=[target], roles=False, everyone=False, replied_user=False),
    )


intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

clients = Client(intents=intents)


# --- Slash commands -------------------------------------------------------
async def slash_finish(interaction, error, result):
    no_mentions = discord.AllowedMentions.none()
    if error:
        await interaction.followup.send(error, allowed_mentions=no_mentions)
    elif isinstance(result, str):
        await interaction.followup.send(result, allowed_mentions=no_mentions)
    else:
        await interaction.followup.send(embed=result)


RULE_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in CATEGORY_LABELS.items()]


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
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    category = rule.value if rule else "general"
    error, result = await ws.do_warn(
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
async def slash_to(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason given"):
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    minutes = parse_duration(duration)
    if minutes is None:
        await interaction.response.send_message(DURATION_HELP, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await ws.do_timeout(
        interaction.guild, interaction.user, member, minutes, reason[:300], interaction.channel_id
    )
    await slash_finish(interaction, error, result)


@clients.tree.command(name="rto", description="Remove a member's timeout (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="Whose timeout to remove")
async def slash_rto(interaction: discord.Interaction, member: discord.Member):
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await ws.do_untimeout(interaction.guild, interaction.user, member, interaction.channel_id)
    await slash_finish(interaction, error, result)


@clients.tree.command(name="kick", description="Kick a member from the server (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="Who to kick", reason="Why they're being kicked")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await ws.do_kick(interaction.guild, interaction.user, member, reason[:300], interaction.channel_id)
    await slash_finish(interaction, error, result)


@clients.tree.command(name="ban", description="Ban a member from the server (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="Who to ban", reason="Why they're being banned")
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given"):
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await ws.do_ban(interaction.guild, interaction.user, member, reason[:300], interaction.channel_id)
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
    if not ws.is_mod(interaction.user):
        await interaction.response.send_message("Only **mods** and **admins** can use that.", ephemeral=True)
        return
    await interaction.response.defer()
    error, result = await ws.do_unban(interaction.guild, interaction.user, parsed, interaction.channel_id)
    await slash_finish(interaction, error, result)


@clients.tree.command(name="role", description="Give or remove a role from a member (Manage Roles only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
@app_commands.describe(member="Who to give or remove the role from", role="The role name to toggle")
async def slash_role(interaction: discord.Interaction, member: discord.Member, role: str):
    problem = ws.validate_role(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    found_role = find_role(interaction.guild, role)
    if found_role is None:
        await interaction.response.send_message(f"I couldn't find a role matching `{role}`.", ephemeral=True)
        return
    await interaction.response.defer()
    error, text = await ws.do_role(interaction.guild, interaction.user, member, found_role, interaction.channel_id)
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


@clients.tree.command(name="streak", description="Send your daily streak to a member")
@app_commands.guild_only()
@app_commands.describe(user="The member you want to streak with")
async def slash_streak(interaction: discord.Interaction, user: discord.Member):
    error, embed, ping = await ss.do_streak(interaction.guild, interaction.user, user, interaction.channel_id)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return
    await interaction.response.send_message(
        content=ping,
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=[user], roles=False, everyone=False),
    )


@clients.tree.command(name="streaks", description="Show the top active streaks in this server")
@app_commands.guild_only()
async def slash_streaks(interaction: discord.Interaction):
    error, embed = await ss.do_streaks_board(interaction.guild)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)


@clients.tree.command(name="slist", description="Show all of your active streaks")
@app_commands.guild_only()
async def slash_slist(interaction: discord.Interaction):
    error, embed = await ss.do_slist(interaction.guild, interaction.user)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)


@clients.tree.command(name="warns", description="Show warnings (yours, or anyone's if you're a mod)")
@app_commands.guild_only()
@app_commands.describe(user="Whose warnings to show (leave empty for yours)")
async def slash_warns(interaction: discord.Interaction, user: discord.User = None):
    target_id = user.id if user else interaction.user.id
    error, embed = await ws.do_warnings(interaction.guild, interaction.user, target_id, get_display_name)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


@clients.tree.command(name="delwarn", description="Remove one of a member's warnings by its number (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(member="Whose warning to remove", number="The warning's number, as shown in /warns")
async def slash_delwarn(interaction: discord.Interaction, member: discord.Member, number: int):
    problem = ws.validate(interaction.guild, interaction.user, member)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, embed = await ws.do_delwarn(interaction.guild, interaction.user, member, number, interaction.channel_id)
    await slash_finish(interaction, error, embed)


@clients.tree.command(name="clearwarns", description="Clear all of a member's warnings (mods/admins only)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(user="Whose warnings to clear")
async def slash_clearwarns(interaction: discord.Interaction, user: discord.User):
    problem = ws.validate(interaction.guild, interaction.user)
    if problem:
        await interaction.response.send_message(problem, ephemeral=True)
        return
    await interaction.response.defer()
    error, embed = await ws.do_clearwarns(interaction.guild, interaction.user, user.id, interaction.channel_id)
    await slash_finish(interaction, error, embed)


clients.run(os.getenv("DISCORD_TOKEN"))