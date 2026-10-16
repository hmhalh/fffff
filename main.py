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
from telethon.tl.functions.channels import JoinChannelRequest

BASE = Path(__file__).parent

SESSIONS_DIR = BASE / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# ==================== معلوماتك الشخصية ====================
CONTROL_TOKEN = "7838301513:AAEq39qhnjhmWwBwPh-c2_VIRTiJV06Qvn8"
API_ID = 20400000  # استبدلها برقم الـ API ID الحقيقي إذا لزم الأمر
API_HASH = "ضع_الـ_API_HASH_هنا"
TARGET_CHAT = -1004309527352
TARGET_CHANNEL_LINK = "https://t.me/DialoguesAi"
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
characters_dict: dict[str, dict] = {}
engine_task: asyncio.Task | None = None
stop_event = asyncio.Event()

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

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

async def load_and_join_sessions():
    """سحب الجلسات، التأكد من انضمامها للكروب، وتحديث القائمة"""
    global characters_dict
    session_files = list(SESSIONS_DIR.glob("*.session"))

    for sf in session_files:
        char_id = sf.stem
        if char_id in characters_dict and char_id in clients:
            continue
            
        try:
            client = TelegramClient(str(sf), API_ID, API_HASH)
            await client.connect()
            if await client.is_user_authorized():
                me = await client.get_me()
                name = me.first_name or char_id
                
                # محاولة انضمام الحساب للكروب تلقائياً
                try:
                    await client(JoinChannelRequest(TARGET_CHANNEL_LINK))
                    print(f"الحساب {name} انضم إلى الكروب بنجاح.")
                except Exception as join_err:
                    print(f"الحساب {name} ربما هو منضم مسبقاً أو حدثت مشكلة بالانضمام: {join_err}")

                clients[char_id] = client
                characters_dict[char_id] = {
                    "id": char_id,
                    "name": name,
                    "model": "gpt-4o-mini",
                    "enabled": True,
                    "system_prompt": f"أنت شخص اسمه {name}, تسولف بطريقة عفوية وطبيعية باللهجة العراقية في مجموعة سوالف."
                }
                print(f"تم تحميل وتجهيز الشخصية: {name}")
            else:
                await client.disconnect()
        except Exception as e:
            print(f"خطأ في تحميل الجلسة {sf.name}: {e}")

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
        text = re.sub(r"^(" + char['name'] + r")\s*[:：-]\s*", "", text)
        return text[:MAX_REPLY_CHARS].strip()
    finally:
        await client.close()

async def run_arena():
    turns = 0
    while not stop_event.is_set() and turns < MAX_TURNS:
        await load_and_join_sessions()
        enabled = [c for c in characters_dict.values() if c.get("enabled", True) and c["id"] in clients]
        if len(enabled) < 2:
            print("العدد غير كافٍ، يرجى التأكد من وجود حسابات مفعلة (على الأقل 2)...")
            await asyncio.sleep(5)
            continue
            
        char = random.choice(enabled)
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
    await load_and_join_sessions()
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
        await callback.message.edit_text("جاري انضمام الحسابات للكروب وبدء الحوار... 🟢", reply_markup=get_main_keyboard())
        await callback.answer("تم التشغيل والانضمام")

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
        await load_and_join_sessions()
        enabled_count = len([c for c in characters_dict.values() if c.get("enabled")])
        state = "شغّال 🟢" if engine_task and not engine_task.done() else "متوقف 🔴"
        text = (
            f"📊 **حالة النظام:**\n"
            f"• الحالة: {state}\n"
            f"• الحسابات المفعلة: {enabled_count} من {len(characters_dict)}\n"
            f"• الكروب: {TARGET_CHANNEL_LINK}\n"
            f"• الفاصل الزمني: {DELAY} ثانية"
        )
        await callback.message.edit_text(text, reply_markup=get_main_keyboard(), parse_mode="Markdown")
        await callback.answer()

    elif action == "btn_chars":
        await load_and_join_sessions()
        if not characters_dict:
            await callback.message.edit_text("👥 **الشخصيات:**\nلم يتم العثور على ملفات جلسات في مجلد `sessions`.", reply_markup=get_main_keyboard())
            await callback.answer()
            return

        keyboard_buttons = []
        for cid, c in characters_dict.items():
            status_icon = "🟢" if c.get("enabled", True) else "⚪"
            keyboard_buttons.append([
                InlineKeyboardButton(text=f"{status_icon} {c['name']}", callback_data=f"toggle_{cid}"),
                InlineKeyboardButton(text="تبديل الحالة 🔄", callback_data=f"toggle_{cid}")
            ])
        
        keyboard_buttons.append([
            InlineKeyboardButton(text="تحديد الكل 🟢", callback_data="all_on"),
            InlineKeyboardButton(text="إلغاء تحديد الكل 🔴", callback_data="all_off")
        ])
        keyboard_buttons.append([InlineKeyboardButton(text="العودة للقائمة الرئيسية 🔙", callback_data="back_home")])

        text = "👥 **إدارة حسابات الجلسات (اضغط لتبديل حالة الحساب):**"
        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons), parse_mode="Markdown")
        await callback.answer()

@dp.callback_query(F.data.startswith("toggle_") | F.data.in_({"all_on", "all_off", "back_home"}))
async def chars_management_handler(callback: CallbackQuery):
    data = callback.data
    if data == "back_home":
        await callback.message.edit_text("أهلاً بك في لوحة تحكم AI Social Arena 🎭\nاختر أحد الخيارات أدناه:", reply_markup=get_main_keyboard())
        await callback.answer()
        return

    if data == "all_on":
        for c in characters_dict.values():
            c["enabled"] = True
        await callback.answer("تم تفعيل جميع الحسابات ✅", show_alert=True)
    elif data == "all_off":
        for c in characters_dict.values():
            c["enabled"] = False
        await callback.answer("تم تعطيل جميع الحسابات ❌", show_alert=True)
    elif data.startswith("toggle_"):
        cid = data.replace("toggle_", "")
        if cid in characters_dict:
            characters_dict[cid]["enabled"] = not characters_dict[cid]["enabled"]
            state_text = "تم التفعيل 🟢" if characters_dict[cid]["enabled"] else "تم التعطيل ⚪"
            await callback.answer(state_text)

    keyboard_buttons = []
    for cid, c in characters_dict.items():
        status_icon = "🟢" if c.get("enabled", True) else "⚪"
        keyboard_buttons.append([
            InlineKeyboardButton(text=f"{status_icon} {c['name']}", callback_data=f"toggle_{cid}"),
            InlineKeyboardButton(text="تبديل الحالة 🔄", callback_data=f"toggle_{cid}")
        ])
    
    keyboard_buttons.append([
        InlineKeyboardButton(text="تحديد الكل 🟢", callback_data="all_on"),
        InlineKeyboardButton(text="إلغاء تحديد الكل 🔴", callback_data="all_off")
    ])
    keyboard_buttons.append([InlineKeyboardButton(text="العودة للقائمة الرئيسية 🔙", callback_data="back_home")])

    try:
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_buttons))
    except Exception:
        pass

async def main():
    print("تم بدء تشغيل بوت التحكم، جاري فحص مجلد sessions والانضمام للكروب...")
    await load_and_join_sessions()
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
