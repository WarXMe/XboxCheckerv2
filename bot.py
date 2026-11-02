"""
Xbox Checker Telegram Bot — Webhook mode for Render deployment.
"""

import asyncio
import json
import os
import time
import logging
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, FSInputFile, BufferedInputFile,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import (
    BOT_TOKEN, WEBHOOK_URL, WEBHOOK_PATH, PORT,
    ADMIN_IDS, CAPTURED_BY, BOT_VERSION,
    DEFAULT_THREADS, MAX_THREADS, MAX_COMBO_MB,
    RESULTS_DIR, STATS_FILE, PROXY_SOURCES,
)
from proxy_manager import ProxyManager
from checker import ResultWriter, run_checker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


# ─── FSM States ───────────────────────────────────────────────────────────────
class CheckSingle(StatesGroup):
    waiting = State()

class SetThreads(StatesGroup):
    waiting = State()

class UploadProxy(StatesGroup):
    waiting = State()


# ─── Per-user sessions ────────────────────────────────────────────────────────
_sessions: dict[int, dict] = {}

def _get_session(uid: int) -> dict:
    if uid not in _sessions:
        _sessions[uid] = {
            "combos":      [],
            "combo_name":  "None",
            "proxy_mgr":   None,
            "threads":     DEFAULT_THREADS,
            "running":     False,
            "kill":        [False],
            "pause":       [False],
            "counters":    {},
            "live_msg_id": None,
            "task":        None,
            "queue":       [],   # pending combo files if running
            "writer":      None,
            "session_dir": None,
        }
    return _sessions[uid]


# ─── Stats ────────────────────────────────────────────────────────────────────
def _load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return json.load(f)
    return {"total_checked": 0, "total_hits": 0, "total_2fa": 0}

def _save_stats(s: dict):
    with open(STATS_FILE, "w") as f:
        json.dump(s, f)

def _add_stats(checked=0, hits=0, tfa=0):
    s = _load_stats()
    s["total_checked"] = s.get("total_checked", 0) + checked
    s["total_hits"]    = s.get("total_hits",    0) + hits
    s["total_2fa"]     = s.get("total_2fa",     0) + tfa
    _save_stats(s)


# ─── Keyboards ────────────────────────────────────────────────────────────────
def kb_main(uid: int) -> InlineKeyboardMarkup:
    sess    = _get_session(uid)
    running = sess["running"]
    combos  = len(sess["combos"])
    proxy   = sess["proxy_mgr"].alive if sess["proxy_mgr"] else 0
    threads = sess["threads"]

    rows = [
        [
            InlineKeyboardButton(text="🎯 Check Account",  callback_data="check_single"),
            InlineKeyboardButton(text="📂 Check File",     callback_data="check_file"),
        ],
        [
            InlineKeyboardButton(text=f"📋 Combo: {combos} lines", callback_data="combo_info"),
            InlineKeyboardButton(text=f"🌐 Proxies: {proxy}",      callback_data="proxy_info"),
        ],
        [
            InlineKeyboardButton(text=f"⚙️ Threads: {threads}", callback_data="set_threads"),
            InlineKeyboardButton(text="📊 Queue Status",         callback_data="queue_status"),
        ],
    ]

    if running:
        rows.append([
            InlineKeyboardButton(text="⏸ Pause",  callback_data="pause"),
            InlineKeyboardButton(text="▶️ Resume", callback_data="resume"),
            InlineKeyboardButton(text="🛑 Kill",   callback_data="kill"),
        ])

    rows += [
        [
            InlineKeyboardButton(text="📈 My Stats", callback_data="my_stats"),
            InlineKeyboardButton(text="ℹ️ About",    callback_data="about"),
        ],
        [
            InlineKeyboardButton(text="👑 Owner",          callback_data="owner"),
            InlineKeyboardButton(text="🔧 Proxy Settings", callback_data="proxy_settings"),
        ],
        [
            InlineKeyboardButton(text="🕷 Scrape Proxies", callback_data="scrape_proxies"),
        ],
    ]

    if uid in ADMIN_IDS:
        rows.append([
            InlineKeyboardButton(text="🛠 Admin Panel", callback_data="admin_panel"),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_proxy_settings() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Upload Proxy",  callback_data="upload_proxy"),
            InlineKeyboardButton(text="🕷 Scrape",        callback_data="scrape_proxies"),
        ],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back_main")],
    ])


