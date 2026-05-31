FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY homedocs/ ./homedocs/

COPY log.sh /usr/local/bin/log.sh
RUN chmod +x /usr/local/bin/log.sh

CMD ["python", "-m", "homedocs", "daemon"]
