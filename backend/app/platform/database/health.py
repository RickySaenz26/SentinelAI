from sqlalchemy import text

from app.platform.database.session import get_engine


def is_database_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
