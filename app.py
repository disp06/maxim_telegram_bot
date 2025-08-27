import os
import uuid
import tempfile
import asyncio
import subprocess
import logging
import datetime
import traceback
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from telegram.error import TimedOut, NetworkError, BadRequest

import pyttsx3

TELEGRAM_TOKEN = 'INSERT_TOKEN_HERE'
MAX_CHARS = 7500
FFMPEG_PATH = 'C:/ffmpeg/bin/ffmpeg.exe'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"ivona_bot_{datetime.datetime.now().strftime('%Y-%m-%d')}.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Глобальные переменные
user_sessions = {}
executor = ThreadPoolExecutor(max_workers=2)
session_lock = threading.Lock()

class UserSession:
    def __init__(self, user_id):
        self.user_id = user_id
        self.parts = []
        self.current_part = 0
        self.filename = ""
        self.processing = False
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.parts = []
            self.current_part = 0
            self.filename = ""
            self.processing = False

    def set_content(self, text, filename):
        with self.lock:
            self.parts = split_text(text)
            self.filename = filename
            self.current_part = 0
            self.processing = False

    def get_next_part(self):
        with self.lock:
            if self.current_part >= len(self.parts):
                return None, None
            part = self.parts[self.current_part]
            part_number = self.current_part + 1
            self.current_part += 1
            return part, part_number

    def is_processing(self):
        with self.lock:
            return self.processing

    def set_processing(self, value):
        with self.lock:
            self.processing = value

    def has_more_parts(self):
        with self.lock:
            return self.current_part < len(self.parts)

    def get_progress(self):
        with self.lock:
            return f"{self.current_part}/{len(self.parts)}"

def get_user_session(user_id):
    with session_lock:
        if user_id not in user_sessions:
            user_sessions[user_id] = UserSession(user_id)
        return user_sessions[user_id]

def split_text(text):
    """Разделяет текст на части по MAX_CHARS символов, стараясь не разрывать предложения"""
    parts = []
    while text:
        if len(text) <= MAX_CHARS:
            parts.append(text)
            break
        
        # Ищем место для разрыва, чтобы не разрывать предложения
        cut = text[:MAX_CHARS]
        last_dot = max(
            cut.rfind('. '), 
            cut.rfind('! '), 
            cut.rfind('? '), 
            cut.rfind('\n'),
            cut.rfind('; ')
        )
        
        if last_dot >= 0:
            split_at = last_dot + 1
        else:
            # Если не нашли подходящее место, разрываем по максимальной длине
            split_at = MAX_CHARS
        
        parts.append(text[:split_at].strip())
        text = text[split_at:].lstrip()
    
    return parts

def convert_wav_to_mp3(wav_path, mp3_path):
    """Конвертирует WAV в MP3 используя FFmpeg"""
    try:
        cmd = [
            FFMPEG_PATH, '-y', '-i', wav_path,
            '-codec:a', 'libmp3lame', '-b:a', '64k', '-ar', '22050',
            '-hide_banner', '-loglevel', 'error', mp3_path
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr.decode('utf-8', errors='ignore')}")
            return False
        
        return os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0
        
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg conversion timed out")
        return False
    except Exception as e:
        logger.error(f"FFmpeg conversion error: {str(e)}")
        return False

def text_to_speech_sync(text, mp3_path):
    """Синхронная функция для преобразования текста в речь"""
    wav_path = mp3_path.replace('.mp3', '.wav')
    
    try:
        # Создаем временную директорию если нужно
        os.makedirs(os.path.dirname(wav_path), exist_ok=True)
        
        # Инициализируем движок TTS
        engine = pyttsx3.init()
        
        # Находим голос Ivona Maxim
        voices = engine.getProperty('voices')
        maxim_voice = None
        for voice in voices:
            if 'IVONA' in voice.name.upper() and 'MAXIM' in voice.name.upper():
                maxim_voice = voice.id
                break
        
        if maxim_voice:
            engine.setProperty('voice', maxim_voice)
        
        # Настраиваем параметры
        engine.setProperty('rate', 160)
        engine.setProperty('volume', 0.9)
        
        # Генерируем аудио
        engine.save_to_file(text, wav_path)
        engine.runAndWait()
        
        # Проверяем что WAV файл создан
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            logger.error("WAV file not created or empty")
            return False
        
        # Конвертируем в MP3
        if not convert_wav_to_mp3(wav_path, mp3_path):
            return False
        
        # Очищаем временные файлы
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
        except:
            pass
        
        # Проверяем результат
        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            logger.error("MP3 file not created or empty")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"TTS error: {str(e)}")
        # Очищаем временные файлы при ошибке
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except:
            pass
        return False

