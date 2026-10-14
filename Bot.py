import os
import sqlite3
import logging
import secrets
import hashlib
import hmac
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

BOT_TOKEN = os.getenv("8864383330:AAHm2b7kNnwXxZALYn0oOYeUFF6SwvRKQj8", "").strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv("7018286267", "").split(",") if x.strip().isdigit()}
DB_PATH = os.getenv("DB_PATH", "store.db")
STORE_NAME = os.getenv("DIGI BOT MS", "Premium Blue Store")
SUPPORT_USERNAME = os.getenv("@noperaonxvision", "").strip().lstrip("@")
UPI_ID = os.getenv("6005932725@fam", "").strip()
UPI_NAME = os.getenv("MUKESH SINGH", "Store Owner").strip()
CURRENCY = os.getenv("CURRENCY", "INR")
MIN_DEPOSIT = Decimal(os.getenv("MIN_DEPOSIT", "1"))
MAX_DEPOSIT = Decimal(os.getenv("MAX_DEPOSIT", "50000"))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "product_files"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Optional gateway settings. This starter deliberately does not guess a provider's API.
GATEWAY_NAME = os.getenv("GATEWAY_NAME", "your payment gateway")
GATEWAY_CHECKOUT_URL = os.getenv("GATEWAY_CHECKOUT_URL", "").strip()

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
          balance_paise INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', price_paise INTEGER NOT NULL CHECK(price_paise >= 0),
          delivery_type TEXT NOT NULL CHECK(delivery_type IN ('file','link')),
          delivery_value TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deposits(
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount_paise INTEGER NOT NULL CHECK(amount_paise > 0),
          method TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          reference TEXT, gateway_order_id TEXT, created_at TEXT NOT NULL, reviewed_at TEXT,
          FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS orders(
          id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL,
          product_name TEXT NOT NULL, amount_paise INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'paid', created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS order_deliveries(
          order_id TEXT PRIMARY KEY, delivery_value TEXT NOT NULL,
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('announcements','No announcements yet.')")

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def money(paise):
    return f"₹{paise / 100:.2f}"

def parse_amount(raw):
    try:
        d = Decimal(raw.strip())
        if not d.is_finite() or d <= 0 or d.as_tuple().exponent < -2:
            return None
        return int(d * 100) if d * 100 == (d * 100).to_integral_value() else None
    except (InvalidOperation, AttributeError):
        return None

def is_admin(user_id):
    return user_id in ADMIN_IDS

def main_menu(user_id):
    rows = [
        [InlineKeyboardButton("🛍️ Buy Product", callback_data="buy"),
         InlineKeyboardButton("💳 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("📦 My Products", callback_data="myproducts"),
         InlineKeyboardButton("🧾 My Orders", callback_data="orders")],
        [InlineKeyboardButton("👛 My Wallet", callback_data="wallet"),
         InlineKeyboardButton("📢 Announcements", callback_data="announcements")],
        [InlineKeyboardButton("🎧 Support", callback_data="support")]
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Product", callback_data="admin_add")],
        [InlineKeyboardButton("📋 List / Disable Products", callback_data="admin_products")],
        [InlineKeyboardButton("💰 Pending Deposits", callback_data="admin_deposits")],
        [InlineKeyboardButton("📣 Set Announcement", callback_data="admin_announcement")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="home")]
    ])

async def ensure_user(update: Update):
    u = update.effective_user
    if not u:
        return
    with db() as c:
        c.execute("""INSERT INTO users(user_id,username,first_name,created_at) VALUES(?,?,?,?)
          ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
          (u.id, u.username or "", u.first_name or "", now()))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    u = update.effective_user
    text = (f"👑 <b>Welcome to {STORE_NAME}!</b>\n\n"
            "💙 Your premium digital product store.\n"
            "Browse products, add wallet funds, track orders and get secure digital delivery.\n\n"
            "Choose an option below to get started.")
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=main_menu(u.id))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "/start — Open store\n/cancel — Cancel current action\n\n"
        "Admin commands are available to configured admin IDs only."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled. Use /start to return to the store.")

async def show_products(q, context):
    with db() as c:
        products = c.execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    if not products:
        await q.message.reply_text("📦 No products are available yet.")
        return
    for p in products:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            f"Buy for {money(p['price_paise'])}", callback_data=f"product:{p['id']}")]])
        desc = p["description"] or "Digital product"
        await q.message.reply_text(
            f"💎 <b>{p['name']}</b>\n{desc}\n\nPrice: <b>{money(p['price_paise'])}</b>",
            parse_mode="HTML", reply_markup=kb)

async def wallet(q, user_id):
    with db() as c:
        r = c.execute("SELECT balance_paise FROM users WHERE user_id=?", (user_id,)).fetchone()
    bal = r["balance_paise"] if r else 0
    await q.message.reply_text(f"👛 <b>My Wallet</b>\nAvailable balance: <b>{money(bal)}</b>",
                               parse_mode="HTML", reply_markup=main_menu(user_id))

async def orders(q, user_id):
    with db() as c:
        rows = c.execute("SELECT id,product_name,amount_paise,status,created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 15",
                         (user_id,)).fetchall()
    if not rows:
        await q.message.reply_text("🧾 You have no orders yet.")
        return
    text = "🧾 <b>Your recent orders</b>\n\n" + "\n".join(
        f"• <code>{r['id']}</code> — {r['product_name']} — {money(r['amount_paise'])} ({r['status']})"
        for r in rows)
    await q.message.reply_text(text, parse_mode="HTML")

async def myproducts(q, user_id):
    with db() as c:
        rows = c.execute("""SELECT DISTINCT o.product_name, o.id, d.delivery_value
          FROM orders o JOIN order_deliveries d ON d.order_id=o.id
          WHERE o.user_id=? ORDER BY o.created_at DESC LIMIT 20""", (user_id,)).fetchall()
    if not rows:
        await q.message.reply_text("📦 Your purchased products will appear here after a successful order.")
        return
    text = "📦 <b>Your purchased products</b>\n\n"
    for r in rows:
        val = r["delivery_value"]
        text += f"• <b>{r['product_name']}</b>\n"
        text += (f"Download: {val}\n" if val.startswith("http://") or val.startswith("https://")
                 else f"File: {Path(val).name}\n")
    await q.message.reply_text(text, parse_mode="HTML")

async def deposit(q, context):
    context.user_data["state"] = "deposit_amount"
    await q.message.reply_text(
        f"💳 Enter deposit amount in INR (₹{MIN_DEPOSIT}–₹{MAX_DEPOSIT}).\n"
        "Example: 100 or 250.50\nSend /cancel to stop."
    )

async def support(q):
    if SUPPORT_USERNAME:
        await q.message.reply_text(f"🎧 Contact support: @{SUPPORT_USERNAME}")
    else:
        await q.message.reply_text("🎧 Support is not configured yet. Please contact the store admin.")

async def announcements(q):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key='announcements'").fetchone()
    await q.message.reply_text("📢 <b>Announcements</b>\n\n" + (row["value"] if row else "No announcements yet."),
                               parse_mode="HTML")

async def admin_deposits(q):
    with db() as c:
        rows = c.execute("SELECT * FROM deposits WHERE status='pending' ORDER BY created_at LIMIT 20").fetchall()
    if not rows:
        await q.message.reply_text("No pending deposits.")
        return
    for d in rows:
        await q.message.reply_text(
            f"Pending deposit <code>{d['id']}</code>\nUser: <code>{d['user_id']}</code>\n"
            f"Amount: {money(d['amount_paise'])}\nMethod: {d['method']}\nReference: {d['reference'] or 'not provided'}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"depapprove:{d['id']}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"depreject:{d['id']}")
            ]]))

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""
    if data == "home":
        await q.message.reply_text(f"💙 {STORE_NAME}", reply_markup=main_menu(uid))
    elif data == "buy":
        await show_products(q, context)
    elif data == "wallet":
        await wallet(q, uid)
    elif data == "orders":
        await orders(q, uid)
    elif data == "myproducts":
        await myproducts(q, uid)
    elif data == "deposit":
        await deposit(q, context)
    elif data == "support":
        await support(q)
    elif data == "announcements":
        await announcements(q)
    elif data == "admin":
        if not is_admin(uid):
            await q.message.reply_text("⛔ Admin only.")
        else:
            await q.message.reply_text("⚙️ <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_menu())
    elif data == "admin_add":
        if not is_admin(uid):
            return
        context.user_data["state"] = "add_name"
        await q.message.reply_text("Send product name.")
    elif data == "admin_products":
        if not is_admin(uid):
            return
        with db() as c:
            rows = c.execute("SELECT id,name,price_paise,active FROM products ORDER BY id DESC LIMIT 30").fetchall()
        if not rows:
            await q.message.reply_text("No products yet.")
        for p in rows:
            await q.message.reply_text(
                f"#{p['id']} {p['name']} — {money(p['price_paise'])} — {'Active' if p['active'] else 'Disabled'}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    "Disable" if p["active"] else "Enable", callback_data=f"toggle:{p['id']}")]]))
    elif data == "admin_deposits":
        if is_admin(uid):
            await admin_deposits(q)
    elif data == "admin_announcement":
        if is_admin(uid):
            context.user_data["state"] = "announcement"
            await q.message.reply_text("Send the new announcement text.")
    elif data.startswith("product:"):
        try:
            pid = int(data.split(":",1)[1])
        except ValueError:
            return
        with db() as c:
            p = c.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not p:
            await q.message.reply_text("This product is unavailable.")
            return
        with db() as c:
            u = c.execute("SELECT balance_paise FROM users WHERE user_id=?", (uid,)).fetchone()
        balance = u["balance_paise"] if u else 0
        if balance < p["price_paise"]:
            await q.message.reply_text(
                f"Insufficient wallet balance. Product costs {money(p['price_paise'])}; your balance is {money(balance)}. Please deposit first.")
            return
        order_id = "ORD-" + secrets.token_hex(5).upper()
        # Balance debit + order + delivery record are atomic.
        try:
            with db() as c:
                c.execute("BEGIN IMMEDIATE")
                cur = c.execute("UPDATE users SET balance_paise=balance_paise-? WHERE user_id=? AND balance_paise>=?",
                                (p["price_paise"], uid, p["price_paise"]))
                if cur.rowcount != 1:
                    raise RuntimeError("Insufficient balance")
                c.execute("INSERT INTO orders(id,user_id,product_id,product_name,amount_paise,status,created_at) VALUES(?,?,?,?,?,'paid',?)",
                          (order_id, uid, pid, p["name"], p["price_paise"], now()))
                c.execute("INSERT INTO order_deliveries(order_id,delivery_value) VALUES(?,?)", (order_id, p["delivery_value"]))
                c.commit()
        except Exception:
            await q.message.reply_text("Could not complete the order. Check your wallet balance and try again.")
            return
        await q.message.reply_text(f"✅ <b>Purchase successful!</b>\nOrder: <code>{order_id}</code>\nProduct: {p['name']}",
                                   parse_mode="HTML")
        val = p["delivery_value"]
        if p["delivery_type"] == "file":
            path = Path(val)
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.exists() and path.is_file():
                try:
                    with path.open("rb") as f:
                        await q.message.reply_document(document=f, filename=path.name, caption=f"📦 {p['name']}")
                except Exception:
                    await q.message.reply_text("Your order is saved, but the file could not be sent. Contact support.")
            else:
                await q.message.reply_text("Your order is saved, but the product file is missing. Contact support.")
        else:
            await q.message.reply_text(f"🔗 Your delivery link:\n{val}")
    elif data.startswith("toggle:"):
        if not is_admin(uid):
            return
        pid = int(data.split(":",1)[1])
        with db() as c:
            c.execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (pid,))
        await q.message.reply_text("Product status updated.")
    elif data.startswith("depapprove:") or data.startswith("depreject:"):
        if not is_admin(uid):
            return
        approve = data.startswith("depapprove:")
        dep_id = data.split(":",1)[1]
        user_to_notify = None
        amount = 0
        with db() as c:
            c.execute("BEGIN IMMEDIATE")
            d = c.execute("SELECT * FROM deposits WHERE id=?", (dep_id,)).fetchone()
            if not d or d["status"] != "pending":
                c.rollback()
                await q.message.reply_text("Deposit already reviewed or not found.")
                return
            status = "approved" if approve else "rejected"
            c.execute("UPDATE deposits SET status=?,reviewed_at=? WHERE id=? AND status='pending'",
                      (status, now(), dep_id))
            if approve:
                c.execute("UPDATE users SET balance_paise=balance_paise+? WHERE user_id=?",
                          (d["amount_paise"], d["user_id"]))
            user_to_notify, amount = d["user_id"], d["amount_paise"]
            c.commit()
        await q.message.reply_text(f"{'Approved' if approve else 'Rejected'} deposit {dep_id}.")
        try:
            await context.bot.send_message(user_to_notify,
                f"{'✅ Deposit approved' if approve else '❌ Deposit rejected'}\nAmount: {money(amount)}\nReference: {dep_id}")
        except Exception:
            pass

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    state = context.user_data.get("state")
    uid = update.effective_user.id
    raw = (update.effective_message.text or "").strip()
    if not state:
        await update.effective_message.reply_text("Choose an option from /start.", reply_markup=main_menu(uid))
        return
    if state == "deposit_amount":
        amount = parse_amount(raw)
        if amount is None or amount < int(MIN_DEPOSIT*100) or amount > int(MAX_DEPOSIT*100):
            await update.effective_message.reply_text(f"Enter a valid amount between ₹{MIN_DEPOSIT} and ₹{MAX_DEPOSIT}, up to 2 decimal places.")
            return
        dep_id = "DEP-" + secrets.token_hex(5).upper()
        context.user_data["deposit_amount"] = amount
        context.user_data["deposit_id"] = dep_id
        context.user_data["state"] = "deposit_method"
        kb = [[InlineKeyboardButton("🇮🇳 UPI (manual verification)", callback_data="unused")]]
        if GATEWAY_CHECKOUT_URL:
            kb.append([InlineKeyboardButton(f"⚡ {GATEWAY_NAME}", url=GATEWAY_CHECKOUT_URL)])
        await update.effective_message.reply_text(
            f"Deposit request: {money(amount)}\nChoose UPI below, or use the configured gateway link if available.",
            reply_markup=InlineKeyboardMarkup(kb))
        # UPI instructions are sent directly; payment proof is not treated as verified payment.
        if UPI_ID:
            await update.effective_message.reply_text(
                f"Pay {money(amount)} to UPI ID: <code>{UPI_ID}</code>\nName: {UPI_NAME}\n"
                "After payment, send the UPI transaction/reference ID here. Admin will verify it before adding balance.",
                parse_mode="HTML")
        else:
            await update.effective_message.reply_text("UPI is not configured. Ask the admin to set UPI_ID in .env.")
        context.user_data["state"] = "deposit_reference"
    elif state == "deposit_reference":
        amount = context.user_data.get("deposit_amount")
        dep_id = context.user_data.get("deposit_id")
        if not amount or not dep_id:
            context.user_data.clear()
            await update.effective_message.reply_text("Session expired. Please start again with /start.")
            return
        if len(raw) < 4 or len(raw) > 100:
            await update.effective_message.reply_text("Please send a valid transaction/reference ID (4–100 characters).")
            return
        # Duplicate references are rejected for manual review protection.
        with db() as c:
            exists = c.execute("SELECT 1 FROM deposits WHERE reference=? AND status!='rejected'", (raw,)).fetchone()
            if exists:
                await update.effective_message.reply_text("That reference has already been submitted. Contact support if this is an error.")
                return
            c.execute("INSERT INTO deposits(id,user_id,amount_paise,method,status,reference,created_at) VALUES(?,?,?,'UPI','pending',?,?)",
                      (dep_id, uid, amount, raw, now()))
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"⏳ Deposit submitted for admin verification.\nRequest: {dep_id}\nAmount: {money(amount)}\n"
            "Your wallet will be credited only after verification.")
        for aid in ADMIN_IDS:
            try:
                await context.bot.send_message(aid,
                    f"💰 Pending UPI deposit\nID: {dep_id}\nUser: {uid}\nAmount: {money(amount)}\nReference: {raw}",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Approve", callback_data=f"depapprove:{dep_id}"),
                        InlineKeyboardButton("❌ Reject", callback_data=f"depreject:{dep_id}")
                    ]]))
            except Exception:
                pass
    elif state == "add_name" and is_admin(uid):
        context.user_data["new_product"] = {"name": raw[:120]}
        context.user_data["state"] = "add_description"
        await update.effective_message.reply_text("Send product description (or send - to skip).")
    elif state == "add_description" and is_admin(uid):
        context.user_data["new_product"]["description"] = "" if raw == "-" else raw[:1000]
        context.user_data["state"] = "add_price"
        await update.effective_message.reply_text("Send price in INR, e.g. 149 or 99.50.")
    elif state == "add_price" and is_admin(uid):
        amount = parse_amount(raw)
        if amount is None:
            await update.effective_message.reply_text("Invalid price. Send a positive amount with up to 2 decimal places.")
            return
        context.user_data["new_product"]["price_paise"] = amount
        context.user_data["state"] = "add_delivery_type"
        await update.effective_message.reply_text("Choose delivery type:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 File path", callback_data="delivery:file"),
             InlineKeyboardButton("🔗 Link", callback_data="delivery:link")]
        ]))
    elif state == "add_delivery_value" and is_admin(uid):
        np = context.user_data.get("new_product", {})
        dtype = np.get("delivery_type")
        if dtype == "link" and not raw.startswith(("https://", "http://")):
            await update.effective_message.reply_text("Send a valid http:// or https:// link.")
            return
        if dtype == "file":
            # Admin supplies a local path relative to the bot folder, e.g. product_files/course.zip
            path = Path(raw)
            if path.is_absolute() or ".." in path.parts:
                await update.effective_message.reply_text("Use a safe relative file path, e.g. product_files/course.zip")
                return
            full = Path.cwd() / path
            if not full.exists() or not full.is_file():
                await update.effective_message.reply_text("File not found. Upload it to the server first, then send its relative path.")
                return
        with db() as c:
            cur = c.execute("INSERT INTO products(name,description,price_paise,delivery_type,delivery_value,active,created_at) VALUES(?,?,?,?,?,1,?)",
                (np["name"], np["description"], np["price_paise"], dtype, raw, now()))
            pid = cur.lastrowid
        context.user_data.clear()
        await update.effective_message.reply_text(f"✅ Product #{pid} added successfully.", reply_markup=admin_menu())
    elif state == "announcement" and is_admin(uid):
        with db() as c:
            c.execute("INSERT INTO settings(key,value) VALUES('announcements',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (raw[:3000],))
        context.user_data.clear()
        await update.effective_message.reply_text("📢 Announcement updated.", reply_markup=admin_menu())
    else:
        context.user_data.clear()
        await update.effective_message.reply_text("Action reset. Use /start.")

async def delivery_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        return
    dtype = q.data.split(":",1)[1]
    if dtype not in ("file", "link"):
        return
    context.user_data.setdefault("new_product", {})["delivery_type"] = dtype
    context.user_data["state"] = "add_delivery_value"
    if dtype == "file":
        await q.message.reply_text("Send a server-relative file path, e.g. product_files/course.zip. Put the file in that folder first.")
    else:
        await q.message.reply_text("Send the product download URL (https://...).")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("Unhandled bot error", exc_info=context.error)

def main():
    if not BOT_TOKEN:
        raise SystemExit("Missing BOT_TOKEN. Copy .env.example to .env and add your bot token.")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS is empty. Admin features will be unavailable.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(delivery_choice, pattern=r"^delivery:(file|link)$"))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_error_handler(error_handler)
    logging.info("Starting %s", STORE_NAME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
