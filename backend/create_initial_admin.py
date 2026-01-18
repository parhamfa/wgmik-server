from backend.db import SessionLocal
from backend.models import User
from backend.auth import get_password_hash
import sys

def create_initial_admin(username="admin", password="password"):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            print(f"User '{username}' already exists.")
            return

        print(f"Creating initial admin user '{username}'...")
        hashed_password = get_password_hash(password)
        new_user = User(username=username, hashed_password=hashed_password, is_admin=True)
        db.add(new_user)
        db.commit()
        print("Done.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 2:
        create_initial_admin(sys.argv[1], sys.argv[2])
    else:
        create_initial_admin()
