import os
import asyncio
from aiohttp import web

from supabase import create_client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not TOKEN:
    raise RuntimeError("Не указан BOT_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Не указаны SUPABASE_URL или SUPABASE_KEY")


supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_user(user_id):
    result = (
        supabase
        .table("users")
        .select("*")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def create_user(tg_user):
    existing = get_user(tg_user.id)

    if existing:
        return existing

    role = "buyer"

    data = {
        "id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "role": role,
        "balance": 100,
    }

    result = (
        supabase
        .table("users")
        .insert(data)
        .execute()
    )

    if result.data:
        return result.data[0]

    return data


def update_balance(user_id, amount):
    user = get_user(user_id)

    if not user:
        return None

    new_balance = user.get("balance", 0) + amount

    result = (
        supabase
        .table("users")
        .update({"balance": new_balance})
        .eq("id", user_id)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def get_store(store_id):
    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("id", store_id)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def get_product(product_id):
    result = (
        supabase
        .table("products")
        .select("*")
        .eq("id", product_id)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def count_rows(table_name):
    result = (
        supabase
        .table(table_name)
        .select("id")
        .execute()
    )

    return len(result.data or [])


def get_all_rows(table_name):
    result = (
        supabase
        .table(table_name)
        .select("*")
        .execute()
    )

    return result.data or []


# =========================================================
# STATISTICS TRACKING
# =========================================================

def track_bot_visit(user_id):
    try:
        supabase.table("bot_visits").insert({
            "user_id": user_id
        }).execute()
    except Exception:
        pass


def track_store_view(user_id, store_id):
    try:
        supabase.table("store_views").insert({
            "user_id": user_id,
            "store_id": store_id
        }).execute()
    except Exception:
        pass


# =========================================================
# MENUS
# =========================================================

def role_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🛒 Покупатель",
                callback_data="role_buyer"
            ),
            InlineKeyboardButton(
                "🏪 Продавец",
                callback_data="role_seller"
            ),
        ]
    ])


def buyer_menu(user_id=None):
    buttons = [
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
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Мои заказы",
                callback_data="my_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Стать продавцом",
                callback_data="switch_seller"
            )
        ],
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠️ Админ-панель",
                callback_data="admin_panel"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def seller_menu(user_id=None):
    buttons = [
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
            ),
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="my_products"
            ),
        ],
        [
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="seller_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Статистика магазина",
                callback_data="store_stats"
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
        ],
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠️ Админ-панель",
                callback_data="admin_panel"
            )
        ])

    return InlineKeyboardMarkup(buttons)


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    db_user = create_user(user)

    track_bot_visit(user.id)

    if user.id == ADMIN_ID:
        await show_admin_panel_message(update)
        return

    if db_user.get("role") == "seller":
        await update.message.reply_text(
            "🏪 VexMart\n\n"
            "Добро пожаловать в магазинную панель!",
            reply_markup=seller_menu(user.id)
        )
    else:
        await update.message.reply_text(
            "🏪 VexMart\n\n"
            "Добро пожаловать!",
            reply_markup=buyer_menu(user.id)
        )


# =========================================================
# PROFILE
# =========================================================

async def profile(query):
    user = get_user(query.from_user.id)

    if not user:
        await query.message.reply_text(
            "❌ Пользователь не найден."
        )
        return

    role = user.get("role", "buyer")

    role_text = (
        "🏪 Продавец"
        if role == "seller"
        else "🛒 Покупатель"
    )

    text = (
        "👤 Профиль\n\n"
        f"Имя: {user.get('first_name', 'Неизвестно')}\n"
        f"Username: @{user.get('username') or 'нет'}\n"
        f"Роль: {role_text}\n"
        f"💰 Баланс: {user.get('balance', 0)} VXC"
    )

    await query.edit_message_text(
        text,
        reply_markup=buyer_menu(query.from_user.id)
        if role == "buyer"
        else seller_menu(query.from_user.id)
    )


# =========================================================
# STORES
# =========================================================

