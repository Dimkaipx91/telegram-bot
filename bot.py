import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackContext,
)

from lessons import LESSONS
from dotenv import load_dotenv

# Google Sheets
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()

# Получаем токен из .env
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Проверка: если токен не загружен — выходим с ошибкой
if not BOT_TOKEN:
    logger.error(
        "❌ Токен не найден! Убедись, что в файле .env есть строка: BOT_TOKEN=твой_токен"
    )
    exit(1)

# Google Sheets setup
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Пользователи бота")
GOOGLE_RESPONSES_SHEET_NAME = os.getenv(
    "GOOGLE_RESPONSES_SHEET_NAME", "Ответы пользователей"
)

# Глобальные переменные для клиентов Google Sheets
gc = None
users_sheet = None
responses_sheet = None


async def init_google_sheets():
    """Инициализация Google Sheets"""
    global gc, users_sheet, responses_sheet
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            GOOGLE_CREDENTIALS_FILE, scope
        )
        gc = gspread.authorize(creds)

        # Инициализация таблицы пользователей
        users_sheet = gc.open(GOOGLE_SHEET_NAME).sheet1
        logger.info("✅ Google Sheets (пользователи) успешно подключены")

        # Создаем заголовки для таблицы пользователей, если таблица пуста
        if not users_sheet.get_all_values():
            users_sheet.append_row(
                [
                    "user_id",
                    "username",
                    "current_lesson",
                    "paused",
                    "last_lesson_sent",
                    "completed",
                    "created_at",
                ]
            )

        # Инициализация таблицы ответов
        try:
            responses_sheet = gc.open(GOOGLE_SHEET_NAME).worksheet(
                GOOGLE_RESPONSES_SHEET_NAME
            )
        except gspread.WorksheetNotFound:
            # Создаем новую вкладку для ответов
            responses_sheet = gc.open(GOOGLE_SHEET_NAME).add_worksheet(
                title=GOOGLE_RESPONSES_SHEET_NAME, rows="1000", cols="20"
            )

        logger.info("✅ Google Sheets (ответы) успешно подключены")

        # Создаем заголовки для таблицы ответов, если таблица пуста
        if not responses_sheet.get_all_values():
            responses_sheet.append_row(
                [
                    "timestamp",
                    "user_id",
                    "username",
                    "lesson_index",
                    "lesson_title",
                    "response_text",
                    "response_type",
                ]
            )

    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
        users_sheet = None
        responses_sheet = None


def get_user_from_sheet(user_id: str) -> Optional[Dict[str, Any]]:
    """Получить пользователя из Google Sheets"""
    if not users_sheet:
        return None

    try:
        all_values = users_sheet.get_all_values()
        if len(all_values) <= 1:  # Только заголовки
            return None

        headers = all_values[0]
        for row in all_values[1:]:
            if row and row[0] == user_id:  # user_id в первом столбце
                user_data = dict(zip(headers, row))
                # Конвертируем типы данных
                user_data["current_lesson"] = int(user_data.get("current_lesson", 0))
                user_data["paused"] = user_data.get("paused", "False").lower() == "true"
                user_data["completed"] = (
                    user_data.get("completed", "False").lower() == "true"
                )
                return user_data
        return None
    except Exception as e:
        logger.error(f"Ошибка получения пользователя из Google Sheets: {e}")
        return None


def save_user_to_sheet(user_data: Dict[str, Any]) -> None:
    """Сохранить пользователя в Google Sheets"""
    if not users_sheet:
        return

    try:
        all_values = users_sheet.get_all_values()
        headers = all_values[0] if all_values else []

        # Если таблица пуста, создаем заголовки
        if not headers:
            headers = [
                "user_id",
                "username",
                "current_lesson",
                "paused",
                "last_lesson_sent",
                "completed",
                "created_at",
            ]
            users_sheet.append_row(headers)

        # Подготавливаем данные для записи
        row_data = [
            str(user_data.get("user_id", "")),
            str(user_data.get("username", "")),
            str(user_data.get("current_lesson", 0)),
            str(user_data.get("paused", False)),
            str(user_data.get("last_lesson_sent", "")),
            str(user_data.get("completed", False)),
            str(user_data.get("created_at", datetime.now().isoformat())),
        ]

        # Ищем существующую строку
        user_id = str(user_data.get("user_id"))
        existing_row_index = None
        for i, row in enumerate(all_values[1:], start=2):
            if row and row[0] == user_id:
                existing_row_index = i
                break

        if existing_row_index:
            # Обновляем существующую строку
            users_sheet.update(
                f"A{existing_row_index}:G{existing_row_index}", [row_data]
            )
        else:
            # Добавляем новую строку
            users_sheet.append_row(row_data)

    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя в Google Sheets: {e}")


