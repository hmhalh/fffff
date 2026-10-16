import asyncio
import hashlib
import json
import os
import random
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
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

# تعريف الشخصيات مع الكلمات المفتاحية المرتبطة بأسمائها في تيليجرام
CHARACTERS = [
    {
        "id": "ali",
        "name": "علي",
        "model": "gpt-4o-mini",
        "api_key_env": "AI_DEFAULT_API_KEY",
        "enabled": True,
        "keywords": ["علي", "ali", "حسين", "محمد"],  # الكلمات اللي ممكن تظهر باسم الحساب ولد
        "gender": "male",
        "system_prompt": "أنت شخص اسمه علي، تحب الأنمي وتسولف بطريقة طبيعية وعراقية عفوية."
    },
    {
        "id": "zainab",
        "name": "زينب",
        "model": "gpt-4o-mini",
        "api_key_env": "AI_DEFAULT_API_KEY",
        "enabled": True,
        "keywords": ["زينب", "zainab", "فاطمة", "fatima", "مريم", "نور"],  # الكلمات اللي ممكن تظهر باسم الحساب بنت
        "gender": "female",
        "system_prompt": "أنتِ شخصية اسمها زينب، مهتمة بالنقاشات والمسلسلات وتتحدثين باللهجة العراقية."
    }
]

# كلاس تخزين داخلي خفيف ومباشر
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


def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


def get_character(char_id: str):
    return next((c for c in CHARACTERS if c["id"] == char_id and c.get("enabled", True)), None)


