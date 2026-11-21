# Telegram MTProto Proxy (Fake TLS)

بروكسي تيليجرام يشتغل بـ Fake TLS حتى ما تحجبه شركات الاتصالات.

## الرفع على Railway
1. ارفع هذي الملفات على GitHub.
2. Railway ← New Project ← Deploy from GitHub repo.
3. Variables: أضف
   - `PORT` = `8443`
   - `SECRET` = السيكرت اللي يبدي بـ `ee`
4. Settings ← Networking ← **TCP Proxy** ← اكتب البورت `8443`.
5. راح يعطيك دومين وبورت مثل `xxx.proxy.rlwy.net:12345`.

## الرابط
```
tg://proxy?server=DOMAIN&port=PORT&secret=SECRET
```
