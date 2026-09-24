FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py panel.py odin.py content_intelligence.py brand.json gunicorn.conf.py sitecustomize.py ./
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:application"]
