import os
import uuid
import tempfile
import asyncio
import subprocess
import logging
import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import pyttsx3

# Configuration
TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'  # Replace with your token
MAX_CHARS = 10000  # Max characters per audio chunk
VOICE_NAME = 'IVONA 2 Maxim OEM'  # Required TTS voice
FFMPEG_PATH = 'C:/ffmpeg/bin/ffmpeg.exe'  # FFmpeg path

# Logger setup
def setup_logger():
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    log_filename = os.path.join(log_dir, f"ivona_bot_{current_date}.log")
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logger()

# User states
user_data = {}
tts_engine = None

def init_tts():
    global tts_engine
    try:
        logger.info("Initializing TTS engine...")
        tts_engine = pyttsx3.init()
        
        voices = tts_engine.getProperty('voices')
        logger.info(f"Available voices: {[v.name for v in voices]}")
        
        for voice in voices:
            if 'IVONA' in voice.name and 'Maxim' in voice.name:
                tts_engine.setProperty('voice', voice.id)
                logger.info(f"Selected voice: {voice.name}")
                break
        else:
            logger.warning("Maxim voice not found! Using default voice")
        
        tts_engine.setProperty('rate', 160)  # Optimized speech rate
        tts_engine.setProperty('volume', 0.9)  # Reduced volume
        logger.info("TTS engine initialized successfully")
    except Exception as e:
        logger.error(f"TTS init error: {str(e)}")
        raise

def convert_wav_to_mp3(wav_path, mp3_path):
    try:
        logger.info(f"Converting {wav_path} to MP3...")
        command = [
            FFMPEG_PATH,
            '-y',
            '-i', wav_path,
            '-codec:a', 'libmp3lame',
            '-b:a', '64k',  # 64kbps bitrate
            '-ar', '22050',  # 22.05 kHz sample rate
            mp3_path
        ]
        
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=300  # 5-minute timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            logger.error(f"FFmpeg error: {error_msg}")
            raise Exception(f"FFmpeg error: {error_msg}")
        
        # Check file size
        file_size = os.path.getsize(mp3_path)
        logger.info(f"Conversion complete: {mp3_path} ({file_size} bytes)")
        return True
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg conversion timeout")
        return False
    except Exception as e:
        logger.error(f"Conversion error: {e}")
        return False

def text_to_speech(text, output_mp3):
    try:
        logger.info(f"Generating audio for {len(text)} characters...")
        wav_path = output_mp3.replace('.mp3', '.wav')
        
        tts_engine.save_to_file(text, wav_path)
        tts_engine.runAndWait()
        
        if not os.path.exists(wav_path):
            logger.error(f"WAV file not created: {wav_path}")
            raise Exception("Generated WAV file doesn't exist")
        
        if os.path.getsize(wav_path) == 0:
            logger.error(f"Empty WAV file: {wav_path}")
            raise Exception("Generated WAV file is empty")
            
        if not convert_wav_to_mp3(wav_path, output_mp3):
            raise Exception("MP3 conversion failed")
        
        if not os.path.exists(output_mp3):
            logger.error(f"MP3 file not created: {output_mp3}")
            raise Exception("Generated MP3 file doesn't exist")
            
        if os.path.getsize(output_mp3) == 0:
            logger.error(f"Empty MP3 file: {output_mp3}")
            raise Exception("Generated MP3 file is empty")
            
        logger.info(f"Audio generated: {output_mp3}")
        return True
    except Exception as e:
        logger.error(f"Audio generation error: {e}")
        for path in [wav_path, output_mp3]:
            if 'path' in locals() and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.info(f"Deleted temp file: {path}")
                except Exception as del_error:
                    logger.error(f"Error deleting {path}: {del_error}")
        return False
    finally:
        if 'wav_path' in locals() and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
                logger.info(f"Deleted temp WAV: {wav_path}")
            except Exception as del_error:
                logger.error(f"Error deleting WAV {wav_path}: {del_error}")

async def send_audio_with_retry(context, chat_id, audio_path, filename, max_retries=3):
    """Send audio with retries"""
    for attempt in range(max_retries):
        try:
            with open(audio_path, 'rb') as audio:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=audio,
                    filename=filename,
                    title=filename,
                    read_timeout=30,
                    write_timeout=30,
                    connect_timeout=30
                )
                return True
        except Exception as e:
            logger.warning(f"Audio send error (attempt {attempt+1}/{max_retries}): {str(e)}")
            await asyncio.sleep(2)  # Retry delay
    
    logger.error(f"Failed to send audio after {max_retries} attempts")
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"/start from {user.full_name} (id: {user.id})")
    
    await update.message.reply_text(
        "Hi! Send me a .txt file or text to convert to Maxim's voice.\n"
        "Use /new for new file, /next for next part."
    )

