# COFFEE!! -kontti. Ajetaan Leafyn nginxin takana (ks. deploy/nginx-lastdrop*.conf).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --system --uid 10001 coffeenow
COPY app ./app
USER coffeenow
EXPOSE 8000
# Vain nginx (sisäverkko, ei julkisia portteja) puhuu tälle; nginx asettaa X-Forwarded-For = asiakkaan IP
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
