#!/bin/bash
set -e

MODEL=${OLLAMA_MODEL:-llama3.2}
DATA_FILE="/app/SAP_GL_Account_Data.xlsx"

# ---------------------------------------------------------------------------
# Download data file from S3 if configured and not already present
# ---------------------------------------------------------------------------
if [ -n "$S3_BUCKET_NAME" ]; then
    if [ ! -f "$DATA_FILE" ]; then
        echo "[entrypoint] Downloading data from s3://${S3_BUCKET_NAME}/${S3_DATA_KEY:-SAP_GL_Account_Data.xlsx} ..."
        python - <<'PYEOF'
import boto3, os, sys
try:
    s3 = boto3.client(
        's3',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
        region_name=os.environ.get('AWS_REGION', 'us-east-1'),
    )
    s3.download_file(
        os.environ['S3_BUCKET_NAME'],
        os.environ.get('S3_DATA_KEY', 'SAP_GL_Account_Data.xlsx'),
        '/app/SAP_GL_Account_Data.xlsx',
    )
    print('[entrypoint] Data file downloaded successfully.')
except Exception as e:
    print(f'[entrypoint] ERROR downloading from S3: {e}', file=sys.stderr)
    sys.exit(1)
PYEOF
    else
        echo "[entrypoint] Data file already present, skipping S3 download."
    fi
fi

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
