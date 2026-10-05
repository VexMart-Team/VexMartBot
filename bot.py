import os
from datetime import datetime, timezone, timedelta

from aiohttp import web
from supabase import create_client, Client

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


# ============================================================
# CONFIG
# ============================================================

VERSION = "0.42.1"

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL is not set")

if not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_KEY is not set")

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


# ============================================================
# DATABASE HELPERS
# ============================================================

def db_select(
    table,
    columns="*",
    filters_dict=None,
    limit=None,
    order_by=None,
    ascending=False,
):
    try:
        query = supabase.table(table).select(columns)

        if filters_dict:
            for key, value in filters_dict.items():
                query = query.eq(key, value)

        if order_by:
            query = query.order(
                order_by,
                desc=not ascending,
            )

        if limit:
            query = query.limit(limit)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB SELECT ERROR] {table}: {e}")
        return []


def db_insert(table, data):
    try:
        result = supabase.table(table).insert(data).execute()
        return result.data or []

    except Exception as e:
        print(f"[DB INSERT ERROR] {table}: {e}")
        return []


def db_update(table, data, filters_dict):
    try:
        query = supabase.table(table).update(data)

        for key, value in filters_dict.items():
            query = query.eq(key, value)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB UPDATE ERROR] {table}: {e}")
        return []


def db_delete(table, filters_dict):
    try:
        query = supabase.table(table).delete()

        for key, value in filters_dict.items():
            query = query.eq(key, value)

        result = query.execute()
        return result.data or []

    except Exception as e:
        print(f"[DB DELETE ERROR] {table}: {e}")
        return []


# ============================================================
# USERS
# ============================================================

def get_user(user_id):
    users = db_select(
        "users",
        filters_dict={"id": user_id},
        limit=1,
    )

    return users[0] if users else None


def ensure_user(tg_user):
    user = get_user(tg_user.id)

    if user:
        updates = {}

        if user.get("username") != tg_user.username:
            updates["username"] = tg_user.username

        if user.get("first_name") != tg_user.first_name:
            updates["first_name"] = tg_user.first_name

        if updates:
            db_update(
                "users",
                updates,
                {"id": tg_user.id},
            )

        return get_user(tg_user.id)

    created = db_insert(
        "users",
        {
            "id": tg_user.id,
            "username": tg_user.username,
            "first_name": tg_user.first_name,
            "role": "buyer",
            "balance": 100,
        },
    )

    return created[0] if created else None


def set_role(user_id, role):
    db_update(
        "users",
        {"role": role},
        {"id": user_id},
    )


# ============================================================
# STORE HELPERS
# ============================================================

def get_store(store_id):
    stores = db_select(
        "stores",
        filters_dict={"id": store_id},
        limit=1,
    )

    return stores[0] if stores else None


def get_owned_store(user_id):
    stores = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
        limit=1,
    )

    return stores[0] if stores else None


def get_store_seller_rows(store_id):
    return db_select(
        "store_sellers",
        filters_dict={"store_id": store_id},
    )


def get_store_member_ids(store_id):
    store = get_store(store_id)

    if not store:
        return []

    ids = []

    owner_id = store.get("owner_id")

    if owner_id:
        ids.append(owner_id)

    seller_rows = get_store_seller_rows(store_id)

    for row in seller_rows:
        user_id = row.get("user_id")

        if user_id and user_id not in ids:
            ids.append(user_id)

    return ids


def is_store_member(user_id, store_id):
    store = get_store(store_id)

    if not store:
        return False

    if store.get("owner_id") == user_id:
        return True

    rows = db_select(
        "store_sellers",
        filters_dict={
            "store_id": store_id,
            "user_id": user_id,
        },
        limit=1,
    )

    return bool(rows)


def get_user_stores(user_id):
    result = []

    owned = db_select(
        "stores",
        filters_dict={"owner_id": user_id},
    )

    for store in owned:
        if store["id"] not in [x["id"] for x in result]:
            result.append(store)

    seller_rows = db_select(
        "store_sellers",
        filters_dict={"user_id": user_id},
    )

    for row in seller_rows:
        store = get_store(row.get("store_id"))

        if store and store["id"] not in [x["id"] for x in result]:
            result.append(store)

    return result


def get_user_store(user_id):
    stores = get_user_stores(user_id)
    return stores[0] if stores else None


# ============================================================
# PRODUCT HELPERS
# ============================================================

def get_product(product_id):
    products = db_select(
        "products",
        filters_dict={"id": product_id},
        limit=1,
    )

    return products[0] if products else None


def is_new_product(product):
    created_at = product.get("created_at")

    if not created_at:
        return False

    try:
        created = datetime.fromisoformat(
            str(created_at).replace("Z", "+00:00")
        )

        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        return (
            datetime.now(timezone.utc) - created
            < timedelta(days=5)
        )

    except Exception:
        return False


def product_title(product):
    title = product.get("name", "Без названия")

    if is_new_product(product):
        title = f"🆕 {title}"

    return title


# ============================================================
# NOTIFICATIONS
# ============================================================

async def notify_user(
    bot,
    user_id,
    text,
    reply_markup=None,
):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
        )

    except Exception as e:
        print(
            f"[NOTIFY ERROR] user={user_id}: {e}"
        )


# ============================================================
# MENUS
# ============================================================

def buyer_menu(user_id=None):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="stores",
            ),
            InlineKeyboardButton(
                "🛒 Корзина",
                callback_data="cart",
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Мои заказы",
                callback_data="buyer_orders",
            ),
            InlineKeyboardButton(
                "📋 Задания",
                callback_data="tasks",
            ),
        ],
        [
            InlineKeyboardButton(
                "📍 Магазины рядом",
                callback_data="nearby_stores",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile",
            ),
        ],
        [
            InlineKeyboardButton(
                "💼 Стать продавцом",
                callback_data="become_seller",
            ),
        ],
    ]

    if user_id == ADMIN_ID:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