# ─── Progress bar helper ──────────────────────────────────────────────────────
def _progress_bar(done: int, total: int, width: int = 10) -> str:
    if total == 0:
        return "░" * width
    filled = int(width * done / total)
    return "█" * filled + "░" * (width - filled)


# ─── Live updater ─────────────────────────────────────────────────────────────
async def _live_updater(uid: int, total: int):
    sess = _get_session(uid)
    while sess["running"]:
        await asyncio.sleep(3)
        c = sess["counters"]
        done  = c.get("checked", 0)
        hits  = c.get("hits", 0)
        bad   = c.get("bad", 0)
        tfa   = c.get("2fa", 0)
        errs  = c.get("errors", 0)
        cps   = c.get("cps", 0.0)
        pct   = int(100 * done / total) if total else 0
        bar   = _progress_bar(done, total)
        text  = (
            f"⚡ <b>XBOX CHECKER V{BOT_VERSION}</b>\n\n"
            f"[{bar}] {pct}%\n\n"
            f"📦 Total   : <code>{total}</code>\n"
            f"✅ Checked : <code>{done}</code>\n"
            f"🎯 Hits    : <code>{hits}</code>\n"
            f"❌ Bad     : <code>{bad}</code>\n"
            f"🔐 2FA     : <code>{tfa}</code>\n"
            f"⚠️ Errors  : <code>{errs}</code>\n"
            f"🚀 CPS     : <code>{cps}</code>\n\n"
            f"🌐 Proxies : <code>{sess['proxy_mgr'].alive if sess['proxy_mgr'] else 'N/A'}</code>\n"
            f"⚙️ Threads : <code>{sess['threads']}</code>"
        )
        if sess["live_msg_id"]:
            try:
                await bot.edit_message_text(
                    text, chat_id=uid, message_id=sess["live_msg_id"],
                    parse_mode="HTML",
                )
            except Exception:
                pass
        if done >= total:
            break


