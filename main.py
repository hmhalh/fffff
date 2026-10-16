import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events
from google import genai

# ==================================================
# إعدادات أساسية
# ==================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

# بيانات تليغرام من متغيرات البيئة
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# مجلدات التخزين
BASE_DIR = Path(os.getenv("DATA_DIR", "/app/storage"))
CHARACTERS_DIR = BASE_DIR / "characters"
SESSIONS_DIR = BASE_DIR / "sessions"
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

# إنشاء المجلدات إن لم تكن موجودة
CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "running": True,
    "gemini_api_key": "",
    "active_character": "",
    "target_groups": [],
}

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with SETTINGS_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for key, value in DEFAULT_SETTINGS.items():
                data.setdefault(key, value)
            return data
        except (OSError, json.JSONDecodeError):
            log.exception("تعذرت قراءة الإعدادات")
    return DEFAULT_SETTINGS.copy()

settings = load_settings()
if GEMINI_API_KEY:
    settings["gemini_api_key"] = GEMINI_API_KEY


# ==================================================
# إدارة ملفات الشخصيات والسلوكيات
# ==================================================

def character_files():
    return sorted(CHARACTERS_DIR.glob("*.json"), key=lambda p: p.name.casefold())

def read_character(path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

def write_character(path, character):
    temp_file = path.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(character, f, ensure_ascii=False, indent=2)
    temp_file.replace(path)

def unique_character_path(name):
    """تجنب تكرار أسماء الشخصيات (مثلاً: زهراء، زهراء 2)"""
    name = " ".join(name.strip().split()).replace("/", "_").replace("\\", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch in " _-").strip(" .")
    if not name:
        name = "شخصية جديدة"

    candidate = CHARACTERS_DIR / f"{name}.json"
    number = 2
    while candidate.exists():
        candidate = CHARACTERS_DIR / f"{name} {number}.json"
        number += 1
    return candidate

def create_character(name, personality="ودودة، مرحة، وتتكلم باللهجة العراقية الطبيعية."):
    path = unique_character_path(name)
    actual_name = path.stem
    character = {
        "name": actual_name,
        "personality": personality,
        "speaking_style": "لهجة عراقية بسيطة كالأشخاص الحقيقيين.",
        "mood": "متوازنة وهادئة",
        "messages": [],
    }
    write_character(path, character)
    return path, character

def find_character(name):
    for path in character_files():
        if path.stem == name:
            return path, read_character(path)
    return None, None

def add_message(character, role, text):
    character.setdefault("messages", []).append({
        "role": role,
        "text": text[:4000],
        "time": datetime.now(timezone.utc).isoformat(),
    })
    character["messages"] = character["messages"][-100:]


# ==================================================
# الذكاء الاصطناعي (Gemini)
# ==================================================

async def generate_reply(character, user_text, sender_name="صديق"):
    api_key = settings.get("gemini_api_key", "")
    if not api_key:
        return None

    client = genai.Client(api_key=api_key)
    history = character.get("messages", [])[-15:]
    history_text = "\n".join(
        f"{'أنت' if m['role'] == 'assistant' else m.get('sender', 'مستخدم')}: {m['text']}"
        for m in history
    )

    prompt = f"""
أنت شخصية حقيقية تتحدث داخل مجموعة على تليغرام، ولست مساعداً ذكياً أو بوت.

اسمك: {character.get('name', 'شخصية')}
صفاتك: {character.get('personality', '')}
أسلوب كلامك: {character.get('speaking_style', '')}
مزاجك: {character.get('mood', '')}

قواعد المهمة:
1. لا تذكر مطلقاً أنك ذكاء اصطناعي أو بوت.
2. تكلم باللهجة العراقية العفوية والبسيطة كإنسان حقيقي.
3. اكتب ردوداً قصيرة وطبيعية كالمحادثات اليومية.

السجل السابق:
{history_text}

رسالة ({sender_name}):
{user_text}

اكتب الرد المباشر فقط:
"""

    def call_gemini():
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )

    try:
        result = await asyncio.to_thread(call_gemini)
        return (result.text or "").strip()
    except Exception:
        log.exception("خطأ في توليد الرد من Gemini")
        return None


# ==================================================
# تشغيل الحسابات من مجلد الجلسات (sessions)
# ==================================================

async def start_client_session(session_path):
    client = TelegramClient(str(session_path.with_suffix("")), API_ID, API_HASH)

    @client.on(events.NewMessage(incoming=True))
    async def handler(event):
        if not event.is_group:
            return

        me = await client.get_me()
        if event.sender_id == me.id:
            return

        # البحث عن شخصية تطابق اسم الحساب أو استخدام الشخصية الافتراضية
        char_name = me.first_name or "شخصية"
        path, character = find_character(char_name)

        if not path or not character:
            path, character = create_character(char_name)

        message_text = (event.raw_text or "").strip()
        if not message_text:
            return

        reply_to = await event.get_reply_message()
        is_reply_to_me = reply_to and reply_to.sender_id == me.id
        is_mentioned = me.username and f"@{me.username}".lower() in message_text.lower()

        # الرد عند المنشن أو الرد المباشر على الحساب
        if not is_reply_to_me and not is_mentioned and not message_text.startswith("!ai"):
            return

        cleaned_text = message_text
        if me.username:
            cleaned_text = cleaned_text.replace(f"@{me.username}", "").strip()
        if cleaned_text.startswith("!ai"):
            cleaned_text = cleaned_text[3:].strip()

        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "صديق")

        add_message(character, "user", f"{sender_name}: {cleaned_text}")

        chat = await event.get_chat()
        async with client.action(chat, 'typing'):
            await asyncio.sleep(2)
            reply_text = await generate_reply(character, cleaned_text, sender_name)

        if reply_text:
            add_message(character, "assistant", reply_text)
            write_character(path, character)
            await event.reply(reply_text)

    await client.connect()
    if not await client.is_user_authorized():
        log.error(f"الجلسة {session_path.name} غير مفعّلة أو تم تسجيل الخروج منها.")
        return None

    log.info(f"تم الاتصال بالحساب من الجلسة: {session_path.name}")
    return client


async def main():
    if not API_ID or not API_HASH:
        raise RuntimeError("يجب ضبط API_ID و API_HASH في متغيرات البيئة.")

    # البحث عن كل ملفات الجلسات (.session) داخل مجلد sessions
    session_files = list(SESSIONS_DIR.glob("*.session"))

    if not session_files:
        log.warning(f"لم يتم العثور على أي ملفات .session داخل المجلد: {SESSIONS_DIR}")
        log.warning("يرجى رفع ملفات الجلسات الخاصة بك إلى هذا المجلد ثم إعادة تشغيل الاستضافة.")
        
        # الانتظار في حلقة تفادياً لتوقف السكربت كلياً
        while not list(SESSIONS_DIR.glob("*.session")):
            await asyncio.sleep(10)
        
        session_files = list(SESSIONS_DIR.glob("*.session"))

    clients = []
    for session_file in session_files:
        c = await start_client_session(session_file)
        if c:
            clients.append(c)

    if not clients:
        log.error("لم يتم تفعيل أي حساب بنجاح.")
        return

    log.info(f"تم تشغيل {len(clients)} حساب/حسابات بنجاح. جاهز للاستجابة في المجموعات...")
    
    # إبقاء التشغيل مستمراً لجميع الحسابات
    await asyncio.gather(*(c.run_until_disconnected() for c in clients))


if __name__ == "__main__":
    asyncio.run(main())
