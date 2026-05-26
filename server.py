import uvicorn

from app.config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        workers=1 if settings.reload else 2,
        reload=settings.reload,
        access_log=False,
        server_header=False,
        date_header=False,
        log_level=settings.log_level.lower(),
    )
