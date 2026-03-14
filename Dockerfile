FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine.py app.py ./
COPY presets/ presets/

RUN mkdir -p jobs

EXPOSE 5111

CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5111", "--timeout", "120", "--workers", "1", "--threads", "4"]
