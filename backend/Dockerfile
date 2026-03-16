FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN python -c "import sys, faster_whisper, ctranslate2, av; print('asr-build-ok', sys.version.split()[0], getattr(faster_whisper, '__version__', 'unknown'), getattr(ctranslate2, '__version__', 'unknown'), getattr(av, '__version__', 'unknown'))"

COPY app /app/app

EXPOSE 10000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
