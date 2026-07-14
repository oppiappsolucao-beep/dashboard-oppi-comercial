FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8501

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p generated/proposals

EXPOSE 8501

CMD ["sh", "-c", "echo Starting Streamlit on port ${PORT:-8501} && streamlit run app.py --server.address=0.0.0.0 --server.port=${PORT:-8501} --browser.gatherUsageStats=false"]
