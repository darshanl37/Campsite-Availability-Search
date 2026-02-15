import os
import sys

# Add the parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.app import app, db
from website.models import SearchHistory

if __name__ == "__main__":
    with app.app_context():
        # Get history for device_id='test-device'
        history_records = SearchHistory.query.filter_by(device_id='test-device').all()
        print(f"Found {len(history_records)} records for device_id='test-device'")
        
        # Print each record
        for record in history_records:
            print(f"ID: {record.id}, Park ID: {record.park_id}, Name: {record.park_name}")
            
        # Try with a different device ID
        other_records = SearchHistory.query.filter_by(device_id='other-device').all()
        print(f"Found {len(other_records)} records for device_id='other-device'")
        
        # Insert a test record
        test_record = SearchHistory(
            device_id='test-insert-device',
            park_id='999999',
            park_name='Test Insert Campground',
            start_date='2025-08-01',
            end_date='2025-08-05',
            nights=4,
            search_preference='all'
        )
        db.session.add(test_record)
        db.session.commit()
        print(f"Inserted new record with ID: {test_record.id}")
        
        # Verify insertion
        inserted_records = SearchHistory.query.filter_by(device_id='test-insert-device').all()
        print(f"Found {len(inserted_records)} records for device_id='test-insert-device'") 