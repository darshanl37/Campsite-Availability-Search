import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.app import app, db
from website.models import SearchHistory

if __name__ == "__main__":
    with app.app_context():
        records = SearchHistory.query.filter_by(device_id='test-device').all()
        print(f"Found {len(records)} records with device_id='test-device'")
        for r in records:
            print(f"  {r.id}: {r.park_id} - {r.park_name}")
            
        print("Adding a test record...")
        new_record = SearchHistory(
            device_id='test-simple',
            park_id='777777',
            park_name='Simple Test Campground',
            start_date=date(2025, 7, 1),
            end_date=date(2025, 7, 5),
            nights=4,
            search_preference='all'
        )
        db.session.add(new_record)
        db.session.commit()
        print(f"Added record with ID: {new_record.id}")