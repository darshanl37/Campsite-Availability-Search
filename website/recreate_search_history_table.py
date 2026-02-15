import os
import sys

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.app import app, db
from website.models import SearchHistory
from sqlalchemy import inspect, text

if __name__ == "__main__":
    with app.app_context():
        inspector = inspect(db.engine)
        
        # Drop the table if it exists
        if 'search_history' in inspector.get_table_names():
            print("Dropping search_history table...")
            db.session.execute(text('DROP TABLE search_history'))
            db.session.commit()
            print("Table dropped successfully.")
        
        # Create the table with the correct schema
        print("Recreating search_history table with updated schema...")
        SearchHistory.__table__.create(db.engine)
        print("Search history table created successfully with the correct schema.") 