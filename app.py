from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from pydub import AudioSegment, silence
import csv, os, shutil, zipfile, uuid
import fitz  # PyMuPDF
import re
import fugashi

tagger = fugashi.Tagger()

app = Flask(__name__)
BASE_FOLDER = "sessions"
os.makedirs(BASE_FOLDER, exist_ok=True)

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
    os.makedirs(folder, exist_ok=True)

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
        match = re.match(r"^\d+[､,]\s*(\S+?)\(([^)]+)\)\s*(.+)$", line)
        if match:
            kanji = match.group(1).strip()
            kana = match.group(2).strip()
            meaning = match.group(3).strip()
            entries.append((kanji, kana, meaning))

    return entries

def format_furigana(kanji, kana):
    # Try to determine if the word is atomic (ateji or compound)
    tokens = list(tagger(kanji))
    if len(tokens) == 1:
        return f"{kanji}[{kana}]"

    result = []
    k_index = 0
    kana_index = 0

    while k_index < len(kanji) and kana_index < len(kana):
        k = kanji[k_index]
        if re.match(r'[一-龯]', k):
            furigana = ''
            for j in range(1, 4):
                candidate = kana[kana_index:kana_index + j]
                if candidate and re.match(r'^[ぁ-んー]+$', candidate):
                    furigana = candidate
            if furigana:
                result.append(f"{k}[{furigana}]")
                kana_index += len(furigana)
            else:
                result.append(k)
        else:
            result.append(k)
        k_index += 1

    if kana_index < len(kana):
        result.append(kana[kana_index:])

    spaced = []
    for i, token in enumerate(result):
        spaced.append(token)
        if i < len(result) - 1:
            if re.match(r'[一-龯]', result[i+1][0]) and not result[i][-1] in 'ぁ-んー':
                spaced.append(' ')

    return ''.join(spaced)

def split_audio(audio_path, audio_folder, expected_count):
    audio = AudioSegment.from_mp3(audio_path)
    chunks = silence.split_on_silence(audio, min_silence_len=300, silence_thresh=-40)
    chunks = chunks[:expected_count]
    for i, chunk in enumerate(chunks):
        chunk = chunk.fade_in(10).fade_out(10)
        chunk.export(os.path.join(audio_folder, f"{i+1:03}.mp3"), format="mp3")

def create_csv(entries, audio_folder, csv_path):
    with open(csv_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, entry in enumerate(entries):
            kanji, kana, meaning = entry
            filename = f"{i+1:03}.mp3"
            formatted = format_furigana(kanji, kana)
            writer.writerow([formatted, meaning, f"[sound:{filename}]"])

@app.route("/generate", methods=["GET"])
def generate():
    import sys
    session_id = request.args.get("session_id")
    if not session_id:
        return {"error": "Missing session_id"}, 400

    folder = os.path.join(BASE_FOLDER, session_id)
    os.makedirs(folder, exist_ok=True)
    pdf_path = os.path.join(folder, "vocab.pdf")
    audio_path = os.path.join(folder, "vocab.mp3")
    audio_folder = os.path.join(folder, "audio")
    os.makedirs(audio_folder, exist_ok=True)
    zip_path = os.path.join(folder, "anki_output.zip")
    csv_path = os.path.join(folder, "anki_cards.csv")

    if not os.path.exists(pdf_path):
        print(f"[ERROR] Missing PDF at: {pdf_path}", file=sys.stderr)
        return {"error": "Missing PDF"}, 400

    if not os.path.exists(audio_path):
        print(f"[ERROR] Missing audio at: {audio_path}", file=sys.stderr)
        return {"error": "Missing audio"}, 400

    try:
        print("[INFO] Parsing PDF...", file=sys.stderr)
        entries = parse_pdf_words(pdf_path)

        print("[INFO] Splitting audio...", file=sys.stderr)
        split_audio(audio_path, audio_folder, len(entries))

        print("[INFO] Creating CSV...", file=sys.stderr)
        create_csv(entries, audio_folder, csv_path)

        print("[INFO] Creating ZIP...", file=sys.stderr)
        with zipfile.ZipFile(zip_path, 'w') as z:
            z.write(csv_path, arcname=os.path.basename(csv_path))
            for file in os.listdir(audio_folder):
                z.write(os.path.join(audio_folder, file), arcname=os.path.join("audio", file))

        print("[INFO] Done! Sending ZIP.", file=sys.stderr)
        return send_file(zip_path, mimetype='application/zip', as_attachment=True)

    except Exception as e:
        print(f"[ERROR] Exception during generation: {e}", file=sys.stderr)
        return {"error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
