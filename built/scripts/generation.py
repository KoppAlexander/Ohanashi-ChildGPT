from flask import Flask, Response, send_from_directory, request, jsonify
import torch
from TTS.api import TTS
from TTS.utils.manage import ModelManager
import os
import re
from flask_cors import CORS
import json
import html
import sys
import chevron
import subprocess
import logging
from threading import Thread

# Add the parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Initialize Flask app
app = Flask(__name__, static_url_path='/built', static_folder=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)

app.config['DEBUG'] = False

logging.basicConfig(level=logging.DEBUG)

# Define speaker, language, and title
SPEAKER = ''.lower().replace(' ', '_')
LANGUAGE = 'en'.lower().replace(' ', '_')
TITLE = 'Androcles and the Lion'.lower().replace(' ', '_')

# Generated text
TEXT = "Once upon a time, in a small village nestled at the edge of a dense and mysterious forest, there lived a little girl named Lily. The villagers called her 'Forest Child' because she spent most of her days exploring the woods, collecting wildflowers, and watching the animals that roamed freely within its boundaries."

# Initialize TTS with the XTTS v2 model
device = "cuda" if torch.cuda.is_available() else "cpu"
model_name = 'tts_models/multilingual/multi-dataset/xtts_v2'

# Initialize the TTS model with the pre-trained XTTS v2
tts = TTS(model_name=model_name, progress_bar=False, gpu=torch.cuda.is_available())


# Initialize the TTS model with the pre-trained XTTS v2
tts = TTS(model_name).to(device)

# Ensure the necessary directories exist
os.makedirs(os.path.join(app.root_path, '..', 'static', 'audio'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, '..', 'config'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, '..', 'static', 'story'), exist_ok=True)
os.makedirs(os.path.join(app.root_path, '..', 'model'), exist_ok=True)

def load_speaker_model(speaker):
    try:
        speaker_wav_path = os.path.join(app.root_path, '..', 'audio', f'{speaker}.wav')
        if os.path.exists(speaker_wav_path):
            global tts
            tts = TTS(model_name=model_name, progress_bar=False, gpu=torch.cuda.is_available())
            app.logger.info(f"Loaded base TTS model for speaker: {speaker}")
            return True
        else:
            app.logger.error(f"Speaker audio file does not exist: {speaker_wav_path}")
    except Exception as e:
        app.logger.error(f"Failed to initialize TTS model: {e}", exc_info=True)
    return False



@app.route('/speaker')
def speaker():
    config_path = os.path.join(app.root_path, '..', 'config', 'speaker.json')
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
        return jsonify(config)
    else:
        return jsonify({"speakers": []})

def split_text(text, max_chars=250, max_sentences=5):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = []
    current_char_count = 0
    for sentence in sentences:
        if len(current_chunk) < max_sentences and current_char_count + len(sentence) <= max_chars:
            current_chunk.append(sentence)
            current_char_count += len(sentence)
        else:
            if current_chunk:
                chunks.append(' '.join(current_chunk))
            current_chunk = [sentence]
            current_char_count = len(sentence)
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    return chunks

def generate_story_html(title, chunks, audio_files, language):
    template_path = os.path.join(app.root_path, '..', 'template', 'template.html')
    
    with open(template_path, 'r', encoding='utf-8') as template_file:
        template = template_file.read()
    
    # Get all speakers and languages for this story
    story_json_path = os.path.join(app.root_path, '..', 'config', 'story.json')
    speaker_json_path = os.path.join(app.root_path, '..', 'config', 'speaker.json')
    
    with open(story_json_path, 'r') as f:
        story_data = json.load(f)
    
    with open(speaker_json_path, 'r') as f:
        all_speakers = json.load(f).get('speakers', [])
    
    story_entry = next((item for item in story_data if item["title"] == title.lower().replace(' ', '_')), None)
    existing_speakers = story_entry["speaker"] if story_entry else []
    languages = story_entry["language"] if story_entry else []

      # Ensure existing_speakers is a list
    if isinstance(existing_speakers, str):
        existing_speakers = [existing_speakers]

    # Convert to JSON and print for debugging
    existing_speakers_json = json.dumps(existing_speakers)
    all_speakers_json = json.dumps(all_speakers)
    print("Existing Speakers JSON:", existing_speakers_json)
    print("All Speakers JSON:", all_speakers_json)

    data = {
        'title': html.escape(title),
        'chunks': [
            {'text': html.escape(chunk), 'audio': audio_file, 'index': i + 1}
            for i, (chunk, audio_file) in enumerate(zip(chunks, audio_files))
        ],
        'story_json_path': '../../config/story.json',
        'language': language,
        'existing_speakers_json': existing_speakers_json,
        'all_speakers_json': all_speakers_json,
        'languages': languages
    }

    # Generate the HTML content
    story_html = chevron.render(template, data)

    return story_html