async def generate_reply(char, shared_memory: str, private_memory: str, recent: list[dict], topic: str) -> str:
    key = os.getenv(char.get("api_key_env", ""), DEFAULT_KEY)
    if not key:
        raise RuntimeError(f"مفتاح النموذج غير مضبوط للشخصية {char['name']}")
    client = AsyncOpenAI(api_key=key, base_url=AI_BASE_URL)
    messages = [
        {"role": "system", "content": char["system_prompt"] + "\n\nقواعد الحوار: احچي عراقي طبيعي، لا تدّعي أنك شخص حقيقي خارج إطار الشخصية، لا تكرر الرسائل السابقة، لا تستخدم مقدمات آلية، والرد يكون 1-4 جمل. إذا ما عندك إضافة مفيدة فقل سؤالاً قصيراً يحرّك النقاش."},
        {"role": "system", "content": f"الموضوع الحالي: {topic or 'سوالف عامة عن الأنمي والحياة اليومية'}\nذاكرتك الخاصة: {private_memory or 'ماكو معلومات خاصة بعد'}\nملخص الذاكرة المشتركة: {shared_memory or 'بداية الحوار'}"},
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


async def run_arena():
    turns = 0
    while not stop_event.is_set() and turns < MAX_TURNS:
        enabled = [c for c in CHARACTERS if c.get("enabled", True) and c["id"] in clients]
        if len(enabled) < 2:
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
    if not is_admin(message):
        return
    await message.answer(
        "أهلاً بلوحة AI Social Arena 🎭\n"
        "/status — الحالة\n/run — تشغيل جولة حوار\n/pause — إيقاف\n"
        "/topic موضوع — تغيير الموضوع\n/next id — تحديد الشخصية التالية\n"
        "/characters — عرض الشخصيات\n/help — المساعدة"
    )


@dp.message(Command("help"))
async def help_cmd(message: Message):
    if is_admin(message):
        await message.answer("الأوامر: /status /run /pause /topic موضوع /next id /characters")


@dp.message(Command("status"))
async def status_cmd(message: Message):
    if not is_admin(message):
        return
    state = "شغّال 🟢" if engine_task and not engine_task.done() else "متوقف 🔴"
    await message.answer(
        f"الحالة: {state}\nالمجموعة: {TARGET_CHAT}\n"
        f"الموضوع: {store.get_setting('topic', 'سوالف عامة')}\n"
        f"الفاصل: {DELAY} ثانية\nحد الجولة: {MAX_TURNS} رسالة"
    )


@dp.message(Command("characters"))
async def characters_cmd(message: Message):
    if not is_admin(message):
        return
    lines = [f"{'🟢' if c.get('enabled', True) else '⚪'} {c['id']} — {c['name']} ({c.get('model')})" for c in CHARACTERS]
    await message.answer("\n".join(lines))


@dp.message(Command("topic"))
async def topic_cmd(message: Message):
    if not is_admin(message):
        return
    topic = (message.text or "").partition(" ")[2].strip()
    if not topic:
        await message.answer("اكتب الموضوع هالشكل:\n/topic نقاش عن أفضل أنمي")
        return
    store.set_setting("topic", topic[:300])
    await message.answer(f"تم تعيين الموضوع: {topic[:300]}")


@dp.message(Command("next"))
async def next_cmd(message: Message):
    if not is_admin(message):
        return
    char_id = (message.text or "").partition(" ")[2].strip()
    char = get_character(char_id)
    if not char:
        await message.answer("ما لكيت شخصية مفعّلة بهذا الـ id. استخدم /characters")
        return
    store.set_setting("next_character", char_id)
    await message.answer(f"الدور الجاي للشخصية: {char['name']}")


@dp.message(Command("pause"))
async def pause_cmd(message: Message):
    global engine_task
    if not is_admin(message):
        return
    stop_event.set()
    if engine_task:
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass
    store.set_setting("running", "0")
    await message.answer("تم إيقاف الحوار فوراً ⏸️")


@dp.message(Command("run"))
async def run_cmd(message: Message):
    global engine_task, stop_event
    if not is_admin(message):
        return
    if not TARGET_CHAT:
        await message.answer("اضبط TARGET_CHAT داخل .env أولاً.")
        return
    if engine_task and not engine_task.done():
        await message.answer("الحوار شغّال حالياً.")
        return
    if len([c for c in CHARACTERS if c.get("enabled", True) and c["id"] in clients]) < 2:
        await message.answer("لازم على الأقل شخصيتين تكون جلساتها متصلة.")
        return
    stop_event = asyncio.Event()
    store.set_setting("running", "1")
    engine_task = asyncio.create_task(run_arena())
    await message.answer(f"بدأت جولة الحوار. الحد الأعلى {MAX_TURNS} رسالة، والفاصل {DELAY} ثانية.")


async def main():
    # البحث عن كل ملفات الجلسات الموجودة في المجلد
    session_files = list(BASE.glob("*.session"))
    print(f"تم العثور على ملفات الجلسات: {[f.name for f in session_files]}")

    matched_sessions = set()

    for char in CHARACTERS:
        if not char.get("enabled", True):
            continue

        assigned_client = None
        assigned_file = None

        # خطوة 1: فحص كل ملف جلسة والتحقق من اسم حساب تيليجرام المرتبط به
        for sf in session_files:
            if sf in matched_sessions:
                continue
            
            try:
                temp_client = TelegramClient(str(sf), API_ID, API_HASH)
                await temp_client.connect()
                if await temp_client.is_user_authorized():
                    me = await temp_client.get_me()
                    full_name = f"{me.first_name or ''} {me.last_name or ''}".lower()
                    
                    # التحقق من مطابقة اسم الحساب مع الكلمات المفتاحية للشخصية
                    matched_by_name = any(kw in full_name for kw in char["keywords"])
                    
                    # التحقق من وجود صورة شخصية (إذا لم يتطابق الاسم تماماً، نتحقق من وجود صورة كخيار إضافي)
                    has_photo = me.photo is not None
                    
                    if matched_by_name or (has_photo and char["id"] in sf.stem.lower()):
                        assigned_client = temp_client
                        assigned_file = sf
                        break
                await temp_client.disconnect()
            except Exception as e:
                print(f"خطأ أثناء فحص الملف {sf.name}: {e}")

        # إذا لم يجد تطابقاً دقيقاً بالاسم، يأخذ أول ملف متاح غير مستخدم
        if not assigned_client and session_files:
            for sf in session_files:
                if sf not in matched_sessions:
                    assigned_file = sf
                    assigned_client = TelegramClient(str(sf), API_ID, API_HASH)
                    break

        if assigned_client and assigned_file:
            matched_sessions.add(assigned_file)
            if not assigned_client.is_connected():
                await assigned_client.start()
            
            clients[char["id"]] = assigned_client
            me_info = await assigned_client.get_me()
            print(f"تم ربط الشخصية ({char['name']}) بحساب تيليجرام: {me_info.first_name} (ملف: {assigned_file.name})")
        else:
            print(f"تحذير: لم يتم العثور على حساب كافٍ للشخصية {char['name']}")

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