def seller_menu(user_id):
    buttons = [
        [
            InlineKeyboardButton(
                "🏪 Мой магазин",
                callback_data="my_store",
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить продавца",
                callback_data="add_seller",
            ),
            InlineKeyboardButton(
                "📨 Запросы в магазин",
                callback_data="seller_requests",
            ),
        ],
        [
            InlineKeyboardButton(
                "📦 Мои товары",
                callback_data="my_products",
            ),
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="seller_orders",
            ),
        ],
        [
            InlineKeyboardButton(
                "👤 Профиль",
                callback_data="profile",
            ),
        ],
        [
            InlineKeyboardButton(
                "🛍 Стать покупателем",
                callback_data="switch_buyer",
            ),
        ],
    ]

    if user_id == ADMIN_ID:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🛠 Админ-панель",
                    callback_data="admin",
                )
            ]
        )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    ensure_user(user)

    db_insert(
        "bot_visits",
        {
            "user_id": user.id,
        },
    )

    await update.message.reply_text(
        f"🛍 Добро пожаловать в VexMart!\n\n"
        f"Версия: {VERSION}",
        reply_markup=buyer_menu(user.id),
    )


# ============================================================
# PROFILE
# ============================================================

async def show_profile(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        await query.answer("Профиль не найден")
        return

    role = user.get("role", "buyer")

    role_text = (
        "🛍 Покупатель"
        if role == "buyer"
        else "💼 Продавец"
    )

    balance = user.get("balance", 0)

    text = (
        "👤 Профиль\n\n"
        f"Имя: {user.get('first_name') or '—'}\n"
        f"Username: @{user.get('username') or '—'}\n"
        f"ID: {user_id}\n"
        f"Роль: {role_text}\n"
        f"💰 Баланс: {balance} ₽"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ]
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ============================================================
# STORES
# ============================================================

async def show_stores(update, context):
    query = update.callback_query

    stores = db_select(
        "stores",
        order_by="created_at",
        ascending=False,
    )

    if not stores:
        await query.edit_message_text(
            "🏪 Магазинов пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )
        return

    buttons = []

    for store in stores:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🏪 {store.get('name', 'Магазин')}",
                    callback_data=f"store_{store['id']}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ]
    )

    await query.edit_message_text(
        "🏪 Магазины:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_store(update, context, store_id):
    query = update.callback_query

    store = get_store(store_id)

    if not store:
        await query.answer("Магазин не найден")
        return

    user_id = query.from_user.id

    products = db_select(
        "products",
        filters_dict={"store_id": store_id},
    )

    member = is_store_member(
        user_id,
        store_id,
    )

    owner_id = store.get("owner_id")

    seller_rows = get_store_seller_rows(store_id)

    seller_count = 1 + len(seller_rows)

    owner = get_user(owner_id)

    seller_names = []

    if owner:
        seller_names.append(
            owner.get("first_name")
            or owner.get("username")
            or "Продавец"
        )

    for row in seller_rows:
        seller = get_user(row.get("user_id"))

        if seller:
            seller_names.append(
                seller.get("first_name")
                or seller.get("username")
                or "Продавец"
            )

    text = (
        f"🏪 {store.get('name', 'Магазин')}\n\n"
        f"{store.get('description') or 'Описание отсутствует.'}\n\n"
        f"👥 Продавцов: {seller_count}\n"
        f"👤 {', '.join(seller_names)}"
    )

    if store.get("address"):
        text += f"\n📍 {store['address']}"

    buttons = []

    if products:
        for product in products:
            stock = product.get("stock", 0)

            badge = (
                "🆕 "
                if is_new_product(product)
                else ""
            )

            buttons.append(
                [
                    InlineKeyboardButton(
                        f"{badge}{product.get('name', 'Товар')} — "
                        f"{product.get('price', 0)} ₽ "
                        f"(ост. {stock})",
                        callback_data=f"product_{product['id']}",
                    )
                ]
            )
    else:
        text += "\n\n📦 Товаров пока нет."

    if not member:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🙋 Запроситься в продавцы",
                    callback_data=f"join_store_{store_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ К магазинам",
                callback_data="stores",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# REQUEST TO JOIN STORE
# ============================================================

async def join_store(update, context, store_id):
    query = update.callback_query

    user_id = query.from_user.id

    store = get_store(store_id)

    if not store:
        await query.answer("Магазин не найден")
        return

    if is_store_member(user_id, store_id):
        await query.answer(
            "Ты уже продавец этого магазина."
        )
        return

    existing = db_select(
        "store_seller_requests",
        filters_dict={
            "store_id": store_id,
            "sender_id": user_id,
            "status": "pending",
        },
    )

    if existing:
        await query.answer(
            "Запрос уже отправлен."
        )
        return

    request = db_insert(
        "store_seller_requests",
        {
            "store_id": store_id,
            "sender_id": user_id,
            "receiver_id": store.get("owner_id"),
            "status": "pending",
        },
    )

    if not request:
        await query.answer(
            "Не удалось отправить запрос."
        )
        return

    request_id = request[0]["id"]

    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Одобрить",
                    callback_data=f"approve_request_{request_id}",
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=f"reject_request_{request_id}",
                ),
            ]
        ]
    )

    await notify_user(
        context.bot,
        store.get("owner_id"),
        (
            "📨 Новый запрос в магазин!\n\n"
            f"🏪 Магазин: {store.get('name')}\n"
            f"👤 Пользователь: "
            f"{query.from_user.first_name or 'Без имени'}\n"
            f"🆔 ID: {user_id}"
        ),
        buttons,
    )

    await query.answer(
        "Запрос отправлен владельцу магазина!"
    )


