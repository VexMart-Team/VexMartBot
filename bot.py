# =========================================================
# VexMart Bot
# VERSION: 0.41.2
# Clean build + Admin UI + Geolocation
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

VERSION = "0.41.2"

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
# DATABASE
# =========================================================

def db_select(
    table,
    columns="*",
    filters_dict=None,
    limit=None,
    order_by=None,
    descending=False
):
    query = supabase.table(table).select(columns)

    if filters_dict:
        for key, value in filters_dict.items():
            query = query.eq(key, value)

    if order_by:
        query = query.order(
            order_by,
            desc=descending
        )

    if limit:
        query = query.limit(limit)

    return query.execute()


def db_insert(table, data):
    return supabase.table(table).insert(data).execute()


def db_update(table, data, filters_dict):
    query = supabase.table(table).update(data)

    for key, value in filters_dict.items():
        query = query.eq(key, value)

    return query.execute()


def get_user(user_id):
    result = db_select(
        "users",
        filters_dict={"id": user_id},
        limit=1
    )

    return result.data[0] if result.data else None


def get_store(store_id):
    if not store_id:
        return None

    result = db_select(
        "stores",
        filters_dict={"id": store_id},
        limit=1
    )

    return result.data[0] if result.data else None


def get_user_store(user_id):
    result = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
        limit=1
    )

    return result.data[0] if result.data else None


def get_product(product_id):
    result = db_select(
        "products",
        filters_dict={"id": product_id},
        limit=1
    )

    return result.data[0] if result.data else None


# =========================================================
# DISTANCE
# =========================================================

def calculate_distance(
    lat1,
    lon1,
    lat2,
    lon2
):
    earth_radius = 6371000

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad)
        * math.cos(lat2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    return earth_radius * (
        2 * math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a)
        )
    )


def format_distance(distance):
    if distance < 1000:
        return f"{int(distance)} м"

    km = distance / 1000

    if km < 10:
        return f"{km:.1f} км"

    return f"{int(km)} км"


# =========================================================
# REVERSE GEOCODING
# =========================================================

async def reverse_geocode(
    latitude,
    longitude
):
    url = (
        "https://nominatim.openstreetmap.org/reverse"
        f"?lat={latitude}"
        f"&lon={longitude}"
        "&format=json"
        "&zoom=18"
        "&addressdetails=1"
    )

    headers = {
        "User-Agent": f"VexMartBot/{VERSION}"
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
                        "Nominatim HTTP status:",
                        response.status
                    )
                    return "Адрес не определён"

                data = await response.json()

                return (
                    data.get("display_name")
                    or "Адрес не определён"
                )

    except Exception as e:
        print("Reverse geocode error:", e)
        return "Адрес не определён"


# =========================================================
# KEYBOARDS
# =========================================================

def buyer_menu(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="stores"
            ),
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="products"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile"
            ),
            InlineKeyboardButton(
                "🎯 Задания",
                callback_data="tasks"
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Мои заказы",
                callback_data="buyer_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Стать продавцом",
                callback_data="switch_seller"
            )
        ]
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def seller_menu(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Мой магазин",
                callback_data="my_store"
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="products"
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
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
                "🛒 Режим покупателя",
                callback_data="switch_buyer"
            )
        ]
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(
                "🛠 Админ-панель",
                callback_data="admin"
            )
        ])

    return InlineKeyboardMarkup(buttons)


def back_keyboard(callback="buyer_menu"):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=callback
            )
        ]
    ])


def location_keyboard():
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton(
                    "📍 Отправить геолокацию",
                    request_location=True
                )
            ],
            [
                KeyboardButton("❌ Отмена")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirm_location_keyboard(
    confirm_callback,
    retry_callback
):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Да, сохранить",
                callback_data=confirm_callback
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Отправить другую",
                callback_data=retry_callback
            )
        ]
    ])


# =========================================================
# ERROR
# =========================================================

async def error_handler(
    update,
    context
):
    print(
        "Exception while handling update:",
        context.error
    )


# =========================================================
# STATISTICS
# =========================================================

async def track_visit(user_id):
    try:
        db_insert(
            "bot_visits",
            {
                "user_id": user_id
            }
        )
    except Exception as e:
        print(
            "Visit stats error:",
            e
        )


async def track_store_view(
    user_id,
    store_id
):
    try:
        db_insert(
            "store_views",
            {
                "user_id": user_id,
                "store_id": store_id
            }
        )
    except Exception as e:
        print(
            "Store view stats error:",
            e
        )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

    if not user:
        return

    user_id = user.id
    existing = get_user(user_id)

    try:
        if existing:
            db_update(
                "users",
                {
                    "username": user.username,
                    "first_name": user.first_name
                },
                {
                    "id": user_id
                }
            )
        else:
            db_insert(
                "users",
                {
                    "id": user_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "role": "buyer",
                    "balance": 100
                }
            )
    except Exception as e:
        print(
            "User save error:",
            e
        )

    await track_visit(user_id)

    await update.message.reply_text(
        f"👋 Привет, {user.first_name}!\n\n"
        "🛒 Добро пожаловать в VexMart!\n"
        "💰 Здесь всё покупается за VexCoin.\n\n"
        f"⚙️ Версия: {VERSION}",
        reply_markup=buyer_menu(user_id)
    )


# =========================================================
# PROFILE
# =========================================================

