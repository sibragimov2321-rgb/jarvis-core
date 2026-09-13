FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8000
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY jarvis ./jarvis
RUN pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home jarvis && mkdir -p /data && chown jarvis:jarvis /data
USER jarvis
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn jarvis.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