# ============================================================
# SELLER REQUESTS
# ============================================================

async def show_seller_requests(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if not stores:
        await query.answer(
            "У тебя нет магазина."
        )
        return

    store_ids = [
        store["id"]
        for store in stores
    ]

    requests = db_select(
        "store_seller_requests",
    )

    requests = [
        r
        for r in requests
        if r.get("store_id") in store_ids
        and r.get("status") == "pending"
        and r.get("receiver_id") == user_id
    ]

    if not requests:
        await query.edit_message_text(
            "📨 Новых запросов нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    text = "📨 Запросы в магазин:\n\n"

    buttons = []

    for request in requests:
        sender = get_user(
            request.get("sender_id")
        )

        store = get_store(
            request.get("store_id")
        )

        sender_name = (
            sender.get("first_name")
            if sender
            else "Пользователь"
        )

        store_name = (
            store.get("name")
            if store
            else "Магазин"
        )

        text += (
            f"👤 {sender_name}\n"
            f"🏪 {store_name}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"👤 {sender_name}",
                    callback_data=(
                        f"request_info_{request['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# APPROVE / REJECT REQUEST
# ============================================================

async def approve_request(update, context, request_id):
    query = update.callback_query

    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await query.answer(
            "Запрос не найден."
        )
        return

    request = requests[0]

    if request.get("status") != "pending":
        await query.answer(
            "Этот запрос уже обработан."
        )
        return

    store_id = request.get("store_id")

    if not is_store_member(
        user_id,
        store_id,
    ):
        await query.answer(
            "У тебя нет доступа к этому магазину."
        )
        return

    requester_id = request.get("sender_id")

    if not is_store_member(
        requester_id,
        store_id,
    ):
        db_insert(
            "store_sellers",
            {
                "store_id": store_id,
                "user_id": requester_id,
            },
        )

    db_update(
        "store_seller_requests",
        {"status": "accepted"},
        {"id": request_id},
    )

    set_role(
        requester_id,
        "seller",
    )

    store = get_store(store_id)

    await query.edit_message_text(
        "✅ Запрос одобрен.\n\n"
        "Пользователь теперь продавец этого магазина."
    )

    await notify_user(
        context.bot,
        requester_id,
        (
            "🎉 Твой запрос одобрен!\n\n"
            f"🏪 Теперь ты продавец магазина "
            f"«{store.get('name') if store else 'Магазин'}»."
        ),
    )


async def reject_request(update, context, request_id):
    query = update.callback_query

    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await query.answer(
            "Запрос не найден."
        )
        return

    request = requests[0]

    if request.get("status") != "pending":
        await query.answer(
            "Этот запрос уже обработан."
        )
        return

    store_id = request.get("store_id")

    if not is_store_member(
        user_id,
        store_id,
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    requester_id = request.get("sender_id")

    db_update(
        "store_seller_requests",
        {"status": "rejected"},
        {"id": request_id},
    )

    store = get_store(store_id)

    await query.edit_message_text(
        "❌ Запрос отклонён."
    )

    await notify_user(
        context.bot,
        requester_id,
        (
            "❌ Твой запрос в магазин отклонён.\n\n"
            f"🏪 {store.get('name') if store else 'Магазин'}\n\n"
            "Ты остаёшься покупателем."
        ),
    )


# ============================================================
# ADD SELLER / INVITE FRIEND
# ============================================================

async def add_seller(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    store = get_user_store(user_id)

    if not store:
        await query.answer(
            "У тебя нет магазина."
        )
        return

    context.user_data[
        "awaiting_seller_username"
    ] = True

    await query.edit_message_text(
        "➕ Добавить продавца\n\n"
        "Отправь username друга в Telegram.\n"
        "Например:\n"
        "@username\n\n"
        "Друг должен хотя бы один раз открыть VexMart "
        "и нажать /start."
    )


async def process_seller_username(update, context):
    if not context.user_data.get(
        "awaiting_seller_username"
    ):
        return False

    context.user_data[
        "awaiting_seller_username"
    ] = False

    username = update.message.text.strip()

    if username.startswith("@"):
        username = username[1:]

    if not username:
        await update.message.reply_text(
            "❌ Username пустой."
        )
        return True

    current_user_id = update.effective_user.id

    store = get_user_store(current_user_id)

    if not store:
        await update.message.reply_text(
            "❌ У тебя нет магазина."
        )
        return True

    users = db_select(
        "users",
        filters_dict={
            "username": username,
        },
        limit=1,
    )

    if not users:
        await update.message.reply_text(
            "❌ Пользователь с таким username "
            "не найден в VexMart.\n\n"
            "Попроси друга сначала открыть бота "
            "и нажать /start."
        )
        return True

    friend = users[0]

    friend_id = friend["id"]

    if friend_id == current_user_id:
        await update.message.reply_text(
            "😄 Нельзя добавить самого себя."
        )
        return True

    if is_store_member(
        friend_id,
        store["id"],
    ):
        await update.message.reply_text(
            "ℹ️ Этот пользователь уже продавец "
            "этого магазина."
        )
        return True

    existing = db_select(
        "store_seller_requests",
        filters_dict={
            "store_id": store["id"],
            "sender_id": current_user_id,
            "receiver_id": friend_id,
            "status": "pending",
        },
    )

    if existing:
        await update.message.reply_text(
            "📨 Приглашение уже отправлено."
        )
        return True

    created = db_insert(
        "store_seller_requests",
        {
            "store_id": store["id"],
            "sender_id": current_user_id,
            "receiver_id": friend_id,
            "status": "pending",
        },
    )

    if not created:
        await update.message.reply_text(
            "❌ Не удалось создать приглашение."
        )
        return True

    request_id = created[0]["id"]

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Принять",
                    callback_data=(
                        f"approve_invite_{request_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отклонить",
                    callback_data=(
                        f"reject_invite_{request_id}"
                    ),
                ),
            ]
        ]
    )

    await notify_user(
        context.bot,
        friend_id,
        (
            "📨 Тебя приглашают стать продавцом!\n\n"
            f"🏪 Магазин: {store.get('name')}\n"
            f"👤 Пригласил: "
            f"{update.effective_user.first_name or 'Продавец'}"
        ),
        keyboard,
    )

    await update.message.reply_text(
        "✅ Приглашение отправлено другу!"
    )

    return True


# ============================================================
# INVITE APPROVAL
# ============================================================

async def approve_invite(update, context, request_id):
    query = update.callback_query

    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await query.answer(
            "Приглашение не найдено."
        )
        return

    request = requests[0]

    if request.get("receiver_id") != user_id:
        await query.answer(
            "Это приглашение предназначено не тебе."
        )
        return

    if request.get("status") != "pending":
        await query.answer(
            "Приглашение уже обработано."
        )
        return

    store_id = request["store_id"]

    if not is_store_member(
        user_id,
        store_id,
    ):
        db_insert(
            "store_sellers",
            {
                "store_id": store_id,
                "user_id": user_id,
            },
        )

    db_update(
        "store_seller_requests",
        {"status": "accepted"},
        {"id": request_id},
    )

    set_role(
        user_id,
        "seller",
    )

    store = get_store(store_id)

    await query.edit_message_text(
        "🎉 Ты принял приглашение!\n\n"
        f"Теперь ты продавец магазина "
        f"«{store.get('name') if store else 'Магазин'}»."
    )

    await notify_user(
        context.bot,
        request.get("sender_id"),
        (
            "🎉 Приглашение принято!\n\n"
            f"Пользователь "
            f"{query.from_user.first_name or 'Пользователь'} "
            f"теперь продавец магазина."
        ),
    )


async def reject_invite(update, context, request_id):
    query = update.callback_query

    user_id = query.from_user.id

    requests = db_select(
        "store_seller_requests",
        filters_dict={"id": request_id},
        limit=1,
    )

    if not requests:
        await query.answer(
            "Приглашение не найдено."
        )
        return

    request = requests[0]

    if request.get("receiver_id") != user_id:
        await query.answer(
            "Это приглашение не для тебя."
        )
        return

    if request.get("status") != "pending":
        await query.answer(
            "Приглашение уже обработано."
        )
        return

    db_update(
        "store_seller_requests",
        {"status": "rejected"},
        {"id": request_id},
    )

    await query.edit_message_text(
        "❌ Ты отклонил приглашение.\n\n"
        "Ты остаёшься покупателем."
    )

    await notify_user(
        context.bot,
        request.get("sender_id"),
        "❌ Пользователь отклонил приглашение "
        "стать продавцом.",
    )


# ============================================================
# PRODUCTS
# ============================================================

async def show_product(update, context, product_id):
    query = update.callback_query

    product = get_product(product_id)

    if not product:
        await query.answer(
            "Товар не найден."
        )
        return

    title = product.get("name", "Товар")

    if is_new_product(product):
        title = f"🆕 {title}"

    text = (
        f"📦 {title}\n\n"
        f"{product.get('description') or 'Описание отсутствует.'}\n\n"
        f"💰 Цена: {product.get('price', 0)} ₽\n"
        f"📦 Остаток: {product.get('stock', 0)}"
    )

    if product.get("cashback"):
        text += (
            f"\n💸 Кэшбэк: "
            f"{product.get('cashback')} ₽"
        )

    buttons = []

    if product.get("stock", 0) > 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🛒 Купить",
                    callback_data=f"buy_{product_id}",
                ),
                InlineKeyboardButton(
                    "➕ В корзину",
                    callback_data=f"addcart_{product_id}",
                ),
            ]
        )

    store_id = product.get("store_id")

    if store_id:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🏪 Открыть магазин",
                    callback_data=f"store_{store_id}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=(
                    f"store_{store_id}"
                    if store_id
                    else "stores"
                ),
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ============================================================
# CART
# ============================================================