def reset_user_data(user_id):
    logger.info(f"Resetting state for user {user_id}")
    user_data[user_id] = {
        'parts': [],
        'current_part': 0,
        'filename': '',
        'total_parts': 0,
        'processing': False
    }

def split_text(text):
    logger.info(f"Splitting text ({len(text)} chars)...")
    parts = []
    while text:
        if len(text) <= MAX_CHARS:
            parts.append(text)
            break
        
        part = text[:MAX_CHARS]
        last_newline = part.rfind('\n')
        last_dot = part.rfind('. ')
        
        split_index = last_dot + 1 if last_dot > 0 else last_newline if last_newline > 0 else MAX_CHARS
        
        if split_index > 0:
            part = text[:split_index]
        else:
            part = text[:MAX_CHARS]
            
        parts.append(part)
        text = text[len(part):].lstrip()
    
    logger.info(f"Text split into {len(parts)} parts")
    return parts

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"Text from {user.full_name} (id: {user.id})")
    
    user_id = user.id
    reset_user_data(user_id)
    
    text = update.message.text
    if not text.strip():
        logger.warning("Empty text received")
        await update.message.reply_text("Text cannot be empty!")
        return
    
    processing_msg = await update.message.reply_text(
        "📚 Text received! Processing first part..."
    )
    
    try:
        filename = f"text_{uuid.uuid4().hex[:8]}"
        user_data[user_id]['filename'] = filename
        user_data[user_id]['parts'] = split_text(text)
        user_data[user_id]['total_parts'] = len(user_data[user_id]['parts'])
        user_data[user_id]['processing'] = True
        
        logger.info(f"Processing text: {len(text)} chars, {user_data[user_id]['total_parts']} parts")
        
        await process_next_part(update, context, user_id)
    except Exception as e:
        logger.error(f"Text processing error: {str(e)}")
        await update.message.reply_text(f"Processing error: {str(e)}")
    finally:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id
            )
        except Exception as e:
            logger.warning(f"Failed to delete message: {str(e)}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    document = update.message.document
    
    logger.info(f"Document from {user.full_name} (id: {user.id}):")
    logger.info(f" - Filename: {document.file_name}")
    logger.info(f" - MIME type: {document.mime_type}")
    logger.info(f" - Size: {document.file_size} bytes")
    
    user_id = user.id
    reset_user_data(user_id)
    
    if document.file_name and not document.file_name.lower().endswith('.txt'):
        logger.warning(f"Invalid file format: {document.file_name}")
        await update.message.reply_text("Please send a .txt file")
        return
    
    processing_msg = await update.message.reply_text(
        "📚 File received! Processing first part..."
    )
    
    temp_dir = tempfile.gettempdir()
    temp_file_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.txt")
    
    try:
        file = await context.bot.get_file(document.file_id)
        logger.info(f"Downloading {document.file_name}...")
        await file.download_to_drive(temp_file_path)
        logger.info(f"File saved: {temp_file_path} ({os.path.getsize(temp_file_path)} bytes)")
        
        encodings = ['utf-8', 'cp1251', 'iso-8859-1', 'windows-1252']
        text = None
        
        for encoding in encodings:
            try:
                with open(temp_file_path, 'r', encoding=encoding) as f:
                    text = f.read()
                logger.info(f"Read with encoding: {encoding}")
                break
            except UnicodeDecodeError:
                continue
        
        if text is None:
            logger.error("Failed to detect file encoding")
            await update.message.reply_text("Error: Couldn't read file (encoding issue)")
            return
            
        if not text.strip():
            logger.warning("Empty/unreadable file")
            await update.message.reply_text("File is empty or unreadable!")
            return
            
        filename = os.path.splitext(document.file_name)[0]
        user_data[user_id]['filename'] = filename
        user_data[user_id]['parts'] = split_text(text)
        user_data[user_id]['total_parts'] = len(user_data[user_id]['parts'])
        user_data[user_id]['processing'] = True
        
        logger.info(f"Processing file: {len(text)} chars, {user_data[user_id]['total_parts']} parts")
        
        await process_next_part(update, context, user_id)
    except Exception as e:
        logger.error(f"File processing error: {str(e)}", exc_info=True)
        await update.message.reply_text(f"File processing error: {str(e)}")
    finally:
        if os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
                logger.info(f"Deleted temp file: {temp_file_path}")
            except Exception as e:
                logger.error(f"File deletion error: {str(e)}")
        
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id
            )
        except Exception as e:
            logger.warning(f"Failed to delete message: {str(e)}")

