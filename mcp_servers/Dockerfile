# Sidecar / task service — not the Lambda API package
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY calclaim_mcp ./calclaim_mcp

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8765

EXPOSE 8765

CMD ["python", "-m", "calclaim_mcp"]
