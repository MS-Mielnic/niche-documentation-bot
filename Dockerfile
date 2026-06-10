#niche-bot/Dockerfile
# Stage 1: Builder
FROM python:3.11.5-slim AS builder

WORKDIR /app

# Install system build tools required for C-extensions (like SQLite/Chroma)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies strictly from your frozen requirements
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# explicitly download spaCy language model
RUN python -m spacy download en_core_web_sm

# Stage 2: Production
FROM python:3.11.5-slim

WORKDIR /app

# Create a non-root user for K8s security best practices
RUN useradd -m botuser

# Copy the compiled dependencies from the builder stage
COPY --from=builder /root/.local /home/botuser/.local
ENV PATH=/home/botuser/.local/bin:$PATH
ENV PYTHONPATH=/home/botuser/.local/lib/python3.11/site-packages

# Copy your application code
COPY src_v2/ src_v2/

# Create the /data mount point and assign ownership to the non-root user
RUN mkdir /data && chown botuser:botuser /data

# Lock down the container to run as the non-root user
USER botuser

# Expose the FastAPI gateway port
EXPOSE 8000

# Start the application
CMD ["opentelemetry-instrument", "uvicorn", "src_v2.main:app", "--host", "0.0.0.0", "--port", "8000"]