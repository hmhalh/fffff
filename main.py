#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-file Telegram + Gemini group assistant. Python 3.10+, no pip packages required."""
import os, json, time, urllib.request, urllib.parse, urllib.error, threading

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "أنت مساعد ذكاء اصطناعي ودود يتحدث باللهجة العراقية بشكل طبيعي ومحترم. أجب باختصار وبحسب سياق المحادثة. وضّح أنك مساعد ذكاء اصطناعي إذا سُئلت عن هويتك. لا تدّعِ أنك إنسان حقيقي."
)

if not BOT_TOKEN or not GEMINI_API_KEY or not ADMIN_ID:
    raise SystemExit("لازم تضيف BOT_TOKEN و GEMINI_API_KEY و ADMIN_ID في Environment Variables بالاستضافة.")
try:
    ADMIN_ID = int(ADMIN_ID)
except ValueError:
    raise SystemExit("ADMIN_ID لازم يكون رقم تيليجرام الرقمي مالك.")

TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}/"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(GEMINI_MODEL, safe='')}:generateContent"
STATE = {"enabled": False, "group_id": None, "group_title": "غير محددة", "offset": 0}
HISTORY = {}  # in-memory chat context; clears when the process restarts
LOCK = threading.Lock()


def http_json(url, payload=None, headers=None, timeout=45):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method="GET" if data is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {detail[:700]}") from e


def tg(method, payload=None):
    result = http_json(TG_API + method, payload, {"Content-Type": "application/json"}, timeout=50)
    if not result.get("ok"):
        raise RuntimeError(str(result))
    return result.get("result")


def send(chat_id, text, reply_markup=None, reply_to=None):
    # Telegram message limit is 4096 chars; leave margin.
    text = str(text or "")
    chunks = [text[i:i+3900] for i in range(0, len(text), 3900)] or [" "]
    for i, chunk in enumerate(chunks):
        body = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
        if i == 0 and reply_markup:
            body["reply_markup"] = reply_markup
        if i == 0 and reply_to:
            body["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        tg("sendMessage", body)


def keyboard():
    return {"inline_keyboard": [
        [{"text": "▶️ تشغيل الردود", "callback_data": "on"}, {"text": "⏸ إيقاف الردود", "callback_data": "off"}],
        [{"text": "📊 الحالة", "callback_data": "status"}, {"text": "ℹ️ المساعدة", "callback_data": "help"}],
    ]}


def status_text():
    return (f"الحالة: {'🟢 شغّال' if STATE['enabled'] else '🔴 متوقف'}\n"
            f"المجموعة: {STATE['group_title']}\n"
            f"الموديل: {GEMINI_MODEL}\n\n"
            "للرد بالمجموعة: منشن البوت أو ردّ على رسالة منه، أو استخدم /ask نص السؤال.")


def is_admin(user_id):
    return user_id == ADMIN_ID


def gemini_reply(chat_id, prompt, user_name="عضو"):
    history = HISTORY.get(chat_id, [])[-12:]
    contents = []
    for item in history:
        contents.append({"role": item["role"], "parts": [{"text": item["text"]}]})
    contents.append({"role": "user", "parts": [{"text": f"رسالة من {user_name}: {prompt[:5000]}"}]})
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.85, "maxOutputTokens": 700}
    }
    result = http_json(GEMINI_URL, payload, {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    })
    candidates = result.get("candidates", [])
    if not candidates:
        feedback = result.get("promptFeedback", {})
        raise RuntimeError("Gemini لم يرجع إجابة. " + json.dumps(feedback, ensure_ascii=False)[:300])
    parts = candidates[0].get("content", {}).get("parts", [])
    answer = "\n".join(p.get("text", "") for p in parts if p.get("text"))
    if not answer.strip():
        raise RuntimeError("الموديل رجّع إجابة فارغة.")
    HISTORY[chat_id] = (history + [
        {"role": "user", "text": f"رسالة من {user_name}: {prompt[:5000]}"},
        {"role": "model", "text": answer[:6000]}
    ])[-12:]
    return answer.strip()


