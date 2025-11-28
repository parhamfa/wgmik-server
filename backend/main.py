from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .api.routes import router as api_router
from .scheduler import ensure_scheduler
from .db import Base, engine
from .settings import settings


app = FastAPI(title="WG Accounting", debug=settings.debug)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _start():
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    ensure_scheduler()


app.include_router(api_router)

# Log exceptions to help diagnose 500s
@app.middleware("http")
async def log_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        # Simple print; uvicorn will also log traceback
        print(f"Unhandled error on {request.url}: {e}")
        raise


