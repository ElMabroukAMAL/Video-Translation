## Project Overview
Developed a comprehensive pipeline to translate YouTube videos, encompassing:

- Downloading video and audio using `yt-dlp`.
- Transcribing audio via OpenAI's Whisper model.
- Translating transcriptions with `deep_translator`.
- Generating translated speech using Google Text-to-Speech (`gTTS`).
- Synchronizing and combining translated audio with the original video using `moviepy`.
