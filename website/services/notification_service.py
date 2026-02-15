import os
import json
import logging
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Setup logging
logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self):
        # Twilio configuration
        self.twilio_account_sid = os.environ.get('TWILIO_ACCOUNT_SID')
        self.twilio_auth_token = os.environ.get('TWILIO_AUTH_TOKEN')
        self.twilio_phone_number = os.environ.get('TWILIO_PHONE_NUMBER')
        self.twilio_whatsapp_number = os.environ.get('TWILIO_WHATSAPP_NUMBER')
        
        # Email configuration
        self.smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.environ.get('SMTP_PORT', 587))
        self.smtp_username = os.environ.get('SMTP_USERNAME')
        self.smtp_password = os.environ.get('SMTP_PASSWORD')
        self.sender_email = os.environ.get('SENDER_EMAIL')
        
        # Initialize Twilio client if credentials are available
        self.twilio_client = None
        if self.twilio_account_sid and self.twilio_auth_token:
            self.twilio_client = Client(self.twilio_account_sid, self.twilio_auth_token)
    
    def send_email(self, recipient_email, subject, html_content, text_content=None):
        """Send an email to the specified recipient."""
        if not text_content:
            text_content = html_content.replace('<br>', '\n').replace('<p>', '').replace('</p>', '\n\n')
        
        try:
            # Create message
            message = MIMEMultipart('alternative')
            message['Subject'] = subject
            message['From'] = self.sender_email
            message['To'] = recipient_email
            
            # Attach parts
            part1 = MIMEText(text_content, 'plain')
            part2 = MIMEText(html_content, 'html')
            message.attach(part1)
            message.attach(part2)
            
            # Send the message
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.sender_email, recipient_email, message.as_string())
            
            logger.info(f"Email sent to {recipient_email}")
            return {
                'success': True,
                'message': f"Email sent to {recipient_email}",
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            error_msg = f"Failed to send email to {recipient_email}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def send_sms(self, recipient_phone, message_text):
        """Send an SMS message to the specified phone number."""
        if not self.twilio_client:
            error = "Twilio client not configured"
            logger.error(error)
            return {'success': False, 'error': error}
        
        try:
            message = self.twilio_client.messages.create(
                body=message_text,
                from_=self.twilio_phone_number,
                to=recipient_phone
            )
            
            logger.info(f"SMS sent to {recipient_phone}, SID: {message.sid}")
            return {
                'success': True,
                'message_sid': message.sid,
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except TwilioRestException as e:
            error_msg = f"Failed to send SMS to {recipient_phone}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def send_whatsapp(self, recipient_whatsapp, message_text):
        """Send a WhatsApp message to the specified phone number."""
        if not self.twilio_client:
            error = "Twilio client not configured"
            logger.error(error)
            return {'success': False, 'error': error}
        
        try:
            # Format WhatsApp number with whatsapp: prefix
            whatsapp_to = f"whatsapp:{recipient_whatsapp}"
            whatsapp_from = f"whatsapp:{self.twilio_whatsapp_number}"
            
            message = self.twilio_client.messages.create(
                body=message_text,
                from_=whatsapp_from,
                to=whatsapp_to
            )
            
            logger.info(f"WhatsApp sent to {recipient_whatsapp}, SID: {message.sid}")
            return {
                'success': True,
                'message_sid': message.sid,
                'timestamp': datetime.utcnow().isoformat()
            }
        
        except TwilioRestException as e:
            error_msg = f"Failed to send WhatsApp to {recipient_whatsapp}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': datetime.utcnow().isoformat()
            }
    
    def format_campsite_availability_notification(self, subscription, changes):
        """Format a notification message for campsite availability changes."""
        campground_name = subscription.campground_name or f"Park {subscription.park_id}"

        # Determine booking URL based on provider
        provider = getattr(subscription, 'provider', 'RecreationGov') or 'RecreationGov'
        park_id = subscription.park_id
        # Strip prefix if present
        raw_id = park_id[3:] if park_id.startswith(('rc:', 'rg:')) else park_id

        if provider == 'ReserveCalifornia' or park_id.startswith('rc:'):
            booking_url = "https://www.reservecalifornia.com"
            provider_label = "ReserveCalifornia"
        else:
            booking_url = f"https://www.recreation.gov/camping/campgrounds/{raw_id}"
            provider_label = "Recreation.gov"

        # Format email HTML content
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #4C6EF5;">Campsite Availability Update</h2>
            <p>We've found new availability at <strong>{campground_name}</strong> on {provider_label}!</p>

            <h3 style="margin-top: 20px;">Changes Detected:</h3>
            <ul>
                {''.join([f'<li style="margin-bottom: 10px;">{change}</li>' for change in changes])}
            </ul>

            <p>Search criteria:</p>
            <ul>
                <li>Dates: {subscription.start_date.strftime('%b %d')} - {subscription.end_date.strftime('%b %d, %Y')}</li>
                <li>Consecutive nights: {subscription.nights}</li>
                <li>Search preference: {subscription.search_preference}</li>
            </ul>

            <p style="margin-top: 20px;">
                <a href="{booking_url}"
                   style="background-color: #4C6EF5; color: white; padding: 10px 15px; text-decoration: none; border-radius: 4px;">
                    Book Now on {provider_label}
                </a>
            </p>

            <p style="color: #666; font-size: 12px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px;">
                To stop receiving these notifications,
                <a href="https://your-website.com/notifications/unsubscribe/{subscription.subscription_id}">click here</a>.
            </p>
        </div>
        """

        # Format plain text content for SMS/WhatsApp
        text_content = f"""
Campsite Alert! New availability at {campground_name}:

{chr(10).join([change for change in changes])}

Book now: {booking_url}

Reply STOP to end notifications.
        """.strip()
        
        return {
            'html': html_content,
            'text': text_content,
            'subject': f"New Availability at {campground_name}"
        }
    
    def send_verification_code(self, user, code, verification_type):
        """Send a verification code via the specified channel."""
        if verification_type == 'sms':
            message = f"Your Camping Alert verification code is: {code}. Valid for 10 minutes."
            return self.send_sms(user.phone, message)
        
        elif verification_type == 'whatsapp':
            message = f"Your Camping Alert verification code is: {code}. Valid for 10 minutes."
            return self.send_whatsapp(user.whatsapp, message)
        
        elif verification_type == 'email':
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: #4C6EF5;">Verify Your Email</h2>
                <p>Your verification code is:</p>
                <div style="background-color: #f5f5f5; padding: 15px; font-size: 24px; font-weight: bold; text-align: center; letter-spacing: 5px;">
                    {code}
                </div>
                <p>This code is valid for 10 minutes.</p>
            </div>
            """
            return self.send_email(
                user.email, 
                "Verify Your Camping Alert Email", 
                html_content
            ) 