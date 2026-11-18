ARG BASE
FROM ${BASE}
USER root
COPY fixture_persona.yaml /app/docs/contracts/personas/fixture_persona.yaml
COPY rendered/fixture_persona.txt /app/docs/contracts/personas/rendered/fixture_persona.txt
COPY inference-provider.yaml /app/docs/contracts/inference-provider.yaml
USER 10001:10001