async def show_profile(query):
    user_id = query.from_user.id
    user = get_user(user_id)

    if not user:
        await query.edit_message_text(
            "❌ Профиль не найден.",
            reply_markup=back_keyboard("buyer_menu")
        )
        return

    role = user.get("role") or "buyer"

    if role == "seller":
        role_text = "🏪 Продавец"
        back = "seller_menu"
    else:
        role_text = "🛒 Покупатель"
        back = "buyer_menu"

    await query.edit_message_text(
        "👤 Профиль\n\n"
        f"🆔 ID: {user_id}\n"
        f"👤 Имя: {user.get('first_name') or 'Не указано'}\n"
        f"💰 Баланс: {user.get('balance', 0)} VexCoin\n"
        f"🎭 Роль: {role_text}",
        reply_markup=back_keyboard(back)
    )


# =========================================================
# STORES
# =========================================================

async def show_stores(query):
    stores = db_select(
        "stores",
        order_by="id",
        descending=True
    ).data or []

    if not stores:
        await query.edit_message_text(
            "🏪 Магазинов пока нет.",
            reply_markup=back_keyboard()
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
            "📍 Найти магазины рядом",
            callback_data="nearby_request"
        )
    ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        "🏪 Магазины VexMart:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_store(
    query,
    store_id
):
    store = get_store(store_id)

    if not store:
        await query.edit_message_text(
            "❌ Магазин не найден.",
            reply_markup=back_keyboard("stores")
        )
        return

    await track_store_view(
        query.from_user.id,
        store_id
    )

    products = db_select(
        "products",
        filters_dict={
            "store_id": store_id
        },
        order_by="id"
    ).data or []

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}\n"
        )

    buttons = []

    if products:
        text += "\n📦 Товары:\n"

        for product in products[:15]:
            text += (
                f"\n• {product['name']}"
                f" — {product['price']} V₽"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"📦 {product['name']}",
                    callback_data=f"product_{product['id']}"
                )
            ])
    else:
        text += "\n\n📦 Товаров пока нет."

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# NEARBY
# =========================================================

async def request_nearby_location(
    query,
    context
):
    context.user_data.clear()

    context.user_data["action"] = "nearby_stores"

    await query.edit_message_text(
        "📍 Поиск магазинов рядом\n\n"
        "Отправь свою геолокацию.\n"
        "Она используется только для расчёта расстояния."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку ниже:",
        reply_markup=location_keyboard()
    )


