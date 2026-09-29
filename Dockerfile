# AgniCalendar API. Build from the repository root: docker build -t agnicalendar .
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY backend/requirements.lock.txt backend/requirements.lock.txt
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY backend backend
RUN useradd --create-home agni && chown -R agni /srv
USER agni
# Configuration comes from the environment: DATABASE_URL, AUTH_JWT_SECRET, FIRMS_MAP_KEY, PORT.
CMD ["sh", "backend/scripts/start.sh"]
