import os
import uuid
import tempfile
import asyncio
import subprocess
import logging
import datetime
import traceback
from concurrent.futures import ThreadPoolExecutor

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

import pyttsx3

TELEGRAM_TOKEN = 'INSERT_TOKEN_HERE'
MAX_CHARS = 7500
FFMPEG_PATH = 'C:/ffmpeg/bin/ffmpeg.exe'

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
if not logger.hasHandlers():
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(ch)

def setup_file_logger():
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(log_dir, f"ivona_bot_{current_date}.log")
    fh = logging.FileHandler(log_filename, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)

setup_file_logger()

user_data = {}
executor = ThreadPoolExecutor(max_workers=3)

def split_text(text):
    parts = []
    while text:
        if len(text) <= MAX_CHARS:
            parts.append(text)
            break
        cut = text[:MAX_CHARS]
        last_dot = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '), cut.rfind('\n'))
        split_at = last_dot + 1 if last_dot >= 0 else MAX_CHARS
        parts.append(text[:split_at].strip())
        text = text[split_at:].lstrip()
    return parts

def convert_wav_to_mp3(wav_path, mp3_path):
    try:
        cmd = [FFMPEG_PATH, '-y', '-i', wav_path, '-codec:a', 'libmp3lame', '-b:a', '64k', '-ar', '22050', mp3_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        if res.returncode != 0:
            logger.error(f"FFmpeg error: {res.stderr.decode(errors='ignore')}")
            return False
        return True
    except Exception:
        logger.error("Convert error:\n" + traceback.format_exc())
        return False

def text_to_speech(text, mp3_path):
    wav_path = mp3_path.replace('.mp3', '.wav')
    try:
        logger.debug("Initializing new TTS engine instance")
        engine = pyttsx3.init()
        voices = engine.getProperty('voices')
        for v in voices:
            if 'IVONA' in v.name and 'Maxim' in v.name:
                engine.setProperty('voice', v.id)
                break
        engine.setProperty('rate', 160)
        engine.setProperty('volume', 0.9)

        logger.debug(f"Generating WAV for text length {len(text)}")
        engine.save_to_file(text, wav_path)
        engine.runAndWait()

        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            logger.error("WAV file missing or empty")
            return False

        if not convert_wav_to_mp3(wav_path, mp3_path):
            return False

        try:
            os.remove(wav_path)
        except Exception as e:
            logger.warning(f"Failed to delete WAV file: {e}")

        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            logger.error("MP3 file missing or empty")
            return False

        logger.debug("MP3 generation successful")
        return True
    except Exception:
        logger.error("TTS error:\n" + traceback.format_exc())
        try:
            if os.path.exists(wav_path):
                os.remove(wav_path)
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except:
            pass
        return False

def reset_user_data(user_id):
    user_data[user_id] = {'parts': [], 'current_part': 0, 'filename': '', 'total_parts': 0, 'processing': False}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Отправь мне текст или .txt файл. Используй /new для нового текста или /next для следующей части.")

async def new_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_user_data(update.message.from_user.id)
    await update.message.reply_text("Жду новый текст или файл. Отправь мне текст или .txt файл.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text
    if not text.strip():
        await update.message.reply_text("Текст пуст.")
        return
    reset_user_data(user_id)
    parts = split_text(text)
    user_data[user_id] = {'parts': parts, 'current_part': 0, 'filename': f"text_{uuid.uuid4().hex[:8]}", 'total_parts': len(parts), 'processing': False}
    await update.message.reply_text(f"Текст разделен на {len(parts)} частей, начинаю обработку...")
    await process_next_part(update, context, user_id)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    document = update.message.document
    if not document.file_name.lower().endswith('.txt'):
        await update.message.reply_text("Пожалуйста, отправьте .txt файл.")
        return
    reset_user_data(user_id)
    temp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.txt")
    file_obj = await context.bot.get_file(document.file_id)
    await file_obj.download_to_drive(temp_path)
    text = None
    for enc in ['utf-8', 'cp1251', 'iso-8859-1', 'windows-1252']:
        try:
            with open(temp_path, 'r', encoding=enc) as f:
                text = f.read()
            break
        except Exception:
            continue
    try:
        os.remove(temp_path)
    except:
        pass
    if not text or not text.strip():
        await update.message.reply_text("Файл пуст или не может быть прочитан.")
        return
    parts = split_text(text)
    filename = os.path.splitext(document.file_name)[0]
    user_data[user_id] = {'parts': parts, 'current_part': 0, 'filename': filename, 'total_parts': len(parts), 'processing': False}
    await update.message.reply_text(f"Файл разделен на {len(parts)} частей, начинаю обработку...")
    await process_next_part(update, context, user_id)

async def process_next_part(update, context, user_id):
    data = user_data.get(user_id)
    if not data or not data['parts']:
        await update.message.reply_text("Сначала отправьте текст или файл.")
        return
    if data['processing']:
        logger.info(f"Пользователь {user_id} пытается начать следующую часть, пока обрабатывается текущая, игнорируем")
        return
    if data['current_part'] >= data['total_parts']:
        await update.message.reply_text("Все части уже обработаны.")
        return

    data['processing'] = True
    part_number = data['current_part'] + 1
    text_part = data['parts'][data['current_part']]
    filename = data['filename']
    audio_path = os.path.join(tempfile.gettempdir(), f"{filename}_{part_number}.mp3")

    try:
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(executor, text_to_speech, text_part, audio_path)
        
        if not success:
            raise RuntimeError("Ошибка преобразования текста в речь")

        with open(audio_path, 'rb') as audio_file:
            await context.bot.send_audio(update.effective_chat.id, audio_file, filename=os.path.basename(audio_path), title=os.path.basename(audio_path))

        data['current_part'] += 1

        if data['current_part'] >= data['total_parts']:
            await update.message.reply_text("Обработка завершена! Используй /new для нового текста.")
        else:
            await update.message.reply_text(f"Обработана часть {part_number}/{data['total_parts']}. Используй /next для следующей части.")

    except Exception:
        logger.error("Ошибка при обработке или отправке части:\n" + traceback.format_exc())
        await update.message.reply_text("Ошибка при обработке или отправке аудио.")
    finally:
        data['processing'] = False
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as exc:
            logger.warning(f"Ошибка при удалении файла {audio_path}: {exc}")

async def next_part(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_data or not user_data[user_id]['parts']:
        await update.message.reply_text("Сначала отправьте текст или файл.")
        return
    await update.message.reply_text("Запрашиваю следующую часть...")
    await process_next_part(update, context, user_id)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Ошибка в обработчике:", exc_info=context.error)

def main():
    try:
        logger.info("=== Запуск бота ===")
        tempfile.tempdir = 'C:\\Temp'
        os.makedirs(tempfile.tempdir, exist_ok=True)

        application = Application.builder().token(TELEGRAM_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("new", new_file))
        application.add_handler(CommandHandler("next", next_part))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(MessageHandler(filters.Document.FileExtension("txt") | filters.Document.MimeType("text/plain"), handle_document))
        application.add_error_handler(error_handler)

        application.run_polling()
    except Exception:
        logger.critical("Критическая ошибка:\n" + traceback.format_exc())
    finally:
        logger.info("=== Бот остановлен ===")

if __name__ == '__main__':
    main()