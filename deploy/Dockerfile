FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 hynak
COPY . .
RUN mkdir -p /data && chown hynak:hynak /data
USER hynak
ENV HYNAK_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1","--no-access-log","--proxy-headers","--forwarded-allow-ips=*"]
