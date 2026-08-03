FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py analysis_v2.py prediction_journal_v3.py ./
COPY public ./public
COPY data ./data

ENV HOST=0.0.0.0
ENV PORT=8787
ENV LOTTO_PERSISTENT_DATA_DIR=/var/data

RUN mkdir -p /var/data

EXPOSE ${PORT}

CMD ["python", "server.py"]
