import random
import string
from datetime import datetime
from django.utils.timezone import now

def generate_random_id(prefix="UA", length=5):
    """Generates a random ID with a prefix and fixed digit length."""
    digits = ''.join(random.choices(string.digits, k=length))
    return f"{prefix}{digits}"

def format_iso_now():
    """Returns current timestamp in ISO format."""
    return now().isoformat()

def get_polished_status_message(status):
    """Returns a user-friendly message based on complaint status."""
    messages = {
        'Pending': "Your complaint is still under review. We aim to resolve it within the next 3 business days.",
        'Resolved': "Your complaint has been successfully resolved. Thank you for your patience!",
        'Created': "Your complaint has been successfully registered and is awaiting assignment.",
        'Escalated': "Your complaint has been escalated to a senior supervisor for priority review.",
        'Closed': "This complaint is now closed. Thank you for your feedback."
    }
    return messages.get(status, "We are currently processing your complaint. Please check back later for updates.")

def parse_cognito_groups(id_token_payload):
    """Extracts and returns user groups from Cognito ID token payload."""
    return id_token_payload.get('cognito:groups', [])
