import json
import jwt
import requests
import boto3
import hmac
import hashlib
import base64
from django.conf import settings
from botocore.exceptions import ClientError
from jwt.algorithms import RSAAlgorithm

def get_secret_hash(username):
    """
    Calculate the SECRET_HASH required for Cognito authentication
    when an App Client Secret is used.
    """
    msg = username + settings.COGNITO_APP_CLIENT_ID
    dig = hmac.new(
        str(settings.COGNITO_CLIENT_SECRET).encode('utf-8'), 
        msg = str(msg).encode('utf-8'), 
        digestmod=hashlib.sha256
    ).digest()
    d2 = base64.b64encode(dig).decode()
    return d2

_COGNITO_JWKS_CACHE = None

def get_cognito_public_key():
    """Fetch Cognito public keys to verify JWT token (cached)"""
    global _COGNITO_JWKS_CACHE
    if _COGNITO_JWKS_CACHE is not None:
        return _COGNITO_JWKS_CACHE
        
    region = settings.AWS_REGION_NAME
    user_pool_id = settings.COGNITO_USER_POOL_ID
    url = f'https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json'
    try:
        response = requests.get(url, timeout=5)
        _COGNITO_JWKS_CACHE = response.json()
        return _COGNITO_JWKS_CACHE
    except Exception as e:
        print(f"DEBUG: Error fetching JWKS: {e}")
        return {"keys": []}

def decode_jwt_token(token):
    """Decode and verify the JWT token from Cognito"""
    try:
        # Fetch the public keys from Cognito
        jwks = get_cognito_public_key()
        unverified_header = jwt.get_unverified_header(token)

        # print(f"DEBUG: Token Header: {unverified_header}")
        # print(f"DEBUG: Available Keys: {[key['kid'] for key in jwks['keys']]}")

        if unverified_header is None:
            raise Exception("Invalid header. Unable to find unverified header.")

        # Find the key that matches the JWT
        rsa_key = None
        for key in jwks['keys']:
            if key['kid'] == unverified_header['kid']:
                rsa_key = key
                break
        
        # If no matching key was found, raise an error
        if not rsa_key:
            print(f"DEBUG: Key mismatch! Token kid: {unverified_header.get('kid')}, Available kids: {[k.get('kid') for k in jwks['keys']]}")
            raise Exception("Unable to find appropriate key.")

        # Convert JWK to Public Key
        public_key = RSAAlgorithm.from_jwk(json.dumps(rsa_key))

        # Decode and verify the token
        payload = jwt.decode(
            token,
            public_key,
            algorithms=['RS256'],
            audience=settings.COGNITO_APP_CLIENT_ID,
            issuer=f"https://cognito-idp.{settings.AWS_REGION_NAME}.amazonaws.com/{settings.COGNITO_USER_POOL_ID}",
        )

        return payload
    except jwt.ExpiredSignatureError:
        raise Exception("Token has expired.")
    except jwt.InvalidTokenError as e: # Catch-all for PyJWT errors including claims errors
        raise Exception(f"Invalid token: {e}")
    except Exception as e:
        raise Exception(f"Unable to parse token: {e}")

def cognito_login(username, password):
    """
    Authenticate user against Cognito User Pool using boto3.
    Returns dictionary with Tokens (AuthenticationResult) if successful,
    or raises Exception/ClientError on failure.
    """
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )

    try:
        secret_hash = get_secret_hash(username)
        response = client.initiate_auth(
            ClientId=settings.COGNITO_APP_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': username,
                'PASSWORD': password,
                'SECRET_HASH': secret_hash,
            }
        )
        return response.get('AuthenticationResult')
    except ClientError as e:
        # You might want to log the error or re-raise with a friendlier message
        raise e

import time
_COGNITO_USERS_CACHE = None
_COGNITO_USERS_CACHE_TIME = 0

def list_cognito_users():
    """
    List all users from the Cognito User Pool (cached for 5 mins).
    """
    global _COGNITO_USERS_CACHE, _COGNITO_USERS_CACHE_TIME
    if _COGNITO_USERS_CACHE is not None and (time.time() - _COGNITO_USERS_CACHE_TIME < 300):
        return _COGNITO_USERS_CACHE
        
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    try:
        response = client.list_users(
            UserPoolId=settings.COGNITO_USER_POOL_ID
        )
        _COGNITO_USERS_CACHE = response.get('Users', [])
        _COGNITO_USERS_CACHE_TIME = time.time()
        return _COGNITO_USERS_CACHE
    except ClientError as e:
        print(f"DEBUG: Error listing users: {e}")
        return []

