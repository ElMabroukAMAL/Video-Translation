import os
import json
import uuid
from yt_dlp import YoutubeDL
from deep_translator import GoogleTranslator
from gtts import gTTS
from pydub import AudioSegment
import whisper
from flask import Flask, request, jsonify
import moviepy.editor as mp
from pytube import YouTube
import subprocess
from moviepy.editor import VideoFileClip, AudioFileClip
import re

app = Flask(__name__)

CACHE_FILE = 'translation_cache.json'
#model = whisper.load_model("large")

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print("Cache file is not a valid JSON. Initializing an empty cache.")
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=4)
        print("Cache mis à jour avec succès.")
    except Exception as e:
        print(f"Erreur lors de la sauvegarde du cache : {e}")


def download_and_convert_to_mp3(video_url, output_path):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'downloaded_audio.%(ext)s',
        'noplaylist': True,
        'quiet': True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    downloaded_file = None
    for file in os.listdir('.'):
        if file.startswith('downloaded_audio'):
            downloaded_file = file
            break

    if downloaded_file is None:
        raise FileNotFoundError("Le fichier audio téléchargé est introuvable.")

    try:
        audio = AudioSegment.from_file(downloaded_file)
        audio.export(output_path, format='mp3')
        print(f'Audio converti avec succès en {output_path}')
    except Exception as e:
        raise RuntimeError(f"Erreur lors de la conversion du fichier audio : {e}")
    finally:
        if downloaded_file:
            os.remove(downloaded_file)

def download_video_as_mp4(video_url):
    ydl_opts = {
        'format': 'bestvideo',
        'outtmpl': 'downloaded_video.%(ext)s',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    downloaded_file = None
    for file in os.listdir('.'):
        if file.startswith('downloaded_video'):
            downloaded_file = file
            break

    if downloaded_file is None:
        raise FileNotFoundError("Le fichier vidéo téléchargé est introuvable.")

    output_dir = "video_output"
    os.makedirs(output_dir, exist_ok=True)

    unique_filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(output_dir, unique_filename)

    try:
        os.rename(downloaded_file, output_path)
        print(f'Vidéo téléchargée et enregistrée avec succès en {output_path}')
    except Exception as e:
        raise RuntimeError(f"Erreur lors du déplacement du fichier vidéo : {e}")

    return output_path
  
def transcribe_with_whisper_cli(file_path):
    try:
        temp_output = f"{uuid.uuid4()}.txt"
        # Construction de la commande pour Whisper CLI
        command = f'whisper "{file_path}" --model large > {temp_output}'
        print(f"Running command: {command}")

        # Exécution de la commande avec subprocess
        result = subprocess.run(command, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONIOENCODING='utf-8'))

        # Affichez les résultats de la commande
        print("STDOUT:", result.stdout.decode('utf-8'))
        print("STDERR:", result.stderr.decode('utf-8'))

        # Lire le fichier temporaire
        with open(temp_output, 'r', encoding='utf-8') as f:
            output = f.read()

        os.remove(temp_output)

        # Chemin du fichier VTT généré par Whisper
        vtt_file_path = file_path.replace('.mp3', '.vtt')

        # Lecture du contenu du fichier VTT
        with open(vtt_file_path, 'r', encoding='utf-8') as vtt_file:
            lines = vtt_file.readlines()

        print(f"Transcription réussie et enregistrée dans {vtt_file_path}")
        return vtt_file_path, lines

    except subprocess.CalledProcessError as e:
        print(f"Erreur lors de la transcription avec Whisper CLI : {e.stderr.decode('utf-8')}")
        return None, None


