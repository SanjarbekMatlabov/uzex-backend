from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL: str = "postgresql://test_avtoraqam_user:7QTW5OHeKf8DdXj02GzgDOwA2ls07OXm@dpg-cvdfhajqf0us73fa0aeg-a/test_avtoraqam"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()