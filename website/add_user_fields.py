import sys
import os

# Add parent directory to path so we can import website modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from website.app import app
from website.models import db
from sqlalchemy import text

def add_user_fields():
    """Add profile picture and language preference fields to the users table."""
    try:
        with app.app_context():
            # Add profile_picture column
            db.session.execute(text("ALTER TABLE users ADD COLUMN profile_picture VARCHAR(500);"))
            print("Added profile_picture column to users table")
            
            # Add language_preference column
            db.session.execute(text("ALTER TABLE users ADD COLUMN language_preference VARCHAR(20);"))
            print("Added language_preference column to users table")
            
            # Commit the changes
            db.session.commit()
            print("Database schema updated successfully")
    except Exception as e:
        db.session.rollback()
        print(f"Error updating database schema: {e}")

if __name__ == "__main__":
    add_user_fields() 