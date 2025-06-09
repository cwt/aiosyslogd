# **aioSyslog Server**

**aioSyslog Server** is a high-performance, asynchronous Syslog server built with Python's asyncio. It is designed for efficiently receiving, parsing, and storing a large volume of syslog messages.

It features an optional integration with uvloop for a significant performance boost and can write messages to a SQLite database, automatically creating monthly tables and maintaining a Full-Text Search (FTS) index for fast queries.

## **Key Features**

* **Asynchronous:** Built on asyncio to handle thousands of concurrent messages with minimal overhead.  
* **Fast:** Supports uvloop for a C-based event loop implementation, making it one of the fastest ways to run asyncio.  
* **SQLite Backend:** Optionally writes all incoming messages to a SQLite database.  
* **Automatic Table Management:** Creates new tables for each month (SystemEventsYYYYMM) to keep the database organized and fast.  
* **Full-Text Search:** Automatically maintains an FTS5 virtual table for powerful and fast message searching.  
* **RFC5424** to RFC3164 **Conversion:** Includes a utility to convert modern RFC5424 formatted messages to the older, more widely compatible RFC3164 format.  
* **Easy to Deploy:** Can be run directly from the command line with simple environment variable configuration.  
* **Extensible:** Can be used as a library in your own Python applications.

## **Installation**

You can install the package directly from PyPI.

**Standard Installation:**

pip install aiosyslog-server

**For Maximum Performance (with uvloop):**

To include the uvloop performance enhancements, install it as an extra:

pip install 'aiosyslog-server\[uvloop\]'

## **Quick Start: Running the Server**

The package installs a command-line script called aiosyslog-server. You can run it directly from your terminal.

\# Enable SQLite writing and run the server  
export SQL\_WRITE=True  
aiosyslog-server

The server will start listening on 0.0.0.0:5140 and create a syslog.db file in the current directory.

## **Configuration**

The server is configured via environment variables:

| Variable | Description | Default |
| :---- | :---- | :---- |
| SQL\_WRITE | Set to True to enable writing to the syslog.db SQLite database. | False |
| BINDING\_IP | The IP address to bind the server to. | 0.0.0.0 |
| BINDING\_PORT | The UDP port to listen on. | 5140 |
| DEBUG | Set to True to enable verbose logging for parsing and database errors. | False |
| LOG\_DUMP | Set to True to print every received message to the console. | False |
| SQL\_DUMP | Set to True to print the SQL command and parameters before execution. | False |

### **Example rsyslog Configuration**

To forward logs from rsyslog to this server, you can add the following to your rsyslog.conf or as a new file in /etc/rsyslog.d/.

**File: /etc/rsyslog.d/99-forward-to-aiosyslog.conf**

\# This forwards all logs (\*) to the server running on localhost:5140  
\*.\* @127.0.0.1:5140

## **Using as a Library**

You can also import and use the SyslogUDPServer in your own asyncio application.

import asyncio  
from aiosyslog import SyslogUDPServer

async def main():  
    \# The server is configured programmatically or via environment variables  
    \# For library use, it's better to pass config directly.  
    \# This example still relies on env vars for simplicity.  
    server \= SyslogUDPServer(host="0.0.0.0", port=5141)

    loop \= asyncio.get\_running\_loop()  
      
    \# Start the UDP server endpoint  
    transport, protocol \= await loop.create\_datagram\_endpoint(  
        lambda: server,  
        local\_addr=(server.host, server.port)  
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

if \_\_name\_\_ \== "\_\_main\_\_":  
    \# Remember to set SQL\_WRITE=True if you want to save to DB  
    \# os.environ\['SQL\_WRITE'\] \= 'True'  
    asyncio.run(main())

## **Contributing**

Contributions are welcome\! If you find a bug or have a feature request, please open an issue on the GitHub repository.

## **License**

This project is licensed under the **MIT License**. See the LICENSE file for details.