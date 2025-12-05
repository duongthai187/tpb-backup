import uvicorn
import os

if __name__ == "__main__":
    # Production configuration
    uvicorn.run(
        "main:app",
        host="0.0.0.0",        # Accept external connections
        port=8443,             # HTTP/HTTPS port
        workers=2,           # REMOVED: Causes issues with in-memory state, SQLite connections
        # SSL Configuration (uncomment if using HTTPS)
        # ssl_keyfile="certs/webhook_key.pem",
        # ssl_certfile="certs/webhook_cert.pem", 
        # ssl_version=ssl.PROTOCOL_TLS,
        reload=False,          # Production mode
        access_log=False,      # Use structured logging instead
        server_header=False,   # Hide server info
        date_header=False,     # Hide date header
        log_level="info"       # Log level
    )