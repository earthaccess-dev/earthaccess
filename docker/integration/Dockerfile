FROM quay.io/jupyter/scipy-notebook:2026-11-02@sha256:a8dd44932e91a6fa942c1b9976d0322302bfaf1d8c2f41b0bacc5f775cc94ce6

ARG EARTHACCESS_REPOSITORY=https://github.com/earthaccess-dev/earthaccess.git
ARG EARTHACCESS_COMMIT
ARG UV_VERSION=0.6.9

LABEL org.opencontainers.image.source="https://github.com/earthaccess-dev/earthaccess"
LABEL org.opencontainers.image.revision="${EARTHACCESS_COMMIT}"

ENV EARTHACCESS_COMMIT="${EARTHACCESS_COMMIT}"
ENV EARTHACCESS_REPO_DIR="/home/${NB_USER}/earthaccess"
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

USER ${NB_UID}
WORKDIR /home/${NB_USER}

RUN test -n "${EARTHACCESS_COMMIT}" \
    && git clone --filter=blob:none "${EARTHACCESS_REPOSITORY}" earthaccess \
    && cd earthaccess \
    && git checkout --detach "${EARTHACCESS_COMMIT}" \
    && uv pip install --system --no-cache --all-extras --group test -e . \
    && python -c "import earthaccess; print(earthaccess.__file__, earthaccess.__version__)"

USER ${NB_UID}
WORKDIR /home/${NB_USER}/earthaccess
