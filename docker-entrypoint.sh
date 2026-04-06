#!/bin/bash
set -e

MODEL=${OLLAMA_MODEL:-llama3.2}

if [ "${DISABLE_OLLAMA:-false}" != "true" ]; then
    echo "[entrypoint] Starting Ollama server..."
    ollama serve &
    OLLAMA_PID=$!

    # Wait for Ollama to be ready
    echo "[entrypoint] Waiting for Ollama to be ready..."
    until curl -sf http://localhost:11434/api/tags > /dev/null 2>&1; do
        sleep 1
    done
    echo "[entrypoint] Ollama is ready."

    # Pull model if not already present
    if ! ollama list | grep -q "^${MODEL}"; then
        echo "[entrypoint] Pulling model: ${MODEL} ..."
        ollama pull "${MODEL}"
        echo "[entrypoint] Model pulled."
    else
        echo "[entrypoint] Model ${MODEL} already present."
    fi
else
    echo "[entrypoint] Ollama disabled (DISABLE_OLLAMA=true), skipping."
fi

echo "[entrypoint] Starting Streamlit..."
exec streamlit run app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --server.headless=true \
    --browser.gatherUsageStats=false
