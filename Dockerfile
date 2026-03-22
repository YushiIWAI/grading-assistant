FROM python:3.12-slim

WORKDIR /app

# System dependencies (psycopg2, PyMuPDF)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq-dev \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Streamlit config
RUN mkdir -p /root/.streamlit
RUN echo '[server]\nheadless = true\nport = 8501\naddress = "0.0.0.0"\nenableCORS = false\n\n[browser]\ngatherUsageStats = false' > /root/.streamlit/config.toml

# Data directories
RUN mkdir -p data output

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8501/_stcore/health')"

CMD ["python3", "-m", "streamlit", "run", "app.py"]