async def show_nearby_stores(
    update,
    context,
    latitude,
    longitude
):
    stores = db_select(
        "stores",
        limit=100
    ).data or []

    nearby = []

    for store in stores:
        store_lat = store.get("latitude")
        store_lon = store.get("longitude")

        if (
            store_lat is None
            or store_lon is None
        ):
            continue

        distance = calculate_distance(
            latitude,
            longitude,
            store_lat,
            store_lon
        )

        nearby.append(
            (
                distance,
                store
            )
        )

    nearby.sort(
        key=lambda item: item[0]
    )

    context.user_data.clear()

    if not nearby:
        await update.message.reply_text(
            "📍 Пока нет магазинов "
            "с указанным местоположением.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=buyer_menu(
                update.effective_user.id
            )
        )
        return

    buttons = []

    for distance, store in nearby[:10]:
        buttons.append([
            InlineKeyboardButton(
                f"🏪 {store['name']} • "
                f"📏 {format_distance(distance)}",
                callback_data=f"store_{store['id']}"
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
        reply_markup=ReplyKeyboardRemove()
    )

    await update.message.reply_text(
        "Выбери магазин:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# PRODUCTS
# =========================================================

async def show_products(query):
    products = db_select(
        "products",
        order_by="id",
        descending=True
    ).data or []

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
                f"📦 {product['name']} — "
                f"{product['price']} V₽",
                callback_data=f"product_{product['id']}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="buyer_menu"
        )
    ])

    await query.edit_message_text(
        "📦 Товары VexMart:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def show_product(
    query,
    product_id
):
    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден.",
            reply_markup=back_keyboard("products")
        )
        return

    store = get_store(
        product.get("store_id")
    )

    text = (
        f"📦 {product['name']}\n\n"
        f"📝 {product.get('description') or 'Без описания'}\n\n"
        f"💰 Цена: {product.get('price', 0)} V₽\n"
        f"📦 Остаток: {product.get('stock', 0)}\n"
        f"💸 Кэшбэк: {product.get('cashback', 5)}%\n"
    )

    if store:
        text += (
            f"\n🏪 Магазин: {store['name']}"
        )

        if store.get("address"):
            text += (
                f"\n📍 {store['address']}"
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
                callback_data="products"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# =========================================================
# BUY
# =========================================================

async def buy_product(
    query,
    product_id
):
    user_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await query.answer(
            "❌ Товар не найден.",
            show_alert=True
        )
        return

    stock = product.get("stock", 0)

    if stock <= 0:
        await query.answer(
            "❌ Товар закончился.",
            show_alert=True
        )
        return

    user = get_user(user_id)

    if not user:
        await query.answer(
            "❌ Пользователь не найден.",
            show_alert=True
        )
        return

    price = product.get("price", 0)
    balance = user.get("balance", 0)

    if balance < price:
        await query.answer(
            "❌ Недостаточно VexCoin.",
            show_alert=True
        )
        return

    seller_id = None
    store = None

    if product.get("store_id"):
        store = get_store(
            product["store_id"]
        )

        if store:
            seller_id = store.get("owner_id")

    cashback_percent = (
        product.get("cashback", 5) or 0
    )

    cashback = int(
        price * cashback_percent / 100
    )

    new_balance = (
        balance - price + cashback
    )

    try:
        db_update(
            "users",
            {
                "balance": new_balance
            },
            {
                "id": user_id
            }
        )

        db_update(
            "products",
            {
                "stock": stock - 1
            },
            {
                "id": product_id
            }
        )

        db_insert(
            "orders",
            {
                "user_id": user_id,
                "product_id": product_id,
                "quantity": 1,
                "total_price": price,
                "status": "pending",
                "store_id": product.get("store_id"),
                "buyer_id": user_id,
                "seller_id": seller_id,
                "product_name": product["name"],
                "price": price,
                "cashback": cashback
            }
        )

    except Exception as e:
        print(
            "Buy product error:",
            e
        )

        await query.answer(
            "❌ Ошибка при оформлении покупки.",
            show_alert=True
        )
        return

    await query.edit_message_text(
        "✅ Покупка успешно оформлена!\n\n"
        f"📦 {product['name']}\n"
        f"💰 Потрачено: {price} V₽\n"
        f"💸 Кэшбэк: +{cashback} V₽\n\n"
        f"💳 Баланс: {new_balance} VexCoin",
        reply_markup=back_keyboard()
    )


# =========================================================
# TASKS
# =========================================================

async def tasks_menu(query):
    user_id = query.from_user.id

    tasks = db_select(
        "tasks",
        filters_dict={
            "user_id": user_id
        }
    ).data or []

    if not tasks:
        default_tasks = [
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

        for task in default_tasks:
            try:
                db_insert(
                    "tasks",
                    task
                )
            except Exception:
                pass

        tasks = db_select(
            "tasks",
            filters_dict={
                "user_id": user_id
            }
        ).data or []

    text = "🎯 Задания\n\n"
    buttons = []

    for task in tasks:
        if task.get("completed"):
            text += (
                f"✅ {task['task']} — "
                f"+{task['reward']} VexCoin\n"
            )
        else:
            text += (
                f"🔲 {task['task']} — "
                f"+{task['reward']} VexCoin\n"
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


async def complete_task(
    query,
    task_name
):
    user_id = query.from_user.id

    result = db_select(
        "tasks",
        filters_dict={
            "user_id": user_id,
            "task": task_name
        },
        limit=1
    )

    if not result.data:
        await query.answer(
            "❌ Задание не найдено.",
            show_alert=True
        )
        return

    task = result.data[0]

    if task.get("completed"):
        await query.answer(
            "Это задание уже выполнено.",
            show_alert=True
        )
        return

    user = get_user(user_id)

    if not user:
        return

    new_balance = (
        user.get("balance", 0)
        + task["reward"]
    )

    try:
        db_update(
            "users",
            {
                "balance": new_balance
            },
            {
                "id": user_id
            }
        )

        db_update(
            "tasks",
            {
                "completed": True
            },
            {
                "user_id": user_id,
                "task": task_name
            }
        )

    except Exception as e:
        print(
            "Complete task error:",
            e
        )
        return

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

    store = get_user_store(user_id)

    if not store:
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

    text = (
        f"🏪 {store['name']}\n\n"
        f"📝 {store.get('description') or 'Без описания'}\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}\n"
        )
    else:
        text += (
            "\n📍 Местоположение ещё не указано\n"
        )

    buttons = [
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
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def create_store(
    query,
    context
):
    context.user_data.clear()

    context.user_data["action"] = (
        "create_store_name"
    )

    await query.edit_message_text(
        "🏪 Создание магазина\n\n"
        "Напиши название магазина:"
    )


async def store_stats(query):
    user_id = query.from_user.id

    store = get_user_store(user_id)

    if not store:
        await query.answer(
            "❌ Магазин не найден.",
            show_alert=True
        )
        return

    products = db_select(
        "products",
        filters_dict={
            "store_id": store["id"]
        }
    ).data or []

    orders = db_select(
        "orders",
        filters_dict={
            "store_id": store["id"]
        }
    ).data or []

    total_sales = sum(
        order.get("total_price", 0)
        for order in orders
    )

    text = (
        "📊 Статистика магазина\n\n"
        f"🏪 {store['name']}\n\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"💰 Продаж: {total_sales} V₽\n"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store['address']}"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard("my_store")
    )


# =========================================================
# STORE LOCATION
# =========================================================

async def set_store_location(
    query,
    context
):
    user_id = query.from_user.id

    store = get_user_store(user_id)

    if not store:
        await query.answer(
            "❌ У тебя нет магазина.",
            show_alert=True
        )
        return

    context.user_data.clear()

    context.user_data.update({
        "action": "update_store_location",
        "update_store_id": store["id"]
    })

    await query.edit_message_text(
        "📍 Указать местоположение магазина\n\n"
        "Отправь геолокацию магазина."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку ниже:",
        reply_markup=location_keyboard()
    )


async def confirm_update_store_location(
    query,
    context
):
    user_id = query.from_user.id

    store_id = context.user_data.get(
        "update_store_id"
    )

    latitude = context.user_data.get(
        "update_store_latitude"
    )

    longitude = context.user_data.get(
        "update_store_longitude"
    )

    address = context.user_data.get(
        "update_store_address"
    )

    if (
        not store_id
        or latitude is None
        or longitude is None
        or not address
    ):
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Данные местоположения потерялись.",
            reply_markup=seller_menu(user_id)
        )
        return

    store = get_store(store_id)

    if (
        not store
        or store.get("owner_id") != user_id
    ):
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Магазин не найден.",
            reply_markup=seller_menu(user_id)
        )
        return

    try:
        db_update(
            "stores",
            {
                "latitude": latitude,
                "longitude": longitude,
                "address": address
            },
            {
                "id": store_id,
                "owner_id": user_id
            }
        )

    except Exception as e:
        print(
            "Update store location error:",
            e
        )

        await query.edit_message_text(
            "❌ Не удалось сохранить "
            "местоположение."
        )
        return

    context.user_data.clear()

    await query.edit_message_text(
        "✅ Местоположение магазина обновлено!\n\n"
        f"📍 {address}",
        reply_markup=seller_menu(user_id)
    )


async def retry_update_store_location(
    query,
    context
):
    context.user_data["action"] = (
        "update_store_location"
    )

    await query.edit_message_text(
        "🔄 Хорошо!\n\n"
        "Отправь геолокацию магазина ещё раз."
    )

    await query.message.reply_text(
        "📍 Нажми кнопку:",
        reply_markup=location_keyboard()
    )


# =========================================================
# ORDERS
# =========================================================

def order_status_text(status):
    return {
        "pending": "⏳ Ожидает",
        "accepted": "📦 Принят",
        "completed": "✅ Выполнен",
        "done": "✅ Выполнен",
        "cancelled": "❌ Отменён"
    }.get(
        status,
        status or "❔ Неизвестен"
    )


async def buyer_orders(query):
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id
        },
        order_by="id",
        descending=True
    ).data or []

    if not orders:
        await query.edit_message_text(
            "🛒 У тебя пока нет заказов.",
            reply_markup=back_keyboard()
        )
        return

    text = "🛒 Мои заказы\n\n"

    for order in orders:
        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{order_status_text(order.get('status'))}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=back_keyboard()
    )


