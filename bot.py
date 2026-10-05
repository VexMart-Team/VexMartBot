# =========================================================
# VexMart Bot
# VERSION: 0.41.0
# =========================================================

import os
import asyncio
import math
from aiohttp import web, ClientSession

from supabase import create_client

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from telegram.error import BadRequest

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================================================
# VERSION
# =========================================================

VERSION = "0.41.0"


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
    raise RuntimeError(
        "Не указаны SUPABASE_URL или SUPABASE_KEY"
    )


supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)


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

    data = {
        "id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "role": "buyer",
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
        .update({
            "balance": new_balance
        })
        .eq("id", user_id)
        .execute()
    )

    if result.data:
        return result.data[0]

    return None


def get_store(store_id):
    if not store_id:
        return None

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


def get_all_rows(table_name):
    result = (
        supabase
        .table(table_name)
        .select("*")
        .execute()
    )

    return result.data or []


# =========================================================
# GEOLOCATION
# =========================================================

def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    """
    Расстояние между двумя точками Земли
    по формуле Хаверсина.
    Результат — метры.
    """

    radius = 6371000

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(
        math.sqrt(a),
        math.sqrt(1 - a)
    )

    return radius * c


def format_distance(distance):
    if distance < 1000:
        return f"{round(distance)} м"

    kilometers = distance / 1000

    if kilometers < 10:
        return f"{kilometers:.1f} км"

    return f"{round(kilometers)} км"


async def reverse_geocode(latitude, longitude):
    """
    Получает примерный адрес по координатам
    через OpenStreetMap Nominatim.
    """

    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={latitude}"
        f"&lon={longitude}"
        "&format=json"
        "&zoom=18"
        "&addressdetails=1"
    )

    headers = {
        "User-Agent": "VexMartBot/0.41.0"
    }

    try:
        async with ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=10
            ) as response:

                if response.status != 200:
                    print(
                        f"[VexMart {VERSION}] "
                        f"Geocoding HTTP {response.status}"
                    )
                    return None

                data = await response.json()

        address = data.get("address", {})

        road = (
            address.get("road")
            or address.get("street")
            or ""
        )

        house = (
            address.get("house_number")
            or ""
        )

        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or ""
        )

        if road and house:
            result = f"{road}, {house}"

            if city:
                result += f", {city}"

            return result

        display_name = data.get("display_name")

        if display_name:
            return display_name

    except Exception as error:
        print(
            f"[VexMart {VERSION}] "
            f"Geocoding error: {error}"
        )

    return None


def location_keyboard():
    return ReplyKeyboardMarkup(
        [[
            KeyboardButton(
                "📍 Отправить геолокацию",
                request_location=True
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# =========================================================
# SAFE ERROR HANDLING
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    error = context.error

    if isinstance(error, BadRequest):
        error_text = str(error).lower()

        if (
            "query is too old" in error_text
            or "query id is invalid" in error_text
            or (
                "query is too old and response "
                "timeout expired" in error_text
            )
        ):
            print(
                f"[VexMart {VERSION}] "
                "Ignored expired callback query"
            )
            return

        if "message is not modified" in error_text:
            return

    print(
        f"[VexMart {VERSION}] "
        f"Unhandled error: {error}"
    )


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

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    context.user_data.clear()

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
        reply_markup=(
            buyer_menu(query.from_user.id)
            if role == "buyer"
            else seller_menu(query.from_user.id)
        )
    )


# =========================================================
# STORES
# =========================================================

def nearby_stores_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📍 Найти магазины рядом",
                callback_data="nearby_stores"
            )
        ],
        [
            InlineKeyboardButton(
                "🔎 Поиск",
                callback_data="search_store"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )
        ]
    ])