def split_text_with_timestamps(vtt_file_path):
    segments = []
    with open(vtt_file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if '-->' in line:
            timestamp = line
            i += 1
            text = ""
            while i < len(lines) and lines[i].strip():
                text += lines[i].strip() + " "
                i += 1
            text = text.strip()
            if text:
                print(f"Timestamp: {timestamp}, Text: {text}")
                segments.append((timestamp, text))
            else:
                print(f"Timestamp ou texte vide : {timestamp} | {text}")
        i += 1
    return segments


def translate_text(segments, target_language):
    translated_chunks = []
    print(f"Text to translate: {[text for _, text in segments]}")

    for timestamp, chunk in segments:
        if not chunk.strip():
            continue
        try:
            translated_chunk = GoogleTranslator(source='auto', target=target_language).translate(chunk)
            print(f"Original: {chunk}")
            print(f"Translated: {translated_chunk}")
            translated_chunks.append((timestamp, translated_chunk))
        except Exception as e:
            print(f"Translation error for chunk '{chunk[:30]}...': {e}")
            translated_chunks.append((timestamp, ""))

    return translated_chunks


def time_to_ms(timestamp):
    try:
        if len(timestamp.split(':')) == 3:
            h, m, s = map(float, timestamp.split(':'))
        elif len(timestamp.split(':')) == 2:
            m, s = map(float, timestamp.split(':'))
            h = 0
        else:
            raise ValueError("Format de timestamp invalide.")
        s, ms = divmod(s, 1)
        return int((h * 3600 + m * 60 + s) * 1000 + ms * 1000)
    except ValueError as e:
        print(f"Erreur lors de la conversion du timestamp {timestamp} : {e}")
        return 0


def synthesize_speech_with_timestamps(translated_chunks, language_code):
    audio_segments = []
    prev_end_time = None

    for i, (timestamp, chunk) in enumerate(translated_chunks):
        start_time, end_time = timestamp.split(' --> ')
        start_ms = time_to_ms(start_time)
        end_ms = time_to_ms(end_time)
        duration_ms = end_ms - start_ms

        if chunk is None or not chunk.strip():
            print(f"Skipping empty or None chunk {i+1}")
            silence = AudioSegment.silent(duration=duration_ms)
            audio_segments.append(silence)
            prev_end_time = end_time
            continue

        try:
            print(f"Synthesizing chunk {i+1}/{len(translated_chunks)}...")
            tts = gTTS(text=chunk, lang=language_code)
            temp_path = f"temp_{uuid.uuid4()}.mp3"
            tts.save(temp_path)
            print(f"Audio enregistré temporairement sous : {temp_path}")
            audio_segment = AudioSegment.from_mp3(temp_path)
            
            # Adjust segment length to match original duration
            segment_duration_ms = len(audio_segment)
            if segment_duration_ms < duration_ms:
                silence_duration_ms = duration_ms - segment_duration_ms
                silence = AudioSegment.silent(duration=silence_duration_ms)
                audio_segment += silence
            elif segment_duration_ms > duration_ms:
                audio_segment = audio_segment[:duration_ms]
            
            if prev_end_time:
                prev_end_ms = time_to_ms(prev_end_time)
                silence_duration = start_ms - prev_end_ms
                if silence_duration > 0:
                    silence = AudioSegment.silent(duration=silence_duration)
                    audio_segments.append(silence)
                    
            audio_segments.append(audio_segment)
            os.remove(temp_path)
        except Exception as e:
            print(f"Erreur lors de la synthèse du chunk {i+1}: {e}")

        prev_end_time = end_time

    if audio_segments:
        final_audio = AudioSegment.silent(duration=0)
        for segment in audio_segments:
            final_audio += segment

        output_dir = "output"
        os.makedirs(output_dir, exist_ok=True)
        final_output_path = os.path.join(output_dir, f"{uuid.uuid4()}.mp3")
        final_audio.export(final_output_path, format="mp3")
        print(f"Translated audio saved to: {final_output_path}")
        return final_output_path
    else:
        print("Aucun contenu audio généré.")
        return None


def combine_video_and_audio(video_path, audio_path):
    try:
        # Charger la vidéo sans audio
        video_clip = VideoFileClip(video_path)
        
        # Charger l'audio
        audio_clip = AudioFileClip(audio_path)
        
        # Assurer que la durée de l'audio et de la vidéo sont les mêmes
        if audio_clip.duration > video_clip.duration:
            audio_clip = audio_clip.subclip(0, video_clip.duration)

        # Ajouter l'audio à la vidéo
        video_with_audio = video_clip.set_audio(audio_clip)

        # Créer le dossier de sortie s'il n'existe pas
        output_dir = "video_final"
        os.makedirs(output_dir, exist_ok=True)

        # Générer un nom de fichier unique pour la vidéo de sortie
        unique_filename = f"{uuid.uuid4()}.mp4"
        output_path = os.path.join(output_dir, unique_filename)

        # Écrire le fichier de sortie
        video_with_audio.write_videofile(output_path, codec='libx264', audio_codec='aac')

        print(f'Vidéo et audio combinés avec succès en {output_path}')
        return output_path
    except Exception as e:
        raise RuntimeError(f"Erreur lors de la combinaison de la vidéo et de l'audio : {e}")


@app.route('/traduction', methods=['POST'])
def translate():
        #data = request.get_json()
        #video_url = data.get('videoUrl')
        #lang = data.get('targetLanguage')
        video_url = 'https://www.youtube.com/watch?v=QVz2GbYFYA8'
        lang = 'fr'
        cache = load_cache()
        output_mp3_path = 'output.mp3'
        output_mp4_path = None
        transcription = None
        translated_chunks = None
        output_audio_path = None
        output_video_path = None
        vtt_file_path = None

        if video_url in cache:
            video_data = cache[video_url]
            if lang in video_data['translations']:
                print(f"Data found in cache for URL: {video_url} and language: {lang}")
                transcription = video_data['transcription']
                translated_chunks = video_data['translations'][lang]
                output_audio_path = video_data['audio_paths'][lang]
                output_video_path = video_data['final_video_paths'][lang]
            else:
                print(f"Data found in cache for URL: {video_url} but not for language: {lang}")
                transcription = video_data['transcription']
                transcription = re.sub(r'\n{2,}', '\n\n', transcription)
                # Sauvegarder dans un fichier .vtt
                vtt_file_path = 'output.vtt'
                with open(vtt_file_path, 'w', encoding='utf-8') as f:
                    f.write(transcription)
                segments = split_text_with_timestamps(vtt_file_path)               
                segments = split_text_with_timestamps(vtt_file_path)
                translated_chunks = translate_text(segments, lang)
                output_audio_path = synthesize_speech_with_timestamps(translated_chunks, lang)
                output_mp4_path = video_data['video_paths_initial']
                output_video_path = combine_video_and_audio(output_mp4_path, output_audio_path)
                video_data['translations'][lang] = translated_chunks
                video_data['audio_paths'][lang] = output_audio_path
                video_data['final_video_paths'][lang] = output_video_path
                save_cache(cache)
                
        else:
            print("Starting downloading and converting audio ...")
            #download_and_convert_to_mp3(video_url, output_mp3_path)

            print("Starting transcription...")
            vtt_file_path, transcription = transcribe_with_whisper_cli(output_mp3_path)
            segments = split_text_with_timestamps(vtt_file_path)
            
            cache[video_url] = {
                'transcription': transcription,
                'video_paths_initial' : '',
                'translations': {},
                'audio_paths': {},
                'final_video_paths' : {}
            }
            save_cache(cache)

            translated_chunks = translate_text(segments, lang)
            output_audio_path = synthesize_speech_with_timestamps(translated_chunks, lang)
            
            print("Starting downloading and converting video ...")
            output_mp4_path = download_video_as_mp4(video_url)
            output_video_path = combine_video_and_audio(output_mp4_path, output_audio_path)

            cache[video_url]['translations'][lang] = translated_chunks
            cache[video_url]['audio_paths'][lang] = output_audio_path
            cache[video_url]['video_paths_initial'] = output_mp4_path
            cache[video_url]['final_video_paths'][lang] = output_video_path

            save_cache(cache)

    
        if os.path.exists('output.json'):
            os.remove('output.json')
        if os.path.exists('output.srt'):
            os.remove('output.srt')
        if os.path.exists('output.tsv'):
            os.remove('output.tsv')
        if os.path.exists('output.txt'):
            os.remove('output.txt')
     
        return jsonify({"final_video_paths": output_video_path })

   
if __name__ == '__main__':
    app.run(host="0.0.0.0", debug=True)


