FROM python:3.11-slim

# Install system dependencies + curl (needed to install Ollama)
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama
RUN curl -fsSL https://ollama.com/install.sh | sh

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (NOT the data file — mounted at runtime)
COPY app.py data_loader.py analysis.py tools.py guardrails.py local_llm.py ./
COPY .streamlit/ .streamlit/

# Create audit log directory
RUN mkdir -p audit_logs

# Expose Streamlit port
EXPOSE 8501

# Entrypoint: start Ollama server in background, pull model, then start Streamlit
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
