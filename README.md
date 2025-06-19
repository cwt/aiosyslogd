
# **aiosyslogd**

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

**Note:** when sql_dump is enabled, log_dump will be disabled.

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

## **Integrating with rsyslog**

You can use **rsyslog** as a robust, battle-tested frontend for **aiosyslogd**. This is useful for receiving logs on the standard privileged port (514) and then forwarding them to **aiosyslogd** running as a non-privileged user on a different port.

Here are two common configurations:

### **1. Forwarding from an Existing rsyslog Instance**

If you already have an **rsyslog** server running and simply want to forward all logs to **aiosyslogd**, add the following lines to a new file in /etc/rsyslog.d/, such as 99-forward-to-aiosyslogd.conf. This configuration includes queueing to prevent log loss if **aiosyslogd** is temporarily unavailable.

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

### **2. Using rsyslog as a Dedicated Forwarder**

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