async def seller_orders(query):
    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "seller_id": user_id
        },
        order_by="id",
        descending=True
    ).data or []

    if not orders:
        await query.edit_message_text(
            "🛒 Заказов пока нет.",
            reply_markup=back_keyboard(
                "seller_menu"
            )
        )
        return

    text = "🛒 Заказы покупателей\n\n"
    buttons = []

    for order in orders:
        status = order.get(
            "status",
            "pending"
        )

        text += (
            f"#{order['id']} — "
            f"{order.get('product_name', 'Товар')}\n"
            f"💰 {order.get('total_price', 0)} V₽\n"
            f"{order_status_text(status)}\n\n"
        )

        if status == "pending":
            buttons.append([
                InlineKeyboardButton(
                    f"✅ Принять #{order['id']}",
                    callback_data=(
                        f"accept_order_{order['id']}"
                    )
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


async def accept_order(
    query,
    order_id
):
    user_id = query.from_user.id

    result = db_select(
        "orders",
        filters_dict={
            "id": order_id,
            "seller_id": user_id
        },
        limit=1
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

    try:
        db_update(
            "orders",
            {
                "status": "accepted"
            },
            {
                "id": order_id
            }
        )
    except Exception as e:
        print(
            "Accept order error:",
            e
        )
        return

    await query.answer(
        "✅ Заказ принят!",
        show_alert=True
    )

    await seller_orders(query)


# =========================================================
# TEXT MESSAGE
# =========================================================

async def text_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text
    user_id = update.effective_user.id

    # CANCEL
    if text == "❌ Отмена":
        context.user_data.clear()

        await update.message.reply_text(
            "❌ Отменено.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Главное меню:",
            reply_markup=buyer_menu(user_id)
        )
        return

    # CREATE STORE NAME
    if context.user_data.get("action") == "create_store_name":
        context.user_data["new_store_name"] = text
        context.user_data["action"] = (
            "create_store_description"
        )

        await update.message.reply_text(
            "📝 Теперь напиши описание магазина."
        )
        return

    # CREATE STORE DESCRIPTION
    if context.user_data.get("action") == "create_store_description":
        name = context.user_data.get(
            "new_store_name"
        )

        try:
            result = db_insert(
                "stores",
                {
                    "owner_id": user_id,
                    "name": name,
                    "description": text
                }
            )

            if not result.data:
                raise Exception(
                    "Store was not created"
                )

        except Exception as e:
            print(
                "Create store error:",
                e
            )

            await update.message.reply_text(
                "❌ Не удалось создать магазин."
            )
            return

        context.user_data.clear()

        await update.message.reply_text(
            "🏪 Магазин создан!\n\n"
            "Теперь можно указать его "
            "местоположение.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "📍 Отправь геолокацию магазина:",
            reply_markup=location_keyboard()
        )

        context.user_data["action"] = (
            "new_store_location"
        )
        return

    # SEARCH STORES
    if context.user_data.get(
        "searching_stores"
    ):
        context.user_data.clear()

        stores = db_select(
            "stores",
            order_by="id",
            descending=True
        ).data or []

        text_lower = text.lower()

        stores = [
            store
            for store in stores
            if text_lower in store["name"].lower()
        ]

        if not stores:
            await update.message.reply_text(
                "🔎 Ничего не найдено.",
                reply_markup=ReplyKeyboardRemove()
            )

            await update.message.reply_text(
                "Меню:",
                reply_markup=buyer_menu(user_id)
            )
            return

        buttons = []

        for store in stores[:10]:
            buttons.append([
                InlineKeyboardButton(
                    f"🏪 {store['name']}",
                    callback_data=f"store_{store['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="stores"
            )
        ])

        await update.message.reply_text(
            "🔎 Результаты поиска:",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Выбери магазин:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # SEARCH PRODUCTS
    if context.user_data.get(
        "searching_products"
    ):
        context.user_data.clear()

        products = db_select(
            "products",
            order_by="id",
            descending=True
        ).data or []

        text_lower = text.lower()

        products = [
            product
            for product in products
            if text_lower in product["name"].lower()
        ]

        if not products:
            await update.message.reply_text(
                "🔎 Товаров не найдено.",
                reply_markup=ReplyKeyboardRemove()
            )

            await update.message.reply_text(
                "Меню:",
                reply_markup=buyer_menu(user_id)
            )
            return

        buttons = []

        for product in products[:10]:
            buttons.append([
                InlineKeyboardButton(
                    f"📦 {product['name']} — "
                    f"{product['price']} V₽",
                    callback_data=f"product_{product['id']}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="products"
            )
        ])

        await update.message.reply_text(
            "🔎 Результаты поиска:",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Выбери товар:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    await update.message.reply_text(
        "🤔 Я не понял это сообщение.\n\n"
        "Используй кнопки меню."
    )


# =========================================================
# LOCATION MESSAGE
# =========================================================

async def location_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    location = update.message.location

    latitude = location.latitude
    longitude = location.longitude

    action = context.user_data.get(
        "action"
    )

    # NEARBY STORES
    if action == "nearby_stores":
        await show_nearby_stores(
            update,
            context,
            latitude,
            longitude
        )
        return

    # NEW STORE LOCATION
    if action == "new_store_location":
        address = await reverse_geocode(
            latitude,
            longitude
        )

        context.user_data[
            "new_store_latitude"
        ] = latitude

        context.user_data[
            "new_store_longitude"
        ] = longitude

        context.user_data[
            "new_store_address"
        ] = address

        await update.message.reply_text(
            "📍 Местоположение получено!\n\n"
            f"🏠 Адрес:\n{address}\n\n"
            "Сохранить его?",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Подтвердить:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Да, сохранить",
                        callback_data="confirm_new_store_location"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Отправить другую",
                        callback_data="retry_new_store_location"
                    )
                ]
            ])
        )
        return

    # EXISTING STORE LOCATION
    if action == "update_store_location":
        address = await reverse_geocode(
            latitude,
            longitude
        )

        context.user_data[
            "update_store_latitude"
        ] = latitude

        context.user_data[
            "update_store_longitude"
        ] = longitude

        context.user_data[
            "update_store_address"
        ] = address

        await update.message.reply_text(
            "📍 Местоположение получено!\n\n"
            f"🏠 Адрес:\n{address}\n\n"
            "Сохранить?",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_text(
            "Подтвердить:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✅ Да, сохранить",
                        callback_data="confirm_update_store_location"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Отправить другую",
                        callback_data="retry_update_store_location"
                    )
                ]
            ])
        )
        return

    await update.message.reply_text(
        "📍 Геолокация получена, "
        "но сейчас она не требуется."
    )