async def text_to_speech_async(text, mp3_path):
    """Асинхронная обертка для TTS"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, text_to_speech_sync, text, mp3_path)

async def send_audio_with_retry(context, chat_id, audio_path, filename, caption, max_retries=5):
    """Отправка аудио с повторными попытками при таймауте"""
    for attempt in range(max_retries):
        try:
            with open(audio_path, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_file,
                    filename=filename,
                    caption=caption,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=120
                )
            return True
        except (TimedOut, NetworkError) as e:
            wait_time = (2 ** attempt) + 1
            logger.warning(f"Attempt {attempt+1}/{max_retries} failed. Retrying in {wait_time} seconds. Error: {e}")
            await asyncio.sleep(wait_time)
        except BadRequest as e:
            logger.error(f"BadRequest error when sending audio: {e}")
            # Попробуем переотправить без filename, если проблема в нем
            if attempt == max_retries - 1:  # Последняя попытка
                try:
                    with open(audio_path, 'rb') as audio_file:
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=audio_file,
                            caption=caption,
                            read_timeout=120,
                            write_timeout=120,
                            connect_timeout=120
                        )
                    return True
                except Exception as e2:
                    logger.error(f"Failed to send audio without filename: {e2}")
                    return False
            else:
                wait_time = (2 ** attempt) + 1
                logger.warning(f"Attempt {attempt+1}/{max_retries} failed. Retrying in {wait_time} seconds. Error: {e}")
                await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send audio: {e}")
            return False
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Я бот для преобразования текста в речь с голосом Ivona Maxim.\n\n"
        "Отправь мне текст сообщением или .txt файлом, и я преобразую его в аудио.\n"
        "Используй /new для начала нового текста.\n"
        "Используй /next для получения следующей части."
    )

async def new_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /new"""
    user_id = update.message.from_user.id
    session = get_user_session(user_id)
    session.reset()
    
    await update.message.reply_text(
        "Готов принять новый текст. Отправь мне текст сообщением или .txt файл."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    
    if not text:
        await update.message.reply_text("Текст не может быть пустым.")
        return
    
    session = get_user_session(user_id)
    session.set_content(text, f"text_{uuid.uuid4().hex[:8]}")
    
    total_parts = len(session.parts)
    await update.message.reply_text(
        f"Текст разделен на {total_parts} частей. Начинаю обработку первой части..."
    )
    
    await process_next_part(update, context, user_id)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов"""
    user_id = update.message.from_user.id
    document = update.message.document
    
    if not document.file_name.lower().endswith('.txt'):
        await update.message.reply_text("Пожалуйста, отправьте файл с расширением .txt")
        return
    
    # Скачиваем файл
    temp_file = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.txt")
    file_obj = await context.bot.get_file(document.file_id)
    await file_obj.download_to_drive(temp_file)
    
    # Пытаемся прочитать файл с разными кодировками
    text = None
    encodings = ['utf-8', 'cp1251', 'windows-1251', 'iso-8859-1', 'windows-1252']
    
    for encoding in encodings:
        try:
            with open(temp_file, 'r', encoding=encoding) as f:
                text = f.read().strip()
            if text:
                break
        except:
            continue
    
    # Удаляем временный файл
    try:
        os.remove(temp_file)
    except:
        pass
    
    if not text:
        await update.message.reply_text("Не удалось прочитать файл. Проверьте кодировку и содержимое.")
        return
    
    session = get_user_session(user_id)
    filename = os.path.splitext(document.file_name)[0]
    session.set_content(text, filename)
    
    total_parts = len(session.parts)
    await update.message.reply_text(
        f"Файл разделен на {total_parts} частей. Начинаю обработку первой части..."
    )
    
    await process_next_part(update, context, user_id)

async def process_next_part(update, context, user_id):
    """Обрабатывает следующую часть текста"""
    session = get_user_session(user_id)
    
    if not session.parts:
        await update.message.reply_text("Сначала отправьте текст или файл.")
        return
    
    if session.is_processing():
        await update.message.reply_text("Подождите, обрабатывается текущая часть...")
        return
    
    if not session.has_more_parts():
        await update.message.reply_text("Все части уже обработаны. Используйте /new для нового текста.")
        return
    
    session.set_processing(True)
    audio_path = None
    
    try:
        # Получаем следующую часть
        text_part, part_number = session.get_next_part()
        if not text_part:
            return
        
        # Создаем временный файл для аудио
        audio_filename = f"{session.filename}_{part_number}.mp3"
        audio_path = os.path.join(tempfile.gettempdir(), audio_filename)
        
        # Преобразуем текст в речь
        logger.info(f"Обрабатываю часть {part_number} для пользователя {user_id}")
        
        success = await text_to_speech_async(text_part, audio_path)
        
        if not success:
            raise Exception("Ошибка преобразования текста в речь")
        
        # Проверяем размер файла
        file_size = os.path.getsize(audio_path)
        if file_size > 50 * 1024 * 1024:  # 50MB limit for Telegram
            logger.warning(f"Audio file too large ({file_size} bytes), splitting text further")
            # Если файл слишком большой, разбиваем текст на более мелкие части
            smaller_parts = split_text(text_part)
            session.parts[part_number-1:part_number] = smaller_parts
            session.current_part = part_number - 1
            await update.message.reply_text("Файл слишком большой. Разбиваю на более мелкие части...")
            session.set_processing(False)
            await process_next_part(update, context, user_id)
            return
        
        # Отправляем аудио с повторными попытками
        caption = f"Часть {part_number} из {len(session.parts)}"
        send_success = await send_audio_with_retry(
            context, update.effective_chat.id, audio_path, audio_filename, caption
        )
        
        if not send_success:
            raise Exception("Не удалось отправить аудиофайл")
        
        # Сообщаем о статусе
        if session.has_more_parts():
            await update.message.reply_text(
                f"Часть {part_number} обработана. Используйте /next для следующей части."
            )
        else:
            await update.message.reply_text(
                f"Часть {part_number} обработана. Все части завершены! Используйте /new для нового текста."
            )
            
    except Exception as e:
        logger.error(f"Ошибка при обработке части: {str(e)}")
        logger.error(traceback.format_exc())
        await update.message.reply_text(
            "Произошла ошибка при обработке. Попробуйте снова или отправьте текст заново."
        )
    finally:
        session.set_processing(False)
        # Очищаем временные файлы
        try:
            if audio_path and os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            logger.warning(f"Не удалось удалить временный файл: {e}")

async def next_part(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /next"""
    user_id = update.message.from_user.id
    session = get_user_session(user_id)
    
    if not session.parts:
        await update.message.reply_text("Сначала отправьте текст или файл.")
        return
    
    if not session.has_more_parts():
        await update.message.reply_text("Все части уже обработаны. Используйте /new для нового текста.")
        return
    
    await update.message.reply_text("Обрабатываю следующую часть...")
    await process_next_part(update, context, user_id)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)
    
    if update and hasattr(update, 'message'):
        try:
            await update.message.reply_text("Произошла внутренняя ошибка. Попробуйте снова.")
        except:
            pass

def main():
    """Основная функция"""
    try:
        logger.info("=== Запуск бота ===")
        
        # Создаем временную директорию
        temp_dir = 'C:\\Temp'
        os.makedirs(temp_dir, exist_ok=True)
        tempfile.tempdir = temp_dir
        
        # Создаем приложение с увеличенными таймаутами
        application = Application.builder().token(TELEGRAM_TOKEN).read_timeout(120).write_timeout(120).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("new", new_file))
        application.add_handler(CommandHandler("next", next_part))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        application.add_error_handler(error_handler)
        
        logger.info("Бот запущен и готов к работе")
        application.run_polling()
        
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске: {str(e)}")
        logger.critical(traceback.format_exc())
    finally:
        logger.info("=== Бот остановлен ===")
        executor.shutdown()

if __name__ == '__main__':
    main()