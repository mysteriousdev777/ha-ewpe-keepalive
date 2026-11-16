# Home Assistant base image, pinned to a dated tag: Python 3.14.10 on Alpine 3.24.
# Change it deliberately, together with the tests (see the repository README).
ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.14-alpine3.24-2026.10.3
FROM ${BUILD_FROM}

WORKDIR /app
# cryptography and its dependencies: prebuilt wheels only, pinned by hash.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore \
        --only-binary=:all: --require-hashes -r /app/requirements.txt
COPY keepalive /app/keepalive

# The server runs directly under Docker's init (config.yaml: init: true), not under the
# image's S6 overlay: one process, nothing else to supervise.
ENTRYPOINT []
CMD ["python3", "-u", "-m", "keepalive", "--options", "/data/options.json"]