@app.route('/')
def index():
    return send_from_directory(os.path.dirname(os.path.dirname(app.root_path)), 'index.html')

@app.route('/process')
def process_text():
    speaker = request.args.get('speaker', default='', type=str)

    def generate():
        text_chunks = split_text(TEXT)
        audio_files = []
        for i, chunk in enumerate(text_chunks):
            audio_filename = f"{speaker}_{LANGUAGE}_{TITLE}_{i+1}.wav" if speaker else f"{LANGUAGE}_{TITLE}_{i+1}.wav"
            audio_path = os.path.join(app.root_path, '..', 'static', 'audio', audio_filename)

            # Generate audio file
            speaker_wav = os.path.join(app.root_path, '..', 'audio', f'{speaker}.wav') if speaker else None
            tts.tts_to_file(text=chunk,
                            speaker_wav=speaker_wav,
                            language=LANGUAGE,
                            file_path=audio_path)

            audio_url = f"/built/static/audio/{audio_filename}"
            audio_files.append(audio_url)

            result = {
                'text': chunk,
                'audio': audio_url
            }

            yield f"data: {json.dumps(result)}\n\n"

        # Update story JSON file
        story_json_path = os.path.join(app.root_path, '..', 'config', 'story.json')
        
        if os.path.exists(story_json_path):
            with open(story_json_path, 'r') as f:
                story_data = json.load(f)
        else:
            story_data = []

        # Find existing entry or create new one
        existing_entry = next((item for item in story_data if item["title"] == TITLE), None)
        if existing_entry:
            if speaker and speaker not in existing_entry["speaker"]:
                existing_entry["speaker"].append(speaker)
            if LANGUAGE not in existing_entry["language"]:
                existing_entry["language"].append(LANGUAGE)
        else:
            new_entry = {
                "title": TITLE,
                "speaker": [speaker] if speaker else [],
                "language": [LANGUAGE]
            }
            story_data.append(new_entry)

        with open(story_json_path, 'w') as f:
            json.dump(story_data, f, indent=2)

        # Generate story HTML file
        story_html = generate_story_html(TITLE.replace('_', ' ').title(), text_chunks, audio_files, LANGUAGE)
        story_html_path = os.path.join(app.root_path, '..', 'static', 'story', f"{TITLE}.html")
        with open(story_html_path, 'w', encoding='utf-8') as f:
            f.write(story_html)

        print(f"HTML file created at: {story_html_path}")

        # Signal completion
        yield "event: complete\ndata: {}\n\n"

    return Response(generate(), mimetype='text/event-stream')


