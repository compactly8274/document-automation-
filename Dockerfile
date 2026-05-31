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

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, time; p='/output/.healthcheck'; m=os.path.getmtime(p) if os.path.exists(p) else 0; exit(0 if time.time()-m < 120 else 1)"

CMD ["python", "-m", "homedocs", "daemon"]
