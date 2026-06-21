# Portable container for the Voltpath backend. Works on Cloud Run, Fly.io,
# Hugging Face Spaces (Docker), Render, etc. Build context = the backend/ dir.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Most hosts inject $PORT (Cloud Run=8080, HF Spaces=7860); default 7860 otherwise.
EXPOSE 7860

# Run migrations + seed (both idempotent), then serve.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed.seed && uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
