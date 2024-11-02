import os
import re
import sqlite3
import tempfile
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.utils import executor
from aiogram.types import ParseMode, InputFile
import yt_dlp as youtube_dl

API_TOKEN = os.getenv('API_TOKEN')
bot = Bot(token=API_TOKEN, parse_mode=ParseMode.HTML)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Ma'lumotlar bazasi bilan ishlash
conn = sqlite3.connect('/app/data/usage_stats.db')
cursor = conn.cursor()
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        platform TEXT,
        count INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
''')
conn.commit()

async def add_user(user: types.User):
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
        VALUES (?, ?, ?, ?)
    ''', (user.id, user.username, user.first_name, user.last_name))
    conn.commit()

async def check_user(user_id: int):
    cursor.execute('''
        SELECT user_id FROM users WHERE user_id = ?
    ''', (user_id,))
    result = cursor.fetchone()
    return result

async def increment_usage(user_id: int, platform: str):
    cursor.execute('''
        SELECT count FROM usage WHERE user_id = ? AND platform = ?
    ''', (user_id, platform))
    result = cursor.fetchone()
    if result:
        cursor.execute('''
            UPDATE usage SET count = count + 1 WHERE user_id = ? AND platform = ?
        ''', (user_id, platform))
    else:
        cursor.execute('''
            INSERT INTO usage (user_id, platform, count) VALUES (?, ?, 1)
        ''', (user_id, platform))
    conn.commit()

# YouTube va Instagram havolalarini aniqlash uchun regex
YOUTUBE_REGEX = re.compile(r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/.+')
INSTAGRAM_REGEX = re.compile(r'(https?://)?(www\.)?instagram\.com/.+')

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    result = await check_user(message.from_user.id)
    if not result:
        await add_user(message.from_user)
    await message.reply("YouTube yoki Instagram video havolasini yuboring.")

@dp.message_handler()
async def handle_message(message: types.Message):
    url = message.text.strip()
    if YOUTUBE_REGEX.match(url):
       await asyncio.create_task(download_and_send_video(message, url, 'YouTube'))
    elif INSTAGRAM_REGEX.match(url):
        await asyncio.create_task(download_and_send_video(message, url, 'Instagram'))
    else:
        await message.reply("Iltimos, to'g'ri YouTube yoki Instagram video havolasini yuboring.")

class UploadProgressInputFile(InputFile):
    def __init__(self, file_path, progress_callback, loop):
        self.file_path = file_path
        self.progress_callback = progress_callback
        self.loop = loop
        self.file_size = os.path.getsize(file_path)
        self.bytes_read = 0
        super().__init__(file_path)

    def _prepare_reader(self, reader):
        original_read = reader.read

        async def read(size=-1):
            data = await original_read(size)
            self.bytes_read += len(data)
            percent = (self.bytes_read / self.file_size) * 100
            # Progressni yangilash chastotasini kamaytirish (har 5%)
            if int(percent) % 10 == 0 and int(percent) != read.last_percent:
                read.last_percent = int(percent)
                if self.progress_callback:
                    asyncio.run_coroutine_threadsafe(
                        self.progress_callback(percent),
                        self.loop
                    )
            return data

        read.last_percent = -1
        reader.read = read
        return reader

async def download_and_send_video(message: types.Message, url: str, platform: str):
    # Yuklanish progressini ko'rsatish uchun xabar yuborish
    progress_message = await message.reply("Video yuklanmoqda, iltimos kuting...")
    loop = asyncio.get_event_loop()

    def download_hook(d):
        if d['status'] == 'downloading':
            percent = d.get('downloaded_bytes', 0) / d.get('total_bytes', 1) * 100
            # Progressni yangilash chastotasini kamaytirish (har 5%)
            if int(percent) % 5 == 0 and int(percent) != download_hook.last_percent:
                download_hook.last_percent = int(percent)
                text = f"Video yuklanmoqda: {percent:.0f}%"
                asyncio.run_coroutine_threadsafe(progress_message.edit_text(text), loop)
        elif d['status'] == 'finished':
            # Yuklab olish tugadi
            text = "Video yuklandi, serverga yuklanmoqda..."
            asyncio.run_coroutine_threadsafe(progress_message.edit_text(text), loop)

    download_hook.last_percent = -1

    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(tempfile.gettempdir(), '%(title)s.%(ext)s'),
        'noplaylist': True,
        'match_filter': '!(age_restricted)',  # Skip age-restricted content
        'prefer_ffmpeg': True,
        'progress_hooks': [download_hook]
          # Skip age-restricted content
    }

    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info_dict)
            video_title = info_dict.get('title', 'video')

        # Video yuklanmoqda xabari
        await progress_message.edit_text("Video serverga yuklanmoqda, iltimos kuting...")

        # Fayl yuborish progressini ko'rsatish uchun funksiyalar
        async def upload_progress(percent):
            if int(percent) % 5 == 0 and int(percent) != upload_progress.last_percent:
                upload_progress.last_percent = int(percent)
                text = f"Video yuborilmoqda: {percent:.0f}%"
                await progress_message.edit_text(text)

        upload_progress.last_percent = -1

        # Custom InputFile yaratish
        input_file = UploadProgressInputFile(video_path, upload_progress, loop)

        # Video yuborish
        await bot.send_video(
            message.chat.id,
            input_file,
            caption=f"{video_title} videosi yuklandi."
        )

        # Progress xabarini o'chirish
        await progress_message.delete()

        os.remove(video_path)

        # Foydalanuvchi foydalanish statistikasini yangilash
        await increment_usage(message.from_user.id, platform)

    except Exception as e:
        await progress_message.edit_text("Video yuklashda xatolik yuz berdi, qayta urinib ko'ring.")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)