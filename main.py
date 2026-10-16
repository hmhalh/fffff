import asyncio
import os
import random
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from openai import AsyncOpenAI
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError

BASE = Path(__file__).parent

# ==================== معلوماتك الشخصية ====================
CONTROL_TOKEN = "7838301513:AAEq39qhnjhmWwBwPh-c2_VIRTiJV06Qvn8"
API_ID = 20400000  # استبدلها برقم الـ API ID الحقيقي إذا لزم الأمر
API_HASH = "ضع_الـ_API_HASH_هنا"
TARGET_CHAT = -1004309527352
ADMIN_IDS = {7199778669}

DEFAULT_KEY = "AQ.Ab8RN6K2LYTWDkLsT-Ll-pRmutAU7DaE-nNYbR9pV6q92LsKsg"
AI_BASE_URL = "https://api.openai.com/v1"

DELAY = 25
MAX_TURNS = 20
MAX_REPLY_CHARS = 900
# ==========================================================

if not CONTROL_TOKEN:
    raise SystemExit("يرجى وضع توكن بوت التحكم في الكود!")
if not ADMIN_IDS:
    raise SystemExit("يرجى وضع ADMIN_IDS في الكود!")

CHARACTERS = [
    {
        "id": "ali",
        "name": "علي",
        "model": "gpt-4o-mini",
        "api_key_env": DEFAULT_KEY,
        "enabled": True,
        "session": "ali.session",
        "system_prompt": "أنت شخص اسمه علي، تحب الأنمي وتسولف بطريقة طبيعية وعراقية عفوية."
    },
    {
        "id": "zainab",
        "name": "زينب",
        "model": "gpt-4o-mini",
        "api_key_env": DEFAULT_KEY,
        "enabled": True,
        "session": "zainab.session",
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
    key = DEFAULT_KEY
    client = AsyncOpenAI(api_key=key, base_url=AI_BASE_URL)
    
    current_topic = topic if topic else 'سوالف عامة عن الأنمي والحياة اليومية'
    priv_mem = private_memory if private_memory else 'ماكو معلومات خاصة بعد'
    shared_mem = shared_memory if shared_memory else 'بداية الحوار'
    
    messages = [
        {"role": "system", "content": char["system_prompt"] + "\n\nقواعد الحوار: احچي عراقي طبيعي، لا تدّعي أنك شخص حقيقي خارج إطار الشخصية، لا تكرر الرسائل السابقة، لا تستخدم مقدمات آلية، والرد يكون 1-4 جمل. إذا ما عندك إضافة مفيدة فقل سؤالاً قصيراً يحرّك النقاش."},
        {"role": "system", "content": f"الموضوع الحالي: {current_topic}\nذاكرتك الخاصة: {priv_mem}\nملخص الذاكرة المشتركة: {shared_mem}"},
    ]
    for item in recent[-12:]:
        messages.append({"role": "assistant" if item["speaker_id"] == char["id"] else "user",
                         "content": f"{item['speaker_name']}: {item['text']}"})
    messages.append({"role": "user", "content": f"اكتب رد الشخصية {char['name']} المناسب للدور الحالي فقط. لا تكتب اسمك كبادئة."})
    try:
        result = await client.chat.completions.create(
            model=char.get("model", "gpt-4o-mini"),
            messages=messages,
            temperature=0.85,
            max_tokens=220,
        )
        text = (result.choices[0].message.content or "").strip()
        text = re.sub(r"^(علي|زينب|مصطفى)\s*[:：-]\s*", "", text)
        return text[:MAX_REPLY_CHARS].strip()
    finally:
        await client.close()

async def connect_clients():
    """محاولة ربط الحسابات إذا كانت ملفات الجلسات موجودة"""
    for char in CHARACTERS:
        if not char.get("enabled", True):
            continue
        if char["id"] in clients:
            continue
        
        session_path = BASE / char["session"]
        if session_path.exists():
            try:
                client = TelegramClient(str(session_path), API_ID, API_HASH)
                await client.connect()
                if await client.is_user_authorized():
                    clients[char["id"]] = client
                    print(f"تم ربط الشخصية بنجاح: {char['name']}")
                else:
                    print(فأرغ("جلسة غير صالحة لـ", char['name']))
            except Exception as e:
                print(f"خطأ في الاتصال للشخصية {char['name']}: {e}")

async def run_arena():
    turns = 0
    while not stop_event.is_set() and turns < MAX_TURNS:
        await connect_clients()
        enabled = [c for c in CHARACTERS if c.get("enabled", True) and c["id"] in clients]
        if len(enabled) < 2:
            print("بانتظار توفر ملفات الجلسات للحسابات...")
            await asyncio.sleep(5)
            continue
            
        next_id = store.get_setting("next_character", "") or ""
        char = get_character(next_id) if next_id else None
        if not char or char["id"] not in clients:
            char = random.choice(enabled)
        store.set_setting("next_character", "")
        try:
            recent = store.recent_messages(12)
            shared = store.get_memory("shared")
            private = store.get_memory(char["id"])
            topic = store.get_setting("topic", "")
            reply = await generate_reply(char, shared, private, recent, topic)
            if not reply or store.is_duplicate(reply):
                await asyncio.sleep(2)
                continue
            await clients[char["id"]].send_message(TARGET_CHAT, reply)
            store.add_message(char["id"], char["name"], reply)
            store.append_memory(char["id"], f"آخر مساهمة: {reply}")
            store.append_memory("shared", f"{char['name']}: {reply}")
            turns += 1
            await asyncio.sleep(DELAY)
        except FloodWaitError as e:
            await asyncio.sleep(min(int(e.seconds) + 2, 3600))
        except (RPCError, RuntimeError, Exception) as e:
            print(f"[arena] {char.get('name', '?')}: {type(e).__name__}: {e}")
            await asyncio.sleep(max(DELAY, 30))
    store.set_setting("running", "0")

@dp.message(Command("start"))
async def start_cmd(message: Message):
    if not message.from_user or not is_admin(message.from_user.id):
        return
    await message.answer(
        "أهلاً بك في لوحة تحكم AI Social Arena 🎭\nاختر أحد الخيارات أدناه للتحكم بالحوار:",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(F.data.startswith("btn_"))
async def callback_handler(callback: CallbackQuery):
    if not callback.from_user or not is_admin(callback.from_user.id):
        await callback.answer("عذراً، هذا البوت مخصص للمشرفين فقط.", show_alert=True)
        return

    action = callback.data
    global engine_task, stop_event

    if action == "btn_run":
        if not TARGET_CHAT:
            await callback.answer("يرجى ضبط TARGET_CHAT في الكود أولاً.", show_alert=True)
            return
        if engine_task and not engine_task.done():
            await callback.answer("الحوار يعمل بالفعل 🟢", show_alert=True)
            return
            
        stop_event = asyncio.Event()
        store.set_setting("running", "1")
        engine_task = asyncio.create_task(run_arena())
        await callback.message.edit_text("بدأت جولة الحوار (سيبدأ النشر فور توفر الجلسات) 🟢", reply_markup=get_main_keyboard())
        await callback.answer("تم التشغيل")

    elif action == "btn_pause":
        stop_event.set()
        if engine_task:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass
        store.set_setting("running", "0")
        await callback.message.edit_text("تم إيقاف الحوار مؤقتاً ⏸️", reply_markup=get_main_keyboard())
        await callback.answer("تم الإيقاف")

    elif action == "btn_status":
        await connect_clients()
        connected_count = len(clients)
        state = "شغّال 🟢" if engine_task and not engine_task.done() else "متوقف 🔴"
        text = (
            f"📊 **حالة النظام:**\n"
            f"• الحالة: {state}\n"
            f"• الجلسات المتصلة: {connected_count} من {len(CHARACTERS)}\n"
            f"• المجموعة: {TARGET_CHAT}\n"
            f"• الموضوع: {store.get_setting('topic', 'سوالف عامة')}\n"
            f"• الفاصل الزمني: {DELAY} ثانية"
        )
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        await callback.answer()

    elif action == "btn_chars":
        lines = [f"• {'🟢' if c['id'] in clients else '⚪'} {c['name']} ({c['id']})" for c in CHARACTERS]
        text = "👥 **الشخصيات وحالة جلساتها:**\n" + "\n".join(lines) + "\n\n(⚪ تعني لم يتم العثور على ملف الجلسة بعد)"
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        await callback.answer()

async def main():
    print("تم بدء تشغيل بوت التحكم بنجاح، بانتظار أوامرك وملفات الجلسات...")
    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        if engine_task:
            engine_task.cancel()
        for client in clients.values():
            await client.disconnect()
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