# =========================================================
# ROLE
# =========================================================

async def switch_role(
    query,
    role
):
    user_id = query.from_user.id

    db_update(
        "users",
        {
            "role": role
        },
        {
            "id": user_id
        }
    )

    if role == "seller":
        await query.edit_message_text(
            "🏪 Ты перешёл в режим продавца.",
            reply_markup=seller_menu(user_id)
        )
    else:
        await query.edit_message_text(
            "🛒 Ты перешёл в режим покупателя.",
            reply_markup=buyer_menu(user_id)
        )


# =========================================================
# ADMIN
# =========================================================

ADMIN_PAGE_SIZE = 5


def admin_role_text(role):
    return {
        "seller": "🏪 Продавец",
        "buyer": "🛒 Покупатель"
    }.get(
        role,
        "❔ Не выбрана"
    )


def admin_status_text(status):
    return {
        "pending": "⏳ Ожидает",
        "accepted": "📦 Принят",
        "completed": "✅ Выполнен",
        "done": "✅ Выполнен",
        "cancelled": "❌ Отменён"
    }.get(
        status,
        status or "❔ Неизвестен"
    )


def admin_user_name(user):
    if not user:
        return "Неизвестный пользователь"

    name = (
        user.get("first_name")
        or "Без имени"
    )

    username = user.get(
        "username"
    )

    if username:
        return f"{name} (@{username})"

    return name


def admin_format_date(value):
    if not value:
        return "Не указано"

    return str(value).replace(
        "T",
        " "
    )[:16]


