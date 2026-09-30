FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# State lives in /app/data - mount a folder there to keep it across container rebuilds.
# The container must listen on all interfaces; docker-compose only publishes the port on localhost.
ENV DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=5000 \
    PYTHONUNBUFFERED=1
VOLUME /app/data

CMD ["python", "shift-bot.py"]
