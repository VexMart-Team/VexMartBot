# =========================================================
# VexMart Bot
# VERSION: 0.41.1
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

VERSION = "0.41.1"


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
        "User-Agent": "VexMartBot/0.41.1"
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
# VexMart Bot
# VERSION: 0.41.1
# PART 2 / 2
# =========================================================


# =========================================================
# PRODUCTS
# =========================================================

async def show_products(query):
    result = (
        supabase.table("products")
        .select("*")
        .order("id")
        .execute()
    )

    products = result.data or []

    if not products:
        await query.edit_message_text(
            "📦 Товаров пока нет.",
            reply_markup=back_keyboard()
        )
        return

    buttons = []

    for product in products:
        buttons.append([
            InlineKeyboardButton(
                f"🛒 {product['name']} — {product['price']} V₽",
                callback_data=f"product_{product['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton("⬅️ Назад", callback_data="buyer_menu")
    ])

    await query.edit_message_text(
        "📦 Товары VexMart:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_product(query, product_id):
    result = (
        supabase.table("products")
        .select("*")
        .eq("id", product_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.answer("❌ Товар не найден.", show_alert=True)
        return

    product = result.data[0]

    store = None

    if product.get("store_id"):
        store_result = (
            supabase.table("stores")
            .select("*")
            .eq("id", product["store_id"])
            .limit(1)
            .execute()
        )

        if store_result.data:
            store = store_result.data[0]

    text = (
        f"📦 {product['name']}\n\n"
        f"📝 {product.get('description') or 'Без описания'}\n\n"
        f"💰 Цена: {product['price']} V₽\n"
        f"📦 Остаток: {product.get('stock', 0)}\n"
        f"💸 Кэшбэк: {product.get('cashback', 5)}%\n"
    )

    if store:
        text += f"\n🏪 Магазин: {store['name']}"

        if store.get("address"):
            text += f"\n📍 {store['address']}"

    keyboard = [
        [
            InlineKeyboardButton(
                "🛒 Купить",
                callback_data=f"buy_{product_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="products"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def buy_product(query, context, product_id):
    user_id = query.from_user.id

    product_result = (
        supabase.table("products")
        .select("*")
        .eq("id", product_id)
        .limit(1)
        .execute()
    )

    if not product_result.data:
        await query.answer("❌ Товар не найден.", show_alert=True)
        return

    product = product_result.data[0]

    if product.get("stock", 0) <= 0:
        await query.answer("❌ Товар закончился.", show_alert=True)
        return

    user_result = (
        supabase.table("users")
        .select("*")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not user_result.data:
        await query.answer("❌ Пользователь не найден.", show_alert=True)
        return

    user = user_result.data[0]

    price = product["price"]

    if user.get("balance", 0) < price:
        await query.answer(
            "❌ Недостаточно VexCoin.",
            show_alert=True
        )
        return

    seller_id = None

    if product.get("store_id"):
        store_result = (
            supabase.table("stores")
            .select("*")
            .eq("id", product["store_id"])
            .limit(1)
            .execute()
        )

        if store_result.data:
            seller_id = store_result.data[0]["owner_id"]

    cashback_percent = product.get("cashback", 5) or 0
    cashback = int(price * cashback_percent / 100)

    new_balance = user["balance"] - price + cashback

    supabase.table("users").update({
        "balance": new_balance
    }).eq("id", user_id).execute()

    supabase.table("products").update({
        "stock": product.get("stock", 0) - 1
    }).eq("id", product_id).execute()

    order_data = {
        "user_id": user_id,
        "product_id": product_id,
        "quantity": 1,
        "total_price": price,
        "status": "pending",
        "buyer_id": user_id,
        "seller_id": seller_id,
        "product_name": product["name"],
        "price": price,
        "cashback": cashback,
        "store_id": product.get("store_id")
    }

    supabase.table("orders").insert(order_data).execute()

    await query.edit_message_text(
        "✅ Покупка успешно оформлена!\n\n"
        f"📦 {product['name']}\n"
        f"💰 Потрачено: {price} V₽\n"
        f"💸 Кэшбэк: +{cashback} V₽\n\n"
        f"💳 Баланс: {new_balance} V₽",
        reply_markup=back_keyboard()
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_menu(query):
    user_id = query.from_user.id

    result = (
        supabase.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )

    tasks = result.data or []

    if not tasks:
        tasks = [
            {
                "user_id": user_id,
                "task": "Зайти в VexMart",
                "reward": 10,
                "completed": False
            },
            {
                "user_id": user_id,
                "task": "Посмотреть магазин",
                "reward": 15,
                "completed": False
            }
        ]

        for task in tasks:
            try:
                supabase.table("tasks").insert(task).execute()
            except Exception:
                pass

    result = (
        supabase.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .execute()
    )

    tasks = result.data or []

    text = "🎯 Задания\n\n"
    buttons = []

    for task in tasks:
        if task["completed"]:
            text += (
                f"✅ {task['task']} — "
                f"+{task['reward']} V₽\n"
            )
        else:
            text += (
                f"🔲 {task['task']} — "
                f"+{task['reward']} V₽\n"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"▶️ Выполнить: {task['task']}",
                    callback_data=f"task_{task['task']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def complete_task(query, task_name):
    user_id = query.from_user.id

    result = (
        supabase.table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("task", task_name)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.answer("❌ Задание не найдено.", show_alert=True)
        return

    task = result.data[0]

    if task["completed"]:
        await query.answer(
            "Это задание уже выполнено.",
            show_alert=True
        )
        return

    user_result = (
        supabase.table("users")
        .select("balance")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    if not user_result.data:
        return

    balance = user_result.data[0].get("balance", 0)

    supabase.table("users").update({
        "balance": balance + task["reward"]
    }).eq("id", user_id).execute()

    supabase.table("tasks").update({
        "completed": True
    }).eq("user_id", user_id).eq(
        "task", task_name
    ).execute()

    await query.answer(
        f"🎉 +{task['reward']} VexCoin!",
        show_alert=True
    )

    await tasks_menu(query)


# =========================================================
# STORE MANAGEMENT
# =========================================================

async def my_store(query):
    user_id = query.from_user.id

    result = (
        supabase.table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.\n\n"
            "Создай его и начни продавать товары!",
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
                        callback_data="seller_menu"
                    )
                ]
            ])
        )
        return

    store = result.data[0]

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += f"\n📍 {store['address']}\n"
    else:
        text += "\n📍 Местоположение ещё не указано\n"

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="store_stats"
            )
        ],
        [
            InlineKeyboardButton(
                "📍 Указать местоположение",
                callback_data="set_store_location"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# UPDATE EXISTING STORE LOCATION
# =========================================================

async def set_store_location(query, context):
    user_id = query.from_user.id

    result = (
        supabase.table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not result.data:
        await query.answer(
            "❌ У тебя нет магазина.",
            show_alert=True
        )
        return

    store = result.data[0]

    context.user_data.clear()
    context.user_data["action"] = "update_store_location"
    context.user_data["update_store_id"] = store["id"]

    await query.edit_message_text(
        "📍 Указать местоположение магазина\n\n"
        "Отправь геолокацию магазина.\n"
        "Я определю адрес и покажу его для подтверждения."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку ниже и отправь геолокацию магазина:",
        reply_markup=location_keyboard()
    )


# =========================================================
# STORE STATS
# =========================================================

async def store_stats(query):
    user_id = query.from_user.id

    store_result = (
        supabase.table("stores")
        .select("*")
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )

    if not store_result.data:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    store = store_result.data[0]

    products_result = (
        supabase.table("products")
        .select("*")
        .eq("store_id", store["id"])
        .execute()
    )

    products = products_result.data or []

    orders_result = (
        supabase.table("orders")
        .select("*")
        .eq("store_id", store["id"])
        .execute()
    )

    orders = orders_result.data or []

    total_sales = sum(
        order.get("total_price", 0)
        for order in orders
    )

    text = (
        f"📊 Статистика магазина\n\n"
        f"🏪 {store['name']}\n\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛍 Заказов: {len(orders)}\n"
        f"💰 Продаж: {total_sales} V₽\n"
    )

    if store.get("address"):
        text += f"\n📍 {store['address']}"

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
# CREATE STORE
# =========================================================

async def create_store(query, context):
    context.user_data.clear()
    context.user_data["action"] = "create_store_name"

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


# =========================================================
# ORDERS
# =========================================================

async def buyer_orders(query):
    user_id = query.from_user.id

    result = (
        supabase.table("orders")
        .select("*")
        .eq("buyer_id", user_id)
        .order("id", desc=True)
        .execute()
    )

    orders = result.data or []

    if not orders:
        await query.edit_message_text(
            "🛍 У тебя пока нет заказов.",
            reply_markup=back_keyboard()
        )
        return

    text = "🛍 Мои заказы\n\n"

    for order in orders:
        status = order.get("status", "pending")

        if status == "pending":
            status_text = "⏳ Ожидает"
        elif status == "accepted":
            status_text = "✅ Принят"
        elif status == "completed":
            status_text = "📦 Выполнен"
        else:
            status_text = status

        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{status_text}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard()
    )


async def seller_orders(query):
    user_id = query.from_user.id

    result = (
        supabase.table("orders")
        .select("*")
        .eq("seller_id", user_id)
        .order("id", desc=True)
        .execute()
    )

    orders = result.data or []

    if not orders:
        await query.edit_message_text(
            "📦 Заказов пока нет.",
            reply_markup=back_keyboard()
        )
        return

    text = "📦 Заказы покупателей\n\n"

    buttons = []

    for order in orders:
        status = order.get("status", "pending")

        if status == "pending":
            status_text = "⏳ Ожидает"
        elif status == "accepted":
            status_text = "✅ Принят"
        elif status == "completed":
            status_text = "📦 Выполнен"
        else:
            status_text = status

        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{status_text}\n\n"
        )

        if status == "pending":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ Принять #{order['id']}",
                    callback_data=f"accept_order_{order['id']}"
                )
            ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="seller_menu"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def accept_order(query, order_id):
    user_id = query.from_user.id

    result = (
        supabase.table("orders")
        .select("*")
        .eq("id", order_id)
        .eq("seller_id", user_id)
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

    if order.get("status") != "pending":
        await query.answer(
            "Этот заказ уже обработан.",
            show_alert=True
        )
        return

    supabase.table("orders").update({
        "status": "accepted"
    }).eq("id", order_id).execute()

    await query.answer(
        "✅ Заказ принят!",
        show_alert=True
    )

    await seller_orders(query)


# =========================================================
# TEXT INPUT
# =========================================================

async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    action = context.user_data.get("action")

    if text == "❌ Отмена":
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Действие отменено.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=buyer_menu(user_id)
        )
        return

    if action == "create_store_name":
        context.user_data["store_name"] = text
        context.user_data["action"] = "create_store_description"

        await update.message.reply_text(
            "📝 Теперь напиши описание магазина:"
        )
        return

    if action == "create_store_description":
        name = context.user_data.get("store_name")

        if not name:
            context.user_data.clear()
            await update.message.reply_text(
                "❌ Что-то пошло не так. Попробуй создать магазин ещё раз."
            )
            return

        try:
            supabase.table("stores").insert({
                "owner_id": user_id,
                "name": name,
                "description": text
            }).execute()
        except Exception as e:
            print("Create store error:", e)

            context.user_data.clear()

            await update.message.reply_text(
                "❌ Не удалось создать магазин."
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "🎉 Магазин успешно создан!\n\n"
            f"🏪 {name}\n\n"
            "Теперь можешь указать его местоположение через "
            "«🏪 Мой магазин» → «📍 Указать местоположение».",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Меню продавца:",
            reply_markup=seller_menu(user_id)
        )
        return

    await update.message.reply_text(
        "🤔 Я не понял сообщение.\n"
        "Используй кнопки меню."
    )


# =========================================================
# LOCATION INPUT
# =========================================================

async def location_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    location = update.message.location

    if not location:
        return

    latitude = location.latitude
    longitude = location.longitude

    action = context.user_data.get("action")

    # -----------------------------------------------------
    # UPDATE EXISTING STORE LOCATION
    # -----------------------------------------------------

    if action == "update_store_location":
        store_id = context.user_data.get("update_store_id")

        store_result = (
            supabase.table("stores")
            .select("*")
            .eq("id", store_id)
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )

        if not store_result.data:
            context.user_data.clear()

            await update.message.reply_text(
                "❌ Магазин не найден."
            )
            return

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

        context.user_data["update_store_latitude"] = latitude
        context.user_data["update_store_longitude"] = longitude
        context.user_data["update_store_address"] = address

        await update.message.reply_text(
            "📍 Вот какой адрес удалось определить:\n\n"
            f"{address}\n\n"
            "Всё верно?"
        )

        await update.message.reply_text(
            "Выбери действие:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Да, сохранить",
                        callback_data="confirm_update_store_location"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Отправить другую геолокацию",
                        callback_data="retry_update_store_location"
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # CREATE STORE LOCATION
    # -----------------------------------------------------

    if action == "create_store_location":
        context.user_data["latitude"] = latitude
        context.user_data["longitude"] = longitude

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

        context.user_data["address"] = address

        await update.message.reply_text(
            "📍 Адрес магазина:\n\n"
            f"{address}\n\n"
            "Всё верно?"
        )

        await update.message.reply_text(
            "Выбери действие:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Да",
                        callback_data="confirm_store_location"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Другая геолокация",
                        callback_data="retry_store_location"
                    )
                ]
            ])
        )

        return

    # -----------------------------------------------------
    # NEARBY STORES
    # -----------------------------------------------------

    if action == "nearby_stores":
        stores_result = (
            supabase.table("stores")
            .select("*")
            .execute()
        )

        stores = stores_result.data or []

        nearby = []

        for store in stores:
            if store.get("latitude") is None or store.get("longitude") is None:
                continue

            distance = calculate_distance(
                latitude,
                longitude,
                store["latitude"],
                store["longitude"]
            )

            nearby.append((distance, store))

        nearby.sort(key=lambda item: item[0])

        context.user_data.clear()

        if not nearby:
            await update.message.reply_text(
                "📍 Рядом магазинов с указанным местоположением пока нет.",
                reply_markup=buyer_menu(user_id)
            )
            return

        buttons = []

        for distance, store in nearby[:10]:
            buttons.append([
                InlineKeyboardButton(
                    f"🏪 {store['name']} • {format_distance(distance)}",
                    callback_data=f"nearstore_{store['id']}_{int(distance)}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            "📍 Ближайшие магазины:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return


# =========================================================
# ROLE SWITCH
# =========================================================

async def switch_role(query, role):
    user_id = query.from_user.id

    supabase.table("users").update({
        "role": role
    }).eq("id", user_id).execute()

    if role == "seller":
        await query.edit_message_text(
            "🏪 Режим продавца включён.",
            reply_markup=seller_menu(user_id)
        )
    else:
        await query.edit_message_text(
            "🛍 Режим покупателя включён.",
            reply_markup=buyer_menu(user_id)
        )


# =========================================================
# ADMIN
# =========================================================

async def admin_panel(query):
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    users_result = supabase.table("users").select("*").execute()
    stores_result = supabase.table("stores").select("*").execute()
    products_result = supabase.table("products").select("*").execute()
    orders_result = supabase.table("orders").select("*").execute()

    users = users_result.data or []
    stores = stores_result.data or []
    products = products_result.data or []
    orders = orders_result.data or []

    text = (
        "🛠 Админ-панель\n\n"
        f"👤 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛍 Заказов: {len(orders)}\n"
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👤 Пользователи",
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
                    "🛍 Заказы",
                    callback_data="admin_orders"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="buyer_menu"
                )
            ]
        ])
    )


async def admin_list(query, table, title):
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    result = supabase.table(table).select("*").limit(50).execute()
    rows = result.data or []

    text = f"{title}\n\n"

    if not rows:
        text += "Пусто."
    else:
        for row in rows:
            text += f"{row}\n\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin"
                )
            ]
        ])
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    # -----------------------------------------------------
    # MENUS
    # -----------------------------------------------------

    if data == "buyer_menu":
        await query.edit_message_text(
            "🛍 Меню покупателя:",
            reply_markup=buyer_menu(query.from_user.id)
        )
        return

    if data == "seller_menu":
        await query.edit_message_text(
            "🏪 Меню продавца:",
            reply_markup=seller_menu(query.from_user.id)
        )
        return

    if data == "profile":
        await show_profile(query)
        return

    if data == "stores":
        await show_stores(query)
        return

    if data == "products":
        await show_products(query)
        return

    if data == "tasks":
        await tasks_menu(query)
        return

    if data == "orders":
        await buyer_orders(query)
        return

    if data == "seller_orders":
        await seller_orders(query)
        return

    # -----------------------------------------------------
    # STORE LOCATION
    # -----------------------------------------------------

    if data == "set_store_location":
        await set_store_location(query, context)
        return

    if data == "confirm_update_store_location":
        user_id = query.from_user.id

        store_id = context.user_data.get("update_store_id")
        latitude = context.user_data.get("update_store_latitude")
        longitude = context.user_data.get("update_store_longitude")
        address = context.user_data.get("update_store_address")

        if not all([
            store_id,
            latitude is not None,
            longitude is not None,
            address
        ]):
            context.user_data.clear()

            await query.edit_message_text(
                "❌ Данные местоположения потерялись.\n"
                "Попробуй указать местоположение ещё раз.",
                reply_markup=seller_menu(user_id)
            )
            return

        store_result = (
            supabase.table("stores")
            .select("*")
            .eq("id", store_id)
            .eq("owner_id", user_id)
            .limit(1)
            .execute()
        )

        if not store_result.data:
            context.user_data.clear()

            await query.edit_message_text(
                "❌ Магазин не найден.",
                reply_markup=seller_menu(user_id)
            )
            return

        try:
            supabase.table("stores").update({
                "latitude": latitude,
                "longitude": longitude,
                "address": address
            }).eq("id", store_id).eq(
                "owner_id", user_id
            ).execute()

        except Exception as e:
            print("Update store location error:", e)

            await query.edit_message_text(
                "❌ Не удалось сохранить местоположение."
            )
            return

        context.user_data.clear()

        await query.edit_message_text(
            "✅ Местоположение магазина обновлено!\n\n"
            f"📍 {address}",
            reply_markup=seller_menu(user_id)
        )
        return

    if data == "retry_update_store_location":
        context.user_data["action"] = "update_store_location"

        await query.edit_message_text(
            "🔄 Хорошо!\n\n"
            "Отправь геолокацию магазина ещё раз."
        )

        await query.message.reply_text(
            "📍 Нажми кнопку ниже:",
            reply_markup=location_keyboard()
        )
        return

    # -----------------------------------------------------
    # OLD STORE CREATION LOCATION
    # -----------------------------------------------------

    if data == "confirm_store_location":
        user_id = query.from_user.id

        name = context.user_data.get("store_name")
        description = context.user_data.get("store_description")
        latitude = context.user_data.get("latitude")
        longitude = context.user_data.get("longitude")
        address = context.user_data.get("address")

        if not name:
            await query.answer(
                "❌ Данные магазина потерялись.",
                show_alert=True
            )
            return

        try:
            supabase.table("stores").insert({
                "owner_id": user_id,
                "name": name,
                "description": description,
                "latitude": latitude,
                "longitude": longitude,
                "address": address
            }).execute()

        except Exception as e:
            print("Confirm store location error:", e)

            await query.edit_message_text(
                "❌ Не удалось создать магазин."
            )
            return

        context.user_data.clear()

        await query.edit_message_text(
            "🎉 Магазин создан!\n\n"
            f"🏪 {name}\n"
            f"📍 {address}",
            reply_markup=seller_menu(user_id)
        )
        return

    if data == "retry_store_location":
        context.user_data["action"] = "create_store_location"

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
    # STORE
    # -----------------------------------------------------

    if data == "my_store":
        await my_store(query)
        return

    if data == "create_store":
        await create_store(query, context)
        return

    if data == "store_stats":
        await store_stats(query)
        return

    # -----------------------------------------------------
    # PRODUCT
    # -----------------------------------------------------

    if data.startswith("product_"):
        product_id = int(data.split("_")[1])
        await show_product(query, product_id)
        return

    if data.startswith("buy_"):
        product_id = int(data.split("_")[1])
        await buy_product(query, context, product_id)
        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if data.startswith("task_"):
        task_name = data[5:]
        await complete_task(query, task_name)
        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------

    if data.startswith("accept_order_"):
        order_id = int(data.split("_")[2])
        await accept_order(query, order_id)
        return

    # -----------------------------------------------------
    # NEARBY STORE
    # -----------------------------------------------------

    if data.startswith("nearstore_"):
        parts = data.split("_")

        if len(parts) >= 2:
            store_id = int(parts[1])
            await show_store(query, store_id)

        return

    # -----------------------------------------------------
    # ROLE
    # -----------------------------------------------------

    if data == "switch_seller":
        await switch_role(query, "seller")
        return

    if data == "switch_buyer":
        await switch_role(query, "buyer")
        return

    # -----------------------------------------------------
    # ADMIN
    # -----------------------------------------------------

    if data == "admin":
        await admin_panel(query)
        return

    if data == "admin_users":
        await admin_list(
            query,
            "users",
            "👤 Пользователи"
        )
        return

    if data == "admin_stores":
        await admin_list(
            query,
            "stores",
            "🏪 Магазины"
        )
        return

    if data == "admin_products":
        await admin_list(
            query,
            "products",
            "📦 Товары"
        )
        return

    if data == "admin_orders":
        await admin_list(
            query,
            "orders",
            "🛍 Заказы"
        )
        return

    await query.answer(
        "❓ Неизвестная команда.",
        show_alert=True
    )


# =========================================================
# HEALTH CHECK
# =========================================================

async def health(request):
    return web.Response(
        text=f"VexMart {VERSION} is running!"
    )


async def start_web_server():
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

    print(f"Web server started on port {port}")


# =========================================================
# MAIN
# =========================================================

def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
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

    application.add_error_handler(error_handler)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(
        start_web_server()
    )

    print(f"🚀 VexMart {VERSION} starting...")

    application.run_polling()


if __name__ == "__main__":
    main()
