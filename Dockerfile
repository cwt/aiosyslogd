# Define build-time arguments for version management
ARG ALMA_IMAGE=10-kitten-minimal
ARG POETRY_VERSION=2.1.3

# Stage 1: Build Stage
# This stage installs dependencies using Poetry into a virtual environment.
FROM almalinux:${ALMA_IMAGE} as builder

# Re-declare ARG to bring it into the scope of this build stage
ARG POETRY_VERSION

# Set the working directory
WORKDIR /app

# Install poetry, the dependency manager
# We pin the version for consistent builds and use a virtual environment for poetry itself.
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VIRTUALENVS_IN_PROJECT=true
RUN microdnf install -y python3-devel
RUN python3 -m venv $POETRY_HOME && $POETRY_HOME/bin/pip install poetry==${POETRY_VERSION}

# Add poetry to the PATH
ENV PATH="$POETRY_HOME/bin:$PATH"

# Copy all project files into the build context.
# It is recommended to have a .dockerignore file in your project root
# to exclude unnecessary files like .git, __pycache__, etc.
COPY . .

# Install project dependencies and the project itself, excluding the 'dev' group.
# --compile: Compile the dependencies to bytecode for performance.
# --without dev: Exclude development dependencies.
# --extras speed: Include optional dependencies for performance.
RUN poetry install --compile --without dev --extras speed


# Stage 2: Final Runtime Stage
# This stage creates the final, lightweight image for running the application.
FROM almalinux:${ALMA_IMAGE}

# Set a base application directory
WORKDIR /app

# Install only the necessary runtime dependencies, and set up a non-root user.
RUN microdnf install -y python3-libs shadow-utils \
 && useradd --create-home --shell /bin/bash aiosyslogd \
 && microdnf remove -y shadow-utils \
 && microdnf clean all

# Copy the virtual environment (which includes the installed project) from the builder stage
COPY --from=builder --chown=aiosyslogd:aiosyslogd /app/.venv ./.venv

# Copy the application source code itself from the builder stage.
# This ensures the code is found by the .pth file in site-packages.
COPY --from=builder --chown=aiosyslogd:aiosyslogd /app/aiosyslogd ./aiosyslogd/

# Set the PATH to include the virtual environment's bin directory
ENV PATH="/app/.venv/bin:$PATH"

# --- Data Persistence Setup ---
# Create the data directory and set ownership. This is done as root.
RUN mkdir /data && chown aiosyslogd:aiosyslogd /data

# Mark the data directory as a volume to enable mounting and persistence.
VOLUME /data
# --- End Data Persistence Setup ---

# Switch to the non-root user for running the application
USER aiosyslogd

# Set the runtime working directory. The application will look for its
# config and database files here by default.
WORKDIR /data

# Expose the default ports for the syslog server and the web UI.
# This serves as documentation for the user.
EXPOSE 5140/udp
EXPOSE 5141/tcp

# Set the default command to run when the container starts.
# Users can override this on the 'docker run' command line to start the web service.
CMD ["aiosyslogd"]
