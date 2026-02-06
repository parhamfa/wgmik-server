from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool
from .settings import settings


class Base(DeclarativeBase):
    pass


db_url = settings.database_url
connect_args = {}
engine_kwargs = {}

if db_url.startswith("sqlite:"):
    connect_args = {"check_same_thread": False}
    if ":memory:" in db_url:
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    db_url,
    future=True,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
    **engine_kwargs,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

