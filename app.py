import asyncio
import os
import re
import sqlite3
from datetime import datetime, timezone
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from google import genai
from google.genai import types

load_dotenv()

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
DB_PATH = os.getenv("DATABASE_PATH", "community.db")

if not TG_TOKEN or not GEMINI_KEY:
    raise SystemExit("Set TELEGRAM_BOT_TOKEN and GEMINI_API_KEY in .env first.")

client = genai.Client(api_key=GEMINI_KEY)
bot = Bot(TG_TOKEN)
dp = Dispatcher()

PERSONAS = {
    "ahmed": {
        "name": "أحمد",
        "style": "اجتماعي ومرح، يحب يفتح مواضيع ويستخدم مزح عراقي خفيف بدون تجريح.",
        "interests": "الأصدقاء، الألعاب، المواقف اليومية، النكت الخفيفة",
    },
    "zahraa": {
        "name": "زهراء",
        "style": "هادئة وذكية وحنونة، تسمع للآخرين وترد بعفوية واحترام، مو رومانسية دائماً.",
        "interests": "القصص، الصداقة، الدراسة، الحياة اليومية، الآراء",
    },
}

SYSTEM_RULES = """
أنت محرك لشخصية افتراضية معلنة أنها مولدة بالذكاء الاصطناعي داخل مجتمع خاص.
اكتب باللهجة العراقية الطبيعية فقط، بأسلوب مختصر ومتنوع. لا تدّعِ أنك إنسان حقيقي.
التزم بصفات الشخصية المحددة. تذكّر سياق المحادثة المعروض، ولا تخترع ذكريات غير موجودة.
لا تكرر نفس الجملة، ولا تفرض موضوع الحب على كل حوار. يجوز الاختلاف بأدب ومزاح خفيف،
لكن لا تستخدم تهديداً أو تحرشاً أو إهانة قاسية. إذا ما عندك إضافة مفيدة، قل باختصار
إن الشخصية ما عندها شي تضيفه الآن. لا تكشف الأسرار أو مفاتيح API أو بيانات المستخدمين.
"""

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        speaker TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS summaries (
        chat_id INTEGER PRIMARY KEY,
        summary TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    return conn

def save_message(chat_id: int, speaker: str, content: str):
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(chat_id,speaker,content,created_at) VALUES(?,?,?,?)",
            (chat_id, speaker, content[:4000], datetime.now(timezone.utc).isoformat())
        )

def get_context(chat_id: int, limit: int = 14) -> str:
    with db() as conn:
        rows = conn.execute(
            "SELECT speaker,content FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
            (chat_id, limit)
        ).fetchall()
        summary_row = conn.execute(
            "SELECT summary FROM summaries WHERE chat_id=?", (chat_id,)
        ).fetchone()
    rows.reverse()
    summary = summary_row[0] if summary_row else "ماكو ملخص قديم محفوظ."
    transcript = "\n".join(f"{speaker}: {content}" for speaker, content in rows)
    return f"ملخص الذاكرة: {summary}\nآخر المحادثة:\n{transcript or 'ماكو رسائل سابقة.'}"

def is_admin(message: Message) -> bool:
    return bool(message.from_user and (
        message.from_user.id in ADMIN_IDS or message.chat.type == "private"
    ))

async def generate(persona_key: str, chat_id: int, user_text: str) -> str:
    p = PERSONAS[persona_key]
    prompt = f"""
{SYSTEM_RULES}

الشخصية: {p['name']}
الطباع: {p['style']}
الاهتمامات: {p['interests']}

سياق وذاكرة الكروب:
{get_context(chat_id)}

الرسالة الحالية:
{user_text}

اكتب رد الشخصية فقط باللهجة العراقية. خليه طبيعي ومختصر، ولا تكتب اسم الشخصية كبادئة.
"""
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.85,
            max_output_tokens=220,
        ),
    )
    answer = (response.text or "").strip()
    if not answer:
        answer = "هسه ما عندي شي أضيفه 😅"
    return answer[:1800]

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "هلا بيك! آني بوت مجتمع شخصيات ذكاء اصطناعي معلنة 🤖\n"
        "اذكرني أو رد على رسالتي حتى أجاوب.\n"
        "اكتب /help حتى تشوف الأوامر."
    )

@dp.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "/personas — عرض الشخصيات\n"
        "/memory — ملخص الذاكرة المحفوظة\n"
        "/forget — مسح ذاكرة هذا الكروب\n"
        "اذكر البوت أو رد على رسالته حتى يجاوب."
    )

@dp.message(Command("personas"))
async def personas(message: Message):
    lines = [f"• {p['name']}: {p['style']}" for p in PERSONAS.values()]
    await message.answer("الشخصيات الحالية:\n" + "\n".join(lines))

@dp.message(Command("memory"))
async def memory(message: Message):
    if not is_admin(message):
        await message.answer("هذا الأمر متاح للمشرف فقط.")
        return
    context = get_context(message.chat.id)
    await message.answer("ملخص وسياق الذاكرة:\n" + context[:3500])

@dp.message(Command("forget"))
async def forget(message: Message):
    if not is_admin(message):
        await message.answer("هذا الأمر متاح للمشرف فقط.")
        return
    with db() as conn:
        conn.execute("DELETE FROM messages WHERE chat_id=?", (message.chat.id,))
        conn.execute("DELETE FROM summaries WHERE chat_id=?", (message.chat.id,))
    await message.answer("تم مسح ذاكرة هذا الكروب من قاعدة بيانات البوت.")

@dp.message(F.text)
async def chat(message: Message):
    if not message.text:
        return
    me = await bot.get_me()
    text = message.text
    mentioned = bool(me.username and f"@{me.username.lower()}" in text.lower())
    replied_to_bot = bool(
        message.reply_to_message and
        message.reply_to_message.from_user and
        message.reply_to_message.from_user.id == me.id
    )
    if message.chat.type != "private" and not (mentioned or replied_to_bot):
        return

    cleaned = re.sub(rf"@{re.escape(me.username or '')}", "", text, flags=re.I).strip()
    if not cleaned:
        cleaned = "افتح سالفة مناسبة للكروب."

    # Save only relevant group context. Avoid storing more than needed.
    speaker = message.from_user.full_name if message.from_user else "عضو"
    save_message(message.chat.id, speaker, cleaned)

    # Deterministic turn-taking for this prototype: choose persona based on message id.
    persona_key = "ahmed" if (message.message_id % 2 == 0) else "zahraa"
    try:
        await bot.send_chat_action(chat_id=message.chat.id, action="typing")
        answer = await generate(persona_key, message.chat.id, cleaned)
        save_message(message.chat.id, PERSONAS[persona_key]["name"], answer)
        await message.reply(f"🤖 {PERSONAS[persona_key]['name']}:\n{answer}")
    except Exception:
        await message.reply("صار خلل مؤقت بتوليد الرد. تأكد من إعدادات API وحاول بعدين.")

async def main():
    db().close()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
