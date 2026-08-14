# Imagen para correr clonavoz en un contenedor (recomendado en Linux/WSL2:
# ver docker-compose.yml para el paso de audio del host hacia el contenedor).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    libportaudio2 \
    pulseaudio-utils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml requirements.txt ./
COPY src ./src

RUN pip install --no-cache-dir torch torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

ENTRYPOINT ["clonavoz"]
CMD ["--help"]
