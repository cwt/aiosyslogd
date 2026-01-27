
# **aiosyslogd**

[![PyPI Version](https://img.shields.io/pypi/v/aiosyslogd.svg)](https://pypi.org/project/aiosyslogd/)
[![Quay.io Build Status](https://quay.io/repository/cwt/aiosyslogd/status "Quay.io Build Status")](https://quay.io/repository/cwt/aiosyslogd)

**aiosyslogd** is a high-performance, asynchronous Syslog server built with Python's asyncio. It is designed for efficiently receiving, parsing, and storing a large volume of syslog messages.

It features an optional integration with uvloop for a significant performance boost and can write messages to a SQLite database or Meilisearch, automatically creating monthly tables/indexes and maintaining a Full-Text Search (FTS) index for fast queries.

## **Key Features**

* **Asynchronous:** Built on asyncio to handle thousands of concurrent messages with minimal overhead.
* **Fast:** Supports uvloop for a C-based event loop implementation, making it one of the fastest ways to run asyncio.
* **Flexible Database Backends:**
  * **SQLite Backend:** Writes all incoming messages to a SQLite database. For easier maintenance and backup, it creates a separate database file for each month (e.g., syslog_YYYYMM.sqlite3). Each file contains a SystemEvents table and a corresponding SystemEvents_FTS virtual table using FTS5 for powerful full-text search.
  * **Meilisearch Backend:** Optionally stores messages in Meilisearch, a fast and lightweight search engine, with automatic monthly indexes and advanced search capabilities like filtering, sorting, and proximity precision.
* **Automatic Table/Index Management:** Creates new database files (SQLite) or indexes (Meilisearch) for each month to keep the database organized and fast.
* **Full-Text Search:** Automatically maintains an FTS5 virtual table (SystemEvents_FTS) for SQLite or fully indexed Meilisearch backend for powerful and fast message searching.
* **RFC5424 Conversion:** Includes a utility to convert older *RFC3164* formatted messages to the modern *RFC5424* format.
* **Flexible Configuration:** Configure the server via a simple aiosyslogd.toml file.
* **Web UI:** A simple web interface for monitoring and searching logs, accessible via a web browser.
* **Container Support:** Pre-built Docker/Podman images for easy deployment.

## **Running with Containers (Docker / Podman)**

The most convenient way to run **aiosyslogd** is by using the pre-built container images available on [Quay.io](https://quay.io/repository/cwt/aiosyslogd).

### **Image Tags**

The container images are automatically built from the GitHub repository:

* Pushes to the `main` branch will build the `quay.io/cwt/aiosyslogd:latest` image.
* New version tags (e.g., `v0.2.5`) will automatically build a corresponding image (`quay.io/cwt/aiosyslogd:v0.2.5`).

### **Quick Start with Containers**

**1. Pull the Image**

You can pull the latest image using Docker or Podman:

```bash
# Using Docker
docker pull quay.io/cwt/aiosyslogd:latest

# Using Podman
podman pull quay.io/cwt/aiosyslogd:latest
```

**2. Run the Server**

To run the server, you must mount a volume to the /data directory inside the container. This is critical for persisting your configuration and log data.

```bash
# Run the server using Docker
docker run -d \
  --name aiosyslogd-server \
  -p 5140:5140/udp \
  -v /path/to/your/data:/data \
  quay.io/cwt/aiosyslogd:latest

# Run the web UI using Docker
docker run -d \
  --name aiosyslogd-web \
  -p 5141:5141/tcp \
  -v /path/to/your/data:/data,ro \
  quay.io/cwt/aiosyslogd:latest \
    aiosyslogd-web
```

**Note:** Be sure to replace /path/to/your/data with a real path on your host machine (e.g., ~/.aiosyslogd/data).

**Explanation of the command:**

- `-d`: Runs the container in detached mode (in the background).
- `--name aiosyslogd-server` (or `aiosyslogd-web` for the web UI): Assigns a convenient name to your container.
- `-p 5140:5140/udp`: Maps the syslog server port.
- `-p 5141:5141/tcp`: Maps the web server port.
- `-v /path/to/your/data:/data`: (IMPORTANT) Mounts a host directory into the container's data directory, and you should add `,ro` to mount it as a read-only storage for the web UI.

On the first run, the server will not find a configuration file in the mounted /data volume and will create a default aiosyslogd.toml for you there. You can then edit this file on your host machine to re-configure the server and simply restart the container for the changes to take effect.

## **Installation**

You can install the package directly from its source repository or via pip.

**Standard Installation:**

    $ pip install aiosyslogd

**For Maximum Performance (with uvloop/winloop):**

To include the performance enhancements, install the speed extra:

    $ pip install 'aiosyslogd[speed]'

## **Quick Start: Running the Server**

The package installs a command-line script called `aiosyslogd`. You can run it directly from your terminal.

    $ aiosyslogd

On the first run, if an `aiosyslogd.toml` file is not found in the current directory, the server will create one with default settings and then start.

The server will begin listening on `0.0.0.0:5140` and, if enabled in the configuration, create a `syslog.sqlite3` file (SQLite) in the current directory or connect to Meilisearch.

## **Configuration**

The server is configured using a TOML file. By default, it looks for aiosyslogd.toml in the current working directory.

#### **Default aiosyslogd.toml**

If a configuration file is not found, this default version will be created:

```toml
[server]
bind_ip = "0.0.0.0"
bind_port = 5140
debug = false
log_dump = false

[database]
driver = "sqlite"
batch_size = 100
batch_timeout = 5
sql_dump = false

[web_server]
bind_ip = "0.0.0.0"
bind_port = 5141
debug = false
redact = false

[database.sqlite]
database = "syslog.sqlite3"

[database.meilisearch]
url = "http://127.0.0.1:7700"
api_key = ""
```

#### **Custom Configuration Path**

You can specify a custom path for the configuration file by setting the `AIOSYSLOGD_CONFIG` environment variable.

    export AIOSYSLOGD_CONFIG="/etc/aiosyslogd/config.toml"
    $ aiosyslogd

When a custom path is provided, the server will **not** create a default file if it's missing and will exit with an error instead.

### **Configuration Options**

| Section              | Key           | Description                                                              | Default                 |
| :------------------- | :------------ | :----------------------------------------------------------------------- | :---------------------- |
| server               | bind_ip       | The IP address the server should bind to.                                | "0.0.0.0"               |
| server               | bind_port     | The UDP port to listen on.                                               | 5140                    |
| server               | debug         | Set to true to enable verbose logging for parsing and database errors.   | false                   |
| server               | log_dump      | Set to true to print every received message to the console.              | false                   |
| database             | driver        | The database backend to use ("sqlite" or "meilisearch").                 | "sqlite"                |
| database             | batch_size    | The number of messages to batch together before writing to the database. | 100                     |
| database             | batch_timeout | The maximum time in seconds to wait before writing an incomplete batch.  | 5                       |
| database             | sql_dump      | Set to true to print the SQLite command and parameters before execution. | false                   |
| database.sqlite      | database      | The path to the SQLite database file.                                    | "syslog.sqlite3"        |
| database.meilisearch | url           | The URL of the Meilisearch instance.                                     | "http://127.0.0.1:7700" |
| database.meilisearch | api_key       | The API key for Meilisearch (optional).                                  | ""                      |
| web_server           | bind_ip       | The IP address the web server should bind to.                            | "0.0.0.0"               |
| web_server           | bind_port     | The TCP port the web server should listen on.                            | 5141                    |
| web_server           | debug         | Set to true to enable verbose logging for the web server.                | false                   |
| web_server           | redact        | Set to true to redact the sensitive information (user, IP, MAC)          | false                   |
| web_server           | users_file    | The path to the JSON file for storing user credentials.                  | "users.json"            |

**Note:** when sql_dump is enabled, log_dump will be disabled.

### **Web Interface Authentication**

The web interface now includes user authentication to protect access to logs.

#### **First-time Setup**
On the first run, `aiosyslogd-web` will create a `users.json` file in the same directory as your `aiosyslogd.toml` file. This file will contain a default admin user with the following credentials:
- **Username**: `admin`
- **Password**: `admin`

You will be required to log in with these credentials to access the web interface. It is highly recommended to change the default password after your first login.

#### **User Roles**
There are two user roles:
- **Admin**: Can view logs, manage users (add, edit, delete), and change their own password.
- **User**: Can view logs and change their own password.

#### **Managing Users (Admins only)**
Admins can access the "Users" page from the navigation bar to:
- **Add new users**: Provide a username, password, and specify if the user should be an admin.
- **Edit existing users**: Change a user's password, admin status, and enable/disable their account.
- **Delete users**: Remove a user from the system.

#### **Changing Your Password**
All users can change their own password by clicking on their username in the navigation bar and selecting "Profile".

### **Performance Tuning: Finding the Optimal batch_size**

The `database.batch_size` setting is critical for performance. It controls how many log messages are grouped together before being written to the database.

* A **larger batch_size** can be more efficient for the database but increases the risk of dropping logs under heavy load. If the database write takes too long, the server's incoming network buffer can overflow, causing the operating system to discard new messages.
* A **smaller batch_size** results in quicker but more frequent database writes, reducing the risk of buffer overflow but potentially increasing I/O overhead.

The optimal `batch_size` depends heavily on your hardware (CPU, disk speed like NVMe vs. HDD) and network conditions. A `batch_size` of 100 is set as a safe default, but you can likely increase this for better performance.

#### **Using the Log Generation Tool**

A benchmarking script is included at scripts/loggen.py to help you find the best setting for your system.

1. Set `server.debug = true` then start aiosyslogd:

       $ aiosyslogd

2. Run the benchmark:
   Open another terminal and run `scripts/loggen.py` to send a large number of messages. For example, to send 100,000 logs:

       $ python scripts/loggen.py -n 100000

   The script will output the number of messages sent.

4. Check the Server Logs:
   When the **aiosyslogd** server writes a batch, it logs the number of messages written.

       [2025-06-18 19:30:00] [12345] [DEBUG] Successfully wrote 100 logs to 'syslog_202506.sqlite3'.

   Sum up the number of logs written by the server and compare it to the number sent by `loggen.py`. If they match, your `batch_size` is good.

5. Tune and Repeat:
   If you see dropped logs (server received < 100,000), your `batch_size` is too high. If all logs are received, you can try increasing the `batch_size` in your `aiosyslogd.toml` file and run the test again. This allows you to find the highest value your specific hardware can handle without dropping packets.

## **Running as a Daemon with Auto-Startup**

You can run **aiosyslogd** as a system service that automatically starts after server reboot. This section describes multiple approaches including Podman Quadlet (easiest), root-less systemd services, and traditional system services.

### **Podman Quadlet Setup (Easiest Method)**

The easiest way to run **aiosyslogd** as a daemon is using Podman Quadlet, which allows you to define container services using simple configuration files.

#### **1. Install Podman**

Make sure Podman is installed on your system:

```bash
# On Ubuntu/Debian
sudo apt install podman

# On RHEL/CentOS/Fedora
sudo dnf install podman
```

#### **2. Enable Podman Socket**

Enable the Podman socket for root and/or user services:

```bash
# For root services
sudo systemctl enable --now podman.socket

# For user services (if running as non-root)
systemctl --user enable --now podman.socket
```

#### **3. Create Quadlet Files**

Create the Quadlet unit files in the appropriate directory:

For user services: `~/.config/containers/systemd/`
For system services: `/etc/containers/systemd/`

**File: ~/.config/containers/systemd/aiosyslogd.container (for user services) or /etc/containers/systemd/aiosyslogd.container (for system services)**

```
[Unit]
Description=aiosyslogd - Asynchronous Syslog Server
After=network.target
Wants=network.target

[Container]
Image=quay.io/cwt/aiosyslogd:latest
Volume=%h/.aiosyslogd:/data
Network=slirp4netns
PublishPort=5140:5140/udp
Environment=AIOSYSLOGD_CONFIG=/data/aiosyslogd.toml
UserNS=keep-id

[Install]
WantedBy=default.target
```

**File: ~/.config/containers/systemd/aiosyslogd-web.container (for user services) or /etc/containers/systemd/aiosyslogd-web.container (for system services)**

```
[Unit]
Description=aiosyslogd-web - Web Interface for aiosyslogd
After=network.target
Wants=network.target
BindsTo=aiosyslogd.container

[Container]
Image=quay.io/cwt/aiosyslogd:latest
Volume=%h/.aiosyslogd:/data
Network=slirp4netns
PublishPort=5141:5141/tcp
Environment=AIOSYSLOGD_CONFIG=/data/aiosyslogd.toml
UserNS=keep-id
Exec=aiosyslogd-web

[Install]
WantedBy=default.target
```

#### **4. Create Configuration Directory**

Create a directory for your configuration files:

```bash
mkdir -p ~/.aiosyslogd
cd ~/.aiosyslogd
```

Copy the default configuration to this directory:

```bash
# If you have the source code, copy the default config from the config directory
cp /path/to/aiosyslogd/config/default-aiosyslogd.toml ~/.aiosyslogd/aiosyslogd.toml

# Or create it manually with your preferred editor
```

#### **5. Start and Enable Services**

Enable lingering for your user to allow services to start at boot even when not logged in (for user services):

```bash
sudo loginctl enable-linger $USER
```

Start and enable the services:

```bash
# For user services
systemctl --user daemon-reload
systemctl --user enable aiosyslogd
systemctl --user enable aiosyslogd-web
systemctl --user start aiosyslogd
systemctl --user start aiosyslogd-web

# For system services
sudo systemctl daemon-reload
sudo systemctl enable aiosyslogd
sudo systemctl enable aiosyslogd-web
sudo systemctl start aiosyslogd
sudo systemctl start aiosyslogd-web
```

Check the status of the services:

```bash
# For user services
systemctl --user status aiosyslogd
systemctl --user status aiosyslogd-web

# For system services
sudo systemctl status aiosyslogd
sudo systemctl status aiosyslogd-web
```

View logs for debugging:

```bash
# For user services
journalctl --user-unit aiosyslogd -f
journalctl --user-unit aiosyslogd-web -f

# For system services
sudo journalctl -u aiosyslogd -f
sudo journalctl -u aiosyslogd-web -f
```

### **Root-less Service Setup (Python Installation)**

If you prefer to run **aiosyslogd** directly from a Python installation rather than containers, you can use systemd user services:

#### **1. Install aiosyslogd**

First, install aiosyslogd using pip:

```bash
# Install globally for all users
sudo pip install aiosyslogd

# Or install for a specific user
pip install --user aiosyslogd
```

#### **2. Create Configuration Directory**

Create a directory for your configuration files:

```bash
mkdir -p ~/.aiosyslogd
cd ~/.aiosyslogd
```

Copy the default configuration to this directory:

```bash
# If you have the source code, copy the default config from the config directory
cp /path/to/aiosyslogd/config/default-aiosyslogd.toml ~/.aiosyslogd/aiosyslogd.toml

# Or create it manually with your preferred editor
```

#### **3. Enable User Services**

Enable lingering for your user to allow services to start at boot even when not logged in:

```bash
sudo loginctl enable-linger $USER
```

#### **4. Create Service Files**

Create the systemd service files in `~/.config/systemd/user/`:

```bash
mkdir -p ~/.config/systemd/user/
```

Create `~/.config/systemd/user/aiosyslogd.service`:

```
[Unit]
Description=aiosyslogd - Asynchronous Syslog Server
After=network.target
Wants=network.target

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/usr/local/bin/aiosyslogd
WorkingDirectory=%h/.aiosyslogd
Environment=AIOSYSLOGD_CONFIG=%h/.aiosyslogd/aiosyslogd.toml
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

Create `~/.config/systemd/user/aiosyslogd-web.service`:

```
[Unit]
Description=aiosyslogd-web - Web Interface for aiosyslogd
After=network.target
Wants=network.target
Requires=aiosyslogd.service

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/usr/local/bin/aiosyslogd-web
WorkingDirectory=%h/.aiosyslogd
Environment=AIOSYSLOGD_CONFIG=%h/.aiosyslogd/aiosyslogd.toml
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
```

#### **5. Start and Enable Services**

Reload the systemd daemon and start the services:

```bash
systemctl --user daemon-reload
systemctl --user enable aiosyslogd.service
systemctl --user enable aiosyslogd-web.service
systemctl --user start aiosyslogd.service
systemctl --user start aiosyslogd-web.service
```

Check the status of the services:

```bash
systemctl --user status aiosyslogd.service
systemctl --user status aiosyslogd-web.service
```

View logs for debugging:

```bash
journalctl --user-unit aiosyslogd.service -f
journalctl --user-unit aiosyslogd-web.service -f
```

### **Traditional System Service Setup (Python Installation)**

If you need to run aiosyslogd as a system-wide service from a Python installation, create the following files in `/etc/systemd/system/`:

**File: /etc/systemd/system/aiosyslogd.service**

```
[Unit]
Description=aiosyslogd - Asynchronous Syslog Server
After=network.target
Wants=network.target

[Service]
Type=simple
Restart=always
RestartSec=5
User=aiosyslogd
Group=aiosyslogd
ExecStart=/usr/local/bin/aiosyslogd
WorkingDirectory=/var/lib/aiosyslogd
Environment=AIOSYSLOGD_CONFIG=/etc/aiosyslogd/aiosyslogd.toml
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**File: /etc/systemd/system/aiosyslogd-web.service**

```
[Unit]
Description=aiosyslogd-web - Web Interface for aiosyslogd
After=network.target
Wants=network.target
Requires=aiosyslogd.service

[Service]
Type=simple
Restart=always
RestartSec=5
User=aiosyslogd
Group=aiosyslogd
ExecStart=/usr/local/bin/aiosyslogd-web
WorkingDirectory=/var/lib/aiosyslogd
Environment=AIOSYSLOGD_CONFIG=/etc/aiosyslogd/aiosyslogd.toml
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then create the user and setup directories:

```bash
# Create system user
sudo useradd -r -s /bin/false aiosyslogd

# Create directories
sudo mkdir -p /var/lib/aiosyslogd
sudo chown aiosyslogd:aiosyslogd /var/lib/aiosyslogd

# Copy configuration from the config directory
sudo cp /path/to/aiosyslogd/config/default-aiosyslogd.toml /etc/aiosyslogd/aiosyslogd.toml
sudo chown aiosyslogd:aiosyslogd /etc/aiosyslogd/aiosyslogd.toml
```

Finally, enable and start the services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable aiosyslogd.service
sudo systemctl enable aiosyslogd-web.service
sudo systemctl start aiosyslogd.service
sudo systemctl start aiosyslogd-web.service
```

## **Integrating with rsyslog**

You can use **rsyslog** as a robust, battle-tested frontend for **aiosyslogd**. This is useful for receiving logs on the standard privileged port (514) and then forwarding them to **aiosyslogd** running as a non-privileged user on a different port.

Here are two common configurations:

### **1. Forwarding from an Existing rsyslog Instance**

If you already have an **rsyslog** server running and simply want to forward all logs to **aiosyslogd**, add the following lines to a new file in /etc/rsyslog.d/, such as 99-forward-to-aiosyslogd.conf. This configuration includes queueing to prevent log loss if **aiosyslogd** is temporarily unavailable.

**File: /etc/rsyslog.d/99-forward-to-aiosyslogd.conf (or use the template from config/rsyslog-forward.conf)**

```
$ActionQueueFileName fwdRule1 # unique name prefix for spool files
$ActionQueueMaxDiskSpace 1g   # 1gb space limit (use as much as possible)
$ActionQueueSaveOnShutdown on # save messages to disk on shutdown
$ActionQueueType LinkedList   # run asynchronously
$ActionResumeRetryCount -1    # infinite retries if host is down
*.* @127.0.0.1:5140
```

You can copy this configuration from the project's config directory:

```bash
sudo cp /path/to/aiosyslogd/config/rsyslog-forward.conf /etc/rsyslog.d/99-forward-to-aiosyslogd.conf
sudo systemctl restart rsyslog
```

### **2. Using rsyslog as a Dedicated Forwarder**

If you want rsyslog to listen on the standard syslog port 514/udp and do nothing but forward to aiosyslogd, you can use a minimal configuration like this. This is a common pattern for privilege separation, allowing aiosyslogd to run as a non-root user.

**File: /etc/rsyslog.conf (Minimal Example from config/rsyslog-minimal.conf)**

```
$WorkDirectory /var/lib/rsyslog

$FileOwner root
$FileGroup adm
$FileCreateMode 0640
$DirCreateMode 0755
$Umask 0022

module(load="immark")
module(load="imuxsock")
module(load="imudp")
input(
	type="imudp"
	port="514"
)

$ActionQueueFileName fwdRule1
$ActionQueueMaxDiskSpace 1g
$ActionQueueSaveOnShutdown on
$ActionQueueType LinkedList
$ActionResumeRetryCount -1
*.* @127.0.0.1:5140
```

You can copy this configuration from the project's config directory:

```bash
sudo cp /path/to/aiosyslogd/config/rsyslog-minimal.conf /etc/rsyslog.conf
sudo systemctl restart rsyslog
```

## **Nginx Reverse Proxy Setup**

For production deployments, it's recommended to put aiosyslogd-web behind a reverse proxy like nginx. This provides benefits like SSL termination, static file serving, and improved security.

### **Basic Nginx Configuration**

Create a configuration file in `/etc/nginx/sites-available/aiosyslogd`:

```
server {
    listen 80;
    server_name your-domain.com;  # Change this to your domain

    # SSL configuration (recommended for production)
    # listen 443 ssl;
    # ssl_certificate /path/to/your/certificate.crt;
    # ssl_certificate_key /path/to/your/private.key;

    location / {
        proxy_pass http://127.0.0.1:5141;  # aiosyslogd-web default port
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support if needed
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    # Optional: Basic security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header X-Content-Type-Options "nosniff" always;
}
```

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/aiosyslogd /etc/nginx/sites-enabled/
sudo nginx -t  # Test configuration
sudo systemctl reload nginx
```

### **SSL/HTTPS Configuration**

For secure access, configure SSL certificates. If you're using Let's Encrypt:

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

This will automatically update your nginx configuration with SSL settings.

## **Using as a Library**

You can also import and use the SyslogUDPServer in your own asyncio application.

```python
import asyncio
from aiosyslogd.server import SyslogUDPServer

async def main():
    # The server is configured via aiosyslogd.toml by default.
    # To configure programmatically, you would need to modify the
    # server class or bypass the config-loading mechanism.
    server = await SyslogUDPServer.create(host="0.0.0.0", port=5141)

    loop = asyncio.get_running_loop()

    # Define the protocol factory as a simple function
    def server_protocol_factory():
        return server

    # Start the UDP server endpoint
    transport, protocol = await loop.create_datagram_endpoint(
        server_protocol_factory,
        local_addr=(server.host, server.port)
    )

    print("Custom server running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("Shutting down custom server.")
        transport.close()
        await server.shutdown()

if __name__ == "__main__":
    asyncio.run(main)
```

## **Contributing**

Contributions are welcome! If you find a bug or have a feature request, please open an issue on the project's repository.

## **License**

This project is licensed under the [**MIT License**](https://hg.sr.ht/~cwt/aiosyslogd/browse/LICENSE?rev=tip).
