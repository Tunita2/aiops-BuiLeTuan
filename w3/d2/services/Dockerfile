FROM python:3.11-slim

# Install tools needed for chaos injection inside containers
RUN apt-get update && apt-get install -y --no-install-recommends \
    stress-ng \
    iproute2 \
    iputils-ping \
    curl \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mock_service.py .
COPY traffic_generator.py .

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
    CMD curl -f http://localhost:${SERVICE_PORT:-8080}/health || exit 1

CMD ["python", "mock_service.py"]
