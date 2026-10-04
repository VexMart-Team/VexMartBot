import os
import asyncio

from aiohttp import web
from supabase import create_client

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Не указаны SUPABASE_URL или SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# DATABASE
# =========================

def get_user(user_id):
    result = (
        supabase
        .table("users")
        .select("*")
        .eq("id", user_id)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def create_user(user):
    existing = get_user(user.id)

    if existing:
        return existing

    result = (
        supabase
        .table("users")
        .insert({
            "id": user.id,
            "username": user.username or "",
            "first_name": user.first_name or "Пользователь",
            "role": None,
            "balance": 100,
        })
        .execute()
    )

    return result.data[0] if result.data else get_user(user.id)


def update_balance(user_id, amount):
    user = get_user(user_id)

    if not user:
        return

    new_balance = user["balance"] + amount

    (
        supabase
        .table("users")
        .update({"balance": new_balance})
        .eq("id", user_id)
        .execute()
    )


def get_store(store_id):
    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("id", store_id)
        .execute()
    )

    return result.data[0] if result.data else None


def get_product(product_id):
    result = (
        supabase
        .table("products")
        .select("*")
        .eq("id", product_id)
        .execute()
    )

    return result.data[0] if result.data else None


# =========================
# MENUS
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
        ],
        [
            InlineKeyboardButton(
                "📦 Стать курьером",
                callback_data="switch_seller"
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
        ],
        [
            InlineKeyboardButton(
                "🛒 Стать покупателем",
                callback_data="switch_buyer"
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
    user = get_user(query.from_user.id)

    role = (
        "🛒 Покупатель"
        if user["role"] == "buyer"
        else "📦 Курьер"
    )

    await query.edit_message_text(
        "👤 Профиль\n\n"
        f"Имя: {user['first_name']}\n"
        f"Роль: {role}\n"
        f"🪙 VexCoin: {user['balance']} VXC",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )
            ]
        ])
    )


# =========================
# STORES
# =========================

STORES_PER_PAGE = 5


async def show_stores(query, page=0):
    result = (
        supabase
        .table("stores")
        .select("*")
        .order("id")
        .execute()
    )

    stores = result.data or []

    if not stores:
        await query.edit_message_text(
            "🏪 Магазины\n\n"
            "Пока магазинов нет 😢\n\n"
            "Стань первым курьером и создай магазин!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu"
                    )
                ]
            ])
        )
        return

    total_pages = (
        len(stores) + STORES_PER_PAGE - 1
    ) // STORES_PER_PAGE

    page = max(0, min(page, total_pages - 1))

    start = page * STORES_PER_PAGE
    end = start + STORES_PER_PAGE

    page_stores = stores[start:end]

    buttons = []

    for store in page_stores:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']}",
                callback_data=f"store_{store['id']}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬆️",
                callback_data=f"stores_page_{page - 1}"
            )
        )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "⬇️",
                callback_data=f"stores_page_{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🔎 Найти магазин",
            callback_data="search_store"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu"
        )
    ])

    text = (
        "🏪 Магазины\n\n"
        f"Страница {page + 1} из {total_pages}\n\n"
    )

    for i, store in enumerate(page_stores, start=start + 1):
        text += f"{i}. 🏪 {store['name']}\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# STORE SEARCH
# =========================

async def search_store(query, context):
    context.user_data["action"] = "search_store"

    await query.edit_message_text(
        "🔎 Поиск магазина\n\n"
        "Напиши название магазина или его часть:"
    )


async def show_search_results(query, search_text):
    result = (
        supabase
        .table("stores")
        .select("*")
        .ilike("name", f"%{search_text}%")
        .order("id")
        .execute()
    )

    stores = result.data or []

    if not stores:
        await query.edit_message_text(
            "🔎 Поиск\n\n"
            f"По запросу «{search_text}» "
            "ничего не найдено 😢",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Новый поиск",
                        callback_data="search_store"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К магазинам",
                        callback_data="stores"
                    )
                ]
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
            "🔎 Новый поиск",
            callback_data="search_store"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ К магазинам",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        "🔎 Результаты поиска:\n\n"
        f"Запрос: «{search_text}»",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# OPEN STORE
# =========================