def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = message.get("from", {})
    user_id = user.get("id")
    text = (message.get("text") or "").strip()
    if not text or not chat_id or user.get("is_bot"):
        return
    first = user.get("first_name", "صديق")
    lower = text.lower()
    command = text.split()[0].split("@")[0] if text.startswith("/") else ""

    if command == "/start" and chat_type == "private":
        if not is_admin(user_id):
            send(chat_id, "هذا بوت خاص بمديره فقط.")
            return
        send(chat_id, "أهلاً! هاي لوحة التحكم. أضف البوت للمجموعة، وخلي عنده صلاحية إرسال الرسائل وقراءة الرسائل المطلوبة، ثم اكتب /setgroup داخل المجموعة.", keyboard())
        return
    if command in ("/help", "/start"):
        if chat_type == "private" and not is_admin(user_id):
            send(chat_id, "هذا بوت خاص بمديره فقط.")
            return
        send(chat_id, "الأوامر:\n/setgroup — تعيين المجموعة (داخل المجموعة)\n/ask نص — سؤال مباشر\n/status — الحالة\n/on — تشغيل الردود\n/off — إيقاف الردود\n\nيرد البوت بالمجموعة عند منشنه أو الرد على رسالته أو استخدام /ask.", keyboard() if chat_type == "private" else None)
        return
    if command == "/setgroup":
        if not is_admin(user_id):
            send(chat_id, "هذا الأمر للمدير فقط.")
            return
        if chat_type not in ("group", "supergroup"):
            send(chat_id, "اكتب /setgroup داخل المجموعة المراد ربطها.")
            return
        STATE["group_id"] = chat_id
        STATE["group_title"] = chat.get("title", str(chat_id))
        send(chat_id, "✅ تم تعيين هذه المجموعة. ارجع للخاص واضغط تشغيل الردود.")
        return
    if command in ("/on", "/off", "/status"):
        if not is_admin(user_id):
            send(chat_id, "هذا الأمر للمدير فقط.")
            return
        if command == "/on": STATE["enabled"] = True
        elif command == "/off": STATE["enabled"] = False
        send(chat_id, status_text(), keyboard() if chat_type == "private" else None)
        return
    if command == "/ask":
        if chat_type == "private" and not is_admin(user_id):
            send(chat_id, "هذا البوت خاص بمديره فقط.")
            return
        prompt = text.partition(" ")[2].strip()
        if not prompt:
            send(chat_id, "اكتب سؤالك بعد الأمر، مثال: /ask شلونك؟")
            return
        if chat_type != "private" and STATE["group_id"] != chat_id:
            send(chat_id, "هذه المجموعة مو محددة للمساعد. المدير يكتب /setgroup هنا أولاً.")
            return
        try:
            send(chat_id, gemini_reply(str(chat_id), prompt, first), reply_to=message.get("message_id"))
        except Exception as e:
            send(chat_id, "صار خطأ بالاتصال بموديل الذكاء الاصطناعي. راجع إعداد GEMINI_MODEL ومفتاح API.\n" + str(e)[:500])
        return

    # Respond only in the explicitly selected group and only when addressed.
    if chat_type not in ("group", "supergroup") or chat_id != STATE["group_id"] or not STATE["enabled"]:
        return
    bot_username = os.getenv("BOT_USERNAME", "").strip().lstrip("@").lower()
    entities = message.get("entities", [])
    mentioned = bool(bot_username and f"@{bot_username}" in lower)
    replied_to_bot = False
    reply = message.get("reply_to_message") or {}
    if reply.get("from", {}).get("is_bot"):
        # Best effort: reply must point to this bot, not any other bot.
        me = os.getenv("BOT_ID", "").strip()
        replied_to_bot = bool(me and str(reply.get("from", {}).get("id")) == me)
        if not me:
            replied_to_bot = bool(bot_username and reply.get("from", {}).get("username", "").lower() == bot_username)
    if not (mentioned or replied_to_bot):
        return
    prompt = text
    if bot_username:
        prompt = prompt.replace("@" + bot_username, "").strip()
    if not prompt:
        prompt = "رد على رسالتي بشكل طبيعي باللهجة العراقية."
    try:
        answer = gemini_reply(str(chat_id), prompt, first)
        send(chat_id, answer, reply_to=message.get("message_id"))
    except Exception as e:
        send(chat_id, "ما كدرت أجيب رد هسه؛ تأكد من مفتاح Gemini واسم الموديل.\n" + str(e)[:350], reply_to=message.get("message_id"))


def handle_callback(update):
    query = update.get("callback_query", {})
    user_id = (query.get("from") or {}).get("id")
    data = query.get("data", "")
    if not is_admin(user_id):
        tg("answerCallbackQuery", {"callback_query_id": query.get("id"), "text": "هذا التحكم للمدير فقط.", "show_alert": True})
        return
    if data == "on": STATE["enabled"] = True
    elif data == "off": STATE["enabled"] = False
    elif data == "status": pass
    elif data == "help":
        tg("answerCallbackQuery", {"callback_query_id": query.get("id"), "text": "اكتب /setgroup داخل المجموعة أولاً."})
        return
    tg("answerCallbackQuery", {"callback_query_id": query.get("id"), "text": "تم تحديث الحالة."})
    msg = query.get("message", {})
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id:
        try:
            tg("editMessageText", {"chat_id": chat_id, "message_id": msg.get("message_id"), "text": status_text(), "reply_markup": keyboard()})
        except Exception:
            send(chat_id, status_text(), keyboard())


def main():
    print("Bot started. Polling Telegram updates...")
    # Clear any pending updates on initial launch to avoid replaying old messages.
    try:
        tg("deleteWebhook", {"drop_pending_updates": False})
    except Exception as e:
        print("Webhook note:", e)
    while True:
        try:
            updates = tg("getUpdates", {"offset": STATE["offset"], "timeout": 30, "allowed_updates": ["message", "callback_query"]})
            for update in updates:
                STATE["offset"] = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        handle_callback(update)
                    elif "message" in update:
                        handle_message(update["message"])
                except Exception as e:
                    print("Update error:", repr(e))
        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            print("Polling error:", repr(e))
            time.sleep(4)

if __name__ == "__main__":
    main()
