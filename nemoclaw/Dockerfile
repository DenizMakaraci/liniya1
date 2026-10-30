# Custom sandbox image for `nemoclaw onboard --from nemoclaw/Dockerfile` (Hermes Agent harness).
# Liniya is installed as a local tool package; state lives in /sandbox (the only rw path
# besides /tmp under OpenShell's filesystem policy).
FROM python:3.12-slim

WORKDIR /sandbox/callpilot
COPY pyproject.toml ./
COPY src ./src
COPY rules ./rules
COPY knowledge ./knowledge
COPY company.yaml ./company.yaml
RUN pip install --no-cache-dir . && mkdir -p /sandbox/callpilot/data

ENV CALLPILOT_DATA_DIR=/sandbox/callpilot/data \
    CALLPILOT_RULES_DIR=/sandbox/callpilot/rules \
    CALLPILOT_KNOWLEDGE_DIR=/sandbox/callpilot/knowledge \
    CALLPILOT_COMPANY_FILE=/sandbox/callpilot/company.yaml \
    # Placeholder: OpenShell rewrites it with the real key at the egress boundary.
    NEBIUS_API_KEY=openshell-placeholder \
    NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/

EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/compliance/rules')" || exit 1
CMD ["callpilot", "serve", "--host", "0.0.0.0", "--port", "8000"]
