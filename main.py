import asyncio
import hashlib
import json
import os
import random
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from dotenv import load_dotenv
from openai import AsyncOpenAI
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

load_dotenv()
BASE = Path(__file__).parent
CONTROL_TOKEN = os.getenv("CONTROL_BOT_TOKEN", "")
API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TARGET_CHAT = os.getenv("TARGET_CHAT", "")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
DELAY = max(10, int(os.getenv("TURN_DELAY_SECONDS", "25")))
MAX_TURNS = max(1, int(os.getenv("MAX_TURNS_PER_RUN", "20")))
MAX_REPLY_CHARS = max(100, int(os.getenv("MAX_REPLY_CHARS", "900")))
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
DEFAULT_KEY = os.getenv("AI_DEFAULT_API_KEY", "")

if not CONTROL_TOKEN:
    raise SystemExit("ضع CONTROL_BOT_TOKEN داخل .env")
if not API_ID or not API_HASH:
    raise SystemExit("ضع TELEGRAM_API_ID و TELEGRAM_API_HASH داخل .env")
if not ADMIN_IDS:
    raise SystemExit("ضع ADMIN_IDS داخل .env")

CHARACTERS = [
    {
        "id": "ali",
        "name": "علي",
        "model": "gpt-4o-mini",
        "api_key_env": "AI_DEFAULT_API_KEY",
        "enabled": True,
        "keywords": ["علي", "ali", "حسين", "محمد"],
        "system_prompt": "أنت شخص اسمه علي، تحب الأنمي وتسولف بطريقة طبيعية وعراقية عفوية."
    },
    {
        "id": "zainab",
        "name": "زينب",
        "model": "gpt-4o-mini",
        "api_key_env": "AI_DEFAULT_API_KEY",
        "enabled": True,
        "keywords": ["زينب", "zainab", "فاطمة", "fatima", "مريم", "نور"],
        "system_prompt": "أنتِ شخصية اسمها زينب، مهتمة بالنقاشات والمسلسلات وتتحدثين باللهجة العراقية."
    }
]

class SimpleStore:
    def __init__(self):
        self.settings = {"topic": "سوالف عامة عن الأنمي والحياة اليومية", "running": "0", "next_character": ""}
        self.messages = []
        self.memories = {"shared": ""}

    def get_setting(self, key, default=""):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_memory(self, key):
        return self.memories.get(key, "")

    def append_memory(self, key, text):
        if key not in self.memories:
            self.memories[key] = ""
        self.memories[key] += "\n" + text

    def add_message(self, speaker_id, speaker_name, text):
        self.messages.append({"speaker_id": speaker_id, "speaker_name": speaker_name, "text": text})

    def recent_messages(self, limit=12):
        return self.messages[-limit:]

    def is_duplicate(self, text):
        return any(m["text"] == text for m in self.messages[-5:])

store = SimpleStore()
bot = Bot(CONTROL_TOKEN)
dp = Dispatcher()
clients: dict[str, TelegramClient] = {}
engine_task: asyncio.Task | None = None
stop_event = asyncio.Event()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_character(char_id: str):
    return next((c for c in CHARACTERS if c["id"] == char_id and c.get("enabled", True)), None)

# لوحة الأزرار الرئيسية
def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="تشغيل الحوار 🟢", callback_data="btn_run"),
            InlineKeyboardButton(text="إيقاف الحوار ⏸️", callback_data="btn_pause")
        ],
        [
            InlineKeyboardButton(text="الحالة 📊", callback_data="btn_status"),
            InlineKeyboardButton(text="الشخصيات 👥", callback_data="btn_chars")
        ]
    ])

async def generate_reply(char, shared_memory: str, private_memory: str, recent: list[dict], topic: str) -> str:
    key = os.getenv(char.get("api_key_env", ""), DEFAULT_KEY)
    if not key:
        raise RuntimeError(f"مفتاح النموذج غير مضبوط للشخصية {char['name']}")
    client = AsyncOpenAI(api_key=key, base_url=AI_BASE_URL)
    messages = [
        {"role": "system", "content": char["system_prompt"] + "\n\nقواعد الحوار: احچي عراقي طبيعي، لا تدّعي أنك شخص حقيقي خارج إطار الشخصية، لا تكرر الرسائل السابقة، لا تستخدم مقدمات آلية، والرد يكون 1-4 جمل. إذا ما عندك إضافة مفيدة فقل سؤالاً قصيراً يحرّك النقاش."},
        {"role": "system", "content": f"الموضوع الحالي: {topic or 'سوالف عامة عن الأنمي والحياة اليومية'}\nذاكرتك الخاصة: {private_memory or '
