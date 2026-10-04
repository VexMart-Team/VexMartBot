import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Токен НЕ вставляй сюда публично.
TOKEN = os.getenv("BOT_TOKEN")

# Сюда позже впишем твой Telegram ID
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PRODUCTS = {
    "leaves": "📦 Коробка листочков",
    "stone": "🪨 Премиум-камень",
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📦 Коробка листочков", callback_data="buy_leaves")],
        [InlineKeyboardButton("🪨 Премиум-камень", callback_data="buy_stone")],
    ]

    await update.message.reply_text(
        "🏪 VexMart\n\n"
        "Добро пожаловать в наш дворовый магазин!\n"
        "Выберите товар:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("buy_"):
        product_id = query.data.replace("buy_", "")
        product = PRODUCTS[product_id]

        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Заказать",
                    callback_data=f"confirm_{product_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel"
                )
            ]
        ]

        await query.edit_message_text(
            f"Вы выбрали:\n\n{product}\n\n"
            "Оформить заказ?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data.startswith("confirm_"):
        product_id = query.data.replace("confirm_", "")
        product = PRODUCTS[product_id]

        user = query.from_user

        username = (
            f"@{user.username}"
            if user.username
            else user.first_name
        )

        # Отправляем заказ владельцу магазина
        if ADMIN_ID:
            keyboard = [
                [
                    InlineKeyboardButton(
                        "✅ Выполнено",
                        callback_data="complete"
                    )
                ]
            ]

            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    "🔔 НОВЫЙ ЗАКАЗ!\n\n"
                    f"👤 Покупатель: {username}\n"
                    f"📦 Товар: {product}\n\n"
                    "Принеси товар покупателю и нажми "
                    "«Выполнено»."
                ),
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        await query.edit_message_text(
            f"✅ Заказ принят!\n\n"
            f"📦 {product}\n\n"
            "Продавец получил уведомление."
        )

    elif query.data == "complete":
        await query.edit_message_text(
            "✅ Заказ выполнен!\n\n"
            "Товар выдан покупателю."
        )

    elif query.data == "cancel":
        await query.edit_message_text(
            "❌ Заказ отменён."
        )


def main():
    if not TOKEN:
        raise RuntimeError("Не указан BOT_TOKEN")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))

    print("VexMart запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
