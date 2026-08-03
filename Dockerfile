FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=8787
ENV LOTTO_PERSISTENT_DATA_DIR=/var/data
ENV OMP_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV NUMEXPR_NUM_THREADS=1

WORKDIR /app

COPY lotto-lab-web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY lotto-lab-web/server.py lotto-lab-web/analysis_v2.py lotto-lab-web/prediction_journal_v3.py ./
COPY lotto-lab-web/public ./public
COPY lotto-lab-web/data ./data

RUN mkdir -p /var/data

EXPOSE ${PORT}

CMD ["python", "server.py"]