async def show_stores(query, page=0):
    stores = get_all_rows("stores")

    per_page = 5

    start_index = page * per_page
    end_index = start_index + per_page

    current = stores[start_index:end_index]

    text = "🏪 Магазины\n\n"

    if not current:
        text += "Пока магазинов нет."
    else:
        for store in current:
            text += (
                f"🏪 {store['name']}\n"
                f"📝 {store.get('description') or 'Без описания'}\n"
                f"ID: {store['id']}\n\n"
            )

    buttons = []

    for store in current:
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
                "⬅️",
                callback_data=f"stores_{page - 1}"
            )
        )

    if end_index < len(stores):
        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=f"stores_{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🔎 Поиск",
            callback_data="search_store"
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


async def search_store(query, context):
    context.user_data["action"] = "search_store"

    await query.edit_message_text(
        "🔎 Напиши название магазина:"
    )


async def show_search_results(query, search_text):
    stores = get_all_rows("stores")

    search_text = search_text.lower()

    found = [
        store for store in stores
        if search_text in store["name"].lower()
    ]

    text = "🔎 Результаты поиска\n\n"

    buttons = []

    if not found:
        text += "Ничего не найдено."
    else:
        for store in found:
            text += f"🏪 {store['name']}\n"

            buttons.append([
                InlineKeyboardButton(
                    store["name"],
                    callback_data=f"store_{store['id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Магазины",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_store(query, store_id):
    store = get_store(store_id)

    if not store:
        await query.edit_message_text(
            "❌ Магазин не найден."
        )
        return

    user_id = query.from_user.id

    track_store_view(user_id, store_id)

    # Награда за первый просмотр
    viewed = (
        supabase
        .table("viewed_stores")
        .select("*")
        .eq("user_id", user_id)
        .eq("store_id", store_id)
        .limit(1)
        .execute()
    )

    if not viewed.data:
        supabase.table("viewed_stores").insert({
            "user_id": user_id,
            "store_id": store_id
        }).execute()

        update_balance(user_id, 15)

    products = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}\n\n"
        "📦 Товары:\n"
    )

    buttons = []

    if products:
        for product in products:
            text += (
                f"\n📦 {product['name']} — "
                f"{product['price']} VXC"
            )

            buttons.append([
                InlineKeyboardButton(
                    product["name"],
                    callback_data=f"product_{product['id']}"
                )
            ])
    else:
        text += "\nПока товаров нет."

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Магазины",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# PRODUCTS
# =========================================================

async def show_product(query, product_id):
    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден."
        )
        return

    cashback = product.get("cashback", 5)

    text = (
        f"📦 {product['name']}\n\n"
        f"📝 {product.get('description') or 'Без описания'}\n\n"
        f"💰 Цена: {product['price']} VXC\n"
        f"🎁 Кешбэк: {cashback} VXC"
    )

    buttons = [
        [
            InlineKeyboardButton(
                "🛒 Купить",
                callback_data=f"buy_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="stores"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy_product(query, product_id, context):
    buyer_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден."
        )
        return

    buyer = get_user(buyer_id)

    price = int(product["price"])
    cashback = int(product.get("cashback", 5) or 0)

    if buyer["balance"] < price:
        await query.edit_message_text(
            "❌ Недостаточно VXC."
        )
        return

    store = get_store(product.get("store_id"))

    if not store:
        await query.edit_message_text(
            "❌ Магазин товара не найден."
        )
        return

    seller_id = store["owner_id"]

    update_balance(buyer_id, -price)
    update_balance(seller_id, price)
    update_balance(buyer_id, cashback)

    order_data = {
        "user_id": buyer_id,
        "product_id": product_id,
        "quantity": 1,
        "total_price": price,
        "buyer_id": buyer_id,
        "seller_id": seller_id,
        "product_name": product["name"],
        "price": price,
        "cashback": cashback,
        "store_id": store["id"],
        "status": "pending",
    }

    supabase.table("orders").insert(
        order_data
    ).execute()

    new_balance = get_user(buyer_id)["balance"]

    await query.edit_message_text(
        "✅ Покупка оформлена!\n\n"
        f"📦 {product['name']}\n"
        f"💰 Потрачено: {price} VXC\n"
        f"🎁 Кешбэк: +{cashback} VXC\n"
        f"💳 Баланс: {new_balance} VXC"
    )

    try:
        await context.bot.send_message(
            chat_id=seller_id,
            text=(
                "🛒 Новый заказ!\n\n"
                f"📦 {product['name']}\n"
                f"💰 Цена: {price} VXC"
            )
        )
    except Exception:
        pass


# =========================================================
# TASKS
# =========================================================

async def show_tasks(query):
    user_id = query.from_user.id

    tasks = [
        ("daily", "🎯 Ежедневное задание", 5),
        ("profile", "👤 Открыть профиль", 3),
    ]

    text = "🎯 Задания\n\n"
    buttons = []

    for task_id, name, reward in tasks:
        completed = (
            supabase
            .table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("task", task_id)
            .limit(1)
            .execute()
        )

        if completed.data and completed.data[0].get("completed"):
            text += f"✅ {name} — выполнено\n"
        else:
            text += f"🟡 {name} — +{reward} VXC\n"

            buttons.append([
                InlineKeyboardButton(
                    f"🎁 Получить {reward} VXC",
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
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def complete_task(query, task_id):
    user_id = query.from_user.id

    rewards = {
        "daily": 5,
        "profile": 3,
    }

    if task_id not in rewards:
        return

    reward = rewards[task_id]

    existing = (
        supabase
        .table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("task", task_id)
        .limit(1)
        .execute()
    )

    if existing.data and existing.data[0].get("completed"):
        await query.answer(
            "Задание уже выполнено!",
            show_alert=True
        )
        return

    supabase.table("tasks").upsert({
        "user_id": user_id,
        "task": task_id,
        "reward": reward,
        "completed": True
    }).execute()

    update_balance(user_id, reward)

    await query.edit_message_text(
        f"🎉 Задание выполнено!\n\n"
        f"💰 Получено: +{reward} VXC",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🎯 К заданиям",
                    callback_data="tasks"
                )
            ]
        ])
    )


# =========================================================
# STORE MANAGEMENT
# =========================================================

async def my_store(query):
    user_id = query.from_user.id

    stores = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not stores.data:
        buttons = [[
            InlineKeyboardButton(
                "➕ Создать магазин",
                callback_data="create_store"
            )
        ]]

        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    store = stores.data[0]

    await query.edit_message_text(
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="store_stats"
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
                    "⬅️ Назад",
                    callback_data="back_menu"
                )
            ]
        ])
    )


