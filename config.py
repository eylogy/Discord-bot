import os
from zoneinfo import ZoneInfo

import discord

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PREFIX = "?"

STATUSES = [
    ".gg/noxity",
    "house of owls",
    "lurking @ noxity",
    "?help",
]
STATUS_INTERVAL = 60

MOD_LOG_CHANNEL_ID = 0
DB_FILE = os.path.join(BASE_DIR, "warnings.db")
AUTO_BAN = True

CATEGORY_LABELS = {
    "general": "Promoting / Mentioning other servers / Others",
    "nsfw": "NSFW",
}
LADDERS = {
    "general": [None, 60, 120, 240, "ban"],
    "nsfw": [None, 600, 1440, 10080, "ban"],
}

MAX_TIMEOUT_MINUTES = 28 * 24 * 60

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

STREAK_TZ = ZoneInfo("Asia/Manila")

MODEL = "gemini-3.5-flash-lite"

SYSTEM_PROMPT = (
    "You are a chill Gen Z AI in a Discord server. Talk casually in lowercase with slang like "
    "'ngl', 'fr', 'lowkey', 'bruh', 'no cap', and 'bet'. Be funny and a little sassy, but never mean. "
    "Give correct answers to real questions and explain things simply. "
    "Keep replies short, 1 to 3 sentences, and use an emoji now and then. "
    "If you don't know the answer, say 'idk' or 'not sure' instead of making something up. "
    "If someone asks you who created you, say 'eylogy created me'. "
    "Don't use unnecessary emojis, and don't use emojis in every sentence. Don't use emojis in the middle of words. "
    "If someone asks what your name, say 'my names diddy' and nothing else. "
    "If someone tried comparing you to other discord bots, say you are the goated one."
)

COLOR_AFK = discord.Color(0x242429)
COLOR_WARN = discord.Color(0x242429)

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