def save_response_to_sheet(
    user_id: str,
    username: str,
    lesson_index: int,
    lesson_title: str,
    response_text: str,
    response_type: str,
) -> None:
    """Сохранить ответ пользователя в Google Sheets"""
    if not responses_sheet:
        return

    try:
        timestamp = datetime.now().isoformat()
        responses_sheet.append_row(
            [
                timestamp,
                user_id,
                username,
                str(lesson_index),
                lesson_title,
                response_text,
                response_type,
            ]
        )
        logger.info(
            f"✅ Ответ пользователя {user_id} по уроку {lesson_index} сохранен в таблицу"
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения ответа в Google Sheets: {e}")


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Получена команда /start от пользователя {update.effective_user.id}")
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"

    try:
        # Создаем или обновляем пользователя в Google Sheets
        user_data = {
            "user_id": user_id,
            "username": username,
            "current_lesson": 0,
            "paused": False,
            "last_lesson_sent": None,
            "completed": False,
            "created_at": datetime.now().isoformat(),
        }
        save_user_to_sheet(user_data)
        logger.info(f"Пользователь {user_id} сохранён в Google Sheets")
    except Exception as e:
        logger.error(f"Ошибка при сохранении пользователя {user_id}: {e}")

    keyboard = [[KeyboardButton("Начать курс")]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True
    )

    await update.message.reply_text(
        "👋 Привет! Я — помощник по дизайну интерьера от Лебедевой Ирины.\n\n"
        "📅 Да да той самой. Именно она сделала дизайн ИвМолокозаводу и твоему будущему дома!\n\n"
        "🎁 По окончании курса — получишь чек-лист “7 шагов к идеальному интерьеру” + скидку 20% на консультацию!\n\n"
        "Готов? Нажми → “Начать курс”",
        reply_markup=reply_markup,
    )


# Обработка кнопки "Начать курс"
async def handle_start_course(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if not user_data:
        await update.message.reply_text("Пожалуйста, начни с команды /start")
        return

    if user_data.get("paused", False):
        await update.message.reply_text(
            "Курс приостановлен. Напиши /resume, чтобы продолжить."
        )
        return

    # Обновляем пользователя
    user_data["paused"] = False
    user_data["last_lesson_sent"] = datetime.now().isoformat()
    save_user_to_sheet(user_data)

    await send_lesson(update, context, user_id, 0)


# Асинхронная обёртка для отправки урока через job_queue
async def send_lesson_job(context: CallbackContext):
    """Асинхронная обёртка для отправки урока через job_queue"""
    job = context.job
    user_id = job.data["user_id"]
    lesson_index = job.data["lesson_index"]
    await send_lesson(None, context, user_id, lesson_index)


# Отправка урока
async def send_lesson(update_or_context, context, user_id, lesson_index):
    # Проверка на пустой список уроков
    if not LESSONS:
        logger.warning("❌ Список уроков пуст!")
        return

    if lesson_index >= len(LESSONS):
        return

    lesson = LESSONS[lesson_index]

    # Получаем и обновляем пользователя
    user_data = get_user_from_sheet(user_id)
    if not user_data:
        return

    # Проверка паузы
    if user_data.get("paused", False):
        return

    user_data["current_lesson"] = lesson_index
    save_user_to_sheet(user_data)

    message_text = f"{lesson['title']}\n\n{lesson['text']}"

    # Отправляем сообщение
    try:
        if isinstance(update_or_context, Update):
            await update_or_context.message.reply_text(message_text)
        else:
            await context.bot.send_message(chat_id=int(user_id), text=message_text)
    except Exception as e:
        logger.error(f"Ошибка при отправке урока пользователю {user_id}: {e}")
        return

    # Если финальный урок — помечаем как завершённый
    if lesson.get("is_final"):
        user_data["completed"] = True
        save_user_to_sheet(user_data)


# Обработка ответов пользователя
async def handle_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if not user_data or user_data.get("current_lesson", -1) == -1:
        return

    current_lesson_index = user_data["current_lesson"]

    # Проверяем, что индекс урока корректный
    if current_lesson_index >= len(LESSONS):
        return

    lesson = LESSONS[current_lesson_index]

    # Логируем и сохраняем ответ
    response_text = update.message.text or ""
    response_type = "text"
    if update.message.photo:
        response_text = "[ФОТО]"
        response_type = "photo"
    elif update.message.voice:
        response_text = "[ГОЛОСОВОЕ СООБЩЕНИЕ]"
        response_type = "voice"
    elif update.message.document:
        response_text = "[ДОКУМЕНТ]"
        response_type = "document"

    logger.info(f"Ответ от {user_id}: {response_text}")

    # Сохраняем ответ в таблицу
    save_response_to_sheet(
        user_id=user_id,
        username=username,
        lesson_index=current_lesson_index,
        lesson_title=lesson.get("title", "Без названия"),
        response_text=response_text,
        response_type=response_type,
    )

    # Переходим к следующему уроку
    if not lesson.get("is_final"):
        next_lesson_index = current_lesson_index + 1
        user_data["current_lesson"] = next_lesson_index
        user_data["last_lesson_sent"] = datetime.now().isoformat()
        save_user_to_sheet(user_data)

        # Запускаем таймер на следующий урок — через 5 секунд (для теста)
        try:
            context.job_queue.run_once(
                send_lesson_job,
                when=5,
                data={"user_id": user_id, "lesson_index": next_lesson_index},
            )
            await update.message.reply_text(
                "Отлично! Следующий урок придёт через 5 секунд 🎯"
            )
        except Exception as e:
            logger.error(f"Ошибка при запуске таймера: {e}")
            await update.message.reply_text("Произошла ошибка. Попробуй позже.")
    else:
        await update.message.reply_text("Спасибо за обратную связь! Ты крут(а) 🙌")


# Обработка фото от пользователя
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Фото будет обработано в handle_response
    pass


# Обработка голосовых сообщений
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if not user_data or user_data.get("current_lesson", -1) == -1:
        return

    current_lesson_index = user_data["current_lesson"]

    # Проверяем, что индекс урока корректный
    if current_lesson_index >= len(LESSONS):
        return

    lesson = LESSONS[current_lesson_index]

    # Сохраняем голосовое сообщение как ответ
    logger.info(f"Голосовое сообщение от {user_id}")
    save_response_to_sheet(
        user_id=user_id,
        username=username,
        lesson_index=current_lesson_index,
        lesson_title=lesson.get("title", "Без названия"),
        response_text="[ГОЛОСОВОЕ СООБЩЕНИЕ]",
        response_type="voice",
    )

    await update.message.reply_text(
        "🎙️ Спасибо за голосовое сообщение! Продолжаем обучение."
    )


# Обработка документов
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "unknown"

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if not user_data or user_data.get("current_lesson", -1) == -1:
        return

    current_lesson_index = user_data["current_lesson"]

    # Проверяем, что индекс урока корректный
    if current_lesson_index >= len(LESSONS):
        return

    lesson = LESSONS[current_lesson_index]

    # Сохраняем документ как ответ
    document_name = (
        update.message.document.file_name if update.message.document else "Документ"
    )
    logger.info(f"Документ от {user_id}: {document_name}")
    save_response_to_sheet(
        user_id=user_id,
        username=username,
        lesson_index=current_lesson_index,
        lesson_title=lesson.get("title", "Без названия"),
        response_text=f"[ДОКУМЕНТ: {document_name}]",
        response_type="document",
    )

    await update.message.reply_text("📄 Спасибо за документ! Продолжаем обучение.")


# Команда /pause
async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if user_data:
        user_data["paused"] = True
        save_user_to_sheet(user_data)
        await update.message.reply_text(
            "Курс приостановлен. Напиши /resume, чтобы продолжить."
        )
    else:
        await update.message.reply_text("Сначала начни курс с /start")


# Команда /resume
async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    # Получаем пользователя из Google Sheets
    user_data = get_user_from_sheet(user_id)

    if user_data:
        user_data["paused"] = False
        save_user_to_sheet(user_data)
        current_lesson = user_data["current_lesson"]
        await update.message.reply_text("Курс возобновлён!")

        # Проверяем, не завершен ли курс
        if user_data.get("completed", False):
            await update.message.reply_text("Курс уже завершён! 🎉")
            return

        await send_lesson(update, context, user_id, current_lesson)
    else:
        await update.message.reply_text("Сначала начни курс с /start")


# Основная функция - полностью асинхронная
async def main():
    # Создаём Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Проверка: job_queue должен быть не None
    if application.job_queue is None:
        logger.error("❌ JobQueue не создан! Проверь версию библиотеки.")
        return

    logger.info("✅ JobQueue успешно инициализирован")

    # Инициализация Google Sheets
    await init_google_sheets()

    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pause", pause))
    application.add_handler(CommandHandler("resume", resume))
    application.add_handler(
        MessageHandler(filters.Regex("^Начать курс$"), handle_start_course)
    )
    application.add_handler(MessageHandler(filters.TEXT, handle_response))
    application.add_handler(MessageHandler(filters.PHOTO, handle_response))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Запуск бота в асинхронном контексте
    logger.info("🚀 Бот запущен...")
    async with application:
        await application.start()
        await application.updater.start_polling()
        # Ждем завершения (в реальном приложении можно добавить сигналы завершения)
        while True:
            await asyncio.sleep(3600)  # Спим 1 час


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