async def process_next_part(update, context, user_id):
    user = update.message.from_user
    logger.info(f"Processing part for {user.full_name} (id: {user_id})")
    
    data = user_data.get(user_id)
    if not data or not data['parts']:
        logger.warning("No data to process")
        await update.message.reply_text("Send text/file first!")
        return
    
    if not data.get('processing', True):
        logger.warning("Request already processing")
        await update.message.reply_text("Request in progress. Please wait...")
        return
    
    if data['current_part'] >= data['total_parts']:
        logger.info("End of file reached")
        await update.message.reply_text("⚠️ Last part processed!")
        return
    
    data['processing'] = True
    
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, 
        action="typing"
    )
    
    text_part = data['parts'][data['current_part']]
    part_number = data['current_part'] + 1
    
    logger.info(f"Processing part {part_number}/{data['total_parts']} ({len(text_part)} chars)")
    
    temp_dir = tempfile.gettempdir()
    audio_filename = f"{data['filename']}_{part_number}.mp3"
    audio_file_path = os.path.join(temp_dir, audio_filename)
    
    processing_msg = None
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, 
            action="record_voice"
        )
        
        processing_msg = await update.message.reply_text(
            f"🔊 Generating part {part_number}/{data['total_parts']}..."
        )
        
        if not text_to_speech(text_part, audio_file_path):
            raise Exception("Audio generation failed")
        
        # Check file size
        file_size = os.path.getsize(audio_file_path)
        logger.info(f"Audio file size: {file_size} bytes")
        
        # Send with retries
        if not await send_audio_with_retry(
            context,
            update.effective_chat.id,
            audio_file_path,
            audio_filename
        ):
            raise Exception("Audio send failed after retries")
            
        logger.info(f"Audio sent: {audio_filename}")
    except Exception as e:
        logger.error(f"Part processing error: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Audio error: {str(e)}")
    finally:
        if os.path.exists(audio_file_path):
            try:
                os.unlink(audio_file_path)
                logger.info(f"Deleted temp audio: {audio_file_path}")
            except Exception as e:
                logger.error(f"Audio deletion error: {str(e)}")
        
        if processing_msg:
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=processing_msg.message_id
                )
            except Exception as e:
                logger.warning(f"Failed to delete message: {str(e)}")
        
        data['processing'] = False
    
    data['current_part'] += 1
    
    if data['current_part'] >= data['total_parts']:
        logger.info("File processing complete")
        await update.message.reply_text(
            "🎧 All parts processed!\n"
            "Send new file/text or use /new"
        )
    else:
        logger.info(f"Processed part {part_number}/{data['total_parts']}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Part {part_number}/{data['total_parts']} sent\n"
                 f"Use /next for next part"
        )

async def next_part(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"/next from {user.full_name} (id: {user.id})")
    
    user_id = user.id
    
    if user_id not in user_data or not user_data[user_id]['parts']:
        logger.warning("No data for /next")
        await update.message.reply_text("Send text/file first!")
        return
    
    processing_msg = await update.message.reply_text(
        "⏳ Fetching next part..."
    )
    
    try:
        await process_next_part(update, context, user_id)
    except Exception as e:
        logger.error(f"/next error: {str(e)}", exc_info=True)
        await update.message.reply_text(f"Error: {str(e)}")
    finally:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id
            )
        except Exception as e:
            logger.warning(f"Failed to delete message: {str(e)}")

async def new_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    logger.info(f"/new from {user.full_name} (id: {user.id})")
    
    user_id = user.id
    reset_user_data(user_id)
    await update.message.reply_text("🆕 Ready for new file! Send text or .txt file")

def main():
    try:
        logger.info("=" * 50)
        logger.info("Starting IVONA Maxim TTS Bot")
        logger.info(f"Python version: {os.sys.version}")
        logger.info(f"Current dir: {os.getcwd()}")
        
        tempfile.tempdir = 'C:\\Temp'
        os.makedirs(tempfile.tempdir, exist_ok=True)
        logger.info(f"Temp directory: {tempfile.tempdir}")
        
        init_tts()
        
        logger.info("Creating Telegram application...")
        application = Application.builder().token(TELEGRAM_TOKEN).read_timeout(30).write_timeout(30).build()
        
        txt_filter = filters.Document.MimeType("text/plain") | filters.Document.FileExtension("txt") | filters.Document.FileExtension("TXT")
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("new", new_file))
        application.add_handler(CommandHandler("next", next_part))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        application.add_handler(MessageHandler(txt_filter, handle_document))
        
        logger.info("Bot running in polling mode...")
        application.run_polling()
    except Exception as e:
        logger.critical(f"Critical error: {str(e)}", exc_info=True)
    finally:
        logger.info("Bot stopped")
        logger.info("=" * 50)

if __name__ == '__main__':
    main()