# ─── Start check ─────────────────────────────────────────────────────────────
async def _start_check(uid: int, combos: list, combo_name: str):
    sess = _get_session(uid)
    if sess["running"]:
        sess["queue"].append((combos, combo_name))
        await bot.send_message(uid, f"⏳ Added to queue: <code>{combo_name}</code> ({len(combos)} lines)", parse_mode="HTML")
        return

    sess["running"]   = True
    sess["kill"]      = [False]
    sess["pause"]     = [False]
    sess["counters"]  = {}

    now_str     = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = os.path.join(RESULTS_DIR, f"{uid}_{now_str}")
    writer      = ResultWriter(session_dir, CAPTURED_BY)
    writer.write_headers(combo_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    sess["writer"]      = writer
    sess["session_dir"] = session_dir

    msg = await bot.send_message(uid,
        f"⚡ <b>XBOX CHECKER V{BOT_VERSION}</b>\n\n"
        f"Starting check on <code>{combo_name}</code>...\n"
        f"Total: <code>{len(combos)}</code> combos",
        parse_mode="HTML",
    )
    sess["live_msg_id"] = msg.message_id

    async def _run():
        try:
            await asyncio.gather(
                run_checker(
                    combos, sess["proxy_mgr"], writer,
                    sess["threads"], sess["kill"], sess["pause"], sess["counters"],
                ),
                _live_updater(uid, len(combos)),
            )
        finally:
            sess["running"] = False
            c = sess["counters"]
            _add_stats(c.get("checked", 0), c.get("hits", 0), c.get("2fa", 0))

            # send result files
            result_files = writer.get_files()
            if result_files:
                await bot.send_message(uid, "✅ <b>Check complete! Sending results...</b>", parse_mode="HTML")
                for key, path in result_files.items():
                    try:
                        await bot.send_document(
                            uid,
                            FSInputFile(path),
                            caption=f"📄 <b>{os.path.basename(path)}</b>",
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.error(f"Failed to send {path}: {e}")
            else:
                await bot.send_message(uid, "✅ Check complete. No hits found.")

            # process queue
            if sess["queue"]:
                next_combos, next_name = sess["queue"].pop(0)
                await _start_check(uid, next_combos, next_name)

    sess["task"] = asyncio.create_task(_run())


# ─── Handlers ─────────────────────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(msg: Message):
    uid  = msg.from_user.id
    name = msg.from_user.first_name or "there"
    text = (
        f"👋 <b>Welcome, {name}!</b>\n\n"
        f"⚡ <b>XBOX CHECKER V{BOT_VERSION}</b>\n"
        f"🎯 <i>Fast • Accurate • Full Capture</i>\n\n"
        f"Captured By: <code>{CAPTURED_BY}</code>"
    )
    await msg.answer(text, reply_markup=kb_main(uid), parse_mode="HTML")


@dp.message(Command("menu"))
async def cmd_menu(msg: Message):
    uid = msg.from_user.id
    await msg.answer("📋 <b>Main Menu</b>", reply_markup=kb_main(uid), parse_mode="HTML")


# ─── Callback handlers ────────────────────────────────────────────────────────
@dp.callback_query(F.data == "back_main")
async def cb_back(call: CallbackQuery):
    uid = call.from_user.id
    await call.message.edit_text("📋 <b>Main Menu</b>", reply_markup=kb_main(uid), parse_mode="HTML")


@dp.callback_query(F.data == "check_single")
async def cb_check_single(call: CallbackQuery, state: FSMContext):
    await call.message.answer("📧 Send me <code>email:password</code> to check a single account:", parse_mode="HTML")
    await state.set_state(CheckSingle.waiting)
    await call.answer()


@dp.message(CheckSingle.waiting)
async def handle_single_check(msg: Message, state: FSMContext):
    await state.clear()
    text = msg.text.strip() if msg.text else ""
    if ":" not in text:
        await msg.reply("❌ Invalid format. Use <code>email:password</code>", parse_mode="HTML")
        return
    parts    = text.split(":", 1)
    email, pw = parts[0].strip(), parts[1].strip()
    uid      = msg.from_user.id
    await _start_check(uid, [(email, pw)], f"{email}")


@dp.callback_query(F.data == "check_file")
async def cb_check_file(call: CallbackQuery):
    await call.message.answer(
        "📂 Upload your combo file (<code>email:pass</code> format, max 500MB):",
        parse_mode="HTML",
    )
    await call.answer()


@dp.message(F.document)
async def handle_document(msg: Message):
    uid  = msg.from_user.id
    doc  = msg.document
    if doc.file_size > MAX_COMBO_MB * 1024 * 1024:
        await msg.reply(f"❌ File too large. Max {MAX_COMBO_MB}MB.")
        return

    # check if proxy upload
    sess = _get_session(uid)
    # download file
    wait_msg = await msg.reply("⏳ Downloading file...")
    file = await bot.get_file(doc.file_id)
    import io
    buf = io.BytesIO()
    await bot.download_file(file.file_path, destination=buf)
    buf.seek(0)
    content = buf.read().decode("utf-8", errors="ignore")
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    # detect proxy file vs combo file
    is_proxy = all(":" in l and not l.count(":") == 1 or "@" not in l for l in lines[:20])
    valid_combos = [(p[0].strip(), p[1].strip()) for l in lines
                    if ":" in l for p in [l.split(":", 1)] if len(p) == 2]

    if not valid_combos:
        # try as proxy file
        proxies = []
        for l in lines:
            l = l.replace("http://", "").replace("https://", "")
            if ":" in l:
                proxies.append(f"http://{l}")
        if proxies:
            sess["proxy_mgr"] = ProxyManager(proxies)
            await wait_msg.edit_text(
                f"✅ Loaded <code>{len(proxies)}</code> proxies!",
                parse_mode="HTML",
                reply_markup=kb_main(uid),
            )
            return
        await wait_msg.edit_text("❌ No valid combos or proxies found in file.")
        return

    sess["combos"]     = valid_combos
    sess["combo_name"] = doc.file_name or "combo.txt"
    await wait_msg.edit_text(
        f"✅ Loaded <code>{len(valid_combos)}</code> combos from <code>{doc.file_name}</code>\n\n"
        f"Press <b>Check File</b> to start or upload more files.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Start Check", callback_data="start_loaded")],
            [InlineKeyboardButton(text="🔙 Back",        callback_data="back_main")],
        ]),
    )


