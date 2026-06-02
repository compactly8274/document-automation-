FROM python:3.12-slim

# docker-ce-cli is needed by the web service to `docker exec homedocs …` and
# trigger regenerates after a form save. Install it from Docker's official
# apt repo. This adds ~100MB; the daemon does not need it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git \
 && install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && chmod a+r /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends docker-ce-cli \
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
