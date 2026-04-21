FROM python:3.11-slim

WORKDIR /app

# Install dependencies first — leverages Docker layer caching when only
# source code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8080

# No healthcheck yet — add one in Phase 6/7 when we pin the MCP
# health/ready protocol endpoint.

CMD ["python", "-m", "src.server"]