_COGNITO_USER_GROUPS_CACHE = {} # username -> {groups: [], time: 0}

def get_user_groups(username):
    """
    List groups for a specialized user. (Cached for 10 mins)
    """
    global _COGNITO_USER_GROUPS_CACHE
    now = time.time()
    if username in _COGNITO_USER_GROUPS_CACHE:
        entry = _COGNITO_USER_GROUPS_CACHE[username]
        if now - entry['time'] < 600:
            return entry['groups']
            
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    try:
        response = client.admin_list_groups_for_user(
            Username=username,
            UserPoolId=settings.COGNITO_USER_POOL_ID
        )
        groups = [g['GroupName'] for g in response.get('Groups', [])]
        _COGNITO_USER_GROUPS_CACHE[username] = {'groups': groups, 'time': now}
        return groups
    except ClientError as e:
        print(f"DEBUG: Error listing groups for user {username}: {e}")
        return []

from concurrent.futures import ThreadPoolExecutor

def get_users_groups_bulk(usernames):
    """
    Fetch groups for multiple users in parallel.
    Returns a dictionary mapping username to list of groups.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_user = {executor.submit(get_user_groups, u): u for u in usernames}
        for future in future_to_user:
            username = future_to_user[future]
            try:
                results[username] = future.result()
            except Exception as e:
                print(f"DEBUG: Error in bulk group fetch for {username}: {e}")
                results[username] = []
    return results

def sign_up_user(username, password, email, name):
    """
    Sign up a new user in Cognito.
    """
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    secret_hash = get_secret_hash(username)
    try:
        response = client.sign_up(
            ClientId=settings.COGNITO_APP_CLIENT_ID,
            SecretHash=secret_hash,
            Username=username,
            Password=password,
            UserAttributes=[
                {'Name': 'email', 'Value': email},
                {'Name': 'given_name', 'Value': name},
            ]
        )
        # Auto-confirm user for demo/quick flow if possible
        # Alternatively, we can use admin_confirm_signup if we have admin perms
        client.admin_confirm_sign_up(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username
        )
        return response
    except ClientError as e:
        print(f"DEBUG: Error in sign_up_user: {e}")
        raise e

def add_user_to_group(username, group_name):
    """
    Add a user to a specific Cognito group.
    """
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    try:
        client.admin_add_user_to_group(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Username=username,
            GroupName=group_name
        )
    except ClientError as e:
        print(f"DEBUG: Error adding user to group {group_name}: {e}")
        raise e

def list_users_in_group(group_name):
    """
    List all users belonging to a specific Cognito group.
    """
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    try:
        response = client.list_users_in_group(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            GroupName=group_name
        )
        users = response.get('Users', [])
        return users
    except ClientError as e:
        if e.response['Error']['Code'] == 'AccessDeniedException':
             print(f"DEBUG: AccessDenied for ListUsersInGroup({group_name}), falling back to scan...")
             # Fallback: List all users and check group for each
             all_users = list_cognito_users()
             users_in_group = []
             for u in all_users:
                 if group_name in get_user_groups(u['Username']):
                     users_in_group.append(u)
             return users_in_group
        print(f"DEBUG: Error listing users in group {group_name}: {e}")
        return []

def get_user_name_by_sub(sub):
    """
    Resolve a user's full name from their Cognito 'sub' (UUID).
    """
    if not sub or sub == 'Unassigned' or sub == 'Pending':
        return sub
        
    client = boto3.client(
        'cognito-idp',
        region_name=settings.AWS_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
    )
    try:
        # Cognito list_users filter: sub is a searchable attribute
        response = client.list_users(
            UserPoolId=settings.COGNITO_USER_POOL_ID,
            Filter=f'sub = "{sub}"'
        )
        users = response.get('Users', [])
        if users:
            attrs = {a['Name']: a['Value'] for a in users[0].get('Attributes', [])}
            given_name = attrs.get('given_name', '')
            family_name = attrs.get('family_name', '')
            if given_name or family_name:
                return f"{given_name} {family_name}".strip()
            return users[0].get('Username', 'Unknown Agent')
        return sub
    except Exception as e:
        print(f"DEBUG: Error resolving name for sub {sub}: {e}")
        return sub