async def add_to_cart(update, context, product_id):
    query = update.callback_query

    user_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await query.answer(
            "Товар не найден."
        )
        return

    if product.get("stock", 0) <= 0:
        await query.answer(
            "Товар закончился."
        )
        return

    existing = db_select(
        "cart",
        filters_dict={
            "user_id": user_id,
            "product_id": product_id,
        },
        limit=1,
    )

    if existing:
        db_update(
            "cart",
            {
                "quantity":
                    existing[0].get("quantity", 1) + 1
            },
            {
                "id": existing[0]["id"],
            },
        )
    else:
        db_insert(
            "cart",
            {
                "user_id": user_id,
                "product_id": product_id,
                "quantity": 1,
            },
        )

    await query.answer(
        "✅ Добавлено в корзину!"
    )


async def show_cart(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    rows = db_select(
        "cart",
        filters_dict={
            "user_id": user_id,
        },
    )

    if not rows:
        await query.edit_message_text(
            "🛒 Корзина пуста.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )
        return

    total = 0
    text = "🛒 Корзина:\n\n"

    buttons = []

    for row in rows:
        product = get_product(
            row.get("product_id")
        )

        if not product:
            continue

        quantity = row.get("quantity", 1)
        price = product.get("price", 0)
        subtotal = price * quantity

        total += subtotal

        text += (
            f"📦 {product.get('name')}\n"
            f"{price} ₽ × {quantity} = "
            f"{subtotal} ₽\n\n"
        )

    text += f"💰 Итого: {total} ₽"

    buttons.append(
        [
            InlineKeyboardButton(
                "💳 Оформить заказ",
                callback_data="checkout_cart",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "🗑 Очистить корзину",
                callback_data="clear_cart",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def clear_cart(update, context):
    query = update.callback_query

    db_delete(
        "cart",
        {
            "user_id": query.from_user.id,
        },
    )

    await query.answer(
        "🗑 Корзина очищена."
    )

    await show_cart(update, context)


# ============================================================
# BUY PRODUCT
# ============================================================

async def buy_product(update, context, product_id):
    query = update.callback_query

    user_id = query.from_user.id

    product = get_product(product_id)

    if not product:
        await query.answer(
            "Товар не найден."
        )
        return

    stock = product.get("stock", 0)

    if stock <= 0:
        await query.answer(
            "Товар закончился."
        )
        return

    price = product.get("price", 0)

    user = get_user(user_id)

    if not user:
        await query.answer(
            "Профиль не найден."
        )
        return

    balance = user.get("balance", 0)

    if balance < price:
        await query.answer(
            f"Недостаточно денег. "
            f"Баланс: {balance} ₽"
        )
        return

    store_id = product.get("store_id")

    store = (
        get_store(store_id)
        if store_id
        else None
    )

    seller_id = (
        store.get("owner_id")
        if store
        else None
    )

    db_update(
        "users",
        {
            "balance": balance - price,
        },
        {
            "id": user_id,
        },
    )

    db_update(
        "products",
        {
            "stock": stock - 1,
        },
        {
            "id": product_id,
        },
    )

    order_data = {
        "user_id": user_id,
        "product_id": product_id,
        "quantity": 1,
        "total_price": price,
        "status": "pending",
        "store_id": store_id,
        "buyer_id": user_id,
        "seller_id": seller_id,
        "product_name": product.get("name"),
        "price": price,
        "cashback": product.get("cashback", 0),
    }

    db_insert(
        "orders",
        order_data,
    )

    await query.answer(
        "✅ Покупка оформлена!"
    )

    await query.edit_message_text(
        "✅ Заказ оформлен!\n\n"
        f"📦 {product.get('name')}\n"
        f"💰 {price} ₽\n\n"
        "Продавец получил уведомление.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "📦 Мои заказы",
                    callback_data="buyer_orders",
                )
            ]]
        ),
    )

    if store and seller_id:
        await notify_user(
            context.bot,
            seller_id,
            (
                "🛒 Новый заказ!\n\n"
                f"📦 {product.get('name')}\n"
                f"💰 {price} ₽\n"
                f"👤 Покупатель: "
                f"{query.from_user.first_name or 'Покупатель'}"
            ),
        )


# ============================================================
# BUYER ORDERS
# ============================================================

async def buyer_orders(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={
            "buyer_id": user_id,
        },
        order_by="created_at",
        ascending=False,
    )

    if not orders:
        await query.edit_message_text(
            "📦 У тебя пока нет заказов.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ]]
            ),
        )
        return

    text = "📦 Мои заказы:\n\n"

    for order in orders:
        status = order.get(
            "status",
            "pending",
        )

        status_text = {
            "pending": "⏳ Ожидает",
            "accepted": "✅ Принят",
            "completed": "🎉 Выполнен",
            "cancelled": "❌ Отменён",
        }.get(
            status,
            status,
        )

        text += (
            f"#{order.get('id')} — "
            f"{order.get('product_name') or 'Товар'}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"{status_text}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )
            ]]
        ),
    )


