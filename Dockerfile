# Stage 1: Build Vue frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Flask runtime
FROM python:3.12-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt ./backend/
COPY requirements.txt ./
RUN pip install --no-cache-dir -r backend/requirements.txt -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY llm_benchmark.py ./
COPY run_benchmarks.py ./
COPY assets/ ./assets/

# Copy built frontend from stage 1
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Create reports directory
RUN mkdir -p /app/reports

# Set environment variables
ENV FLASK_APP=backend/app.py
ENV PYTHONUNBUFFERED=1
ENV BENCH_USER=admin
ENV BENCH_PASS=admin
ENV JWT_SECRET_KEY=change-me-in-production

EXPOSE 5000

WORKDIR /app
CMD ["python", "backend/app.py"]
