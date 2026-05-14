from django.shortcuts import redirect
from functools import wraps
from .cognito_helper import decode_jwt_token

class CognitoUser:
    """A wrapper class to make Cognito payload look like a Django User object."""
    def __init__(self, payload):
        self.payload = payload
        self.is_authenticated = True
        self.is_active = True
        self.is_staff = False
        self.is_superuser = False
        
        # Standard claims
        self.pk = payload.get('sub')
        self.id = payload.get('sub')
        self.username = payload.get('cognito:username') or payload.get('username') or payload.get('sub')
        self.email = payload.get('email', '')
        # Map first_name to sub as requested by user
        self.first_name = payload.get('sub')
        self.last_name = payload.get('family_name', '')
        self.name = payload.get('name') or payload.get('given_name', 'User')
        self.groups = payload.get('cognito:groups', [])
        
        # Helper for common template usage
        self.get_full_name = lambda: f"{self.first_name} {self.last_name}".strip()
        self.get_short_name = lambda: self.first_name

    @property
    def is_anonymous(self):
        return False
        
    def save(self, *args, **kwargs):
        pass # Read-only
        
    def delete(self, *args, **kwargs):
        pass

def cognito_required(view_func):
    """
    Custom decorator to check if the user is authenticated using Cognito ID Token.
    Sets request.user to a CognitoUser instance.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        token = request.session.get('id_token')
        
        if not token:
            print("DEBUG: No id_token in session, redirecting to login")
            return redirect('login')

        try:
            # Decode and verify the JWT token
            decoded_token = decode_jwt_token(token)
            request.user = CognitoUser(decoded_token)
            # print(f"DEBUG: User authenticated: {request.user.username}")
        except Exception as e:
            print(f"DEBUG: Cognito Auth verification failed: {str(e)}")
            # Optional: Clear invalid session
            request.session.flush()
            return redirect('login')

        return view_func(request, *args, **kwargs)

    return _wrapped_view
