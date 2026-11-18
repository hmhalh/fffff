FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y git ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone --depth 1 \
    --branch stable \
    https://github.com/alexbers/mtprotoproxy.git \
    /app/mtprotoproxy

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY config.py /app/mtprotoproxy/config.py

WORKDIR /app/mtprotoproxy

EXPOSE 443

CMD ["python", "mtprotoproxy.py", "/app/mtprotoproxy/config.py"]