async def show_store(query, store_id):
    user_id = query.from_user.id

    store = get_store(store_id)

    if not store:
        await query.answer(
            "Магазин не найден.",
            show_alert=True
        )
        return

    viewed = (
        supabase
        .table("viewed_stores")
        .select("*")
        .eq("user_id", user_id)
        .eq("store_id", store_id)
        .execute()
    )

    reward_text = ""

    if not viewed.data:
        (
            supabase
            .table("viewed_stores")
            .insert({
                "user_id": user_id,
                "store_id": store_id
            })
            .execute()
        )

        update_balance(user_id, 15)

        reward_text = (
            "\n\n🎁 Новый магазин!\n"
            "🪙 +15 VXC"
        )

    products_result = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store_id)
        .order("id")
        .execute()
    )

    products = products_result.data or []

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

    text += reward_text

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
# PRODUCTS
# =========================

async def show_product(query, product_id):
    product = get_product(product_id)

    if not product:
        await query.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    store = get_store(product["store_id"])

    if not store:
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

    product = get_product(product_id)

    if not product:
        await query.answer(
            "Товар не найден.",
            show_alert=True
        )
        return

    store = get_store(product["store_id"])

    if not store:
        return

    buyer = get_user(buyer_id)

    if buyer["balance"] < product["price"]:
        await query.answer(
            "❌ Недостаточно VexCoin!",
            show_alert=True
        )
        return

    update_balance(
        buyer_id,
        -product["price"]
    )

    update_balance(
        store["owner_id"],
        product["price"]
    )

    cashback = int(
        product["price"]
        * product["cashback"]
        / 100
    )

    update_balance(
        buyer_id,
        cashback
    )

    order_result = (
        supabase
        .table("orders")
        .insert({
            "buyer_id": buyer_id,
            "seller_id": store["owner_id"],
            "product_id": product["id"],
            "product_name": product["name"],
            "price": product["price"],
            "cashback": cashback,
            "status": "new",
            "user_id": buyer_id,
            "quantity": 1,
            "total_price": product["price"],
            "store_id": store["id"],
        })
        .execute()
    )

    order_id = (
        order_result.data[0]["id"]
        if order_result.data
        else "?"
    )

    await context.bot.send_message(
        chat_id=store["owner_id"],
        text=(
            "🔔 НОВЫЙ ЗАКАЗ!\n\n"
            f"📦 Товар: {product['name']}\n"
            f"💰 Цена: {product['price']} VXC\n"
            f"🎁 Кэшбэк: {cashback} VXC\n\n"
            f"🆔 Заказ #{order_id}\n\n"
            "Открой «📋 Мои заказы», "
            "чтобы отметить его выполненным."
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
            [
                InlineKeyboardButton(
                    "🏠 Главное меню",
                    callback_data="back_menu"
                )
            ]
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
        result = (
            supabase
            .table("tasks")
            .select("completed")
            .eq("user_id", user_id)
            .eq("task", task_id)
            .execute()
        )

        existing = result.data[0] if result.data else None

        status = (
            "✅"
            if existing and existing["completed"]
            else "🎯"
        )

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

    result = (
        supabase
        .table("tasks")
        .select("completed")
        .eq("user_id", user_id)
        .eq("task", task_id)
        .execute()
    )

    existing = result.data[0] if result.data else None

    if existing and existing["completed"]:
        await query.answer(
            "Это задание уже выполнено!",
            show_alert=True
        )
        return

    (
        supabase
        .table("tasks")
        .upsert({
            "user_id": user_id,
            "task": task_id,
            "reward": reward,
            "completed": True
        })
        .execute()
    )

    update_balance(
        user_id,
        reward
    )

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

    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .execute()
    )

    store = result.data[0] if result.data else None

    if not store:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.\n\n"
            "Создай его!",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "➕ Создать магазин",
                        callback_data="create_store"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu"
                    )
                ]
            ])
        )
        return

    await query.edit_message_text(
        f"🏪 {store['name']}\n\n"
        f"{store['description']}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )
            ]
        ])
    )


async def create_store(query, context):
    context.user_data["action"] = "create_store_name"

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


async def add_product(query, context):
    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", query.from_user.id)
        .execute()
    )

    if not result.data:
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
    stores_result = (
        supabase
        .table("stores")
        .select("id")
        .eq("owner_id", query.from_user.id)
        .execute()
    )

    if not stores_result.data:
        products = []
    else:
        store_id = stores_result.data[0]["id"]

        result = (
            supabase
            .table("products")
            .select("*")
            .eq("store_id", store_id)
            .order("id")
            .execute()
        )

        products = result.data or []

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
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )
            ]
        ])
    )


# =========================
# COMPLETE ORDER
# =========================

