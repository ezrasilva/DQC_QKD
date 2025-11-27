# Dockerfile
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1


RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG SERVICE_NAME

COPY ${SERVICE_NAME}/requirements.txt /app/${SERVICE_NAME}/requirements.txt

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r /app/${SERVICE_NAME}/requirements.txt

COPY . /app

RUN mkdir -p /data && chmod 777 /data

ENV PYTHONPATH=/app

CMD ["bash"]