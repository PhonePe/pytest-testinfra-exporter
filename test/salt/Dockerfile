FROM python:3.11-slim-bookworm

ARG SALT_VERSION=3008.2
ARG TESTINFRA_VERSION=10.2.2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        build-essential \
        curl \
        git \
        iproute2 \
        libffi-dev \
        libssl-dev \
        mariadb-client \
        postgresql-client \
        procps \
    && python -m pip install --no-cache-dir \
        "salt==${SALT_VERSION}" \
        "pytest-testinfra[salt]==${TESTINFRA_VERSION}" \
        build \
        twine \
    && apt-get purge --yes --auto-remove build-essential libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["salt-master", "--log-level=info"]
