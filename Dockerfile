# syntax=docker/dockerfile:1
FROM python:3.12-slim

# garminconnect requires >=3.12, which sets the floor for this image.
# curl_cffi ships prebuilt manylinux/musllinux wheels for amd64 and arm64, so
# nothing compiles here — including on an Apple Silicon laptop.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GARMINTOKENS=/data/garmin_tokens.json

WORKDIR /app

# Exact pins, resolved and committed. curl_cffi is a binary wheel whose purpose
# is TLS impersonation, so an unpinned rebuild is a real supply-chain exposure
# (docs/SCOPE.md §8).
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY garmin_mcp ./garmin_mcp
RUN pip install --no-cache-dir --no-deps .

# Runs unprivileged. /data is chowned before the volume is declared so a fresh
# named volume inherits the ownership.
RUN useradd --create-home --uid 10001 garmin \
    && mkdir -p /data \
    && chown garmin:garmin /data
USER garmin

VOLUME ["/data"]

# Stamped by CI with the commit so the running container can report which build
# it is (`docker run` never re-pulls a moving tag). Declared here, after the
# expensive layers, so a changing value does not invalidate the pip install.
ARG BUILD_REF=""
ENV GARMIN_MCP_BUILD=${BUILD_REF}

# No EXPOSE, deliberately. The HTTP transport binds 0.0.0.0 inside the container;
# exposure is controlled at the Docker layer and must be loopback-scoped:
#   -p 127.0.0.1:3001:3001    correct
#   -p 3001:3001              serves health data to every network the host joins
ENTRYPOINT ["python", "-m", "garmin_mcp"]
CMD ["serve"]
