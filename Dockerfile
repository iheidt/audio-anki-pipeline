FROM python:3.10-slim

RUN apt-get update &&     apt-get install -y ffmpeg &&     pip install --no-cache-dir Flask pydub PyMuPDF

WORKDIR /app
COPY . /app

CMD ["python", "app.py"]
