FROM python:3.13-slim
WORKDIR /app
COPY service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home verifier && mkdir /data && chown verifier:verifier /data
COPY service/ ./service/
COPY config/ ./config/
WORKDIR /app/service
USER verifier
ENV VERIFY_DB=/data/verification.sqlite3
EXPOSE 8000
CMD ["uvicorn", "server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
