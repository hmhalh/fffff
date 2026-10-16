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

# بيانات تليغرام (احصل عليها من my.telegram.org)
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# مجلدات التخزين
BASE_DIR = Path(os.getenv("DATA_DIR", "/app/storage"))
CHARACTERS_DIR = BASE_DIR / "characters"
SESSIONS_DIR = BASE_DIR / "sessions"
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

CHARACTERS_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "running": False,
    "gemini_api_key": "",
    "active_character": "",
    "target_groups": [], # المعرفات أو روابط المجموعات المستهدفة
}


# ==================================================
# حفظ وقراءة الإعدادات
# ==================================================

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

def save_settings():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = SETTINGS_FILE.with_suffix(".tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    temp_file.replace(SETTINGS_FILE)


# ==================================================
# إدارة ملفات الشخصيات والسلوكيات المستقلة
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
    """ينشئ اسماً فريداً للملف حتى لا يحدث تداخل (مثلاً زهراء، زهراء 2)"""
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

def create_character(name, personality="ودودة، هادئة، وتتكلم باللهجة العراقية الطبيعية.", mood="طبيعية ومتوازنة"):
    path = unique_character_path(name)
    actual_name = path.stem
    character = {
        "name": actual_name,
        "personality": personality,
        "speaking_style": "لهجة عراقية عفوية وبسيطة كالأشخاص الحقيقيين.",
        "mood": mood,
        "background": "شخصية تفاعلية طبيعية داخل المجموعات.",
        "created_at": datetime.now(timezone.utc).isoformat(),
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
    # الاحتفاظ بآخر 100 رسالة فقط لسياق المحادثة
    character["messages"] = character["messages"][-100:]


# ==================================================
# محرك الذكاء الاصطناعي (Gemini)
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
أنت شخصية حقيقية تتحدث داخل مجموعة على تليغرام، ولست مساعداً ذكياً أو بوت. ادمج تماماً في الشخصية الموضحة أدناه:

اسمك: {character.get('name', 'شخصية')}
صفاتك المزاجية والنفسية: {character.get('personality', '')}
طريقة كلامك: {character.get('speaking_style', '')}
حالتك المزاجية الحالية: {character.get('mood', '')}

قواعد أساسية للكلام كشخص حقيقي:
1. لا تقل أبداً "أنا ذكاء اصطناعي" أو "أنا نموذج لغوي"، تصرف كامتداد لشخصيتك الحقيقية تماماً.
2. استخدم اللهجة العراقية العفوية والبسيطة والملائمة لمزاجك (سواء كنت هادئاً، عصبياً، مرحاً، إلخ).
3. كن مختصراً وطبيعياً ككتابة البشر (لا تكتب مقالات طويلة إلا إذا طلب منك ذلك).
4. تفاعل مع كلام الشخص الذي يكلمك بشكل مباشر وبدون تكلف.

سجل المحادثات السابقة:
{history_text}

الرسالة الموجهة إليك من ({sender_name}):
{user_text}

اكتب ردك العفوي المباشر فقط:
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
        log.exception("خطأ في الاتصال بـ Gemini")
        return None


# ==================================================
# تشغيل حسابات تليغرام (Userbot) عبر Telethon
# ==================================================

async def main():
    if not API_ID or not API_HASH:
        raise RuntimeError("يجب توفير API_ID و API_HASH الخاصة بحسابات تليغرام.")

    session_name = str(SESSIONS_DIR / "user_account")
    client = TelegramClient(session_name, API_ID, API_HASH)

    @client.on(events.NewMessage(incoming=True))
    async def handle_group_messages(event):
        # التحقق مما إذا كان التشغيل مفَعلاً
        if not settings.get("running"):
            return

        # التأكد من أن الرسالة في مجموعة أو مجموعة خارقة (Group/Supergroup)
        if not event.is_group:
            return

        chat = await event.get_chat()
        chat_id_str = str(chat.id)
        target_groups = settings.get("target_groups", [])

        # إذا كانت المجموعات محددة، نتحقق أن المجموعة ضمن القائمة المستهدفة
        if target_groups and chat_id_str not in target_groups and str(chat.chat_id) not in target_groups:
            return

        # تجنب الرد على الرسائل الصادرة من نفس الحساب
        me = await client.get_me()
        if event.sender_id == me.id:
            return

        active_name = settings.get("active_character", "")
        if not active_name:
            return

        path, character = find_character(active_name)
        if not path or not character:
            return

        message_text = (event.raw_text or "").strip()
        if not message_text:
            return

        # تحديد ما إذا كان يجب الرد (مثلاً إذا تم الرد على رسالة الحساب، أو تم ذكر اسمه، أو عشوائياً بنسبة معينة، أو محادثة طبيعية)
        reply_to = await event.get_reply_message()
        is_reply_to_me = reply_to and reply_to.sender_id == me.id
        is_mentioned = me.username and f"@{me.username}".lower() in message_text.lower()

        # يمكنك جعل الحساب يرد بناءً على الرد المباشر، أو المنشن، أو إذا كانت الرسالة موجهة له
        if not is_reply_to_me and not is_mentioned and not message_text.startswith("!ai"):
            # إذا أردت أن يرد الحساب على بعض الرسائل بشكل عفوي، يمكنك وضع شرط هنا، والاكتفاء بالمنشن والردود حالياً لتجنب الإزعاج
            return

        cleaned_text = message_text
        if me.username:
            cleaned_text = cleaned_text.replace(f"@{me.username}", "").strip()
        if cleaned_text.startswith("!ai"):
            cleaned_text = cleaned_text[3:].strip()

        if not cleaned_text:
            cleaned_text = "هلا بيها"

        sender = await event.get_sender()
        sender_name = getattr(sender, "first_name", "شخص")

        # حفظ رسالة المستخدم في ملف الشخصية
        add_message(character, "user", f"{sender_name}: {cleaned_text}")

        # محاكاة الكتابة لفترة قصيرة لتبدو كإنسان حقيقي يكتب
        async with client.action(chat, 'typing'):
            await asyncio.sleep(1.5)
            reply_text = await generate_reply(character, cleaned_text, sender_name)

        if reply_text:
            # حفظ رد الشخصية في ملفها
            add_message(character, "assistant", reply_text)
            write_character(path, character)

            # إرسال الرد كشخص حقيقي (بالرد على الرسالة أو بشكل مباشر)
            await event.reply(reply_text)

    print("جاري بدء تشغيل حساب تليغرام...")
    await client.start()
    print("تم تسجيل الدخول بنجاح والبدء بمراقبة المجموعات.")
    
    # إبقاء التشغيل مستمراً
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
