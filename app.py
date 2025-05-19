from flask import Flask, request, send_file, jsonify
from werkzeug.utils import secure_filename
from pydub import AudioSegment, silence
from dotenv import load_dotenv
import csv, os, zipfile, uuid, fitz, openai, re

load_dotenv()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)
BASE_FOLDER = "sessions"
os.makedirs(BASE_FOLDER, exist_ok=True)

@app.route("/status", methods=["GET"])
def status():
    return jsonify({"status": "running"})

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

def extract_clean_vocab(pdf_path):
    doc = fitz.open(pdf_path)
    raw_text = "\n".join(page.get_text() for page in doc)
    lines = raw_text.split("\n")
    entries = []

    pattern_with_kana = re.compile(r"^\d+[､,]?\s*(\S+?)娔\S+娘\s*(.+)$")
    pattern_simple = re.compile(r"^\d+[､,]?\s*(\S+?)\s+(.+)$")

    for line in lines:
        match = pattern_with_kana.match(line)
        if not match:
            match = pattern_simple.match(line)
        if match:
            vocab = match.group(1).strip()
            meaning = match.group(2).strip()
            entries.append((vocab, meaning))

    return entries

def ask_openai_to_format(entries):
    joined = "\n".join([f"{kanji}: {meaning}" for kanji, meaning in entries])
    prompt = (
        "You are a Japanese teacher creating Anki vocabulary cards. For each word below, output in this exact CSV format:\n"
        "KANJI[FURIGANA],ENGLISH MEANING\n"
        "Rules:\n"
        "- Put furigana in square brackets after each kanji.\n"
        "- If the word contains multiple kanji, separate each with a space.\n"
        "- Do NOT insert a space between kanji and attached hiragana.\n"
        "- Do NOT include romaji.\n"
        "- Example 1: 努[ど] 力[りょく]する,To make an effort\n"
        "- Example 2 (ateji): 今日[きょう],Today\n"
        f"\nWords:\n{joined}"
    )

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}]
    )

    csv_lines = completion.choices[0].message.content.split("\n")
    return [line for line in csv_lines if line.strip()]

def transcribe_audio_chunks(audio_path, expected_count, session_id):
    audio = AudioSegment.from_mp3(audio_path)
    chunks = silence.split_on_silence(audio, min_silence_len=300, silence_thresh=-40)
    chunks = chunks[:expected_count]
    transcriptions = []

    for i, chunk in enumerate(chunks):
        chunk_path = os.path.join("sessions", session_id, "audio", f"{i+1:03}.mp3")
        chunk.export(chunk_path, format="mp3")

    with open(chunk_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
            language="ja"
        )
        transcriptions.append(transcript.strip())

    return transcriptions


def write_csv(lines, audio_folder, csv_path):
    with open(csv_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, line in enumerate(lines):
            if "," in line:
                jp, en = line.split(",", 1)
                filename = f"{i+1:03}.mp3"
                writer.writerow([jp, en, f"[sound:{filename}]"])

@app.route("/generate", methods=["GET"])
def generate():
    session_id = request.args.get("session_id")
    if not session_id:
        return {"error": "Missing session_id"}, 400

    folder = os.path.join(BASE_FOLDER, session_id)
    os.makedirs(folder, exist_ok=True)
    pdf_path = os.path.join(folder, "vocab.pdf")
    audio_path = os.path.join(folder, "vocab.mp3")
    audio_folder = os.path.join(folder, "audio")
    os.makedirs(audio_folder, exist_ok=True)
    zip_path = os.path.join(folder, f"anki_output_{session_id}.zip")
    csv_path = os.path.join(folder, "anki_cards.csv")

    if not os.path.exists(pdf_path) or not os.path.exists(audio_path):
        return {"error": "Missing PDF or audio"}, 400

    entries = extract_clean_vocab(pdf_path)
    csv_lines = ask_openai_to_format(entries)
    transcriptions = transcribe_audio_chunks(audio_path, len(csv_lines), session_id)

    write_csv(csv_lines, audio_folder, csv_path)

    with zipfile.ZipFile(zip_path, 'w') as z:
        z.write(csv_path, arcname="anki_cards.csv")
        for file in os.listdir(audio_folder):
            z.write(os.path.join(audio_folder, file), arcname=os.path.join("audio", file))

    return send_file(zip_path, mimetype='application/zip', as_attachment=True, download_name=f"anki_output_{session_id}.zip")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
