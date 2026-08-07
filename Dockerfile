# Long-running collector + timed Liquipedia scrape, for ECS Fargate.
#
# Built for ARM64 (Graviton) — ~20% cheaper than x86 and nothing here is
# architecture-sensitive. Build for another platform with --platform.
#
#   docker build -t mr-mouse-stats .
#   docker run --rm -e MR_MOUSE_STATS_DB=... mr-mouse-stats

FROM python:3.12-slim AS build

# uv gives us the same resolution as local development, from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:0.9.29 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first: this layer only changes when the lockfile does.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Then the project itself. Package data (migrations/*.sql, templates) is
# included by hatchling — a naive "copy the .py files" build would break
# both apply_migrations() and the Jinja site renderer.
COPY mr_mouse_stats ./mr_mouse_stats
COPY README.md ./
RUN uv sync --locked --no-dev


FROM python:3.12-slim

# Non-root: the task needs no privileges, and only makes outbound
# connections (Twitch IRC, Liquipedia, Postgres).
RUN useradd --create-home --uid 10001 collector
WORKDIR /app

COPY --from=build --chown=collector:collector /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Logs are timestamped with localtime; without this they disagree with
    # everything else in the system.
    TZ=UTC

USER collector

# The collector reconnects internally; ECS restarts the task if the process
# dies. SIGTERM is handled in service.py so in-flight captures are drained.
STOPSIGNAL SIGTERM

ENTRYPOINT ["mr-mouse-stats"]
CMD ["serve"]