@dp.callback_query(F.data == "start_loaded")
async def cb_start_loaded(call: CallbackQuery):
    uid  = call.from_user.id
    sess = _get_session(uid)
    if not sess["combos"]:
        await call.answer("❌ No combo loaded!", show_alert=True)
        return
    await call.message.delete()
    await _start_check(uid, sess["combos"], sess["combo_name"])
    await call.answer()


@dp.callback_query(F.data == "set_threads")
async def cb_set_threads(call: CallbackQuery, state: FSMContext):
    await call.message.answer(
        f"⚙️ Current threads: <code>{_get_session(call.from_user.id)['threads']}</code>\n"
        f"Send new thread count (1 - {MAX_THREADS}):",
        parse_mode="HTML",
    )
    await state.set_state(SetThreads.waiting)
    await call.answer()


@dp.message(SetThreads.waiting)
async def handle_set_threads(msg: Message, state: FSMContext):
    await state.clear()
    try:
        n = int(msg.text.strip())
        if not 1 <= n <= MAX_THREADS:
            raise ValueError
        uid = msg.from_user.id
        _get_session(uid)["threads"] = n
        await msg.reply(f"✅ Threads set to <code>{n}</code>", parse_mode="HTML", reply_markup=kb_main(uid))
    except ValueError:
        await msg.reply(f"❌ Invalid. Enter a number between 1 and {MAX_THREADS}.")


@dp.callback_query(F.data == "pause")
async def cb_pause(call: CallbackQuery):
    sess = _get_session(call.from_user.id)
    sess["pause"][0] = True
    await call.answer("⏸ Paused", show_alert=False)
    await call.message.edit_reply_markup(reply_markup=kb_main(call.from_user.id))


@dp.callback_query(F.data == "resume")
async def cb_resume(call: CallbackQuery):
    sess = _get_session(call.from_user.id)
    sess["pause"][0] = False
    await call.answer("▶️ Resumed", show_alert=False)


@dp.callback_query(F.data == "kill")
async def cb_kill(call: CallbackQuery):
    sess = _get_session(call.from_user.id)
    sess["kill"][0]  = True
    sess["pause"][0] = False
    await call.answer("🛑 Stopping...", show_alert=False)


@dp.callback_query(F.data == "proxy_settings")
async def cb_proxy_settings(call: CallbackQuery):
    sess  = _get_session(call.from_user.id)
    alive = sess["proxy_mgr"].alive if sess["proxy_mgr"] else 0
    total = sess["proxy_mgr"].total if sess["proxy_mgr"] else 0
    text  = (
        f"🌐 <b>Proxy Settings</b>\n\n"
        f"Alive : <code>{alive}</code>\n"
        f"Total : <code>{total}</code>"
    )
    await call.message.edit_text(text, reply_markup=kb_proxy_settings(), parse_mode="HTML")


@dp.callback_query(F.data == "upload_proxy")
async def cb_upload_proxy(call: CallbackQuery):
    await call.message.answer("📤 Upload your proxy file (<code>ip:port</code> format):", parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "scrape_proxies")
