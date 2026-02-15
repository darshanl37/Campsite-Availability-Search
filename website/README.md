# Camping Reservation Website

A web application that helps users find and book available campsites on Recreation.gov, with the added ability to get notifications when availability changes.

## Features

* 🏕 Search for campgrounds within 200 miles of any US city
* 🔔 Get notified when campsite availability changes
* 📱 Receive notifications via email, SMS, or WhatsApp
* 💳 Free tier (5 notifications) and paid tier ($1 lifetime access)
* 🔐 User authentication with email or Google login
* 🔄 Customizable check frequency for subscriptions

## Installation

### Prerequisites

* Python 3.12+
* Flask and other dependencies (see requirements.txt)
* Google Maps API key
* Recreation.gov API key
* Twilio account (for SMS and WhatsApp notifications)
* SMTP server access (for email notifications)
* Stripe account (for payment processing)
* Google OAuth credentials (for Google login)

### Environment Variables

Copy the `.env.example` file to `.env` and fill in your API keys and configuration:

```
MAPS_API_KEY=your_google_maps_api_key
RECREATION_API_KEY=your_recreation_gov_api_key
SECRET_KEY=your_secure_random_secret_key
DATABASE_URI=sqlite:///camping.db
CAMPING_SCRIPT_DIR=/path/to/Camping_Reservation_python_script/
CAMPING_SCRIPT_NAME=camping_notification.py
VENV_PYTHON=/path/to/python

# Stripe Integration
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_WEBHOOK_SECRET=your_stripe_webhook_secret
STRIPE_PRICE_ID=your_stripe_price_id

# Twilio Integration
TWILIO_ACCOUNT_SID=your_twilio_account_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=your_twilio_phone_number
TWILIO_WHATSAPP_NUMBER=your_twilio_whatsapp_number

# Email Configuration
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your_email_username
SMTP_PASSWORD=your_email_password
SENDER_EMAIL=your_sender_email

# Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret

# Venmo
VENMO_USERNAME=your_venmo_username
```

### Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Initialize the database:

```bash
python -c "from app import app, db; app.app_context().push(); db.create_all()"
```

3. Start the server:

```bash
./manage.sh start
```

## Directory Structure

- `app.py`: Main Flask application
- `models.py`: Database models for users, subscriptions, notifications, etc.
- `routes/`: Route handlers for different features
  - `auth_routes.py`: Authentication routes (login, signup, etc.)
  - `subscription_routes.py`: Subscription management routes
  - `payment_routes.py`: Payment processing routes
- `services/`: Business logic services
  - `auth_service.py`: Authentication service
  - `notification_service.py`: Notification service for sending emails, SMS, etc.
  - `payment_service.py`: Payment processing service
  - `subscription_service.py`: Subscription management service
- `static/`: Static assets (CSS, JavaScript, images)
- `templates/`: HTML templates
  - `auth/`: Authentication templates
  - `payment/`: Payment templates
  - `subscription/`: Subscription templates
- `tmp/`: Temporary files for background processes
- `logs/`: Log files

## Background Processes

This application uses background processes to continuously check for campsite availability. These processes are managed by the `subscription_service.py` module, which:

1. Starts a new process for each active subscription
2. Monitors the process output for changes in availability
3. Sends notifications to users when changes are detected
4. Automatically restarts processes if they fail

## Management Script

The `manage.sh` script provides several commands for managing the application:

- `./manage.sh start`: Start the server
- `./manage.sh stop`: Stop the server
- `./manage.sh restart`: Restart the server
- `./manage.sh status`: Check server status
- `./manage.sh logs`: View logs

## Payment Processing

The application supports two payment methods:

1. Credit card payments via Stripe
2. Venmo payments (manual verification)

Users can upgrade from the free tier (5 notifications) to the paid tier ($1 lifetime access) using either method.

## Notification Channels

The application supports multiple notification channels:

1. Email: Requires a verified email address
2. SMS: Requires a verified phone number
3. WhatsApp: Requires a verified WhatsApp number

Users can configure their notification preferences in their profile settings. 