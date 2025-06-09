# aiosyslogd

**aiosyslogd** is a high-performance, asynchronous Syslog server built with Python's asyncio. It is designed for efficiently receiving, parsing, and storing a large volume of syslog messages.

It features an optional integration with uvloop for a significant performance boost and can write messages to a SQLite database, automatically creating monthly tables and maintaining a Full-Text Search (FTS) index for fast queries.

## Key Features

* **Asynchronous:** Built on asyncio to handle thousands of concurrent messages with minimal overhead.
* **Fast:** Supports uvloop for a C-based event loop implementation, making it one of the fastest ways to run asyncio.
* **SQLite Backend:** Optionally writes all incoming messages to a SQLite database.
* **Automatic Table Management:** Creates new tables for each month (`SystemEventsYYYYMM`) to keep the database organized and fast.
* **Full-Text Search:** Automatically maintains an `FTS5` virtual table for powerful and fast message searching.
* **RFC5424 Conversion:** Includes a utility to convert older *RFC3164* formatted messages to the modern *RFC5424* format.
* **Flexible Configuration:** Configure the server via a simple `aiosyslogd.toml` file.

## Installation

You can install the package directly from its source repository or via pip.

**Standard Installation:**

```console
$ pip install aiosyslogd
```

**For Maximum Performance (with uvloop/winloop):**

To include the performance enhancements, install the speed extra:

```console
$ pip install 'aiosyslogd[speed]'
```

## Quick Start: Running the Server

The package installs a command-line script called aiosyslogd. You can run it directly from your terminal.

```console
$ aiosyslogd
```

On the first run, if an `aiosyslogd.toml` file is not found in the current directory, the server will create one with default settings and then start.

The server will begin listening on 0.0.0.0:5140 and, if enabled in the configuration, create a syslog.db file in the current directory.

## Configuration

The server is configured using a TOML file. By default, it looks for `aiosyslogd.toml` in the current working directory.

#### Default aiosyslogd.toml

If a configuration file is not found, this default version will be created:

```toml
[server]
bind\_ip = "0.0.0.0"
bind\_port = 5140
debug = false
log_dump = false

[sqlite]
enabled = true
database = "syslog.db"
batch_size = 1000
batch_timeout = 5
sql_dump = false
```

#### Custom Configuration Path

You can specify a custom path for the configuration file by setting the `AIOSYSLOGD_CONFIG` environment variable.

```console
export AIOSYSLOGD_CONFIG="/etc/aiosyslogd/config.toml"
$ aiosyslogd
```

When a custom path is provided, the server will **not** create a default file if it's missing and will exit with an error instead.

### Configuration Options

| Section | Key | Description | Default |
| :---- | :---- | :---- | :---- |
| server | bind\_ip | The IP address the server should bind to. | "0.0.0.0" |
| server | bind\_port | The UDP port to listen on. | 5140 |
| server | debug | Set to true to enable verbose logging for parsing and database errors. | false |
| server | log\_dump | Set to true to print every received message to the console. | false |
| sqlite | enabled | Set to true to enable writing to the SQLite database. | true |
| sqlite | database | The path to the SQLite database file. | "syslog.db" |
| sqlite | batch\_size | The number of messages to batch together before writing to the database. | 1000 |
| sqlite | batch\_timeout | The maximum time in seconds to wait before writing an incomplete batch. | 5 |
| sqlite | sql\_dump | Set to true to print the SQL command and parameters before execution. | false |

## Integrating with rsyslog

You can use **rsyslog** as a robust, battle-tested frontend for **aiosyslogd**. This is useful for receiving logs on the standard privileged port (514) and then forwarding them to **aiosyslogd** running as a non-privileged user on a different port.

Here are two common configurations:

### 1\. Forwarding from an Existing rsyslog Instance

If you already have an **rsyslog** server running and simply want to forward all logs to **aiosyslogd**, add the following lines to a new file in `/etc/rsyslog.d/`, such as `99-forward-to-aiosyslogd.conf`. This configuration includes queueing to prevent log loss if **aiosyslogd** is temporarily unavailable.

**File: /etc/rsyslog.d/rsyslog-forward.conf**

```
# This forwards all logs (*) to the server running on localhost:5140
# with queueing enabled for reliability.
$ActionQueueFileName fwdRule1
$ActionQueueMaxDiskSpace 1g
$ActionQueueSaveOnShutdown on
$ActionQueueType LinkedList
$ActionResumeRetryCount -1
*.* @127.0.0.1:5140
```

### 2\. Using rsyslog as a Dedicated Forwarder

If you want rsyslog to listen on the standard syslog port 514/udp and do nothing but forward to aiosyslogd, you can use a minimal configuration like this. This is a common pattern for privilege separation, allowing aiosyslogd to run as a non-root user.

**File: /etc/rsyslog.conf (Minimal Example)**

```
# Minimal rsyslog.conf to listen on port 514 and forward to aiosyslogd

# --- Global Settings ---
$WorkDirectory /var/lib/rsyslog
$FileOwner root
$FileGroup adm
$FileCreateMode 0640
$DirCreateMode 0755
$Umask 0022

# --- Modules ---
# Unload modules we don't need
module(load="immark" mode="off")
module(load="imuxsock" mode="off")
# Load the UDP input module
module(load="imudp")
input(
    type="imudp"
    port="514"
)

# --- Forwarding Rule ---
# Forward all received messages to aiosyslogd
$ActionQueueFileName fwdToAiosyslogd
$ActionQueueMaxDiskSpace 1g
$ActionQueueSaveOnShutdown on
$ActionQueueType LinkedList
$ActionResumeRetryCount -1
*.* @127.0.0.1:5140
```

## Using as a Library

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

    # Start the UDP server endpoint
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: server,
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
    asyncio.run(main())
```

## Contributing

Contributions are welcome\! If you find a bug or have a feature request, please open an issue on the project's repository.

## License

This project is licensed under the **MIT License**.