async def complete_order(query, order_id, context):
    courier_id = query.from_user.id

    result = (
        supabase
        .table("orders")
        .select("*")
        .eq("id", order_id)
        .eq("seller_id", courier_id)
        .execute()
    )

    if not result.data:
        await query.answer(
            "❌ Заказ не найден.",
            show_alert=True
        )
        return

    order = result.data[0]

    if order["status"] == "done":
        await query.answer(
            "Этот заказ уже выполнен.",
            show_alert=True
        )
        return

    (
        supabase
        .table("orders")
        .update({"status": "done"})
        .eq("id", order_id)
        .execute()
    )

    await query.answer(
        "✅ Заказ отмечен выполненным!",
        show_alert=True
    )

    try:
        await context.bot.send_message(
            chat_id=order["buyer_id"],
            text=(
                "🟢 ЗАКАЗ ВЫПОЛНЕН!\n\n"
                f"📦 {order['product_name']}\n"
                f"🆔 Заказ #{order['id']}\n\n"
                "Курьер отметил заказ как выполненный."
            )
        )
    except Exception:
        pass

    await seller_orders(query)


# =========================
# TEXT INPUT
# =========================

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get("action")

    if not action:
        return

    user_id = update.effective_user.id
    text = update.message.text.strip()

    # SEARCH

    if action == "search_store":
        context.user_data.clear()

        result = (
            supabase
            .table("stores")
            .select("*")
            .ilike("name", f"%{text}%")
            .order("id")
            .execute()
        )

        stores = result.data or []

        if not stores:
            await update.message.reply_text(
                "🔎 Поиск магазина\n\n"
                f"По запросу «{text}» "
                "ничего не найдено 😢",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔎 Новый поиск",
                            callback_data="search_store"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ К магазинам",
                            callback_data="stores"
                        )
                    ]
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
                "🔎 Новый поиск",
                callback_data="search_store"
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ К магазинам",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            "🔎 Результаты поиска:\n\n"
            f"Запрос: «{text}»",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # CREATE STORE

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

        existing = (
            supabase
            .table("stores")
            .select("*")
            .eq("owner_id", user_id)
            .execute()
        )

        if existing.data:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ У тебя уже есть магазин.",
                reply_markup=seller_menu()
            )
            return

        result = (
            supabase
            .table("stores")
            .insert({
                "owner_id": user_id,
                "name": name,
                "description": text
            })
            .execute()
        )

        if not result.data:
            await update.message.reply_text(
                "❌ Не удалось создать магазин."
            )
            return

        update_balance(user_id, 15)

        if ADMIN_ID:
            update_balance(ADMIN_ID, 30)

        context.user_data.clear()

        user = get_user(user_id)

        await update.message.reply_text(
            "🎉 Магазин создан!\n\n"
            f"🏪 {name}\n\n"
            "🪙 Тебе начислено: +15 VXC\n"
            f"💰 Твой баланс: {user['balance']} VXC",
            reply_markup=seller_menu()
        )

        if ADMIN_ID and ADMIN_ID != user_id:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        "🏪 НОВЫЙ МАГАЗИН НА VEXMART!\n\n"
                        f"🏪 {name}\n"
                        f"👤 Создатель: "
                        f"{update.effective_user.first_name}\n\n"
                        "🎁 Награда VexMart: +30 VXC"
                    )
                )
            except Exception:
                pass

        return

    # PRODUCT NAME

    if action == "product_name":
        context.user_data["product_name"] = text
        context.user_data["action"] = "product_description"

        await update.message.reply_text(
            "Теперь напиши описание товара:"
        )

        return

    # PRODUCT DESCRIPTION

    if action == "product_description":
        context.user_data["product_description"] = text
        context.user_data["action"] = "product_price"

        await update.message.reply_text(
            "Теперь напиши цену в VexCoin.\n\n"
            "Например: 50"
        )

        return

    # PRODUCT PRICE

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

        store_result = (
            supabase
            .table("stores")
            .select("*")
            .eq("owner_id", user_id)
            .execute()
        )

        if not store_result.data:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Магазин не найден.",
                reply_markup=seller_menu()
            )
            return

        store = store_result.data[0]

        product_name = context.user_data["product_name"]
        product_description = context.user_data["product_description"]

        result = (
            supabase
            .table("products")
            .insert({
                "store_id": store["id"],
                "name": product_name,
                "description": product_description,
                "price": price,
                "cashback": 5,
                "stock": 0
            })
            .execute()
        )

        if not result.data:
            await update.message.reply_text(
                "❌ Не удалось добавить товар."
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Товар добавлен!\n\n"
            f"📦 {product_name}\n"
            f"💰 Цена: {price} VXC\n"
            "🎁 Кэшбэк: 5%",
            reply_markup=seller_menu()
        )


# =========================
# ORDERS
# =========================

