import os
import sys

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.app import app, db
from website.models import SearchHistory, User
from sqlalchemy import inspect

if __name__ == "__main__":
    with app.app_context():
        # Check if the search_history table exists
        inspector = inspect(db.engine)
        print(f"Tables in database: {inspector.get_table_names()}")
        
        # Check users table
        if 'users' in inspector.get_table_names():
            from sqlalchemy import text
            users = db.session.execute(text("SELECT id, email FROM users")).fetchall()
            print(f"\nUsers in database: {len(users)}")
            for user in users:
                print(f"  {user}")
        
        # Check if the search_history table has the correct columns
        if 'search_history' in inspector.get_table_names():
            columns = inspector.get_columns('search_history')
            print("Columns in search_history table:")
            for column in columns:
                print(f"  {column['name']}: {column['type']}")
            
            # Check records in the table
            from sqlalchemy import text
            records = db.session.execute(text("SELECT * FROM search_history")).fetchall()
            print(f"\nRecords in search_history table: {len(records)}")
            for record in records:
                print(f"  {record}")
            
            # Check if device_id query works
            device_records = SearchHistory.query.filter_by(device_id='test-device').all()
            print(f"\nRecords with device_id='test-device': {len(device_records)}")
            for record in device_records:
                print(f"  {record}")
        else:
            print("search_history table does not exist!") 