async def cb_scrape(call: CallbackQuery):
    uid = call.from_user.id
    msg = await call.message.answer("🕷 Scraping proxies from public sources...")
    try:
        pm = await ProxyManager.scrape()
        _get_session(uid)["proxy_mgr"] = pm
        await msg.edit_text(
            f"✅ Scraped <code>{pm.total}</code> proxies!",
            parse_mode="HTML",
            reply_markup=kb_main(uid),
        )
    except Exception as e:
        await msg.edit_text(f"❌ Scrape failed: {e}")
    await call.answer()


@dp.callback_query(F.data == "my_stats")
async def cb_my_stats(call: CallbackQuery):
    s    = _load_stats()
    text = (
        f"📈 <b>Your Stats</b>\n\n"
        f"Total Checked : <code>{s.get('total_checked', 0)}</code>\n"
        f"Total Hits    : <code>{s.get('total_hits', 0)}</code>\n"
        f"Total 2FA     : <code>{s.get('total_2fa', 0)}</code>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    text = (
        f"⚡ <b>XBOX CHECKER V{BOT_VERSION}</b>\n\n"
        f"🎯 Full capture: Gamertag, GScore, GamePass,\n"
        f"   Fortnite, Minecraft Java (Name/UUID/Capes)\n\n"
        f"🚀 High-speed async checking\n"
        f"🌐 Proxy support with auto-scrape\n"
        f"📂 Separate hit files per category\n\n"
        f"Captured By: <code>{CAPTURED_BY}</code>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "owner")
async def cb_owner(call: CallbackQuery):
    await call.message.answer(f"👑 <b>Owner:</b> {CAPTURED_BY}", parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "queue_status")
async def cb_queue(call: CallbackQuery):
    sess = _get_session(call.from_user.id)
    q    = len(sess.get("queue", []))
    text = (
        f"📊 <b>Queue Status</b>\n\n"
        f"Running : {'Yes ✅' if sess['running'] else 'No ❌'}\n"
        f"Queued  : <code>{q}</code> file(s) waiting"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "combo_info")
async def cb_combo_info(call: CallbackQuery):
    sess = _get_session(call.from_user.id)
    text = (
        f"📋 <b>Combo Info</b>\n\n"
        f"File    : <code>{sess['combo_name']}</code>\n"
        f"Lines   : <code>{len(sess['combos'])}</code>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "proxy_info")
async def cb_proxy_info(call: CallbackQuery):
    sess  = _get_session(call.from_user.id)
    pm    = sess["proxy_mgr"]
    text  = (
        f"🌐 <b>Proxy Info</b>\n\n"
        f"Alive : <code>{pm.alive if pm else 0}</code>\n"
        f"Total : <code>{pm.total if pm else 0}</code>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


@dp.callback_query(F.data == "admin_panel")
async def cb_admin(call: CallbackQuery):
    uid = call.from_user.id
    if uid not in ADMIN_IDS:
        await call.answer("❌ Not authorized", show_alert=True)
        return
    s    = _load_stats()
    text = (
        f"🛠 <b>Admin Panel</b>\n\n"
        f"Active Sessions : <code>{len([s for s in _sessions.values() if s['running']])}</code>\n"
        f"Total Sessions  : <code>{len(_sessions)}</code>\n\n"
        f"Global Checked  : <code>{s.get('total_checked', 0)}</code>\n"
        f"Global Hits     : <code>{s.get('total_hits', 0)}</code>\n"
        f"Global 2FA      : <code>{s.get('total_2fa', 0)}</code>"
    )
    await call.message.answer(text, parse_mode="HTML")
    await call.answer()


# ─── Webhook setup ────────────────────────────────────────────────────────────
async def on_startup(app):
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
    logger.info(f"Webhook set: {WEBHOOK_URL}{WEBHOOK_PATH}")

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