def generate_audio_for_speaker(speaker, language, title, text):
    try:
        if not load_speaker_model(speaker):
            app.logger.error(f"Failed to load TTS model for speaker: {speaker}")
            return False

        chunks = text.split('. ')
        audio_files = []
        speaker_wav = os.path.join(app.root_path, '..', 'audio', f'{speaker}.wav')
        
        for i, chunk in enumerate(chunks):
            audio_filename = f"{speaker}_{language}_{title}_{i+1}.wav"
            audio_path = os.path.join(app.root_path, '..', 'static', 'audio', audio_filename)

            tts.tts_to_file(text=chunk,
                            file_path=audio_path,
                            speaker_wav=speaker_wav,
                            language=language)

            audio_files.append(f"/built/static/audio/{audio_filename}")

        # Update story.json
        story_json_path = os.path.join(app.root_path, '..', 'config', 'story.json')
        if os.path.exists(story_json_path):
            with open(story_json_path, 'r') as f:
                story_data = json.load(f)
        else:
            story_data = []

        # Find existing entry or create new one
        existing_entry = next((item for item in story_data if item["title"] == title), None)
        if existing_entry:
            if speaker not in existing_entry["speaker"]:
                existing_entry["speaker"].append(speaker)
            if language not in existing_entry["language"]:
                existing_entry["language"].append(language)
        else:
            new_entry = {
                "title": title,
                "speaker": [speaker],
                "language": [language]
            }
            story_data.append(new_entry)

        with open(story_json_path, 'w') as f:
            json.dump(story_data, f, indent=2)

        app.logger.info(f"Successfully generated audio for speaker: {speaker}")
        return True
    except Exception as e:
        app.logger.error(f"Error in generate_audio_for_speaker: {str(e)}", exc_info=True)
        return False