async def my_orders(query):
    result = (
        supabase
        .table("orders")
        .select("*")
        .eq("buyer_id", query.from_user.id)
        .order("id", desc=True)
        .execute()
    )

    orders = result.data or []

    if not orders:
        text = "📋 У тебя пока нет заказов."
    else:
        text = "📋 Мои заказы:\n\n"

        for order in orders:
            status = {
                "new": "🟡 Новый",
                "done": "🟢 Выполнен"
            }.get(
                order["status"],
                order["status"]
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
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu"
                )
            ]
        ])
    )


async def seller_orders(query):
    result = (
        supabase
        .table("orders")
        .select("*")
        .eq("seller_id", query.from_user.id)
        .order("id", desc=True)
        .execute()
    )

    orders = result.data or []

    if not orders:
        text = "📋 Заказов пока нет."
    else:
        text = "📋 Заказы магазина:\n\n"

    buttons = []

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

        if order["status"] == "new":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ Выполнено #{order['id']}",
                    callback_data=f"complete_{order['id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================
# BUTTONS
# =========================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    create_user(query.from_user)

    data = query.data

    # FIRST ROLE

    if data == "role_buyer":
        (
            supabase
            .table("users")
            .update({"role": "buyer"})
            .eq("id", user_id)
            .execute()
        )

        await query.edit_message_text(
            "🛒 Отлично!\n\n"
            "Ты теперь покупатель VexMart.\n\n"
            "Тебе начислено 100 VXC на старт!",
            reply_markup=buyer_menu()
        )

    elif data == "role_seller":
        (
            supabase
            .table("users")
            .update({"role": "seller"})
            .eq("id", user_id)
            .execute()
        )

        await query.edit_message_text(
            "📦 Отлично!\n\n"
            "Ты теперь курьер VexMart.\n\n"
            "Создай свой магазин "
            "и добавляй товары!",
            reply_markup=seller_menu()
        )

    # SWITCH ROLE

    elif data == "switch_seller":
        (
            supabase
            .table("users")
            .update({"role": "seller"})
            .eq("id", user_id)
            .execute()
        )

        await query.edit_message_text(
            "📦 Режим курьера включён!\n\n"
            "Твой прогресс покупателя сохранён.",
            reply_markup=seller_menu()
        )

    elif data == "switch_buyer":
        (
            supabase
            .table("users")
            .update({"role": "buyer"})
            .eq("id", user_id)
            .execute()
        )

        user = get_user(user_id)

        await query.edit_message_text(
            "🛒 Режим покупателя включён!\n\n"
            "Твой прогресс курьера сохранён.\n\n"
            f"🪙 Баланс: {user['balance']} VXC",
            reply_markup=buyer_menu()
        )

    # MAIN MENU

    elif data == "back_menu":
        user = get_user(user_id)

        if user["role"] == "seller":
            await query.edit_message_text(
                "📦 Панель курьера:",
                reply_markup=seller_menu()
            )
        else:
            await query.edit_message_text(
                "🏪 VexMart\n\n"
                f"🪙 Баланс: {user['balance']} VXC",
                reply_markup=buyer_menu()
            )

    # PROFILE

    elif data == "profile":
        await profile(query)

    # STORES

    elif data == "stores":
        await show_stores(query, 0)

    elif data.startswith("stores_page_"):
        page = int(
            data.replace("stores_page_", "")
        )

        await show_stores(query, page)

    elif data == "search_store":
        await search_store(query, context)

    elif data.startswith("store_"):
        await show_store(
            query,
            int(data.split("_")[1])
        )

    # PRODUCTS

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

    # TASKS

    elif data == "tasks":
        await show_tasks(query)

    elif data.startswith("task_"):
        await complete_task(
            query,
            data.replace("task_", "")
        )

    # SELLER

    elif data == "my_store":
        await my_store(query)

    elif data == "create_store":
        await create_store(query, context)

    elif data == "add_product":
        await add_product(query, context)

    elif data == "my_products":
        await my_products(query)

    # ORDERS

    elif data == "my_orders":
        await my_orders(query)

    elif data == "seller_orders":
        await seller_orders(query)

    elif data.startswith("complete_"):
        await complete_order(
            query,
            int(data.replace("complete_", "")),
            context
        )


# =========================
# RENDER
# =========================

async def health(request):
    return web.Response(
        text="VexMart 0.40 is alive! 🏪"
    )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "Не указан BOT_TOKEN"
        )

    application = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button
        )
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

    runner = web.AppRunner(
        web_app
    )

    await runner.setup()

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"VexMart 0.40 запущен "
        f"на порту {port}!"
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
