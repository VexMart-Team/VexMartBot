import os
import asyncio
import sqlite3

from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
DB_NAME = "vexmart.db"

# =========================
# DATABASE
# =========================

db = sqlite3.connect(DB_NAME, check_same_thread=False)
db.row_factory = sqlite3.Row


def init_db():
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            role TEXT,
            balance INTEGER DEFAULT 100
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS stores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER UNIQUE,
            name TEXT,
            description TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id INTEGER,
            name TEXT,
            description TEXT,
            price INTEGER,
            cashback INTEGER DEFAULT 5
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER,
            seller_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            price INTEGER,
            cashback INTEGER,
            status TEXT DEFAULT 'new'
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            user_id INTEGER,
            task TEXT,
            reward INTEGER,
            completed INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, task)
        )
    """)

    db.commit()


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()


def create_user(user):
    db.execute("""
        INSERT OR IGNORE INTO users
        (id, username, first_name, role, balance)
        VALUES (?, ?, ?, NULL, 100)
    """, (
        user.id,
        user.username or "",
        user.first_name or "Пользователь"
    ))

    db.commit()


def update_balance(user_id, amount):
    db.execute(
        "UPDATE users SET balance = balance + ? WHERE id = ?",
        (amount, user_id)
    )
    db.commit()


# =========================
# KEYBOARDS
# =========================

def role_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛒 Я покупатель",
                callback_data="role_buyer"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Я курьер",
                callback_data="role_seller"
            )
        ]
    ])


def buyer_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="stores"
            )
        ],
        [
            InlineKeyboardButton(
                "🎯 Задания",
                callback_data="tasks"
            ),
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Мои заказы",
                callback_data="my_orders"
            )
        ]
    ])


def seller_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏪 Мой магазин",
                callback_data="my_store"
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить товар",
                callback_data="add_product"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="my_products"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Мои заказы",
                callback_data="seller_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile"
            )
        ]
    ])


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    create_user(user)

    db_user = get_user(user.id)

    if not db_user["role"]:
        await update.message.reply_text(
            "🏪 VexMart\n\n"
            "Добро пожаловать в VexMart!\n\n"
            "Кто ты?",
            reply_markup=role_keyboard()
        )
        return

    if db_user["role"] == "buyer":
        await update.message.reply_text(
            "🏪 VexMart\n\n"
            f"🪙 Баланс: {db_user['balance']} VXC",
            reply_markup=buyer_menu()
        )
    else:
        await update.message.reply_text(
            "📦 Панель курьера\n\n"
            "Управляй своим магазином:",
            reply_markup=seller_menu()
        )


# =========================
# PROFILE
# =========================

async def profile(query):
    user_id = query.from_user.id
    user = get_user(user_id)

    role = "🛒 Покупатель" if user["role"] == "buyer" else "📦 Курьер"

    await query.edit_message_text(
        "👤 Профиль\n\n"
        f"Имя: {user['first_name']}\n"
        f"Роль: {role}\n"
        f"🪙 VexCoin: {user['balance']} VXC",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )]
        ])
    )


# =========================
# STORES
# =========================

async def show_stores(query):
    stores = db.execute(
        "SELECT * FROM stores"
    ).fetchall()

    if not stores:
        await query.edit_message_text(
            "🏪 Магазины\n\n"
            "Пока магазинов нет 😢",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )]
            ])
        )
        return

    buttons = []

    for store in stores:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']}",
                callback_data=f"store_{store['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu"
        )
    ])

    await query.edit_message_text(
        "🏪 Магазины\n\n"
        "Выбери магазин:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_store(query, store_id):
    store = db.execute(
        "SELECT * FROM stores WHERE id = ?",
        (store_id,)
    ).fetchone()

    products = db.execute(
        "SELECT * FROM products WHERE store_id = ?",
        (store_id,)
    ).fetchall()

    if not store:
        return

    text = (
        f"🏪 {store['name']}\n\n"
        f"{store['description']}\n\n"
        "📦 Товары:\n"
    )

    buttons = []

    if products:
        for product in products:
            text += (
                f"\n📦 {product['name']}"
                f" — {product['price']} VXC"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"🛒 {product['name']}",
                    callback_data=f"product_{product['id']}"
                )
            ])
    else:
        text += "\nПока товаров нет."

    buttons.append([
        InlineKeyboardButton(
            "⬅️ К магазинам",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# PRODUCT
# =========================

async def show_product(query, product_id):
    product = db.execute("""
        SELECT products.*, stores.owner_id, stores.name AS store_name
        FROM products
        JOIN stores ON products.store_id = stores.id
        WHERE products.id = ?
    """, (product_id,)).fetchone()

    if not product:
        return

    text = (
        f"📦 {product['name']}\n\n"
        f"{product['description']}\n\n"
        f"💰 Цена: {product['price']} VXC\n"
        f"🎁 Кэшбэк: {product['cashback']}%"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🛒 Купить",
                    callback_data=f"buy_{product_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data=f"store_{product['store_id']}"
                )
            ]
        ])
    )


# =========================
# BUY
# =========================

async def buy_product(query, product_id, context):
    buyer_id = query.from_user.id

    product = db.execute("""
        SELECT products.*, stores.owner_id
        FROM products
        JOIN stores ON products.store_id = stores.id
        WHERE products.id = ?
    """, (product_id,)).fetchone()

    buyer = get_user(buyer_id)

    if not product:
        return

    if buyer["balance"] < product["price"]:
        await query.answer(
            "❌ Недостаточно VexCoin!",
            show_alert=True
        )
        return

    # Списываем деньги
    update_balance(buyer_id, -product["price"])

    # Отправляем продавцу деньги
    update_balance(product["owner_id"], product["price"])

    # Кэшбэк
    cashback = int(
        product["price"] * product["cashback"] / 100
    )

    update_balance(buyer_id, cashback)

    # Заказ
    db.execute("""
        INSERT INTO orders
        (buyer_id, seller_id, product_id, product_name,
         price, cashback, status)
        VALUES (?, ?, ?, ?, ?, ?, 'new')
    """, (
        buyer_id,
        product["owner_id"],
        product["id"],
        product["name"],
        product["price"],
        cashback
    ))

    db.commit()

    order_id = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    # Уведомление продавцу
    await context.bot.send_message(
        chat_id=product["owner_id"],
        text=(
            "🔔 НОВЫЙ ЗАКАЗ!\n\n"
            f"📦 Товар: {product['name']}\n"
            f"💰 Цена: {product['price']} VXC\n"
            f"🎁 Кэшбэк покупателя: {cashback} VXC\n\n"
            f"🆔 Заказ #{order_id}"
        )
    )

    new_balance = get_user(buyer_id)["balance"]

    await query.edit_message_text(
        "✅ Покупка совершена!\n\n"
        f"📦 {product['name']}\n"
        f"💰 Потрачено: {product['price']} VXC\n"
        f"🎁 Кэшбэк: +{cashback} VXC\n\n"
        f"🪙 Новый баланс: {new_balance} VXC",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🏠 Главное меню",
                callback_data="back_menu"
            )]
        ])
    )


# =========================
# TASKS
# =========================

async def show_tasks(query):
    user_id = query.from_user.id

    tasks = [
        ("daily", "📅 Зайти в VexMart", 5),
        ("profile", "👤 Открыть профиль", 3),
    ]

    buttons = []

    for task_id, name, reward in tasks:
        existing = db.execute("""
            SELECT completed FROM tasks
            WHERE user_id = ? AND task = ?
        """, (user_id, task_id)).fetchone()

        if existing and existing["completed"]:
            status = "✅"
        else:
            status = "🎯"

        buttons.append([
            InlineKeyboardButton(
                f"{status} {name} +{reward} VXC",
                callback_data=f"task_{task_id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu"
        )
    ])

    await query.edit_message_text(
        "🎯 Задания\n\n"
        "Выполняй задания и получай VexCoin!",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def complete_task(query, task_id):
    user_id = query.from_user.id

    rewards = {
        "daily": 5,
        "profile": 3,
    }

    reward = rewards.get(task_id)

    if not reward:
        return

    existing = db.execute("""
        SELECT completed FROM tasks
        WHERE user_id = ? AND task = ?
    """, (user_id, task_id)).fetchone()

    if existing and existing["completed"]:
        await query.answer(
            "Это задание уже выполнено!",
            show_alert=True
        )
        return

    db.execute("""
        INSERT OR REPLACE INTO tasks
        (user_id, task, reward, completed)
        VALUES (?, ?, ?, 1)
    """, (
        user_id,
        task_id,
        reward
    ))

    db.commit()

    update_balance(user_id, reward)

    await query.answer(
        f"+{reward} VXC!",
        show_alert=True
    )

    await show_tasks(query)


# =========================
# STORE MANAGEMENT
# =========================

async def my_store(query):
    user_id = query.from_user.id

    store = db.execute(
        "SELECT * FROM stores WHERE owner_id = ?",
        (user_id,)
    ).fetchone()

    if not store:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.\n\n"
            "Создай его!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "➕ Создать магазин",
                    callback_data="create_store"
                )],
                [InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )]
            ])
        )
        return

    await query.edit_message_text(
        f"🏪 {store['name']}\n\n"
        f"{store['description']}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 Изменить описание",
                callback_data="edit_store"
            )],
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )]
        ])
    )


async def create_store(query, context):
    context.user_data["action"] = "create_store_name"

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


async def add_product(query, context):
    store = db.execute(
        "SELECT * FROM stores WHERE owner_id = ?",
        (query.from_user.id,)
    ).fetchone()

    if not store:
        await query.answer(
            "Сначала создай магазин!",
            show_alert=True
        )
        return

    context.user_data["action"] = "product_name"

    await query.edit_message_text(
        "➕ Добавление товара\n\n"
        "Напиши название товара:"
    )


async def my_products(query):
    products = db.execute("""
        SELECT products.*
        FROM products
        JOIN stores ON products.store_id = stores.id
        WHERE stores.owner_id = ?
    """, (query.from_user.id,)).fetchall()

    if not products:
        text = "📦 У тебя пока нет товаров."
    else:
        text = "📦 Твои товары:\n\n"

        for product in products:
            text += (
                f"• {product['name']} — "
                f"{product['price']} VXC\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )]
        ])
    )


# =========================
# TEXT INPUT
# =========================

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("action")

    if not action:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Создание магазина
    if action == "create_store_name":
        context.user_data["store_name"] = text
        context.user_data["action"] = "create_store_description"

        await update.message.reply_text(
            "Отлично!\n\n"
            "Теперь напиши описание магазина:"
        )
        return

    if action == "create_store_description":
        name = context.user_data["store_name"]

        db.execute("""
            INSERT INTO stores
            (owner_id, name, description)
            VALUES (?, ?, ?)
        """, (
            user_id,
            name,
            text
        ))

        db.commit()

        context.user_data.clear()

        await update.message.reply_text(
            "🎉 Магазин создан!\n\n"
            f"🏪 {name}",
            reply_markup=seller_menu()
        )
        return

    # Добавление товара
    if action == "product_name":
        context.user_data["product_name"] = text
        context.user_data["action"] = "product_description"

        await update.message.reply_text(
            "Теперь напиши описание товара:"
        )
        return

    if action == "product_description":
        context.user_data["product_description"] = text
        context.user_data["action"] = "product_price"

        await update.message.reply_text(
            "Теперь напиши цену в VexCoin.\n\n"
            "Например: 50"
        )
        return

    if action == "product_price":
        try:
            price = int(text)

            if price <= 0:
                raise ValueError

        except ValueError:
            await update.message.reply_text(
                "❌ Цена должна быть положительным числом."
            )
            return

        store = db.execute(
            "SELECT * FROM stores WHERE owner_id = ?",
            (user_id,)
        ).fetchone()

        db.execute("""
            INSERT INTO products
            (store_id, name, description, price, cashback)
            VALUES (?, ?, ?, ?, 5)
        """, (
            store["id"],
            context.user_data["product_name"],
            context.user_data["product_description"],
            price
        ))

        db.commit()

        product_name = context.user_data["product_name"]

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Товар добавлен!\n\n"
            f"📦 {product_name}\n"
            f"💰 Цена: {price} VXC\n"
            f"🎁 Кэшбэк: 5%",
            reply_markup=seller_menu()
        )


# =========================
# ORDERS
# =========================

async def my_orders(query):
    orders = db.execute("""
        SELECT * FROM orders
        WHERE buyer_id = ?
        ORDER BY id DESC
    """, (query.from_user.id,)).fetchall()

    if not orders:
        text = "📋 У тебя пока нет заказов."
    else:
        text = "📋 Мои заказы:\n\n"

        for order in orders:
            status = {
                "new": "🟡 Новый",
                "done": "🟢 Выполнен"
            }.get(order["status"], order["status"])

            text += (
                f"🆔 #{order['id']}\n"
                f"📦 {order['product_name']}\n"
                f"💰 {order['price']} VXC\n"
                f"{status}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )]
        ])
    )


async def seller_orders(query):
    orders = db.execute("""
        SELECT * FROM orders
        WHERE seller_id = ?
        ORDER BY id DESC
    """, (query.from_user.id,)).fetchall()

    if not orders:
        text = "📋 Заказов пока нет."
    else:
        text = "📋 Заказы магазина:\n\n"

        for order in orders:
            status = (
                "🟡 Новый"
                if order["status"] == "new"
                else "🟢 Выполнен"
            )

            text += (
                f"🆔 #{order['id']}\n"
                f"📦 {order['product_name']}\n"
                f"💰 {order['price']} VXC\n"
                f"{status}\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )]
        ])
    )


# =========================
# CALLBACKS
# =========================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    create_user(query.from_user)

    data = query.data

    if data == "role_buyer":
        db.execute(
            "UPDATE users SET role = 'buyer' WHERE id = ?",
            (user_id,)
        )
        db.commit()

        await query.edit_message_text(
            "🛒 Отлично!\n\n"
            "Ты теперь покупатель VexMart.\n\n"
            "Тебе начислено 100 VXC на старт!",
            reply_markup=buyer_menu()
        )

    elif data == "role_seller":
        db.execute(
            "UPDATE users SET role = 'seller' WHERE id = ?",
            (user_id,)
        )
        db.commit()

        await query.edit_message_text(
            "📦 Отлично!\n\n"
            "Ты теперь курьер VexMart.\n\n"
            "Создай свой магазин и добавляй товары!",
            reply_markup=seller_menu()
        )

    elif data == "back_menu":
        user = get_user(user_id)

        if user["role"] == "seller":
            await query.edit_message_text(
                "📦 Панель курьера:",
                reply_markup=seller_menu()
            )
        else:
            await query.edit_message_text(
                f"🏪 VexMart\n\n"
                f"🪙 Баланс: {user['balance']} VXC",
                reply_markup=buyer_menu()
            )

    elif data == "profile":
        await profile(query)

    elif data == "stores":
        await show_stores(query)

    elif data.startswith("store_"):
        await show_store(
            query,
            int(data.split("_")[1])
        )

    elif data.startswith("product_"):
        await show_product(
            query,
            int(data.split("_")[1])
        )

    elif data.startswith("buy_"):
        await buy_product(
            query,
            int(data.split("_")[1]),
            context
        )

    elif data == "tasks":
        await show_tasks(query)

    elif data.startswith("task_"):
        await complete_task(
            query,
            data.replace("task_", "")
        )

    elif data == "my_store":
        await my_store(query)

    elif data == "create_store":
        await create_store(query, context)

    elif data == "add_product":
        await add_product(query, context)

    elif data == "my_products":
        await my_products(query)

    elif data == "my_orders":
        await my_orders(query)

    elif data == "seller_orders":
        await seller_orders(query)


# =========================
# RENDER WEB SERVER
# =========================

async def health(request):
    return web.Response(
        text="VexMart 0.2 is alive! 🏪"
    )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "Не указан BOT_TOKEN"
        )

    init_db()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(button)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_message
        )
    )

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    web_app = web.Application()

    web_app.router.add_get(
        "/",
        health
    )

    runner = web.AppRunner(web_app)
    await runner.setup()

    port = int(
        os.getenv("PORT", "10000")
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"VexMart 0.2 запущен на порту {port}!"
    )

    try:
        while True:
            await asyncio.sleep(3600)

    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
