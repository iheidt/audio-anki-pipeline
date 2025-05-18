from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from pydub import AudioSegment, silence
import csv, os, shutil, zipfile, uuid
import fitz  # PyMuPDF

app = Flask(__name__)
BASE_FOLDER = "sessions"
os.makedirs(BASE_FOLDER, exist_ok=True)

# This is a test comment

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "running"})

def get_session_folder():
    session_id = str(uuid.uuid4())
    folder = os.path.join(BASE_FOLDER, session_id)
    os.makedirs(os.path.join(folder, "audio"), exist_ok=True)
    return session_id, folder

@app.route("/upload/pdf", methods=["POST"])
def upload_pdf():
    session_id = request.args.get("session_id")
    if not session_id:
        return {"error": "Missing session_id"}, 400

    folder = os.path.join(BASE_FOLDER, session_id)
    os.makedirs(folder, exist_ok=True)  # ✅ Ensure folder exists

    try:
        pdf_path = os.path.join(folder, "vocab.pdf")
        f = request.files['file']
        f.save(pdf_path)
        return {"message": "PDF uploaded", "session_id": session_id}, 200
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/upload/audio", methods=["POST"])
def upload_audio():
    session_id = request.args.get("session_id")
    folder = os.path.join(BASE_FOLDER, session_id)
    audio_path = os.path.join(folder, "vocab.mp3")
    f = request.files['file']
    f.save(audio_path)
    return {"message": "Audio uploaded"}, 200

def parse_pdf_words(pdf_path):
    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    lines = text.split("\n")
    entries = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0][0].isdigit():
            try:
                number = int(parts[0].strip("､"))
                kanji = parts[1]
                kana = parts[2]
                meaning = " ".join(parts[3:])
                entries.append((number, kanji, kana, meaning))
            except:
                continue
    return entries

def split_audio(audio_path, audio_folder, expected_count):
    audio = AudioSegment.from_mp3(audio_path)
    chunks = silence.split_on_silence(audio, min_silence_len=300, silence_thresh=-40)
    chunks = chunks[:expected_count]  # match parsed entries count
    for i, chunk in enumerate(chunks):
        chunk = chunk.fade_in(10).fade_out(10)  # smooth edges
        chunk.export(os.path.join(audio_folder, f"{i+1:03}.mp3"), format="mp3")

def create_csv(entries, audio_folder, csv_path):
    with open(csv_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, entry in enumerate(entries):
            number, kanji, kana, meaning = entry
            filename = f"{i+1:03}.mp3"
            writer.writerow([f"{kana}　{kanji}", meaning, f"[sound:{filename}]"])

@app.route("/generate", methods=["GET"])
def generate():
    session_id = request.args.get("session_id")
    folder = os.path.join(BASE_FOLDER, session_id)
    pdf_path = os.path.join(folder, "vocab.pdf")
    audio_path = os.path.join(folder, "vocab.mp3")
    audio_folder = os.path.join(folder, "audio")  # 🔧 <- FIX: Ensure this is created
    os.makedirs(audio_folder, exist_ok=True)      # 🔧 <- ADD THIS LINE
    zip_path = os.path.join(folder, "anki_output.zip")
    csv_path = os.path.join(folder, "anki_cards.csv")

    if not os.path.exists(pdf_path) or not os.path.exists(audio_path):
        return {"error": "Missing PDF or audio"}, 400

    entries = parse_pdf_words(pdf_path)
    split_audio(audio_path, audio_folder, len(entries))
    create_csv(entries, audio_folder, csv_path)

    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(csv_path, arcname=os.path.basename(csv_path))
        for file in os.listdir(audio_folder):
            z.write(os.path.join(audio_folder, file), arcname=os.path.join("audio", file))

    return send_file(zip_path, mimetype='application/zip', as_attachment=True)

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
