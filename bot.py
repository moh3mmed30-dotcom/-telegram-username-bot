# -*- coding: utf-8 -*-
"""
بوت توليد وفحص يوزرات تيليجرام - نسخة احترافية
- أزرار ملونة (Bot API 9.4: primary=أزرق / success=أخضر / danger=أحمر)
- لوحة تحكم كاملة للأدمن
- فحص وجود اليوزر قبل إرساله

التشغيل:
    pip install aiohttp
    set BOT_TOKEN=xxxx      (ويندوز)   |   export BOT_TOKEN=xxxx   (لينكس)
    python bot.py
"""
import asyncio
import csv
import html
import io
import logging
import os
import random
import re
import sqlite3
import string
import time
from datetime import datetime
from urllib.parse import quote

import aiohttp

# ============================ الإعدادات ============================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9084522969:AAFwpXcNiTWfzrdA2e_6WCnfiMuZ111heGc")
OWNER_ID = 7391716280          # المطور الرئيسي (لا يمكن حذفه)
DB_FILE = os.getenv("DB_FILE", "bot.db")

DEFAULTS = {
    "welcome": "👋 أهلاً بك يا <b>{name}</b>\n\n🎯 مرحباً بك في بوت <b>توليد وفحص يوزرات تيليجرام</b>\n"
               "⚡ يولّد لك يوزرات مميزة ويتأكد أنها غير مستخدمة قبل إرسالها.\n\n👇 اختر من القائمة:",
    "dev_user": "M_zeuo",
    "ch1": "https://t.me/HR9_J",
    "ch2": "https://t.me/HR9_J",
    "ch1_title": "📢 القناة الأساسية",
    "ch2_title": "📣 القناة الثانية",
    "daily_limit": "50",      # أقصى عدد يوزرات متاحة يستلمها المستخدم يومياً (0 = بلا حد)
    "max_per_req": "20",      # أقصى عدد في الطلب الواحد
    "enabled": "1",
    "force_channel": "",      # مثال: @HR9_J  (البوت لازم يكون أدمن في القناة)
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot")

# ============================ قاعدة البيانات ============================
db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT, joined TEXT,
                                 banned INTEGER DEFAULT 0, total INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS buttons(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, kind TEXT, value TEXT, style TEXT);
CREATE TABLE IF NOT EXISTS sale(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, price TEXT);
CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS found(username TEXT, user_id INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS usage(user_id INTEGER, day TEXT, n INTEGER, PRIMARY KEY(user_id, day));
""")


def q(sql, *a):
    cur = db.execute(sql, a)
    db.commit()
    return cur


def S(k):
    r = q("SELECT v FROM settings WHERE k=?", k).fetchone()
    return r["v"] if r else DEFAULTS[k]


def setS(k, v):
    q("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", k, v)


def is_admin(uid):
    return uid == OWNER_ID or bool(q("SELECT 1 FROM admins WHERE id=?", uid).fetchone())


def today():
    return datetime.utcnow().strftime("%Y-%m-%d")


# ============================ طبقة Telegram API ============================
session: aiohttp.ClientSession = None
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/"


class APIError(Exception):
    def __init__(self, code, desc, params=None):
        super().__init__(f"{code}: {desc}")
        self.code, self.desc, self.params = code, desc or "", params or {}


async def api(method, _retry=True, **p):
    async with session.post(API_URL + method, json=p) as r:
        d = await r.json()
    if not d.get("ok"):
        e = APIError(d.get("error_code"), d.get("description"), d.get("parameters"))
        if e.code == 429 and _retry:
            await asyncio.sleep(e.params.get("retry_after", 3) + 1)
            return await api(method, False, **p)
        raise e
    return d["result"]


def strip_styles(kb):
    return [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in kb]


async def send(chat, text, kb=None, **extra):
    p = dict(chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True, **extra)
    if kb:
        p["reply_markup"] = {"inline_keyboard": kb}
    try:
        return await api("sendMessage", **p)
    except APIError as e:
        if kb and e.code == 400 and "style" in e.desc.lower():   # احتياط لو السيرفر لا يدعم الألوان
            p["reply_markup"] = {"inline_keyboard": strip_styles(kb)}
            return await api("sendMessage", **p)
        raise


async def show(chat, mid, text, kb=None):
    """تعديل الرسالة الحالية (تنقل سلس) وإلا إرسال رسالة جديدة."""
    p = dict(chat_id=chat, message_id=mid, text=text, parse_mode="HTML", disable_web_page_preview=True)
    if kb:
        p["reply_markup"] = {"inline_keyboard": kb}
    try:
        return await api("editMessageText", **p)
    except APIError as e:
        if "not modified" in e.desc:
            return None
        if kb and e.code == 400 and "style" in e.desc.lower():
            p["reply_markup"] = {"inline_keyboard": strip_styles(kb)}
            return await api("editMessageText", **p)
        return await send(chat, text, kb)


async def send_file(chat, filename, data: bytes, caption=""):
    fd = aiohttp.FormData()
    fd.add_field("chat_id", str(chat))
    fd.add_field("caption", caption)
    fd.add_field("document", data, filename=filename)
    async with session.post(API_URL + "sendDocument", data=fd) as r:
        return await r.json()


def B(text, cb=None, url=None, st=None):
    d = {"text": text}
    if url in ("primary", "success", "danger") and st is None:
        st, url = url, None
    if cb:
        d["callback_data"] = cb
    if url:
        d["url"] = url
    if st:
        d["style"] = st
    return d


P, G, R = "primary", "success", "danger"   # أزرق / أخضر / أحمر
h = html.escape

# ============================ توليد وفحص اليوزرات ============================
LET, DIG = string.ascii_lowercase, string.digits
VALID = re.compile(r"^[a-z][a-z0-9_]{4,31}$")

PRESETS = {
    "qn": ("✨ نفس شكل @QN_A7", "LL_LD"),
    "l5": ("🔤 خماسي حروف", "LLLLL"),
    "l6": ("🔠 سداسي حروف", "LLLLLL"),
    "us": ("➖ حرف_ثلاثي", "L_LLL"),
    "dbl": ("🪞 حروف مكررة", "DBL"),
    "ld": ("🔢 حروف + أرقام", "LLLDD"),
}


def valid_username(u):
    return bool(VALID.match(u)) and "__" not in u and not u.endswith("_")


def gen_one(p):
    """L=حرف  D=رقم  X=حرف أو رقم  وأي رمز آخر يُكتب كما هو."""
    if p == "DBL":
        a, b, c = random.sample(LET, 3)
        return a + a + b + b + c
    out = []
    for ch in p:
        if ch == "L":
            out.append(random.choice(LET))
        elif ch == "D":
            out.append(random.choice(DIG))
        elif ch == "X":
            out.append(random.choice(LET + DIG))
        else:
            out.append(ch.lower())
    return "".join(out)


async def is_taken(u):
    """True = موجود/مستخدم | False = غير موجود | None = تعذر الفحص."""
    try:
        await api("getChat", chat_id="@" + u)
        return True
    except APIError:
        pass  # chat not found -> نكمل بالفحص الثاني
    try:
        async with session.get(f"https://t.me/{u}", timeout=aiohttp.ClientTimeout(total=15),
                               headers={"User-Agent": "Mozilla/5.0"}) as r:
            t = await r.text()
        m = re.search(r'<meta property="og:title" content="([^"]*)"', t)
        if not m:
            return None
        return not m.group(1).strip().lower().startswith("telegram: contact")
    except Exception:
        return None


jobs = {}
state = {}       # uid -> (key, data)
custom_pat = {}  # uid -> pattern


def used_today(uid):
    r = q("SELECT n FROM usage WHERE user_id=? AND day=?", uid, today()).fetchone()
    return r["n"] if r else 0


def add_usage(uid, n):
    q("INSERT INTO usage VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET n=n+?", uid, today(), n, n)


async def run_job(uid, chat, mid, pattern, n):
    stop = asyncio.Event()
    jobs[uid] = stop
    seen = {r["username"] for r in q("SELECT username FROM found WHERE user_id=?", uid)}
    tried, checked, results = set(), 0, []
    cap = max(300, n * 80)
    stop_kb = [[B("⛔ إيقاف", "stop", R)]]
    last_edit = 0
    try:
        while len(results) < n and checked < cap and not stop.is_set():
            batch = []
            for _ in range(200):
                u = gen_one(pattern)
                if valid_username(u) and u not in tried and u not in seen:
                    tried.add(u)
                    batch.append(u)
                if len(batch) >= 6:
                    break
            if not batch:
                break
            res = await asyncio.gather(*[is_taken(u) for u in batch])
            for u, taken in zip(batch, res):
                checked += 1
                if taken is False and len(results) < n:
                    results.append(u)
            if time.time() - last_edit > 2:
                last_edit = time.time()
                try:
                    await show(chat, mid, f"⏳ <b>جاري الفحص...</b>\n\n🔎 تم فحص: <b>{checked}</b>\n"
                                          f"✅ متاح: <b>{len(results)}/{n}</b>", stop_kb)
                except Exception:
                    pass
            await asyncio.sleep(0.4)
    finally:
        jobs.pop(uid, None)

    if results:
        for u in results:
            q("INSERT INTO found VALUES(?,?,?)", u, uid, datetime.utcnow().isoformat())
        if not is_admin(uid):
            add_usage(uid, len(results))
        q("UPDATE users SET total=total+? WHERE id=?", len(results), uid)
    body = "\n".join(f"✅ <code>@{u}</code>" for u in results) or "😕 لم أجد يوزرات متاحة هذه المرة، جرّب نمطاً آخر."
    note = "\n\n⏹ تم الإيقاف." if stop.is_set() else ""
    await show(chat, mid, f"🎯 <b>النتيجة</b> (فُحص {checked})\n\n{body}{note}\n\n"
                          f"ℹ️ <i>اليوزر المتاح قد يكون محجوزاً أو محظوراً من تيليجرام، جرّب إنشاءه بسرعة.</i>",
               [[B("🎲 توليد مرة أخرى", "gen", G)], [B("🏠 القائمة الرئيسية", "home", P)]])


# ============================ القوائم ============================
def home_kb(uid):
    dev = S("dev_user")
    kb = [
        [B("🎲 توليد يوزرات", "gen", G), B("🔍 فحص يوزر", "chk", P)],
        [B("🛒 يوزرات للبيع", "sale", P), B("👤 حسابي", "me", R)],
        [B("👨‍💻 تواصل مع المطور", url=f"https://t.me/{dev}", st=G)],
        [B(S("ch1_title"), url=S("ch1"), st=R), B(S("ch2_title"), url=S("ch2"), st=P)],
    ]
    row = []
    for b in q("SELECT * FROM buttons ORDER BY id").fetchall():
        btn = B(b["text"], url=b["value"], st=b["style"]) if b["kind"] == "url" else B(b["text"], f"cb:{b['id']}", st=b["style"])
        row.append(btn)
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    if is_admin(uid):
        kb.append([B("👑 لوحة تحكم المطور", "adm", R)])
    return kb


def home_text(name):
    return S("welcome").replace("{name}", h(name))


def admin_kb():
    return [
        [B("📊 الإحصائيات", "a:stats", P), B("📥 تصدير المستخدمين", "a:export", P)],
        [B("📢 إذاعة", "a:bc", G), B("👥 المستخدمين", "a:users", G)],
        [B("🚫 حظر", "a:ban", R), B("✅ فك حظر", "a:unban", G)],
        [B("➕ إضافة زر", "a:badd", G), B("🗑 حذف زر", "a:bdel", R)],
        [B("🛒 إضافة يوزر للبيع", "a:sadd", G), B("🗑 حذف يوزر للبيع", "a:sdel", R)],
        [B("✏️ رسالة الترحيب", "a:ed:welcome", P), B("🔗 روابط المطور", "a:links", P)],
        [B("⚙️ الإعدادات", "a:set", P), B("👮 الأدمنية", "a:admins", P)],
        [B("🔙 رجوع", "home", R)],
    ]


def cancel_kb():
    return [[B("❌ إلغاء", "adm_back", R)]]


def count_kb(key):
    mx = int(S("max_per_req"))
    opts = [x for x in (3, 5, 10, 20, 50) if x <= mx] or [mx]
    cols = [G, P, R, G, P]
    row = [B(f"{x}", f"gn:{key}:{x}", cols[i % 5]) for i, x in enumerate(opts)]
    return [row, [B("🔙 رجوع", "gen", R)]]


# ============================ مساعدات ============================
async def subscribed(uid):
    ch = S("force_channel")
    if not ch or is_admin(uid):
        return True
    try:
        r = await api("getChatMember", chat_id=ch, user_id=uid)
        return r["status"] in ("member", "administrator", "creator", "restricted")
    except APIError:
        return True


async def gate(uid, chat, mid=None):
    """يرجع True لو مسموح للمستخدم يكمل."""
    if S("enabled") != "1" and not is_admin(uid):
        await send(chat, "🔧 البوت متوقف مؤقتاً للصيانة، عد لاحقاً.")
        return False
    if not await subscribed(uid):
        ch = S("force_channel")
        url = f"https://t.me/{ch.lstrip('@')}"
        await send(chat, "⚠️ يجب الاشتراك في القناة أولاً لاستخدام البوت:",
                   [[B("📢 اشترك الآن", url=url, st=G)], [B("✅ تحققت", "home", P)]])
        return False
    return True


def register(u):
    new = not q("SELECT 1 FROM users WHERE id=?", u["id"]).fetchone()
    name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "مستخدم"
    if new:
        q("INSERT INTO users(id,name,username,joined) VALUES(?,?,?,?)", u["id"], name, u.get("username", ""),
          datetime.utcnow().isoformat())
    else:
        q("UPDATE users SET name=?, username=? WHERE id=?", name, u.get("username", ""), u["id"])
    return new, name


def is_banned(uid):
    r = q("SELECT banned FROM users WHERE id=?", uid).fetchone()
    return bool(r and r["banned"])


# ============================ معالجة الرسائل ============================
async def on_message(m):
    if m["chat"]["type"] != "private":
        return
    u, chat = m["from"], m["chat"]["id"]
    uid = u["id"]
    new, name = register(u)
    if new:
        total = q("SELECT COUNT(*) c FROM users").fetchone()["c"]
        un = f"@{u['username']}" if u.get("username") else "لا يوجد"
        try:
            await send(OWNER_ID, f"🆕 <b>عضو جديد دخل البوت</b>\n\n👤 {h(name)}\n🔗 {un}\n🆔 <code>{uid}</code>\n\n📊 المجموع: <b>{total}</b>")
        except Exception:
            pass
    if is_banned(uid) and not is_admin(uid):
        return await send(chat, "🚫 تم حظرك من استخدام البوت.")
    text = (m.get("text") or "").strip()

    if text.startswith("/start"):
        state.pop(uid, None)
        if await gate(uid, chat):
            await send(chat, home_text(name), home_kb(uid))
        return
    if text.startswith("/admin") and is_admin(uid):
        state.pop(uid, None)
        return await send(chat, "👑 <b>لوحة تحكم المطور</b>", admin_kb())
    if uid in state:
        return await on_state(m)
    if await gate(uid, chat):
        await send(chat, home_text(name), home_kb(uid))


async def on_state(m):
    uid, chat = m["from"]["id"], m["chat"]["id"]
    text = (m.get("text") or "").strip()
    key, data = state[uid]

    # ---- حالات المستخدم العادي ----
    if key == "pattern":
        if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", text):
            return await send(chat, "⚠️ نمط غير صالح (5-32 حرف إنجليزي/أرقام/_). حاول مرة أخرى.")
        if not any(valid_username(gen_one(text)) for _ in range(50)):
            return await send(chat, "⚠️ هذا النمط لا ينتج يوزراً صالحاً (لازم يبدأ بحرف ولا ينتهي بـ _).")
        custom_pat[uid] = text
        state.pop(uid)
        return await send(chat, f"✅ النمط: <code>{h(text)}</code>\n\nكم يوزر تريد؟", count_kb("c"))
    if key == "check":
        state.pop(uid)
        u = text.lstrip("@").lower()
        if not valid_username(u):
            return await send(chat, "⚠️ يوزر غير صالح.", [[B("🏠 القائمة", "home", P)]])
        r = await is_taken(u)
        msg = {True: f"❌ <code>@{u}</code> موجود ومستخدم.", False: f"✅ <code>@{u}</code> غير موجود (متاح).",
               None: "⚠️ تعذر الفحص الآن، حاول لاحقاً."}[r]
        return await send(chat, msg, [[B("🔍 فحص آخر", "chk", G)], [B("🏠 القائمة", "home", P)]])

    # ---- حالات الأدمن ----
    if not is_admin(uid):
        state.pop(uid, None)
        return
    back = [[B("🔙 لوحة التحكم", "adm_back", P)]]

    if key == "bc":
        state.pop(uid)
        ids = [r["id"] for r in q("SELECT id FROM users WHERE banned=0").fetchall()]
        await send(chat, f"📢 بدأت الإذاعة إلى {len(ids)} مستخدم...")

        async def go():
            ok = bad = 0
            for i in ids:
                try:
                    await api("copyMessage", chat_id=i, from_chat_id=chat, message_id=m["message_id"])
                    ok += 1
                except Exception:
                    bad += 1
                await asyncio.sleep(0.05)
            await send(chat, f"✅ انتهت الإذاعة\n\n📨 نجح: <b>{ok}</b>\n❌ فشل: <b>{bad}</b>", back)
        asyncio.create_task(go())
    elif key in ("ban", "unban", "aadd", "adel"):
        state.pop(uid)
        if not text.lstrip("-").isdigit():
            return await send(chat, "⚠️ أرسل ID رقمي صحيح.", back)
        t = int(text)
        if key == "ban":
            if t == OWNER_ID:
                return await send(chat, "⚠️ لا يمكن حظر المطور.", back)
            q("UPDATE users SET banned=1 WHERE id=?", t)
            await send(chat, f"🚫 تم حظر <code>{t}</code>", back)
        elif key == "unban":
            q("UPDATE users SET banned=0 WHERE id=?", t)
            await send(chat, f"✅ تم فك الحظر عن <code>{t}</code>", back)
        elif key == "aadd":
            q("INSERT OR IGNORE INTO admins VALUES(?)", t)
            await send(chat, f"👮 تمت إضافة <code>{t}</code> كأدمن", back)
        else:
            q("DELETE FROM admins WHERE id=?", t)
            await send(chat, f"🗑 تم حذف الأدمن <code>{t}</code>", back)
    elif key == "set":
        state.pop(uid)
        k = data
        if k in ("daily_limit", "max_per_req"):
            if not text.isdigit() or (k == "max_per_req" and int(text) < 1):
                return await send(chat, "⚠️ أرسل رقماً صحيحاً.", back)
        elif k == "dev_user":
            text = text.lstrip("@").replace("https://t.me/", "")
        elif k in ("ch1", "ch2"):
            if text.startswith("@"):
                text = "https://t.me/" + text[1:]
            if not text.startswith("http"):
                return await send(chat, "⚠️ أرسل رابطاً يبدأ بـ https:// أو @يوزر.", back)
        elif k == "force_channel" and text == "0":
            text = ""
        setS(k, text)
        await send(chat, "✅ تم الحفظ.", back)
    elif key == "badd_text":
        state[uid] = ("badd_kind", {"text": text})
        await send(chat, "اختر نوع الزر:", [[B("🔗 رابط", "a:bk:url", G), B("💬 رسالة نصية", "a:bk:text", P)],
                                            [B("❌ إلغاء", "adm_back", R)]])
    elif key == "badd_val":
        if data["kind"] == "url":
            if text.startswith("@"):
                text = "https://t.me/" + text[1:]
            if not text.startswith("http"):
                return await send(chat, "⚠️ أرسل رابطاً صحيحاً.")
        data["value"] = text
        state[uid] = ("badd_style", data)
        await send(chat, "🎨 اختر لون الزر:", [[B("🔵 أزرق", "a:bs:primary", P), B("🟢 أخضر", "a:bs:success", G),
                                              B("🔴 أحمر", "a:bs:danger", R)]])
    elif key == "sadd":
        state.pop(uid)
        parts = text.split(None, 1)
        if not parts:
            return await send(chat, "⚠️ الصيغة: @username السعر", back)
        q("INSERT INTO sale(username,price) VALUES(?,?)", parts[0].lstrip("@"), parts[1] if len(parts) > 1 else "—")
        await send(chat, f"✅ تمت إضافة <code>@{h(parts[0].lstrip('@'))}</code> للبيع.", back)


# ============================ معالجة الأزرار ============================
async def on_cb(c):
    uid, chat, mid = c["from"]["id"], c["message"]["chat"]["id"], c["message"]["message_id"]
    d = c["data"]
    try:
        await api("answerCallbackQuery", callback_query_id=c["id"])
    except Exception:
        pass
    new, name = register(c["from"])
    if is_banned(uid) and not is_admin(uid):
        return
    if d == "stop":
        if uid in jobs:
            jobs[uid].set()
        return
    if d in ("home", "adm_back"):
        state.pop(uid, None)
        if d == "adm_back" and is_admin(uid):
            return await show(chat, mid, "👑 <b>لوحة تحكم المطور</b>", admin_kb())
        if await gate(uid, chat):
            return await show(chat, mid, home_text(name), home_kb(uid))
        return
    if not await gate(uid, chat):
        return

    home_back = [[B("🏠 القائمة الرئيسية", "home", P)]]

    # ---------- المستخدم ----------
    if d == "gen":
        kb, row, cols = [], [], [G, P, R]
        for i, (k, (title, _)) in enumerate(PRESETS.items()):
            row.append(B(title, f"gp:{k}", cols[i % 3]))
            if len(row) == 2:
                kb.append(row)
                row = []
        if row:
            kb.append(row)
        kb.append([B("🧩 نمط مخصص", "gp:custom", G)])
        kb.append([B("🔙 رجوع", "home", R)])
        lim = int(S("daily_limit"))
        info = "" if (lim == 0 or is_admin(uid)) else f"\n📅 المتبقي لك اليوم: <b>{max(0, lim - used_today(uid))}</b>"
        return await show(chat, mid, f"🎲 <b>اختر نمط اليوزر:</b>{info}", kb)
    if d == "gp:custom":
        state[uid] = ("pattern", None)
        return await show(chat, mid, "🧩 <b>أرسل النمط</b>\n\n<code>L</code> = حرف\n<code>D</code> = رقم\n<code>X</code> = حرف أو رقم\n"
                                     "وأي شيء آخر يُكتب كما هو.\n\nمثال: <code>LL_LD</code> ← qn_a7\n<code>zeuoLLD</code>",
                          [[B("🔙 رجوع", "gen", R)]])
    if d.startswith("gp:"):
        k = d[3:]
        return await show(chat, mid, f"{PRESETS[k][0]}\n\nكم يوزر تريد؟", count_kb(k))
    if d.startswith("gn:"):
        _, k, n = d.split(":")
        n = int(n)
        if uid in jobs:
            return
        pattern = custom_pat.get(uid) if k == "c" else PRESETS[k][1]
        if not pattern:
            return await show(chat, mid, "⚠️ انتهت الجلسة.", [[B("🎲 ابدأ", "gen", G)]])
        lim = int(S("daily_limit"))
        if lim and not is_admin(uid):
            left = lim - used_today(uid)
            if left <= 0:
                return await show(chat, mid, "⛔ وصلت للحد اليومي، عد غداً أو تواصل مع المطور للمزيد.",
                                  [[B("👨‍💻 المطور", url=f"https://t.me/{S('dev_user')}", st=G)], *home_back])
            n = min(n, left)
        await show(chat, mid, "⏳ <b>جاري البدء...</b>", [[B("⛔ إيقاف", "stop", R)]])
        return await run_job(uid, chat, mid, pattern, n)
    if d == "chk":
        state[uid] = ("check", None)
        return await show(chat, mid, "🔍 أرسل اليوزر الذي تريد فحصه (مثال: <code>@QN_A7</code>)", [[B("🔙 رجوع", "home", R)]])
    if d == "me":
        r = q("SELECT * FROM users WHERE id=?", uid).fetchone()
        lim = int(S("daily_limit"))
        rem = "غير محدود" if (lim == 0 or is_admin(uid)) else max(0, lim - used_today(uid))
        return await show(chat, mid, f"👤 <b>حسابي</b>\n\n🆔 <code>{uid}</code>\n📅 انضممت: {r['joined'][:10]}\n"
                                     f"🎯 مجموع ما وجدته: <b>{r['total']}</b>\n📊 المتبقي اليوم: <b>{rem}</b>", home_back)
    if d == "sale":
        items = q("SELECT * FROM sale ORDER BY id DESC LIMIT 40").fetchall()
        if not items:
            return await show(chat, mid, "🛒 لا توجد يوزرات للبيع حالياً.", home_back)
        dev, kb, cols = S("dev_user"), [], [G, P, R]
        for i, it in enumerate(items):
            msg = quote(f"مرحباً، أريد شراء اليوزر @{it['username']} بسعر {it['price']}")
            kb.append([B(f"🛒 @{it['username']} — {it['price']}", url=f"https://t.me/{dev}?text={msg}", st=cols[i % 3])])
        kb += home_back
        return await show(chat, mid, "🛒 <b>يوزرات للبيع</b>\nاضغط على اليوزر للتواصل مع المطور وشرائه:", kb)
    if d.startswith("cb:"):
        b = q("SELECT * FROM buttons WHERE id=?", int(d[3:])).fetchone()
        if b:
            await show(chat, mid, b["value"], home_back)
        return
    if d == "adm":
        if is_admin(uid):
            return await show(chat, mid, "👑 <b>لوحة تحكم المطور</b>", admin_kb())
        return

    # ---------- الأدمن ----------
    if not d.startswith("a:") or not is_admin(uid):
        return
    back = [[B("🔙 لوحة التحكم", "adm_back", P)]]
    cmd = d[2:]

    if cmd == "stats":
        c = lambda s, *a: q(s, *a).fetchone()[0]
        t = today()
        txt = (f"📊 <b>إحصائيات البوت</b>\n\n👥 المستخدمين: <b>{c('SELECT COUNT(*) FROM users')}</b>\n"
               f"🆕 جدد اليوم: <b>{c('SELECT COUNT(*) FROM users WHERE joined LIKE ?', t + '%')}</b>\n"
               f"🚫 المحظورين: <b>{c('SELECT COUNT(*) FROM users WHERE banned=1')}</b>\n"
               f"🎯 يوزرات تم توليدها: <b>{c('SELECT COUNT(*) FROM found')}</b>\n"
               f"📅 اليوم: <b>{c('SELECT COUNT(*) FROM found WHERE ts LIKE ?', t + '%')}</b>\n"
               f"🛒 للبيع: <b>{c('SELECT COUNT(*) FROM sale')}</b>\n🔘 أزرار مضافة: <b>{c('SELECT COUNT(*) FROM buttons')}</b>\n"
               f"⚡ عمليات جارية: <b>{len(jobs)}</b>")
        return await show(chat, mid, txt, back)
    if cmd == "users":
        rows = q("SELECT * FROM users ORDER BY joined DESC LIMIT 20").fetchall()
        txt = "👥 <b>آخر 20 مستخدم</b>\n\n" + "\n".join(
            f"{'🚫' if r['banned'] else '👤'} {h(r['name'])} | @{r['username'] or '-'} | <code>{r['id']}</code>" for r in rows)
        return await show(chat, mid, txt, back)
    if cmd == "export":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "name", "username", "joined", "banned", "total"])
        for r in q("SELECT * FROM users").fetchall():
            w.writerow([r["id"], r["name"], r["username"], r["joined"], r["banned"], r["total"]])
        await send_file(chat, "users.csv", buf.getvalue().encode("utf-8-sig"), "📥 قائمة المستخدمين")
        return
    prompts = {
        "bc": ("bc", "📢 أرسل الرسالة التي تريد إذاعتها (نص/صورة/ملف...)"),
        "ban": ("ban", "🚫 أرسل ID المستخدم لحظره"),
        "unban": ("unban", "✅ أرسل ID المستخدم لفك حظره"),
        "badd": ("badd_text", "➕ أرسل <b>نص الزر</b>"),
        "sadd": ("sadd", "🛒 أرسل اليوزر والسعر بهذه الصيغة:\n<code>@QN_A7 50$</code>"),
        "aadd": ("aadd", "👮 أرسل ID الأدمن الجديد"),
        "adel": ("adel", "🗑 أرسل ID الأدمن لحذفه"),
    }
    if cmd in prompts:
        if cmd in ("aadd", "adel") and uid != OWNER_ID:
            return
        state[uid] = (prompts[cmd][0], None)
        return await show(chat, mid, prompts[cmd][1], cancel_kb())
    if cmd.startswith("ed:"):
        k = cmd[3:]
        state[uid] = ("set", k)
        extra = "\nاستخدم <code>{name}</code> لاسم المستخدم." if k == "welcome" else ""
        cur = h(S(k)) or "—"
        return await show(chat, mid, f"✏️ القيمة الحالية:\n<code>{cur}</code>\n\nأرسل القيمة الجديدة:{extra}", cancel_kb())
    if cmd == "links":
        kb = [[B("👨‍💻 يوزر المطور", "a:ed:dev_user", G)],
              [B("📢 رابط القناة 1", "a:ed:ch1", P), B("📣 رابط القناة 2", "a:ed:ch2", P)],
              [B("🏷 اسم زر القناة 1", "a:ed:ch1_title", G), B("🏷 اسم زر القناة 2", "a:ed:ch2_title", G)], *back]
        return await show(chat, mid, f"🔗 <b>روابط المطور</b>\n\nالمطور: @{h(S('dev_user'))}\nقناة 1: {h(S('ch1'))}\nقناة 2: {h(S('ch2'))}", kb)
    if cmd == "set":
        on = S("enabled") == "1"
        kb = [[B("🔴 إيقاف البوت" if on else "🟢 تشغيل البوت", "a:tog", R if on else G)],
              [B("📅 الحد اليومي", "a:ed:daily_limit", P), B("🔢 أقصى عدد/طلب", "a:ed:max_per_req", P)],
              [B("📌 اشتراك إجباري", "a:ed:force_channel", G)], *back]
        return await show(chat, mid, f"⚙️ <b>الإعدادات</b>\n\nالحالة: {'يعمل 🟢' if on else 'متوقف 🔴'}\n"
                                     f"الحد اليومي: <b>{S('daily_limit')}</b> (0 = بلا حد)\nأقصى عدد/طلب: <b>{S('max_per_req')}</b>\n"
                                     f"الاشتراك الإجباري: <b>{h(S('force_channel')) or 'معطل'}</b> (أرسل 0 للتعطيل)", kb)
    if cmd == "tog":
        setS("enabled", "0" if S("enabled") == "1" else "1")
        d = "a:set"
        return await on_cb({**c, "data": d, "id": "x"})
    if cmd == "admins":
        ids = [r["id"] for r in q("SELECT id FROM admins").fetchall()]
        txt = f"👮 <b>الأدمنية</b>\n\n👑 <code>{OWNER_ID}</code> (الرئيسي)\n" + "\n".join(f"👮 <code>{i}</code>" for i in ids)
        kb = [[B("➕ إضافة أدمن", "a:aadd", G), B("🗑 حذف أدمن", "a:adel", R)], *back] if uid == OWNER_ID else back
        return await show(chat, mid, txt, kb)
    if cmd.startswith("bk:"):
        if uid in state and state[uid][0] == "badd_kind":
            t = state[uid][1]["text"]
            kind = cmd[3:]
            state[uid] = ("badd_val", {"text": t, "kind": kind})
            return await show(chat, mid, "🔗 أرسل الرابط:" if kind == "url" else "💬 أرسل النص الذي يظهر عند الضغط:", cancel_kb())
        return
    if cmd.startswith("bs:"):
        if uid in state and state[uid][0] == "badd_style":
            dt = state.pop(uid)[1]
            q("INSERT INTO buttons(text,kind,value,style) VALUES(?,?,?,?)", dt["text"], dt["kind"], dt["value"], cmd[3:])
            return await show(chat, mid, "✅ تمت إضافة الزر للقائمة الرئيسية.", back)
        return
    if cmd == "bdel":
        rows = q("SELECT * FROM buttons").fetchall()
        kb = [[B(f"🗑 {r['text']}", f"a:bd:{r['id']}", R)] for r in rows] + back
        return await show(chat, mid, "اختر الزر لحذفه:" if rows else "لا توجد أزرار مضافة.", kb)
    if cmd.startswith("bd:"):
        q("DELETE FROM buttons WHERE id=?", int(cmd[3:]))
        return await show(chat, mid, "🗑 تم حذف الزر.", back)
    if cmd == "sdel":
        rows = q("SELECT * FROM sale").fetchall()
        kb = [[B(f"🗑 @{r['username']} — {r['price']}", f"a:sd:{r['id']}", R)] for r in rows[:50]] + back
        return await show(chat, mid, "اختر اليوزر لحذفه:" if rows else "لا توجد يوزرات للبيع.", kb)
    if cmd.startswith("sd:"):
        q("DELETE FROM sale WHERE id=?", int(cmd[3:]))
        return await show(chat, mid, "🗑 تم الحذف.", back)


# ============================ التشغيل ============================
async def handle(u):
    try:
        if "message" in u:
            await on_message(u["message"])
        elif "callback_query" in u:
            await on_cb(u["callback_query"])
    except Exception:
        log.exception("handler error")


async def main():
    global session
    session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=70))
    me = await api("getMe")
    log.info("Bot started: @%s", me["username"])
    offset = 0
    while True:
        try:
            ups = await api("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "callback_query"])
        except Exception as e:
            log.warning("polling error: %s", e)
            await asyncio.sleep(3)
            continue
        for u in ups:
            offset = u["update_id"] + 1
            asyncio.create_task(handle(u))


if __name__ == "__main__":
    if BOT_TOKEN.startswith("PUT_"):
        raise SystemExit("ضع توكن البوت في BOT_TOKEN أولاً")
    asyncio.run(main())