async def create_store(query, context):
    context.user_data["action"] = "create_store"

    await query.edit_message_text(
        "🏪 Введи название магазина:"
    )


async def add_product(query, context):
    user_id = query.from_user.id

    stores = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not stores.data:
        await query.edit_message_text(
            "❌ Сначала создай магазин."
        )
        return

    context.user_data["action"] = "add_product_name"
    context.user_data["store_id"] = stores.data[0]["id"]

    await query.edit_message_text(
        "📦 Введи название товара:"
    )


async def my_products(query):
    user_id = query.from_user.id

    stores = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not stores.data:
        await query.edit_message_text(
            "❌ У тебя нет магазина."
        )
        return

    store_id = stores.data[0]["id"]

    products = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    text = "📦 Мои товары\n\n"

    if not products:
        text += "Товаров пока нет."
    else:
        for product in products:
            text += (
                f"📦 {product['name']}\n"
                f"💰 {product['price']} VXC\n"
                f"🎁 Кешбэк: {product.get('cashback', 5)} VXC\n\n"
            )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "➕ Добавить товар",
                    callback_data="add_product"
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


# =========================================================
# STORE STATISTICS
# =========================================================

async def store_stats(query):
    user_id = query.from_user.id

    stores = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not stores.data:
        await query.edit_message_text(
            "❌ У тебя нет магазина."
        )
        return

    store = stores.data[0]
    store_id = store["id"]

    views = (
        supabase
        .table("store_views")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    products = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    orders = (
        supabase
        .table("orders")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    done_orders = [
        order for order in orders
        if order.get("status") == "done"
    ]

    new_orders = [
        order for order in orders
        if order.get("status") != "done"
    ]

    revenue = sum(
        int(order.get("price") or order.get("total_price") or 0)
        for order in orders
    )

    cashback = sum(
        int(order.get("cashback") or 0)
        for order in orders
    )

    product_sales = {}

    for order in orders:
        name = order.get("product_name") or "Неизвестно"
        product_sales[name] = product_sales.get(name, 0) + 1

    top_product = "Нет продаж"

    if product_sales:
        top_product = max(
            product_sales,
            key=product_sales.get
        )

    text = (
        f"📊 Статистика магазина\n\n"
        f"🏪 {store['name']}\n\n"
        f"👀 Просмотров: {len(views)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"✅ Выполнено: {len(done_orders)}\n"
        f"🆕 Новых: {len(new_orders)}\n"
        f"💰 Оборот: {revenue} VXC\n"
        f"🎁 Кешбэк: {cashback} VXC\n\n"
        f"🏆 Топ-товар: {top_product}"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="store_stats"
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


# =========================================================
# ORDERS
# =========================================================

async def my_orders(query):
    user_id = query.from_user.id

    orders = (
        supabase
        .table("orders")
        .select("*")
        .eq("buyer_id", user_id)
        .order("id", desc=True)
        .execute()
    ).data or []

    text = "📦 Мои заказы\n\n"

    if not orders:
        text += "Заказов пока нет."
    else:
        for order in orders:
            status = (
                "✅ Выполнено"
                if order.get("status") == "done"
                else "🆕 Новый"
            )

            text += (
                f"#{order['id']} — "
                f"{order.get('product_name', 'Товар')}\n"
                f"💰 {order.get('price', 0)} VXC\n"
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
    user_id = query.from_user.id

    orders = (
        supabase
        .table("orders")
        .select("*")
        .eq("seller_id", user_id)
        .order("id", desc=True)
        .execute()
    ).data or []

    text = "🛒 Заказы магазина\n\n"

    buttons = []

    if not orders:
        text += "Заказов пока нет."
    else:
        for order in orders:
            status = (
                "✅ Выполнено"
                if order.get("status") == "done"
                else "🆕 Новый"
            )

            text += (
                f"#{order['id']} — "
                f"{order.get('product_name', 'Товар')}\n"
                f"💰 {order.get('price', 0)} VXC\n"
                f"{status}\n\n"
            )

            if order.get("status") != "done":
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Выполнить #{order['id']}",
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


async def complete_order(query, order_id, context):
    user_id = query.from_user.id

    result = (
        supabase
        .table("orders")
        .select("*")
        .eq("id", order_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.edit_message_text(
            "❌ Заказ не найден."
        )
        return

    order = result.data[0]

    if order.get("seller_id") != user_id:
        await query.answer(
            "❌ Это не твой заказ!",
            show_alert=True
        )
        return

    if order.get("status") == "done":
        await query.answer(
            "Заказ уже выполнен!",
            show_alert=True
        )
        return

    supabase.table("orders").update({
        "status": "done"
    }).eq("id", order_id).execute()

    await query.edit_message_text(
        f"✅ Заказ #{order_id} выполнен!"
    )

    try:
        await context.bot.send_message(
            chat_id=order["buyer_id"],
            text=(
                "📦 Твой заказ выполнен!\n\n"
                f"🛒 Заказ #{order_id}\n"
                f"📦 {order.get('product_name', 'Товар')}"
            )
        )
    except Exception:
        pass


# =========================================================
# ADMIN PANEL
# =========================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="admin_panel"
            )
        ],
        [
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin_users"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="admin_stores"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="admin_products"
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="admin_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ В меню",
                callback_data="back_menu"
            )
        ]
    ])


def get_admin_statistics():
    users = get_all_rows("users")
    stores = get_all_rows("stores")
    products = get_all_rows("products")
    orders = get_all_rows("orders")
    visits = get_all_rows("bot_visits")
    store_views = get_all_rows("store_views")
    tasks = get_all_rows("tasks")

    buyers = [
        user for user in users
        if user.get("role") == "buyer"
    ]

    sellers = [
        user for user in users
        if user.get("role") == "seller"
    ]

    done_orders = [
        order for order in orders
        if order.get("status") == "done"
    ]

    new_orders = [
        order for order in orders
        if order.get("status") != "done"
    ]

    total_balance = sum(
        int(user.get("balance") or 0)
        for user in users
    )

    turnover = sum(
        int(
            order.get("price")
            or order.get("total_price")
            or 0
        )
        for order in orders
    )

    cashback = sum(
        int(order.get("cashback") or 0)
        for order in orders
    )

    completed_tasks = [
        task for task in tasks
        if task.get("completed")
    ]

    unique_viewers = len(set(
        view.get("user_id")
        for view in store_views
        if view.get("user_id") is not None
    ))

    return {
        "users": len(users),
        "buyers": len(buyers),
        "sellers": len(sellers),
        "stores": len(stores),
        "products": len(products),
        "orders": len(orders),
        "done_orders": len(done_orders),
        "new_orders": len(new_orders),
        "visits": len(visits),
        "store_views": len(store_views),
        "unique_viewers": unique_viewers,
        "total_balance": total_balance,
        "turnover": turnover,
        "cashback": cashback,
        "completed_tasks": len(completed_tasks),
    }


async def show_admin_panel_message(update):
    stats = get_admin_statistics()

    text = (
        "🛠️ АДМИН-ПАНЕЛЬ\n\n"

        "👥 ПОЛЬЗОВАТЕЛИ\n"
        f"Всего: {stats['users']}\n"
        f"🛒 Покупателей: {stats['buyers']}\n"
        f"🏪 Продавцов: {stats['sellers']}\n\n"

        "🤖 БОТ\n"
        f"▶️ Запусков: {stats['visits']}\n\n"

        "🏪 МАГАЗИНЫ\n"
        f"Создано: {stats['stores']}\n"
        f"👀 Просмотров: {stats['store_views']}\n"
        f"👤 Уникальных зрителей: {stats['unique_viewers']}\n\n"

        "📦 ТОВАРЫ\n"
        f"Всего товаров: {stats['products']}\n\n"

        "🛒 ЗАКАЗЫ\n"
        f"Всего: {stats['orders']}\n"
        f"✅ Выполнено: {stats['done_orders']}\n"
        f"🆕 Новых: {stats['new_orders']}\n\n"

        "💰 ЭКОНОМИКА\n"
        f"VXC у пользователей: {stats['total_balance']}\n"
        f"Оборот заказов: {stats['turnover']} VXC\n"
        f"Кешбэк: {stats['cashback']} VXC\n\n"

        "🎯 ЗАДАНИЯ\n"
        f"Выполнено: {stats['completed_tasks']}"
    )

    await update.message.reply_text(
        text,
        reply_markup=admin_keyboard()
    )


async def admin_panel(query):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return

    stats = get_admin_statistics()

    text = (
        "🛠️ АДМИН-ПАНЕЛЬ\n\n"

        "👥 ПОЛЬЗОВАТЕЛИ\n"
        f"Всего: {stats['users']}\n"
        f"🛒 Покупателей: {stats['buyers']}\n"
        f"🏪 Продавцов: {stats['sellers']}\n\n"

        "🤖 БОТ\n"
        f"▶️ Запусков: {stats['visits']}\n\n"

        "🏪 МАГАЗИНЫ\n"
        f"Создано: {stats['stores']}\n"
        f"👀 Просмотров: {stats['store_views']}\n"
        f"👤 Уникальных зрителей: {stats['unique_viewers']}\n\n"

        "📦 ТОВАРЫ\n"
        f"Всего: {stats['products']}\n\n"

        "🛒 ЗАКАЗЫ\n"
        f"Всего: {stats['orders']}\n"
        f"✅ Выполнено: {stats['done_orders']}\n"
        f"🆕 Новых: {stats['new_orders']}\n\n"

        "💰 ЭКОНОМИКА\n"
        f"VXC у пользователей: {stats['total_balance']}\n"
        f"Оборот: {stats['turnover']} VXC\n"
        f"Кешбэк: {stats['cashback']} VXC\n\n"

        "🎯 ЗАДАНИЯ\n"
        f"Выполнено: {stats['completed_tasks']}"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_keyboard()
    )


async def admin_list(query, table_name, title):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ Доступ запрещён.",
            show_alert=True
        )
        return

    rows = get_all_rows(table_name)

    text = f"{title}\n\n"

    if not rows:
        text += "Пока пусто."
    else:
        for row in rows[:50]:
            if table_name == "users":
                text += (
                    f"👤 {row.get('first_name', 'Без имени')} "
                    f"(ID {row.get('id')})\n"
                    f"💰 {row.get('balance', 0)} VXC\n"
                    f"Роль: {row.get('role')}\n\n"
                )

            elif table_name == "stores":
                text += (
                    f"🏪 {row.get('name')}\n"
                    f"ID: {row.get('id')}\n"
                    f"Владелец: {row.get('owner_id')}\n\n"
                )

            elif table_name == "products":
                text += (
                    f"📦 {row.get('name')}\n"
                    f"ID: {row.get('id')}\n"
                    f"Цена: {row.get('price')} VXC\n\n"
                )

            elif table_name == "orders":
                text += (
                    f"🛒 Заказ #{row.get('id')}\n"
                    f"Товар: {row.get('product_name')}\n"
                    f"Цена: {row.get('price') or row.get('total_price')} VXC\n"
                    f"Статус: {row.get('status')}\n\n"
                )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Админ-панель",
                    callback_data="admin_panel"
                )
            ]
        ])
    )


