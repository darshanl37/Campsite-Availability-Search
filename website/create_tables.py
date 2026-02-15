import os
import sys

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.app import app, db
from website.models import SearchHistory

if __name__ == "__main__":
    with app.app_context():
        # Create the search_history table
        SearchHistory.__table__.create(db.engine, checkfirst=True)
        print("Created search_history table") 