# ============================================================
# SELLER ORDERS
# ============================================================

async def seller_orders(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    store_ids = {
        store["id"]
        for store in stores
    }

    if not store_ids:
        await query.edit_message_text(
            "🏪 У тебя нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    all_orders = db_select(
        "orders",
        order_by="created_at",
        ascending=False,
    )

    orders = [
        order
        for order in all_orders
        if order.get("store_id") in store_ids
    ]

    if not orders:
        await query.edit_message_text(
            "🛒 Заказов пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    text = "🛒 Заказы магазина:\n\n"

    buttons = []

    for order in orders:
        status = order.get(
            "status",
            "pending",
        )

        product_name = (
            order.get("product_name")
            or "Товар"
        )

        text += (
            f"#{order.get('id')} — "
            f"{product_name}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"Статус: {status}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"📦 Заказ #{order.get('id')}",
                    callback_data=(
                        f"seller_order_{order['id']}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def seller_order_details(
    update,
    context,
    order_id,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await query.answer(
            "Заказ не найден."
        )
        return

    order = orders[0]

    store_id = order.get("store_id")

    if not store_id or not is_store_member(
        user_id,
        store_id,
    ):
        await query.answer(
            "Нет доступа к этому заказу."
        )
        return

    buyer = get_user(
        order.get("buyer_id")
        or order.get("user_id")
    )

    buyer_name = (
        buyer.get("first_name")
        if buyer
        else "Покупатель"
    )

    status = order.get(
        "status",
        "pending",
    )

    text = (
        f"📦 Заказ #{order.get('id')}\n\n"
        f"Товар: "
        f"{order.get('product_name') or 'Товар'}\n"
        f"Количество: {order.get('quantity', 1)}\n"
        f"Сумма: {order.get('total_price', 0)} ₽\n"
        f"Покупатель: {buyer_name}\n"
        f"Статус: {status}"
    )

    buttons = []

    if status == "pending":
        buttons.append(
            [
                InlineKeyboardButton(
                    "✅ Принять заказ",
                    callback_data=(
                        f"accept_order_{order_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "❌ Отменить",
                    callback_data=(
                        f"cancel_order_{order_id}"
                    ),
                ),
            ]
        )

    if status == "accepted":
        buttons.append(
            [
                InlineKeyboardButton(
                    "🎉 Выполнить",
                    callback_data=(
                        f"complete_order_{order_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "⬅️ Заказы",
                callback_data="seller_orders",
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def accept_order(
    update,
    context,
    order_id,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await query.answer(
            "Заказ не найден."
        )
        return

    order = orders[0]

    store_id = order.get("store_id")

    if not store_id or not is_store_member(
        user_id,
        store_id,
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    if order.get("status") != "pending":
        await query.answer(
            "Заказ уже обработан."
        )
        return

    db_update(
        "orders",
        {
            "status": "accepted",
            "seller_id": user_id,
        },
        {
            "id": order_id,
        },
    )

    await query.answer(
        "✅ Заказ принят!"
    )

    await seller_order_details(
        update,
        context,
        order_id,
    )

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "✅ Продавец принял твой заказ!\n\n"
            f"📦 {order.get('product_name') or 'Товар'}"
        ),
    )


async def cancel_order(
    update,
    context,
    order_id,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await query.answer(
            "Заказ не найден."
        )
        return

    order = orders[0]

    if not is_store_member(
        user_id,
        order.get("store_id"),
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    if order.get("status") != "pending":
        await query.answer(
            "Нельзя отменить этот заказ."
        )
        return

    db_update(
        "orders",
        {
            "status": "cancelled",
            "seller_id": user_id,
        },
        {
            "id": order_id,
        },
    )

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "❌ Твой заказ отменён продавцом.\n\n"
            f"📦 {order.get('product_name') or 'Товар'}"
        ),
    )

    await query.answer(
        "❌ Заказ отменён."
    )

    await seller_orders(
        update,
        context,
    )


async def complete_order(
    update,
    context,
    order_id,
):
    query = update.callback_query

    user_id = query.from_user.id

    orders = db_select(
        "orders",
        filters_dict={"id": order_id},
        limit=1,
    )

    if not orders:
        await query.answer(
            "Заказ не найден."
        )
        return

    order = orders[0]

    if not is_store_member(
        user_id,
        order.get("store_id"),
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    if order.get("status") != "accepted":
        await query.answer(
            "Заказ ещё не принят."
        )
        return

    db_update(
        "orders",
        {
            "status": "completed",
        },
        {
            "id": order_id,
        },
    )

    buyer_id = (
        order.get("buyer_id")
        or order.get("user_id")
    )

    await notify_user(
        context.bot,
        buyer_id,
        (
            "🎉 Твой заказ выполнен!\n\n"
            f"📦 {order.get('product_name') or 'Товар'}"
        ),
    )

    await query.answer(
        "🎉 Заказ выполнен!"
    )

    await seller_orders(
        update,
        context,
    )


# ============================================================
# MY STORE
# ============================================================

async def my_store(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    store = get_user_store(user_id)

    if not store:
        await query.edit_message_text(
            "🏪 У тебя пока нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    store_id = store["id"]

    members = get_store_member_ids(
        store_id
    )

    text = (
        f"🏪 {store.get('name')}\n\n"
        f"{store.get('description') or 'Описание отсутствует.'}\n\n"
        f"👥 Продавцов: {len(members)}"
    )

    if store.get("address"):
        text += (
            f"\n📍 {store.get('address')}"
        )

    buttons = [
        [
            InlineKeyboardButton(
                "👥 Продавцы",
                callback_data=(
                    f"store_sellers_{store_id}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары магазина",
                callback_data=f"store_{store_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить продавца",
                callback_data="add_seller",
            )
        ],
        [
            InlineKeyboardButton(
                "📨 Запросы",
                callback_data="seller_requests",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="seller_menu",
            )
        ],
    ]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def store_sellers(
    update,
    context,
    store_id,
):
    query = update.callback_query

    store = get_store(store_id)

    if not store:
        await query.answer(
            "Магазин не найден."
        )
        return

    if not is_store_member(
        query.from_user.id,
        store_id,
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    members = get_store_member_ids(
        store_id
    )

    text = (
        f"👥 Продавцы магазина "
        f"«{store.get('name')}»:\n\n"
    )

    for member_id in members:
        member = get_user(member_id)

        if not member:
            continue

        name = (
            member.get("first_name")
            or "Без имени"
        )

        username = member.get("username")

        if username:
            name += f" (@{username})"

        if member_id == store.get("owner_id"):
            text += f"👑 {name} — владелец\n"
        else:
            text += f"👤 {name}\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="my_store",
                )
            ]]
        ),
    )


# ============================================================
# OLD STORE STATS CALLBACK
# ============================================================
# Этот обработчик нужен на случай, если у пользователя
# осталась старая кнопка store_stats от предыдущей версии.
# Раньше она попадала в store_* и вызывала int("stats").

async def store_stats(update, context):
    query = update.callback_query

    await query.answer(
        "📊 Статистика магазина пока недоступна."
    )


# ============================================================
# TASKS
# ============================================================

async def show_tasks(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    tasks = db_select(
        "tasks",
        filters_dict={"user_id": user_id},
    )

    if not tasks:
        default_tasks = [
            (
                "Открыть магазин",
                10,
            ),
            (
                "Посмотреть товар",
                5,
            ),
            (
                "Посетить VexMart",
                15,
            ),
        ]

        for task_name, reward in default_tasks:
            db_insert(
                "tasks",
                {
                    "user_id": user_id,
                    "task": task_name,
                    "reward": reward,
                    "completed": False,
                },
            )

        tasks = db_select(
            "tasks",
            filters_dict={
                "user_id": user_id,
            },
        )

    text = "📋 Задания:\n\n"

    for task in tasks:
        mark = (
            "✅"
            if task.get("completed")
            else "⬜"
        )

        text += (
            f"{mark} {task.get('task')}\n"
            f"💰 Награда: "
            f"{task.get('reward', 0)} ₽\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )
            ]]
        ),
    )


# ============================================================
# SWITCH ROLES
# ============================================================

async def become_seller(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if stores:
        set_role(
            user_id,
            "seller",
        )

        await query.edit_message_text(
            "💼 Ты уже являешься продавцом.\n\n"
            "Переключаю тебя в меню продавца.",
            reply_markup=seller_menu(user_id),
        )

        return

    await query.edit_message_text(
        "💼 Чтобы стать продавцом магазина, "
        "открой магазин и отправь запрос "
        "«Запроситься в продавцы».\n\n"
        "После одобрения владельцем магазина "
        "ты автоматически станешь продавцом.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🏪 Магазины",
                        callback_data="stores",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="back_menu",
                    )
                ],
            ]
        ),
    )


async def switch_buyer(update, context):
    query = update.callback_query

    set_role(
        query.from_user.id,
        "buyer",
    )

    await query.edit_message_text(
        "🛍 Ты переключён в режим покупателя.",
        reply_markup=buyer_menu(
            query.from_user.id
        ),
    )


# ============================================================
# SELLER MENU
# ============================================================

async def show_seller_menu(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        ensure_user(query.from_user)
        user = get_user(user_id)

    await query.edit_message_text(
        "💼 Меню продавца",
        reply_markup=seller_menu(user_id),
    )


# ============================================================
# MY PRODUCTS
# ============================================================

async def my_products(update, context):
    query = update.callback_query

    user_id = query.from_user.id

    stores = get_user_stores(user_id)

    if not stores:
        await query.edit_message_text(
            "📦 У тебя нет магазина.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    store_ids = {
        store["id"]
        for store in stores
    }

    products = db_select(
        "products",
    )

    products = [
        p
        for p in products
        if p.get("store_id") in store_ids
    ]

    if not products:
        await query.edit_message_text(
            "📦 Товаров пока нет.",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="seller_menu",
                    )
                ]]
            ),
        )
        return

    text = "📦 Твои товары:\n\n"

    for product in products:
        badge = (
            "🆕 "
            if is_new_product(product)
            else ""
        )

        text += (
            f"{badge}{product.get('name')}\n"
            f"💰 {product.get('price', 0)} ₽\n"
            f"📦 Остаток: {product.get('stock', 0)}\n\n"
        )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="seller_menu",
                )
            ]]
        ),
    )


