FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV HOME=/tmp
COPY docker/persona/requirements-persona.txt /tmp/requirements-persona.txt
RUN --mount=type=secret,id=build_ca,required=false \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && pip install --no-cache-dir --require-hashes --no-deps --only-binary :all: -r /tmp/requirements-persona.txt \
    && rm /tmp/requirements-persona.txt
COPY scripts/__init__.py /app/scripts/__init__.py
COPY scripts/run_persona.py /app/scripts/run_persona.py
COPY scripts/llm/__init__.py /app/scripts/llm/__init__.py
COPY scripts/llm/litellm_cold_start.py /app/scripts/llm/litellm_cold_start.py
COPY scripts/llm/litellm_transport.py /app/scripts/llm/litellm_transport.py
COPY scripts/llm/litellm_wire.py /app/scripts/llm/litellm_wire.py
COPY scripts/llm/tier_selection.py /app/scripts/llm/tier_selection.py
COPY src/__init__.py /app/src/__init__.py
COPY src/persona_backends/ /app/src/persona_backends/
COPY docs/contracts/personas/ /app/docs/contracts/personas/
COPY docs/contracts/inference-provider.yaml /app/docs/contracts/inference-provider.yaml
COPY docs/contracts/project-configuration.yaml /app/docs/contracts/project-configuration.yaml
USER 10001:10001
WORKDIR /app
ENTRYPOINT ["python", "-I", "/app/scripts/run_persona.py"]
LABEL org.opencontainers.image.source="https://github.com/otl-labs/ontheloop"