# =========================================================
# TEXT INPUT
# =========================================================

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    action = context.user_data.get("action")

    if action == "search_store":
        context.user_data.pop("action", None)

        stores = get_all_rows("stores")

        found = [
            store for store in stores
            if text.lower() in store["name"].lower()
        ]

        buttons = []

        result_text = "🔎 Результаты поиска\n\n"

        if not found:
            result_text += "Ничего не найдено."
        else:
            for store in found:
                result_text += f"🏪 {store['name']}\n"

                buttons.append([
                    InlineKeyboardButton(
                        store["name"],
                        callback_data=f"store_{store['id']}"
                    )
                ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Магазины",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    if action == "create_store":
        context.user_data["store_name"] = text
        context.user_data["action"] = "create_store_description"

        await update.message.reply_text(
            "📝 Теперь введи описание магазина:"
        )

        return

    if action == "create_store_description":
        name = context.user_data.get("store_name")

        supabase.table("stores").insert({
            "owner_id": user_id,
            "name": name,
            "description": text
        }).execute()

        update_balance(user_id, 15)

        if ADMIN_ID:
            update_balance(ADMIN_ID, 30)

        context.user_data.clear()

        await update.message.reply_text(
            "🎉 Магазин создан!\n\n"
            f"🏪 {name}\n"
            "💰 Ты получил +15 VXC",
            reply_markup=seller_menu(user_id)
        )

        return

    if action == "add_product_name":
        context.user_data["product_name"] = text
        context.user_data["action"] = "add_product_description"

        await update.message.reply_text(
            "📝 Введи описание товара:"
        )

        return

    if action == "add_product_description":
        context.user_data["product_description"] = text
        context.user_data["action"] = "add_product_price"

        await update.message.reply_text(
            "💰 Введи цену товара в VXC:"
        )

        return

    if action == "add_product_price":
        try:
            price = int(text)
        except ValueError:
            await update.message.reply_text(
                "❌ Цена должна быть числом."
            )
            return

        store_id = context.user_data.get("store_id")
        name = context.user_data.get("product_name")
        description = context.user_data.get("product_description")

        supabase.table("products").insert({
            "name": name,
            "description": description,
            "price": price,
            "stock": 0,
            "store_id": store_id,
            "cashback": 5
        }).execute()

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Товар добавлен!",
            reply_markup=seller_menu(user_id)
        )

        return


# =========================================================
# CALLBACK BUTTONS
# =========================================================

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # Безопасно отвечаем на callback.
    # Если Telegram уже сделал запрос просроченным,
    # ошибка не должна ломать обработчик кнопки.
    try:
        await query.answer()
    except Exception:
        pass

    user_id = query.from_user.id
    data = query.data

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if data == "admin_panel":
        await admin_panel(query)
        return

    if data == "admin_users":
        await admin_list(
            query,
            "users",
            "👥 ПОЛЬЗОВАТЕЛИ"
        )
        return

    if data == "admin_stores":
        await admin_list(
            query,
            "stores",
            "🏪 МАГАЗИНЫ"
        )
        return

    if data == "admin_products":
        await admin_list(
            query,
            "products",
            "📦 ТОВАРЫ"
        )
        return

    if data == "admin_orders":
        await admin_list(
            query,
            "orders",
            "🛒 ЗАКАЗЫ"
        )
        return

    # -----------------------------------------------------
    # ROLES
    # -----------------------------------------------------

    if data == "role_buyer":
        supabase.table("users").update({
            "role": "buyer"
        }).eq("id", user_id).execute()

        await query.edit_message_text(
            "🛒 Ты теперь покупатель!",
            reply_markup=buyer_menu(user_id)
        )
        return

    if data == "role_seller":
        supabase.table("users").update({
            "role": "seller"
        }).eq("id", user_id).execute()

        await query.edit_message_text(
            "🏪 Ты теперь продавец!",
            reply_markup=seller_menu(user_id)
        )
        return

    if data == "switch_seller":
        supabase.table("users").update({
            "role": "seller"
        }).eq("id", user_id).execute()

        await query.edit_message_text(
            "🏪 Ты теперь продавец!",
            reply_markup=seller_menu(user_id)
        )
        return

    if data == "switch_buyer":
        supabase.table("users").update({
            "role": "buyer"
        }).eq("id", user_id).execute()

        await query.edit_message_text(
            "🛒 Ты теперь покупатель!",
            reply_markup=buyer_menu(user_id)
        )
        return

    # -----------------------------------------------------
    # MENUS
    # -----------------------------------------------------

    if data == "back_menu":
        user = get_user(user_id)

        if user and user.get("role") == "seller":
            await query.edit_message_text(
                "🏪 Главное меню",
                reply_markup=seller_menu(user_id)
            )
        else:
            await query.edit_message_text(
                "🛒 Главное меню",
                reply_markup=buyer_menu(user_id)
            )

        return

    if data == "profile":
        await profile(query)
        return

    # -----------------------------------------------------
    # STORES
    # -----------------------------------------------------

    if data == "stores":
        await show_stores(query)
        return

    if data.startswith("stores_"):
        page = int(data.split("_")[1])
        await show_stores(query, page)
        return

    if data == "search_store":
        await search_store(query, context)
        return

    if data.startswith("store_"):
        store_id = int(data.split("_")[1])
        await show_store(query, store_id)
        return

    # -----------------------------------------------------
    # PRODUCTS
    # -----------------------------------------------------

    if data.startswith("product_"):
        product_id = int(data.split("_")[1])
        await show_product(query, product_id)
        return

    if data.startswith("buy_"):
        product_id = int(data.split("_")[1])
        await buy_product(query, product_id, context)
        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if data == "tasks":
        await show_tasks(query)
        return

    if data.startswith("task_"):
        task_id = data.split("_", 1)[1]
        await complete_task(query, task_id)
        return

    # -----------------------------------------------------
    # STORE MANAGEMENT
    # -----------------------------------------------------

    if data == "my_store":
        await my_store(query)
        return

    if data == "create_store":
        await create_store(query, context)
        return

    if data == "add_product":
        await add_product(query, context)
        return

    if data == "my_products":
        await my_products(query)
        return

    if data == "store_stats":
        await store_stats(query)
        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------

    if data == "my_orders":
        await my_orders(query)
        return

    if data == "seller_orders":
        await seller_orders(query)
        return

    if data.startswith("complete_"):
        order_id = int(data.split("_")[1])
        await complete_order(query, order_id, context)
        return


# =========================================================
# HEALTH CHECK
# =========================================================

async def health(request):
    return web.Response(
        text="VexMart 0.41 is alive! 🏪"
    )


# =========================================================
# MAIN
# =========================================================

async def main():
    application = (
        Application
        .builder()
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

    app = web.Application()
    app.router.add_get("/", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "10000"))

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"VexMart 0.41 started on port {port}"
    )

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
