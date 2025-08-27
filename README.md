# IVONA Maxim TTS Telegram Bot

Converts text to speech using IVONA 2 Maxim OEM voice on Windows servers.

## Features
- Process text messages and .txt files
- Split large texts into 10,000-character chunks
- Generate MP3 audio using IVONA Maxim voice
- Streamline audio delivery via Telegram
- Persistent logging with daily rotation

## Prerequisites
1. **Windows Server** (Tested on Windows Server 2019)
2. **IVONA 2 Maxim OEM** TTS engine
3. **FFmpeg** ([Install guide](https://www.ffmpeg.org/download.html))
4. Python 3.10+

## Installation
1. Install IVONA 2 Maxim:
   - Download installer from official source
   - Complete installation (voice will appear in Windows TTS voices)
   - Verify installation via:  
     `Control Panel > Speech Recognition > Text to Speech`

2. Install FFmpeg (version for Windows) and put ffmpeg folder to drive C:\, must be C:\ffmpeg

## RUN
 ```bash
   python app.py
