# Minimal derivative of python:3.12-slim with strace + the harness's
# Python deps installed. strace is needed inside the container so the
# harness child can be traced from within (Docker's PID/mount
# namespaces hide container syscalls from a host-level strace).
#
# Deps mirror evals/escape/pyproject.toml's runtime requirements; they
# get installed system-wide so we can run as the bind-mounted user
# without venv path mismatches (the host venv's python is a symlink to
# /usr/bin/python3 which doesn't exist in the container image).

FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends strace && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir \
        "pyyaml>=6.0" \
        "jsonschema>=4.0" \
        "httpx>=0.27" \
        "click>=8.1"

ENTRYPOINT []
CMD ["python3", "--version"]
