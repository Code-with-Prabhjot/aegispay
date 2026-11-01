# AegisPay sandbox runner: executes untrusted worker code against buyer tests.
# Run only via DockerExecutionRunner (hardened flags: no network, read-only fs, caps dropped).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir "pytest>=8.0.0" "pytest-asyncio>=0.23.0" \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin sandbox

USER sandbox
WORKDIR /workspace

ENTRYPOINT ["pytest"]