# ============================================================
# NEARBY STORES
# ============================================================

async def nearby_stores(update, context):
    query = update.callback_query

    await query.edit_message_text(
        "📍 Чтобы найти магазины рядом, "
        "отправь свою геолокацию.",
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back_menu",
                )
            ]]
        ),
    )

    context.user_data[
        "awaiting_location"
    ] = True


async def receive_location(update, context):
    if not context.user_data.get(
        "awaiting_location"
    ):
        return

    context.user_data[
        "awaiting_location"
    ] = False

    location = update.message.location

    latitude = location.latitude
    longitude = location.longitude

    stores = db_select(
        "stores",
    )

    nearby = []

    for store in stores:
        lat = store.get("latitude")
        lon = store.get("longitude")

        if lat is None or lon is None:
            continue

        lat_diff = abs(
            float(lat) - latitude
        )

        lon_diff = abs(
            float(lon) - longitude
        )

        distance = (
            (lat_diff ** 2 + lon_diff ** 2)
            ** 0.5
        )

        nearby.append(
            (
                distance,
                store,
            )
        )

    nearby.sort(
        key=lambda x: x[0]
    )

    nearby = nearby[:10]

    if not nearby:
        await update.message.reply_text(
            "📍 Рядом магазинов не найдено.",
            reply_markup=buyer_menu(
                update.effective_user.id
            ),
        )
        return

    text = "📍 Магазины рядом:\n\n"

    buttons = []

    for _, store in nearby:
        text += (
            f"🏪 {store.get('name')}\n"
            f"📍 {store.get('address') or 'Адрес не указан'}\n\n"
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"🏪 {store.get('name')}",
                    callback_data=f"store_{store['id']}",
                )
            ]
        )

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(
            buttons,
        ),
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def admin_panel(update, context):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    buttons = [
        [
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin_users",
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Магазины",
                callback_data="admin_stores",
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Товары",
                callback_data="admin_products",
            )
        ],
        [
            InlineKeyboardButton(
                "🛒 Заказы",
                callback_data="admin_orders",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back_menu",
            )
        ],
    ]

    await query.edit_message_text(
        "🛠 Админ-панель\n\n"
        f"VexMart {VERSION}",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_users(update, context):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    users = db_select(
        "users",
        order_by="id",
        ascending=False,
        limit=30,
    )

    text = "👥 Пользователи:\n\n"

    for user in users:
        text += (
            f"🆔 {user.get('id')}\n"
            f"👤 {user.get('first_name') or '—'}\n"
            f"@{user.get('username') or '—'}\n"
            f"Роль: {user.get('role')}\n"
            f"💰 {user.get('balance', 0)} ₽\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )


async def admin_stores(update, context):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    stores = db_select(
        "stores",
        order_by="created_at",
        ascending=False,
    )

    text = "🏪 Магазины:\n\n"

    for store in stores:
        members = get_store_member_ids(
            store["id"]
        )

        text += (
            f"#{store['id']} "
            f"{store.get('name')}\n"
            f"👑 Владелец: "
            f"{store.get('owner_id')}\n"
            f"👥 Продавцов: {len(members)}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )


async def admin_products(update, context):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    products = db_select(
        "products",
        order_by="id",
        ascending=False,
        limit=50,
    )

    text = "📦 Товары:\n\n"

    for product in products:
        badge = (
            "🆕 "
            if is_new_product(product)
            else ""
        )

        text += (
            f"#{product.get('id')} "
            f"{badge}{product.get('name')}\n"
            f"💰 {product.get('price', 0)} ₽\n"
            f"📦 {product.get('stock', 0)}\n"
            f"🏪 {product.get('store_id') or '—'}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )


async def admin_orders(update, context):
    query = update.callback_query

    if not is_admin(
        query.from_user.id
    ):
        await query.answer(
            "Нет доступа."
        )
        return

    orders = db_select(
        "orders",
        order_by="created_at",
        ascending=False,
        limit=50,
    )

    text = "🛒 Заказы:\n\n"

    for order in orders:
        text += (
            f"#{order.get('id')}\n"
            f"📦 {order.get('product_name') or 'Товар'}\n"
            f"💰 {order.get('total_price', 0)} ₽\n"
            f"👤 Покупатель: "
            f"{order.get('buyer_id') or order.get('user_id')}\n"
            f"💼 Продавец: "
            f"{order.get('seller_id') or '—'}\n"
            f"Статус: {order.get('status')}\n\n"
        )

    await query.edit_message_text(
        text[:4000],
        reply_markup=InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="admin",
                )
            ]]
        ),
    )