@app.route('/generate_audio', methods=['POST'])
def generate_audio():
    try:
        data = request.json
        app.logger.debug(f"Received data: {data}")
        speaker = data['speaker']
        language = data['language']
        title = data['title']
        text = data['text']

        app.logger.debug(f"Attempting to generate audio for speaker: {speaker}")
        success, audio_files = generate_audio_for_speaker(speaker, language, title, text)

        if success:
            app.logger.info(f"Audio generated successfully for speaker: {speaker}")
            return jsonify({"success": True, "message": "Audio generated successfully", "audio_files": audio_files}), 200
        else:
            app.logger.error(f"Failed to generate audio for speaker: {speaker}")
            return jsonify({"success": False, "message": "Failed to generate audio"}), 500
    except Exception as e:
        app.logger.error(f"Error in generate_audio: {str(e)}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

def generate_audio_for_speaker(speaker, language, title, text):
    try:
        if not load_speaker_model(speaker):
            app.logger.error(f"Failed to load TTS model for speaker: {speaker}")
            return False, []

        chunks = text.split('. ')
        audio_files = []
        speaker_wav = os.path.join(app.root_path, '..', 'audio', f'{speaker}.wav')
        
        for i, chunk in enumerate(chunks):
            audio_filename = f"{speaker}_{language}_{title}_{i+1}.wav"
            audio_path = os.path.join(app.root_path, '..', 'static', 'audio', audio_filename)

            tts.tts_to_file(text=chunk,
                            file_path=audio_path,
                            speaker_wav=speaker_wav,
                            language=language)

            audio_files.append({"text": chunk, "audio": f"/built/static/audio/{audio_filename}"})

        # Update story.json
        story_json_path = os.path.join(app.root_path, '..', 'config', 'story.json')
        if os.path.exists(story_json_path):
            with open(story_json_path, 'r') as f:
                story_data = json.load(f)
        else:
            story_data = []

        # Find existing entry or create new one
        existing_entry = next((item for item in story_data if item["title"] == title), None)
        if existing_entry:
            if speaker not in existing_entry["speaker"]:
                existing_entry["speaker"].append(speaker)
            if language not in existing_entry["language"]:
                existing_entry["language"].append(language)
        else:
            new_entry = {
                "title": title,
                "speaker": [speaker],
                "language": [language]
            }
            story_data.append(new_entry)

        with open(story_json_path, 'w') as f:
            json.dump(story_data, f, indent=2)

        app.logger.info(f"Successfully generated audio for speaker: {speaker}")
        return True, audio_files
    except Exception as e:
        app.logger.error(f"Error in generate_audio_for_speaker: {str(e)}", exc_info=True)
        return False, []


@app.route('/built/static/audio/<path:filename>')
def serve_audio(filename):
    return send_from_directory(os.path.join(app.root_path, '..', 'static', 'audio'), filename)

@app.route('/built/static/story/<path:filename>')
def serve_story(filename):
    return send_from_directory(os.path.join(app.root_path, '..', 'static', 'story'), filename)

@app.route('/built/config/<path:filename>')
def serve_config(filename):
    return send_from_directory(os.path.join(app.root_path, '..', 'config'), filename)

@app.route('/story')
def story_page():
    return send_from_directory(os.path.join(app.root_path, '..', 'built', 'content'), 'story.html')

def update_speaker_config(speaker):
    config_path = os.path.join(app.root_path, '..', 'config', 'speaker.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)

    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            config = json.load(f)
    else:
        config = {"speakers": []}

    if speaker not in config["speakers"]:
        config["speakers"].append(speaker)

    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    logging.debug(f"Updated speaker.json with user: {speaker}")
    logging.debug(f"Current speaker.json content: {json.dumps(config, indent=2)}")

@app.route('/save-audio', methods=['POST'])
def save_audio():
    try:
        if 'audio' not in request.files:
            return jsonify({'success': False, 'error': 'No audio file'}), 400

        audio_file = request.files['audio']
        speaker = request.form.get('speaker', 'unknown_user')

        if not speaker:
            return jsonify({'success': False, 'error': 'Invalid speaker'}), 400

        audio_dir = os.path.join(app.root_path, '..', 'audio')
        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, f'{speaker}.wav')

        logging.debug(f"Attempting to save audio to: {audio_path}")

        # Save the raw audio data
        audio_file.save(audio_path)

        # Use FFmpeg to convert the audio to a valid WAV format
        try:
            ffmpeg_command = [
                'ffmpeg',
                '-i', audio_path,
                '-acodec', 'pcm_s16le',
                '-ar', '44100',
                '-ac', '2',
                '-y',  # Overwrite output file if it exists
                f'{audio_path}.converted.wav'
            ]
            result = subprocess.run(ffmpeg_command, capture_output=True, text=True)
            if result.returncode != 0:
                logging.error(f"FFmpeg error: {result.stderr}")
                return jsonify({'success': False, 'error': f'Error converting audio: {result.stderr}'}), 500

            # Replace the original file with the converted one
            os.replace(f'{audio_path}.converted.wav', audio_path)
        except Exception as e:
            logging.error(f"Error running FFmpeg: {e}", exc_info=True)
            return jsonify({'success': False, 'error': f'Error converting audio: {e}'}), 500

        file_size = os.path.getsize(audio_path)
        logging.debug(f"Audio saved and converted successfully! File size: {file_size} bytes")

        # Check if the file is not empty
        if file_size == 0:
            os.remove(audio_path)
            return jsonify({'success': False, 'error': 'Saved audio file is empty'}), 400

        # Update speaker.json
        update_speaker_config(speaker)

        return jsonify({'success': True, 'message': f'Audio saved as {audio_path}'})

    except Exception as e:
        logging.error(f"Error saving audio: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

def train_model_task(speaker):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    os.environ["COQUI_TOS_AGREED"] = "1"

    manager = ModelManager()
    model = 'tts_models/multilingual/multi-dataset/xtts_v2'
    manager.download_model(model)

    tts = TTS(model).to(device)

    audio_path = os.path.join(app.root_path, '..', 'audio', f'{speaker}.wav')
    logging.debug(f"Training model with audio: {audio_path}")

    try:
        model_dir = os.path.join(app.root_path, '..', 'model')
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, f'{speaker}.pth')
        logging.debug(f"Saving model to: {model_path}")
        torch.save(tts.state_dict(), model_path)
        logging.debug("Model saved successfully!")

        # Update speaker.json
        update_speaker_config(speaker)

    except Exception as e:
        logging.error(f"Error during training or saving model: {e}", exc_info=True)

@app.route('/train-model', methods=['POST'])
def train_model():
    try:
        data = request.json
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data provided'}), 400

        speaker = data.get('speaker')
        if not speaker:
            return jsonify({'success': False, 'error': 'No speaker provided'}), 400

        Thread(target=train_model_task, args=(speaker,)).start()
        return jsonify({'success': True, 'message': 'Model training started'})
    except Exception as e:
        logging.error(f"Error in train_model: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/test', methods=['GET'])
def test():
    return jsonify({'message': 'Test successful'})

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error(f"Unhandled exception: {str(e)}", exc_info=True)
    return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000, use_reloader=False, threaded=False)