async def show_stores(query, page=0):
    stores = get_all_rows("stores")

    per_page = 5

    start_index = page * per_page
    end_index = start_index + per_page

    current = stores[
        start_index:end_index
    ]

    text = "🏪 Магазины\n\n"

    if not current:
        text += "Пока магазинов нет."
    else:
        for store in current:
            address = store.get("address")

            text += (
                f"🏪 {store['name']}\n"
                f"📝 "
                f"{store.get('description') or 'Без описания'}\n"
            )

            if address:
                text += f"📍 {address}\n"

            text += "\n"

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
            "📍 Найти магазины рядом",
            callback_data="nearby_stores"
        )
    ])

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
        store
        for store in stores
        if search_text in store["name"].lower()
    ]

    text = "🔎 Результаты поиска\n\n"

    buttons = []

    if not found:
        text += "Ничего не найдено."
    else:
        for store in found:
            text += (
                f"🏪 {store['name']}\n"
            )

            if store.get("address"):
                text += (
                    f"📍 {store['address']}\n"
                )

            text += "\n"

            buttons.append([
                InlineKeyboardButton(
                    store["name"],
                    callback_data=f"store_{store['id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "📍 Магазины рядом",
            callback_data="nearby_stores"
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


async def request_nearby_location(
    query,
    context
):
    context.user_data["action"] = (
        "nearby_location"
    )

    await query.message.reply_text(
        "📍 Отправь свою геолокацию.\n\n"
        "Я использую её только для расчёта "
        "расстояния до магазинов.",
        reply_markup=location_keyboard()
    )


async def show_store(
    query,
    store_id,
    distance=None
):
    store = get_store(store_id)

    if not store:
        await query.edit_message_text(
            "❌ Магазин не найден."
        )
        return

    user_id = query.from_user.id

    track_store_view(
        user_id,
        store_id
    )

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

        update_balance(
            user_id,
            15
        )

    products = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 "
        f"{store.get('description') or 'Без описания'}\n"
    )

    address = store.get("address")

    if address:
        text += f"\n📍 {address}"

    if distance is not None:
        text += (
            f"\n📏 Расстояние: "
            f"{format_distance(distance)}"
        )

    text += "\n\n📦 Товары:\n"

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
                    callback_data=(
                        f"product_{product['id']}"
                    )
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


async def show_nearby_stores(
    query,
    latitude,
    longitude
):
    stores = get_all_rows("stores")

    nearby = []

    for store in stores:
        store_lat = store.get("latitude")
        store_lon = store.get("longitude")

        if store_lat is None or store_lon is None:
            continue

        try:
            distance = calculate_distance(
                float(latitude),
                float(longitude),
                float(store_lat),
                float(store_lon)
            )
        except (TypeError, ValueError):
            continue

        nearby.append((
            distance,
            store
        ))

    nearby.sort(
        key=lambda item: item[0]
    )

    text = "📍 Магазины рядом\n\n"

    buttons = []

    if not nearby:
        text += (
            "Пока нет магазинов с указанным "
            "местоположением."
        )
    else:
        for distance, store in nearby[:10]:
            text += (
                f"🏪 {store['name']}\n"
                f"📏 {format_distance(distance)}"
            )

            if store.get("address"):
                text += (
                    f"\n📍 {store['address']}"
                )

            text += "\n\n"

            buttons.append([
                InlineKeyboardButton(
                    (
                        f"🏪 {store['name']} — "
                        f"{format_distance(distance)}"
                    ),
                    callback_data=(
                        f"nearstore_{store['id']}_"
                        f"{distance:.2f}"
                    )
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "📍 Обновить местоположение",
            callback_data="nearby_stores"
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
        )# =========================================================
# PRODUCTS
# =========================================================

async def show_product(query, product_id):
    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден."
        )
        return

    store = get_store(product.get("store_id"))

    text = (
        f"📦 {product['name']}\n\n"
        f"📝 {product.get('description') or 'Без описания'}\n\n"
        f"💰 Цена: {product['price']} VXC\n"
        f"🎁 Кэшбэк: {product.get('cashback', 5)} VXC"
    )

    if store:
        text += (
            f"\n\n🏪 Магазин: {store['name']}"
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
                callback_data=(
                    f"store_{product.get('store_id')}"
                )
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def buy_product(query, product_id):
    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден."
        )
        return

    buyer_id = query.from_user.id
    buyer = get_user(buyer_id)

    if not buyer:
        await query.edit_message_text(
            "❌ Пользователь не найден."
        )
        return

    price = int(product["price"])
    balance = int(buyer.get("balance", 0))

    if balance < price:
        await query.answer(
            "❌ Недостаточно VXC!",
            show_alert=True
        )
        return

    store = get_store(product.get("store_id"))

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    seller_id = store["owner_id"]

    if seller_id == buyer_id:
        await query.answer(
            "❌ Нельзя покупать свой товар.",
            show_alert=True
        )
        return

    cashback = int(
        product.get("cashback", 5) or 0
    )

    update_balance(
        buyer_id,
        -price + cashback
    )

    update_balance(
        seller_id,
        price
    )

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
        "status": "pending"
    }

    try:
        result = (
            supabase
            .table("orders")
            .insert(order_data)
            .execute()
        )

        order_id = (
            result.data[0]["id"]
            if result.data
            else None
        )

    except Exception as error:
        print(
            f"[VexMart {VERSION}] "
            f"Order error: {error}"
        )

        update_balance(
            buyer_id,
            price - cashback
        )

        update_balance(
            seller_id,
            -price
        )

        await query.answer(
            "❌ Ошибка оформления заказа.",
            show_alert=True
        )
        return

    await query.answer(
        "✅ Покупка оформлена!"
    )

    await query.edit_message_text(
        "✅ Заказ оформлен!\n\n"
        f"📦 Товар: {product['name']}\n"
        f"💰 Цена: {price} VXC\n"
        f"🎁 Кэшбэк: +{cashback} VXC\n"
        f"🏪 Магазин: {store['name']}\n"
        f"📋 Заказ №{order_id or '—'}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📦 Мои заказы",
                    callback_data="my_orders"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ В меню",
                    callback_data="back_menu"
                )
            ]
        ])
    )

    try:
        await query.get_bot().send_message(
            chat_id=seller_id,
            text=(
                "🛒 Новый заказ!\n\n"
                f"📦 Товар: {product['name']}\n"
                f"💰 Цена: {price} VXC\n"
                f"👤 Покупатель: "
                f"{query.from_user.first_name}\n"
                f"📋 Заказ №{order_id or '—'}"
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
        (
            "daily",
            "🎁 Ежедневный бонус",
            5
        ),
        (
            "profile",
            "👤 Открыть профиль",
            3
        )
    ]

    text = "🎯 Задания\n\n"

    buttons = []

    for task_id, name, reward in tasks:
        existing = (
            supabase
            .table("tasks")
            .select("*")
            .eq("user_id", user_id)
            .eq("task", task_id)
            .limit(1)
            .execute()
        )

        completed = (
            existing.data
            and existing.data[0].get("completed")
        )

        if completed:
            text += (
                f"✅ {name} — выполнено\n"
            )
        else:
            text += (
                f"🎯 {name} — +{reward} VXC\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    name,
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
        "profile": 3
    }

    if task_id not in rewards:
        return

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
            "Это задание уже выполнено!",
            show_alert=True
        )
        return

    reward = rewards[task_id]

    if existing.data:
        (
            supabase
            .table("tasks")
            .update({
                "completed": True
            })
            .eq("user_id", user_id)
            .eq("task", task_id)
            .execute()
        )
    else:
        (
            supabase
            .table("tasks")
            .insert({
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
        f"🎉 +{reward} VXC!"
    )

    await show_tasks(query)


# =========================================================
# STORE MANAGEMENT
# =========================================================

async def my_store(query):
    user_id = query.from_user.id

    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.",
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

    store = result.data[0]

    products = (
        supabase
        .table("products")
        .select("*")
        .eq("store_id", store["id"])
        .execute()
    ).data or []

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}"
        )

    text += (
        f"\n\n📦 Товаров: {len(products)}"
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
                    "📦 Мои товары",
                    callback_data="my_products"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Статистика",
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


async def create_store(query, context):
    context.user_data.clear()
    context.user_data["action"] = "create_store_name"

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


async def add_product(query, context):
    user_id = query.from_user.id

    result = (
        supabase
        .table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.answer(
            "Сначала создай магазин.",
            show_alert=True
        )
        return

    context.user_data.clear()
    context.user_data["action"] = "product_name"
    context.user_data["product_store_id"] = (
        result.data[0]["id"]
    )

    await query.edit_message_text(
        "➕ Добавление товара\n\n"
        "Напиши название товара:"
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
            "❌ У тебя нет магазина.",
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
                f"🎁 Кэшбэк: "
                f"{product.get('cashback', 5)} VXC\n\n"
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
                    callback_data="my_store"
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
            "❌ Магазин не найден."
        )
        return

    store = stores.data[0]
    store_id = store["id"]

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

    views = (
        supabase
        .table("store_views")
        .select("id")
        .eq("store_id", store_id)
        .execute()
    ).data or []

    done_orders = [
        order
        for order in orders
        if order.get("status") == "done"
    ]

    new_orders = [
        order
        for order in orders
        if order.get("status") == "pending"
    ]

    revenue = sum(
        int(order.get("total_price", 0) or 0)
        for order in done_orders
    )

    cashback = sum(
        int(order.get("cashback", 0) or 0)
        for order in done_orders
    )

    product_count = {}

    for order in orders:
        name = order.get("product_name")

        if name:
            product_count[name] = (
                product_count.get(name, 0)
                + int(order.get("quantity", 1) or 1)
            )

    top_product = "Нет данных"

    if product_count:
        top_product = max(
            product_count,
            key=product_count.get
        )

    text = (
        f"📊 Статистика магазина\n\n"
        f"🏪 {store['name']}\n\n"
        f"👀 Просмотров: {len(views)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"🆕 Новых: {len(new_orders)}\n"
        f"✅ Выполнено: {len(done_orders)}\n"
        f"💰 Выручка: {revenue} VXC\n"
        f"🎁 Кэшбэк: {cashback} VXC\n"
        f"🏆 Популярный товар: {top_product}"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="my_store"
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
        .order("created_at", desc=True)
        .execute()
    ).data or []

    text = "📦 Мои заказы\n\n"

    if not orders:
        text += "Заказов пока нет."
    else:
        for order in orders:
            status = order.get(
                "status",
                "pending"
            )

            status_text = {
                "pending": "🕐 Ожидает",
                "done": "✅ Выполнен"
            }.get(
                status,
                status
            )

            text += (
                f"📦 {order.get('product_name', 'Товар')}\n"
                f"💰 {order.get('price', order.get('total_price', 0))} VXC\n"
                f"📋 Заказ №{order['id']}\n"
                f"{status_text}\n\n"
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
        .order("created_at", desc=True)
        .execute()
    ).data or []

    text = "🛒 Заказы магазина\n\n"

    buttons = []

    if not orders:
        text += "Заказов пока нет."
    else:
        for order in orders:
            status = order.get(
                "status",
                "pending"
            )

            status_text = (
                "🕐 Новый"
                if status == "pending"
                else "✅ Выполнен"
            )

            text += (
                f"📦 {order.get('product_name', 'Товар')}\n"
                f"💰 {order.get('price', order.get('total_price', 0))} VXC\n"
                f"📋 Заказ №{order['id']}\n"
                f"{status_text}\n\n"
            )

            if status == "pending":
                buttons.append([
                    InlineKeyboardButton(
                        f"✅ Выполнить №{order['id']}",
                        callback_data=(
                            f"complete_{order['id']}"
                        )
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


async def complete_order(query, order_id):
    result = (
        supabase
        .table("orders")
        .select("*")
        .eq("id", order_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.answer(
            "❌ Заказ не найден.",
            show_alert=True
        )
        return

    order = result.data[0]

    if order.get("seller_id") != query.from_user.id:
        await query.answer(
            "❌ Это не твой заказ.",
            show_alert=True
        )
        return

    if order.get("status") == "done":
        await query.answer(
            "Заказ уже выполнен.",
            show_alert=True
        )
        return

    (
        supabase
        .table("orders")
        .update({
            "status": "done"
        })
        .eq("id", order_id)
        .execute()
    )

    await query.answer(
        "✅ Заказ выполнен!"
    )

    buyer_id = order.get("buyer_id")

    if buyer_id:
        try:
            await query.get_bot().send_message(
                chat_id=buyer_id,
                text=(
                    "✅ Твой заказ выполнен!\n\n"
                    f"📦 {order.get('product_name', 'Товар')}\n"
                    f"📋 Заказ №{order_id}"
                )
            )
        except Exception:
            pass

    await seller_orders(query)


# =========================================================
# TEXT INPUT
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text
    action = context.user_data.get("action")

    if not action:
        return

    # -----------------------------------------------------
    # SEARCH STORE
    # -----------------------------------------------------

    if action == "search_store":
        stores = get_all_rows("stores")

        search_text = text.lower()

        found = [
            store
            for store in stores
            if search_text in store["name"].lower()
        ]

        result_text = "🔎 Результаты поиска\n\n"

        buttons = []

        if not found:
            result_text += "Ничего не найдено."
        else:
            for store in found:
                result_text += (
                    f"🏪 {store['name']}\n"
                )

                if store.get("address"):
                    result_text += (
                        f"📍 {store['address']}\n"
                    )

                result_text += "\n"

                buttons.append([
                    InlineKeyboardButton(
                        store["name"],
                        callback_data=(
                            f"store_{store['id']}"
                        )
                    )
                ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Магазины",
                callback_data="stores"
            )
        ])

        context.user_data.clear()

        await update.message.reply_text(
            result_text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    # -----------------------------------------------------
    # CREATE STORE — NAME
    # -----------------------------------------------------

    if action == "create_store_name":
        context.user_data["store_name"] = text
        context.user_data["action"] = (
            "create_store_description"
        )

        await update.message.reply_text(
            "📝 Теперь напиши описание магазина:"
        )

        return

    # -----------------------------------------------------
    # CREATE STORE — DESCRIPTION
    # -----------------------------------------------------

    if action == "create_store_description":
        context.user_data["store_description"] = text
        context.user_data["action"] = (
            "create_store_location"
        )

        await update.message.reply_text(
            "📍 Теперь отправь геолокацию магазина.\n\n"
            "Нажми кнопку ниже и выбери место, "
            "где находится магазин.",
            reply_markup=location_keyboard()
        )

        return

    # -----------------------------------------------------
    # CREATE STORE — CANCEL
    # -----------------------------------------------------

    if (
        action == "create_store_location"
        and text == "❌ Отмена"
    ):
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Создание магазина отменено.",
            reply_markup=ReplyKeyboardRemove()
        )

        return

    # -----------------------------------------------------
    # ADD PRODUCT — NAME
    # -----------------------------------------------------

    if action == "product_name":
        context.user_data["product_name"] = text
        context.user_data["action"] = (
            "product_description"
        )

        await update.message.reply_text(
            "📝 Напиши описание товара:"
        )

        return

    # -----------------------------------------------------
    # ADD PRODUCT — DESCRIPTION
    # -----------------------------------------------------

    if action == "product_description":
        context.user_data["product_description"] = text
        context.user_data["action"] = (
            "product_price"
        )

        await update.message.reply_text(
            "💰 Напиши цену товара в VXC:"
        )

        return

    # -----------------------------------------------------
    # ADD PRODUCT — PRICE
    # -----------------------------------------------------

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

        store_id = context.user_data.get(
            "product_store_id"
        )

        try:
            (
                supabase
                .table("products")
                .insert({
                    "name": context.user_data["product_name"],
                    "description": context.user_data[
                        "product_description"
                    ],
                    "price": price,
                    "stock": 0,
                    "store_id": store_id,
                    "cashback": 5
                })
                .execute()
            )

        except Exception as error:
            print(
                f"[VexMart {VERSION}] "
                f"Product error: {error}"
            )

            await update.message.reply_text(
                "❌ Не удалось добавить товар."
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "✅ Товар добавлен!",
            reply_markup=ReplyKeyboardRemove()
        )

        user = get_user(update.effective_user.id)

        if user and user.get("role") == "seller":
            await update.message.reply_text(
                "🏪 Панель продавца:",
                reply_markup=seller_menu(
                    update.effective_user.id
                )
            )

        return


# =========================================================
# LOCATION INPUT
# =========================================================

async def location_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    location = update.message.location

    if not location:
        return

    action = context.user_data.get("action")

    latitude = location.latitude
    longitude = location.longitude

    # -----------------------------------------------------
    # STORE CREATION LOCATION
    # -----------------------------------------------------

    if action == "create_store_location":
        context.user_data["store_latitude"] = latitude
        context.user_data["store_longitude"] = longitude

        await update.message.reply_text(
            "🔎 Определяю адрес..."
        )

        address = await reverse_geocode(
            latitude,
            longitude
        )

        if not address:
            await update.message.reply_text(
                "❌ Не удалось определить адрес.\n\n"
                "Попробуй отправить геолокацию ещё раз.",
                reply_markup=location_keyboard()
            )
            return

        context.user_data["store_address"] = address

        await update.message.reply_text(
            f"📍 Адрес вашего магазина:\n\n"
            f"{address}\n\n"
            "Всё верно?",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Подтверди выбор:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Всё верно",
                        callback_data=(
                            "confirm_store_location"
                        )
                    ),
                    InlineKeyboardButton(
                        "🔄 Выбрать заново",
                        callback_data=(
                            "retry_store_location"
                        )
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # NEARBY STORES
    # -----------------------------------------------------

    if action == "nearby_location":
        context.user_data.clear()

        stores = get_all_rows("stores")

        nearby = []

        for store in stores:
            store_lat = store.get("latitude")
            store_lon = store.get("longitude")

            if store_lat is None or store_lon is None:
                continue

            try:
                distance = calculate_distance(
                    latitude,
                    longitude,
                    float(store_lat),
                    float(store_lon)
                )
            except (TypeError, ValueError):
                continue

            nearby.append((
                distance,
                store
            ))

        nearby.sort(
            key=lambda item: item[0]
        )

        text = "📍 Магазины рядом\n\n"

        buttons = []

        if not nearby:
            text += (
                "Пока нет магазинов, "
                "которые указали геолокацию."
            )
        else:
            for distance, store in nearby[:10]:
                text += (
                    f"🏪 {store['name']}\n"
                    f"📏 {format_distance(distance)}"
                )

                if store.get("address"):
                    text += (
                        f"\n📍 {store['address']}"
                    )

                text += "\n\n"

                buttons.append([
                    InlineKeyboardButton(
                        (
                            f"🏪 {store['name']} — "
                            f"{format_distance(distance)}"
                        ),
                        callback_data=(
                            f"nearstore_{store['id']}_"
                            f"{distance:.2f}"
                        )
                    )
                ])

        buttons.append([
            InlineKeyboardButton(
                "📍 Обновить местоположение",
                callback_data="nearby_stores"
            )
        ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Магазины",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            text,
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Выбери магазин:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return


# =========================================================
# SWITCH ROLES
# =========================================================

async def switch_seller(query):
    user_id = query.from_user.id

    (
        supabase
        .table("users")
        .update({
            "role": "seller"
        })
        .eq("id", user_id)
        .execute()
    )

    await query.edit_message_text(
        "🏪 Теперь ты продавец!",
        reply_markup=seller_menu(user_id)
    )


async def switch_buyer(query):
    user_id = query.from_user.id

    (
        supabase
        .table("users")
        .update({
            "role": "buyer"
        })
        .eq("id", user_id)
        .execute()
    )

    await query.edit_message_text(
        "🛒 Теперь ты покупатель!",
        reply_markup=buyer_menu(user_id)
    )


# =========================================================
# ADMIN
# =========================================================

def admin_menu():
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
            ),
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="admin_stores"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="admin_products"
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="admin_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu"
            )
        ]
    ])


async def show_admin_panel_message(update):
    await update.message.reply_text(
        "🛠️ Админ-панель VexMart",
        reply_markup=admin_menu()
    )


async def show_admin_panel(query):
    users = get_all_rows("users")
    stores = get_all_rows("stores")
    products = get_all_rows("products")
    orders = get_all_rows("orders")
    visits = get_all_rows("bot_visits")
    views = get_all_rows("store_views")
    tasks = get_all_rows("tasks")

    text = (
        "🛠️ Админ-панель\n\n"
        f"👥 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"👀 Запусков бота: {len(visits)}\n"
        f"🏪 Просмотров магазинов: {len(views)}\n"
        f"🎯 Заданий: {len(tasks)}"
    )

    await query.edit_message_text(
        text,
        reply_markup=admin_menu()
    )


async def admin_list(
    query,
    table_name,
    title,
    formatter
):
    if query.from_user.id != ADMIN_ID:
        return

    rows = get_all_rows(table_name)

    text = f"{title}\n\n"

    if not rows:
        text += "Нет данных."
    else:
        for row in rows[:50]:
            text += formatter(row) + "\n"

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
# CALLBACK ROUTER
# =========================================================

async def button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        return

    data = query.data

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if data.startswith("admin_"):
        if query.from_user.id != ADMIN_ID:
            return

        if data == "admin_panel":
            await show_admin_panel(query)
            return

        if data == "admin_users":
            await admin_list(
                query,
                "users",
                "👥 Пользователи",
                lambda row: (
                    f"👤 {row.get('first_name', '—')} "
                    f"({row.get('id')}) — "
                    f"{row.get('balance', 0)} VXC"
                )
            )
            return

        if data == "admin_stores":
            await admin_list(
                query,
                "stores",
                "🏪 Магазины",
                lambda row: (
                    f"🏪 {row.get('name', '—')} "
                    f"(ID {row.get('id')})"
                )
            )
            return

        if data == "admin_products":
            await admin_list(
                query,
                "products",
                "📦 Товары",
                lambda row: (
                    f"📦 {row.get('name', '—')} — "
                    f"{row.get('price', 0)} VXC"
                )
            )
            return

        if data == "admin_orders":
            await admin_list(
                query,
                "orders",
                "🛒 Заказы",
                lambda row: (
                    f"📋 №{row.get('id')} — "
                    f"{row.get('product_name', 'Товар')} — "
                    f"{row.get('status', 'pending')}"
                )
            )
            return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if data == "profile":
        await profile(query)
        return

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    if data == "back_menu":
        user = get_user(query.from_user.id)

        if user and user.get("role") == "seller":
            await query.edit_message_text(
                "🏪 Панель продавца:",
                reply_markup=seller_menu(
                    query.from_user.id
                )
            )
        else:
            await query.edit_message_text(
                "🏪 VexMart",
                reply_markup=buyer_menu(
                    query.from_user.id
                )
            )

        return

    # -----------------------------------------------------
    # STORES
    # -----------------------------------------------------

    if data == "stores":
        await show_stores(query)
        return

    if data.startswith("stores_"):
        try:
            page = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            page = 0

        await show_stores(
            query,
            page
        )
        return

    if data == "search_store":
        await search_store(
            query,
            context
        )
        return

    if data == "nearby_stores":
        await request_nearby_location(
            query,
            context
        )
        return

    # -----------------------------------------------------
    # STORE CREATION LOCATION
    # -----------------------------------------------------

    if data == "confirm_store_location":
        user_id = query.from_user.id

        name = context.user_data.get(
            "store_name"
        )

        description = context.user_data.get(
            "store_description"
        )

        latitude = context.user_data.get(
            "store_latitude"
        )

        longitude = context.user_data.get(
            "store_longitude"
        )

        address = context.user_data.get(
            "store_address"
        )

        if not all([
            name,
            description,
            latitude is not None,
            longitude is not None,
            address
        ]):
            await query.edit_message_text(
                "❌ Данные магазина потерялись.\n"
                "Попробуй создать магазин заново."
            )

            context.user_data.clear()
            return

        try:
            result = (
                supabase
                .table("stores")
                .insert({
                    "owner_id": user_id,
                    "name": name,
                    "description": description,
                    "latitude": latitude,
                    "longitude": longitude,
                    "address": address
                })
                .execute()
            )

            if not result.data:
                raise Exception(
                    "Store insert returned no data"
                )

        except Exception as error:
            print(
                f"[VexMart {VERSION}] "
                f"Store creation error: {error}"
            )

            await query.edit_message_text(
                "❌ Не удалось создать магазин."
            )
            return

        update_balance(
            user_id,
            15
        )

        if ADMIN_ID:
            update_balance(
                ADMIN_ID,
                30
            )

        context.user_data.clear()

        await query.edit_message_text(
            "🎉 Магазин создан!\n\n"
            f"🏪 {name}\n"
            f"📍 {address}\n\n"
            "💰 Ты получил +15 VXC!",
            reply_markup=seller_menu(user_id)
        )

        return

    if data == "retry_store_location":
        context.user_data["action"] = (
            "create_store_location"
        )

        await query.edit_message_text(
            "🔄 Хорошо!\n\n"
            "Отправь геолокацию магазина ещё раз."
        )

        await query.message.reply_text(
            "📍 Нажми кнопку:",
            reply_markup=location_keyboard()
        )

        return

    # -----------------------------------------------------
    # STORES WITH DISTANCE
    # -----------------------------------------------------

    if data.startswith("nearstore_"):
        parts = data.split("_")

        try:
            store_id = int(parts[1])
            distance = float(parts[2])
        except (ValueError, IndexError):
            await query.edit_message_text(
                "❌ Ошибка магазина."
            )
            return

        await show_store(
            query,
            store_id,
            distance
        )
        return

    # -----------------------------------------------------
    # STORE
    # -----------------------------------------------------

    if data == "store_stats":
        await store_stats(query)
        return

    if data.startswith("store_"):
        try:
            store_id = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            return

        await show_store(
            query,
            store_id
        )
        return

    # -----------------------------------------------------
    # PRODUCTS
    # -----------------------------------------------------

    if data.startswith("product_"):
        try:
            product_id = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            return

        await show_product(
            query,
            product_id
        )
        return

    if data.startswith("buy_"):
        try:
            product_id = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            return

        await buy_product(
            query,
            product_id
        )
        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if data == "tasks":
        await show_tasks(query)
        return

    if data.startswith("task_"):
        task_id = data.split(
            "_",
            1
        )[1]

        await complete_task(
            query,
            task_id
        )
        return

    # -----------------------------------------------------
    # STORE MANAGEMENT
    # -----------------------------------------------------

    if data == "my_store":
        await my_store(query)
        return

    if data == "create_store":
        await create_store(
            query,
            context
        )
        return

    if data == "add_product":
        await add_product(
            query,
            context
        )
        return

    if data == "my_products":
        await my_products(query)
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
        try:
            order_id = int(
                data.split("_", 1)[1]
            )
        except ValueError:
            return

        await complete_order(
            query,
            order_id
        )
        return

    # -----------------------------------------------------
    # ROLE
    # -----------------------------------------------------

    if data == "switch_seller":
        await switch_seller(query)
        return

    if data == "switch_buyer":
        await switch_buyer(query)
        return


# =========================================================
# HEALTH CHECK
# =========================================================

async def health(request):
    return web.Response(
        text=(
            f"VexMart {VERSION} "
            f"is alive! 🏪"
        )
    )


# =========================================================
# MAIN
# =========================================================

async def main():
    print(
        f"Starting VexMart Bot {VERSION}"
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_error_handler(
        error_handler
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
            filters.LOCATION,
            location_message
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

    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    port = int(
        os.getenv("PORT", "10000")
    )

    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"VexMart {VERSION} "
        f"running on port {port}"
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
