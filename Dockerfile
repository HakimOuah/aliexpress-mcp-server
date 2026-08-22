FROM python:3.11-slim

WORKDIR /app

# Install dependencies first — leverages Docker layer caching when only
# source code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application source
COPY src/ ./src/

# Reference sample for operators troubleshooting on the VPS. The real
# `.env` is injected at runtime via `env_file` in docker-compose.yml —
# never bake it into the image.
COPY .env.example ./

# Live-mode diagnostic tool, invoked via
# `docker exec aliexpress-mcp python /app/scripts/mcp_live_smoke_test.py`.
# Test fixtures and other scripts (ae_oauth.py, smoke_test.py) stay
# out of the image on purpose: they're dev-only.
COPY scripts/mcp_live_smoke_test.py ./scripts/

EXPOSE 8080

# Healthcheck: TCP probe on port 8080. Simpler and more reliable than a
# bare MCP HTTP GET. If the port is listening, the server process is alive.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1', 8080)); s.close()" \
    || exit 1

# Product Factory entrypoint: imports the proven AliExpress MCP tools and
# registers the DataForSEO market-research tools on the same FastMCP server.
CMD ["python", "-m", "src.product_factory_server"]