# ============================================================
# BACK TO MENU
# ============================================================

async def back_menu(update, context):
    query = update.callback_query

    user = get_user(
        query.from_user.id
    )

    if not user:
        ensure_user(query.from_user)

        user = get_user(
            query.from_user.id
        )

    if user and user.get("role") == "seller":
        await query.edit_message_text(
            "💼 Меню продавца",
            reply_markup=seller_menu(
                query.from_user.id
            ),
        )
    else:
        await query.edit_message_text(
            "🛍 Главное меню",
            reply_markup=buyer_menu(
                query.from_user.id
            ),
        )


# ============================================================
# CALLBACK ROUTER
# ============================================================

async def callback_router(
    update,
    context,
):
    query = update.callback_query
    data = query.data

    try:

        # ----------------------------------------------------
        # OLD / STALE STORE STATS BUTTON
        # ----------------------------------------------------

        if data == "store_stats":
            await store_stats(
                update,
                context,
            )
            return

        # ----------------------------------------------------
        # REQUESTS
        # ----------------------------------------------------

        if data.startswith("approve_request_"):
            request_id = int(
                data.split("_")[-1]
            )

            await approve_request(
                update,
                context,
                request_id,
            )
            return

        if data.startswith("reject_request_"):
            request_id = int(
                data.split("_")[-1]
            )

            await reject_request(
                update,
                context,
                request_id,
            )
            return

        if data.startswith("approve_invite_"):
            request_id = int(
                data.split("_")[-1]
            )

            await approve_invite(
                update,
                context,
                request_id,
            )
            return

        if data.startswith("reject_invite_"):
            request_id = int(
                data.split("_")[-1]
            )

            await reject_invite(
                update,
                context,
                request_id,
            )
            return

        # ----------------------------------------------------
        # JOIN STORE
        # ----------------------------------------------------

        if data.startswith("join_store_"):
            store_id = int(
                data.split("_")[-1]
            )

            await join_store(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------------------------------
        # SELLER ORDER
        # ----------------------------------------------------

        if data.startswith("seller_order_"):
            order_id = int(
                data.split("_")[-1]
            )

            await seller_order_details(
                update,
                context,
                order_id,
            )
            return

        if data.startswith("accept_order_"):
            order_id = int(
                data.split("_")[-1]
            )

            await accept_order(
                update,
                context,
                order_id,
            )
            return

        if data.startswith("cancel_order_"):
            order_id = int(
                data.split("_")[-1]
            )

            await cancel_order(
                update,
                context,
                order_id,
            )
            return

        if data.startswith("complete_order_"):
            order_id = int(
                data.split("_")[-1]
            )

            await complete_order(
                update,
                context,
                order_id,
            )
            return

        # ----------------------------------------------------
        # STORE SELLERS
        # ----------------------------------------------------

        if data.startswith("store_sellers_"):
            store_id = int(
                data.split("_")[-1]
            )

            await store_sellers(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------------------------------
        # PRODUCTS
        # ----------------------------------------------------

        if data.startswith("addcart_"):
            product_id = int(
                data.split("_")[-1]
            )

            await add_to_cart(
                update,
                context,
                product_id,
            )
            return

        if data.startswith("buy_"):
            product_id = int(
                data.split("_")[-1]
            )

            await buy_product(
                update,
                context,
                product_id,
            )
            return

        if data.startswith("product_"):
            product_id = int(
                data.split("_")[-1]
            )

            await show_product(
                update,
                context,
                product_id,
            )
            return

        # ----------------------------------------------------
        # STORES
        # ----------------------------------------------------

        if data.startswith("store_"):
            suffix = data[len("store_"):]

            # Защита от старых/неправильных callback_data.
            if not suffix.isdigit():
                await query.answer(
                    "⚠️ Старая кнопка больше не действует."
                )
                return

            store_id = int(suffix)

            await show_store(
                update,
                context,
                store_id,
            )
            return

        # ----------------------------------------------------
        # ADMIN
        # ----------------------------------------------------

        if data == "admin":
            await
