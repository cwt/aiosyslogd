# Define build-time arguments for version management
ARG ALMA_IMAGE=9-minimal
ARG POETRY_VERSION=2.2.1

# Stage 1: Build Stage
# This stage installs dependencies using Poetry into a virtual environment.
FROM almalinux:${ALMA_IMAGE} AS builder

# Re-declare ARG to bring it into the scope of this build stage
ARG POETRY_VERSION

# Set the working directory
WORKDIR /app

# Install poetry, the dependency manager
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
ENV PATH="$POETRY_HOME/bin:$PATH"

RUN microdnf install -y python3.12-devel \
 && python3.12 -m venv $POETRY_HOME \
 && $POETRY_HOME/bin/pip install --no-cache-dir poetry==${POETRY_VERSION} \
 && microdnf clean all

# Copy poetry dependency manifests first for build caching
COPY pyproject.toml poetry.lock ./

# Install project dependencies only (excluding dev and root project)
RUN poetry install --no-root --compile --without dev --extras speed --extras gemini

# Copy all project files into the build context
COPY . .

# Install the project itself
RUN poetry install --compile --without dev --extras speed --extras gemini


# Stage 2: Final Runtime Stage
# This stage creates the final, lightweight image for running the application.
FROM almalinux:${ALMA_IMAGE}

# Ensure stdout and stderr streams are unbuffered for log viewing
ENV PYTHONUNBUFFERED=1

# Set a base application directory
WORKDIR /app

# Install runtime dependencies and set up a dedicated non-root user with fixed UID/GID
RUN microdnf install -y python3.12-libs shadow-utils \
 && groupadd -g 10001 aiosyslogd \
 && useradd -u 10001 -g 10001 --create-home --shell /bin/bash aiosyslogd \
 && microdnf remove -y shadow-utils \
 && microdnf clean all

# Copy the virtual environment from the builder stage
COPY --from=builder --chown=aiosyslogd:aiosyslogd /app/.venv ./.venv

# Copy the application source code from the builder stage
COPY --from=builder --chown=aiosyslogd:aiosyslogd /app/aiosyslogd ./aiosyslogd/

# Set the PATH to include the virtual environment's bin directory
ENV PATH="/app/.venv/bin:$PATH"

# --- Data Persistence Setup ---
RUN mkdir /data && chown aiosyslogd:aiosyslogd /data
VOLUME /data
# --- End Data Persistence Setup ---

# Switch to the non-root user
USER aiosyslogd

# Runtime working directory
WORKDIR /data

# Expose ports for syslog (UDP) and web UI (TCP)
EXPOSE 5140/udp
EXPOSE 5141/tcp

# Handle graceful shutdown
STOPSIGNAL SIGTERM

# Default command
CMD ["aiosyslogd"]