async def admin_panel(query):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    users = db_select(
        "users"
    ).data or []

    stores = db_select(
        "stores"
    ).data or []

    products = db_select(
        "products"
    ).data or []

    orders = db_select(
        "orders"
    ).data or []

    text = (
        "🛠 Админ-панель\n\n"
        f"👤 Пользователей: {len(users)}\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}"
    )

    keyboard = [
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
                "🛒 Заказы",
                callback_data="admin_orders"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="buyer_menu"
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_list(
    query,
    table,
    title,
    page=0
):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "⛔ Нет доступа.",
            show_alert=True
        )
        return

    rows = db_select(
        table,
        order_by="id",
        descending=True,
        limit=1000
    ).data or []

    total = len(rows)

    total_pages = max(
        1,
        math.ceil(
            total / ADMIN_PAGE_SIZE
        )
    )

    page = max(
        0,
        min(
            page,
            total_pages - 1
        )
    )

    start = (
        page * ADMIN_PAGE_SIZE
    )

    end = (
        start + ADMIN_PAGE_SIZE
    )

    page_rows = rows[start:end]

    text = (
        f"{title}\n\n"
        f"📄 Страница {page + 1} "
        f"из {total_pages}\n"
        f"Всего: {total}"
    )

    keyboard = []

    for row in page_rows:

        if table == "users":
            name = (
                row.get("first_name")
                or row.get("username")
                or str(row.get("id"))
            )

            balance = row.get(
                "balance",
                0
            )

            button_text = (
                f"👤 {name} • "
                f"{balance} VexCoin"
            )

            callback = (
                f"admin_user_{row['id']}"
            )

        elif table == "stores":
            name = (
                row.get("name")
                or "Без названия"
            )

            button_text = (
                f"🏪 {name}"
            )

            callback = (
                f"admin_store_{row['id']}"
            )

        elif table == "products":
            name = (
                row.get("name")
                or "Без названия"
            )

            price = row.get(
                "price",
                0
            )

            button_text = (
                f"📦 {name} • "
                f"{price} V₽"
            )

            callback = (
                f"admin_product_{row['id']}"
            )

        elif table == "orders":
            product_name = (
                row.get("product_name")
                or (
                    f"Товар "
                    f"#{row.get('product_id')}"
                )
            )

            button_text = (
                f"🛒 #{row['id']} • "
                f"{product_name}"
            )

            callback = (
                f"admin_order_{row['id']}"
            )

        else:
            continue

        keyboard.append([
            InlineKeyboardButton(
                button_text[:60],
                callback_data=callback
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️",
                callback_data=(
                    f"admin_{table}_"
                    f"{page - 1}"
                )
            )
        )

    if page < total_pages - 1:
        navigation.append(
            InlineKeyboardButton(
                "➡️",
                callback_data=(
                    f"admin_{table}_"
                    f"{page + 1}"
                )
            )
        )

    if navigation:
        keyboard.append(
            navigation
        )

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ В админ-панель",
            callback_data="admin"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_user_details(
    query,
    user_id
):
    if query.from_user.id != ADMIN_ID:
        return

    user = get_user(user_id)

    if not user:
        await query.edit_message_text(
            "❌ Пользователь не найден.",
            reply_markup=back_keyboard(
                "admin_users"
            )
        )
        return

    stores = db_select(
        "stores",
        filters_dict={
            "owner_id": user_id
        }
    ).data or []

    buyer_orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id
        }
    ).data or []

    seller_orders = db_select(
        "orders",
        filters_dict={
            "seller_id": user_id
        }
    ).data or []

    username = user.get(
        "username"
    )

    text = (
        "👤 Пользователь\n\n"
        f"🆔 ID: {user.get('id')}\n"
        f"👤 Имя: "
        f"{user.get('first_name') or 'Не указано'}\n"
        f"🔗 Username: "
        f"@{username if username else 'нет'}\n"
        f"🎭 Роль: "
        f"{admin_role_text(user.get('role'))}\n"
        f"💰 Баланс: "
        f"{user.get('balance', 0)} VexCoin\n\n"
        f"🏪 Магазинов: {len(stores)}\n"
        f"🛒 Покупок: {len(buyer_orders)}\n"
        f"📦 Продаж: {len(seller_orders)}"
    )

    keyboard = []

    for store in stores[:5]:
        keyboard.append([
            InlineKeyboardButton(
                f"🏪 {store.get('name', 'Магазин')}",
                callback_data=(
                    f"admin_store_{store['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К пользователям",
            callback_data="admin_users"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_store_details(
    query,
    store_id
):
    if query.from_user.id != ADMIN_ID:
        return

    store = get_store(store_id)

    if not store:
        await query.edit_message_text(
            "❌ Магазин не найден.",
            reply_markup=back_keyboard(
                "admin_stores"
            )
        )
        return

    owner = get_user(
        store.get("owner_id")
    )

    products = db_select(
        "products",
        filters_dict={
            "store_id": store_id
        }
    ).data or []

    orders = db_select(
        "orders",
        filters_dict={
            "store_id": store_id
        }
    ).data or []

    address = (
        store.get("address")
        or "Не указано"
    )

    text = (
        "🏪 Магазин\n\n"
        f"📛 Название: "
        f"{store.get('name')}\n"
        f"📝 Описание: "
        f"{store.get('description') or 'Нет описания'}\n\n"
        f"👤 Владелец: "
        f"{admin_user_name(owner)}\n"
        f"🆔 ID владельца: "
        f"{store.get('owner_id')}\n\n"
        f"📦 Товаров: {len(products)}\n"
        f"🛒 Заказов: {len(orders)}\n"
        f"📍 Адрес: {address}\n\n"
        f"📅 Создан: "
        f"{admin_format_date(store.get('created_at'))}"
    )

    keyboard = []

    for product in products[:5]:
        keyboard.append([
            InlineKeyboardButton(
                f"📦 {product.get('name', 'Товар')}",
                callback_data=(
                    f"admin_product_{product['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К магазинам",
            callback_data="admin_stores"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_product_details(
    query,
    product_id
):
    if query.from_user.id != ADMIN_ID:
        return

    product = get_product(product_id)

    if not product:
        await query.edit_message_text(
            "❌ Товар не найден.",
            reply_markup=back_keyboard(
                "admin_products"
            )
        )
        return

    store = get_store(
        product.get("store_id")
    )

    text = (
        "📦 Товар\n\n"
        f"📛 Название: "
        f"{product.get('name')}\n"
        f"📝 Описание: "
        f"{product.get('description') or 'Нет описания'}\n\n"
        f"💰 Цена: "
        f"{product.get('price', 0)} V₽\n"
        f"📦 Остаток: "
        f"{product.get('stock', 0)}\n"
        f"💸 Кэшбэк: "
        f"{product.get('cashback', 0)}%\n"
        f"🏪 Магазин: "
        f"{store.get('name') if store else 'Не указан'}\n"
        f"🆔 ID товара: "
        f"{product.get('id')}"
    )

    keyboard = []

    if store:
        keyboard.append([
            InlineKeyboardButton(
                "🏪 Открыть магазин",
                callback_data=(
                    f"admin_store_{store['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К товарам",
            callback_data="admin_products"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_order_details(
    query,
    order_id
):
    if query.from_user.id != ADMIN_ID:
        return

    rows = db_select(
        "orders",
        filters_dict={
            "id": order_id
        },
        limit=1
    ).data or []

    if not rows:
        await query.edit_message_text(
            "❌ Заказ не найден.",
            reply_markup=back_keyboard(
                "admin_orders"
            )
        )
        return

    order = rows[0]

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    seller_id = order.get(
        "seller_id"
    )

    buyer = (
        get_user(buyer_id)
        if buyer_id
        else None
    )

    seller = (
        get_user(seller_id)
        if seller_id
        else None
    )

    store = get_store(
        order.get("store_id")
    )

    product_name = (
        order.get("product_name")
        or (
            f"Товар "
            f"#{order.get('product_id')}"
        )
    )

    text = (
        "🛒 Заказ\n\n"
        f"🆔 Номер: #{order.get('id')}\n"
        f"📦 Товар: {product_name}\n"
        f"🔢 Количество: "
        f"{order.get('quantity', 1)}\n\n"
        f"💰 Цена: "
        f"{order.get('price', 0)} V₽\n"
        f"💵 Сумма: "
        f"{order.get('total_price', 0)} V₽\n"
        f"💸 Кэшбэк: "
        f"{order.get('cashback', 0)} V₽\n\n"
        f"👤 Покупатель: "
        f"{admin_user_name(buyer)}\n"
        f"🏪 Продавец: "
        f"{admin_user_name(seller)}\n"
        f"🏪 Магазин: "
        f"{store.get('name') if store else 'Не указан'}\n\n"
        f"📊 Статус: "
        f"{admin_status_text(order.get('status'))}\n"
        f"📅 Создан: "
        f"{admin_format_date(order.get('created_at'))}"
    )

    if order.get("delivery_address"):
        text += (
            f"\n📍 Доставка: "
            f"{order['delivery_address']}"
        )

    keyboard = []

    if buyer:
        keyboard.append([
            InlineKeyboardButton(
                "👤 Покупатель",
                callback_data=(
                    f"admin_user_{buyer['id']}"
                )
            )
        ])

    if seller:
        keyboard.append([
            InlineKeyboardButton(
                "👤 Продавец",
                callback_data=(
                    f"admin_user_{seller['id']}"
                )
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ К заказам",
            callback_data="admin_orders"
        )
    ])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    data = query.data
    user_id = query.from_user.id

    # -----------------------------------------------------
    # MENUS
    # -----------------------------------------------------

    if data == "buyer_menu":
        await query.edit_message_text(
            "🛒 Меню покупателя",
            reply_markup=buyer_menu(user_id)
        )
        return

    if data == "seller_menu":
        await query.edit_message_text(
            "🏪 Меню продавца",
            reply_markup=seller_menu(user_id)
        )
        return

    # -----------------------------------------------------
    # PROFILE
    # -----------------------------------------------------

    if data == "profile":
        await show_profile(query)
        return

    # -----------------------------------------------------
    # STORES
    # -----------------------------------------------------

    if data == "stores":
        await show_stores(query)
        return

    if data == "nearby_request":
        await request_nearby_location(
            query,
            context
        )
        return

    if data == "store_search":
        context.user_data.clear()
        context.user_data[
            "searching_stores"
        ] = True

        await query.edit_message_text(
            "🔎 Напиши название магазина:"
        )
        return

    if data.startswith("store_"):
        store_id = int(
            data.split("_")[1]
        )

        await show_store(
            query,
            store_id
        )
        return

    # -----------------------------------------------------
    # PRODUCTS
    # -----------------------------------------------------

    if data == "products":
        await show_products(query)
        return

    if data == "product_search":
        context.user_data.clear()
        context.user_data[
            "searching_products"
        ] = True

        await query.edit_message_text(
            "🔎 Напиши название товара:"
        )
        return

    if data.startswith("product_"):
        product_id = int(
            data.split("_")[1]
        )

        await show_product(
            query,
            product_id
        )
        return

    # -----------------------------------------------------
    # BUY
    # -----------------------------------------------------

    if data.startswith("buy_"):
        product_id = int(
            data.split("_")[1]
        )

        await buy_product(
            query,
            product_id
        )
        return

    # -----------------------------------------------------
    # TASKS
    # -----------------------------------------------------

    if data == "tasks":
        await tasks_menu(query)
        return

    if data.startswith("task_"):
        task_name = data[
            len("task_"):
        ]

        await complete_task(
            query,
            task_name
        )
        return

    # -----------------------------------------------------
    # ORDERS
    # -----------------------------------------------------

    if data == "buyer_orders":
        await buyer_orders(query)
        return

    if data == "seller_orders":
        await seller_orders(query)
        return

    if data.startswith("accept_order_"):
        order_id = int(
            data.split("_")[2]
        )

        await accept_order(
            query,
            order_id
        )
        return

    # -----------------------------------------------------
    # SELLER
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

    if data == "store_stats":
        await store_stats(query)
        return

    if data == "set_store_location":
        await set_store_location(
            query,
            context
        )
        return

    # -----------------------------------------------------
    # NEW STORE LOCATION
    # -----------------------------------------------------

    if data == "confirm_new_store_location":
        latitude = context.user_data.get(
            "new_store_latitude"
        )

        longitude = context.user_data.get(
            "new_store_longitude"
        )

        address = context.user_data.get(
            "new_store_address"
        )

        name = context.user_data.get(
            "new_store_name"
        )

        if (
            latitude is None
            or longitude is None
            or not address
            or not name
        ):
            context.user_data.clear()

            await query.edit_message_text(
                "❌ Данные магазина потерялись.",
                reply_markup=seller_menu(user_id)
            )
            return

        try:
            result = db_insert(
                "stores",
                {
                    "owner_id": user_id,
                    "name": name,
                    "description": "",
                    "latitude": latitude,
                    "longitude": longitude,
                    "address": address
                }
            )

            if not result.data:
                raise Exception(
                    "Store insert failed"
                )

        except Exception as e:
            print(
                "New store location error:",
                e
            )

            await query.edit_message_text(
                "❌ Не удалось сохранить магазин."
            )
            return

        context.user_data.clear()

        await query.edit_message_text(
            "✅ Магазин создан!\n\n"
            f"🏪 {name}\n"
            f"📍 {address}",
            reply_markup=seller_menu(user_id)
        )
        return

    if data == "retry_new_store_location":
        context.user_data["action"] = (
            "new_store_location"
        )

        await query.edit_message_text(
            "🔄 Отправь другую геолокацию."
        )

        await query.message.reply_text(
            "📍 Нажми кнопку:",
            reply_markup=location_keyboard()
        )
        return

    # -----------------------------------------------------
    # UPDATE STORE LOCATION
    # -----------------------------------------------------

    if data == "confirm_update_store_location":
        await confirm_update_store_location(
            query,
            context
        )
        return

    if data == "retry_update_store_location":
        await retry_update_store_location(
            query,
            context
        )
        return

    # -----------------------------------------------------
    # ROLES
    # -----------------------------------------------------

    if data == "switch_seller":
        await switch_role(
            query,
            "seller"
        )
        return

    if data == "switch_buyer":
        await switch_role(
            query,
            "buyer"
        )
        return

    # -----------------------------------------------------
    # LOCATION CANCEL
    # -----------------------------------------------------

    if data == "cancel_location":
        context.user_data.clear()

        await query.edit_message_text(
            "❌ Отменено.",
            reply_markup=buyer_menu(user_id)
        )
        return

    # -----------------------------------------------------
    # ADMIN MAIN
    # -----------------------------------------------------

    if data == "admin":
        await admin_panel(query)
        return

    if data == "admin_users":
        await admin_list(
            query,
            "users",
            "👤 Пользователи",
            0
        )
        return

    if data == "admin_stores":
        await admin_list(
            query,
            "stores",
            "🏪 Магазины",
            0
        )
        return

    if data == "admin_products":
        await admin_list(
            query,
            "products",
            "📦 Товары",
            0
        )
        return

    if data == "admin_orders":
        await admin_list(
            query,
            "orders",
            "🛒 Заказы",
            0
        )
        return

    # -----------------------------------------------------
    # ADMIN PAGINATION
    # -----------------------------------------------------

    if data.startswith("admin_users_"):
        page = int(
            data.rsplit("_", 1)[1]
        )

        await admin_list(
            query,
            "users",
            "👤 Пользователи",
            page
        )
        return

    if data.startswith("admin_stores_"):
        page = int(
            data.rsplit("_", 1)[1]
        )

        await admin_list(
            query,
            "stores",
            "🏪 Магазины",
            page
        )
        return

    if data.startswith("admin_products_"):
        page = int(
            data.rsplit("_", 1)[1]
        )

        await admin_list(
            query,
            "products",
            "📦 Товары",
            page
        )
        return

    if data.startswith("admin_orders_"):
        page = int(
            data.rsplit("_", 1)[1]
        )

        await admin_list(
            query,
            "orders",
            "🛒 Заказы",
            page
        )
        return

    # -----------------------------------------------------
    # ADMIN DETAILS
    # -----------------------------------------------------

    if data.startswith("admin_user_"):
        target_id = int(
            data.rsplit("_", 1)[1]
        )

        await admin_user_details(
            query,
            target_id
        )
        return

    if data.startswith("admin_store_"):
        target_id = int(
            data.rsplit("_", 1)[1]
        )

        await admin_store_details(
            query,
            target_id
        )
        return

    if data.startswith("admin_product_"):
        target_id = int(
            data.rsplit("_", 1)[1]
        )

        await admin_product_details(
            query,
            target_id
        )
        return

    if data.startswith("admin_order_"):
        target_id = int(
            data.rsplit("_", 1)[1]
        )

        await admin_order_details(
            query,
            target_id
        )
        return

    # -----------------------------------------------------
    # BACK
    # -----------------------------------------------------

    if data == "back":
        await query.edit_message_text(
            "🛒 Меню покупателя",
            reply_markup=buyer_menu(user_id)
        )
        return

    await query.edit_message_text(
        "❌ Неизвестная команда.",
        reply_markup=buyer_menu(user_id)
    )


# =========================================================
# WEB SERVER
# =========================================================

async def health(request):
    return web.Response(
        text=f"VexMart {VERSION} is running!"
    )


async def start_web_server():
    app = web.Application()

    app.router.add_get(
        "/",
        health
    )

    app.router.add_get(
        "/health",
        health
    )

    runner = web.AppRunner(app)

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
        f"🌐 Web server started on port {port}"
    )


# =========================================================
# MAIN
# =========================================================

async def main():
    print(
        f"🚀 Starting VexMart {VERSION}"
    )

    application = (
        Application.builder()
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

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    await start_web_server()

    print(
        "🤖 Telegram bot starting..."
    )

    await application.initialize()
    await application.start()

    if application.updater:
        await application.updater.start_polling()

    print(
        "✅ VexMart is running!"
    )

    try:
        while True:
            await asyncio.sleep(3600)

    finally:
        if application.updater:
            await application.updater.stop()

        await application.stop()
        await application.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
