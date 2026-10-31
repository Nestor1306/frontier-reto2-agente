FROM python:3.11-slim

# Configuracion de Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar OCR en ingles y español
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-spa \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Instalar dependencias del servidor
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalar motor visual de Claude
COPY motor-visual/ ./motor-visual/
RUN pip install --no-cache-dir ./motor-visual

# Copiar servidor
COPY main.py ejecutor.py seguridad.py gestor.py voz.py ./

# Memoria del agente (lo que aprende de cada aplicación) en una carpeta escribible
ENV AGENT_MEMORY_PATH=/app/data/memoria_apps.json
RUN mkdir -p /app/data

# Puerto del servidor
ENV PORT=8000
EXPOSE 8000

# Encender FastAPI
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
