import uvicorn
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "toto.settings")

if __name__ == "__main__":
    uvicorn.run(
        "toto.asgi:application",
        host="127.0.0.1",
        port=8000,
        reload=True
    )
