import requests
import os
from typing import List, Tuple
import tempfile
import json
import random
import string
from django.shortcuts import render, redirect, get_object_or_404
from .forms import ComplaintForm
from .models import Complaint, ComplaintAction
# from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
import boto3
from boto3.dynamodb.conditions import Key, Attr
from django.views.decorators.http import require_POST  
from datetime import datetime
from django.http import JsonResponse
from .comprehend_client import generate_intent_tags, extract_insurance_details, generate_complaint_subject, generate_ai_resolution, extract_transaction_id, extract_transaction_amount
from operator import itemgetter
import logging
import uuid
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt  # use only if you can't send CSRF token
from botocore.client import Config
from .upload_files import upload_files  # Adjust the import path if necessary
# from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from datetime import datetime
import json, re, requests, uuid
from django.conf import settings
from .cognito_helper import decode_jwt_token, cognito_login, list_cognito_users, get_user_groups, sign_up_user, add_user_to_group, get_users_groups_bulk, list_users_in_group, get_user_name_by_sub
from .decorators import cognito_required 
from .invokeai import callAI, callAIForIntent
from fuzzywuzzy import fuzz
from operator import itemgetter
from datetime import timedelta
from django.utils.timezone import now
import uuid
from datetime import datetime
import boto3
from django.shortcuts import redirect
# from django.contrib.auth.decorators import login_required
from django.utils.timezone import now
from .models import Complaint
from .forms import ComplaintForm
import re
from django.core.paginator import Paginator, EmptyPage
from django.views.decorators.csrf import csrf_exempt
from django.core.files.storage import default_storage

# AWS Settings from settings.py
aws_access_key_id = settings.AWS_ACCESS_KEY_ID
aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_REGION_NAME

API_GATEWAY_URL = 'https://410zih9dbh.execute-api.us-east-1.amazonaws.com/dev/register_complaint'  # Replace with your actual URL
API_GATEWAY_INSURANCE_URL = 'https://onpspfqtbj.execute-api.us-east-1.amazonaws.com/default/verifyInsuranceClaim'
API_GATEWAY_TRANSACTION_URL = 'https://wmjdmqwfbk.execute-api.us-east-1.amazonaws.com/default/transactionLogic' # Placeholder URL

# Initialize DynamoDB resource
dynamodb = boto3.resource(
    'dynamodb',
    region_name=AWS_REGION,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)
complaints_table = dynamodb.Table('Complaints')
actions_table = dynamodb.Table('ComplaintActions') 
policy_table=dynamodb.Table('PolicyDetails')
transaction_table = dynamodb.Table('TransactionsTable')
reassignment_requests_table = dynamodb.Table('ReassignmentRequests')


transcribe_client = boto3.client(
    'transcribe', 
    region_name=AWS_REGION,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)
s3_client = boto3.client(
    's3', 
    region_name=AWS_REGION,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)

def generate_s3_presigned_url(bucket_name, object_key, expiration=3600):
    try:
        response = s3_client.generate_presigned_url('get_object',
                                                    Params={'Bucket': bucket_name,
                                                            'Key': object_key},
                                                    ExpiresIn=expiration)
        return response
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None

import google.generativeai as genai

# Configure Gemini once at import using settings
genai.configure(api_key=settings.GOOGLE_API_KEY)

# Choose a model suitable for audio understanding
MODEL_NAME = "gemini-1.5-flash"

@csrf_exempt  # if you want CSRF, use a token; for quick test, exempt
def transcribe_view(request):
    if request.method != 'POST':
        return HttpResponseBadRequest("Only POST is allowed.")

    if 'audio' not in request.FILES:
        return HttpResponseBadRequest("Missing 'audio' file.")

    audio_file = request.FILES['audio']
    audio_bytes = audio_file.read()

    # Infer MIME type from upload; default to common WebM if unknown
    mime_type = getattr(audio_file, 'content_type', None) or 'audio/webm'

    try:
        model = genai.GenerativeModel(MODEL_NAME)

        # Send audio inline as a "part"
        # You can provide a prompt to steer transcription behavior (speaker labels, language, etc.)
        response = model.generate_content([
            {"mime_type": mime_type, "data": audio_bytes},
            # Prompt engineering to ask clearly for a transcript
            "Transcribe the audio verbatim. If language detection is needed, handle it automatically. Return only the transcript text."
        ])

        # The SDK returns text in response.text for simple outputs
        transcript = (response.text or "").strip()

        # Fallback in case text is empty (rare)
        if not transcript and hasattr(response, 'candidates'):
            # Try to pull from structured candidates
            transcript = response.candidates[0].content.parts[0].text

        return JsonResponse({"transcript": transcript})
    except Exception as e:
        # Log on server and return message to client
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def start_transcription(request):
    if request.method == 'POST' and request.FILES.get('audio'):
        # 1. Get the uploaded audio file
        audio_file = request.FILES['audio']

        # 2. Save the file to S3 bucket
        file_name = f'audio/{audio_file.name}'
        s3_client.upload_fileobj(audio_file, 'complaint-attachments-tcs', file_name)

        # 3. Start the transcription job
        job_name = f"transcription-job-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        media_url = f"s3://complaint-attachments-tcs/{file_name}"

        try:
            response = transcribe_client.start_transcription_job(
                TranscriptionJobName=job_name,
                Media={'MediaFileUri': media_url},
                MediaFormat='wav',  # Adjust this according to the file format
                LanguageCode='en-US',  # Adjust this if needed
                OutputBucketName='complaint-attachments-tcs'
            )
            return JsonResponse({'success': True, 'message': 'Transcription job started', 'job_name': job_name})

        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Error starting transcription: {str(e)}'}, status=500)

    return JsonResponse({'success': False, 'message': 'No audio file found.'}, status=400)

# Function to get the status of the transcription job
def check_transcription_status(request, job_name):
    try:
        response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        job_status = response['TranscriptionJob']['TranscriptionJobStatus']

        if job_status == 'COMPLETED':
            # If the transcription is complete, get the URL of the transcript
            transcript_url = response['TranscriptionJob']['Transcript']['TranscriptFileUri']

            # Download the transcript file (it's a JSON object containing the text)
            transcript_data = s3_client.get_object(Bucket='complaint-attachments-tcs', Key=transcript_url.split('complaint-attachments-tcs/')[1])
            transcript_json = json.loads(transcript_data['Body'].read().decode('utf-8'))

            # Extract the transcription text
            transcription_text = transcript_json['results']['transcripts'][0]['transcript']

            return JsonResponse({'success': True, 'transcription': transcription_text})
        else:
            return JsonResponse({'success': False, 'message': 'Transcription job still in progress'})

    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Error retrieving transcription status: {str(e)}'})


@cognito_required
def close_and_register_new_complaint(request, old_complaint_id):
    # Close the old complaint
    try:
        # Update the status of the old complaint to "Closed"
        complaints_table.update_item(
            Key={'ComplaintId': old_complaint_id},
            UpdateExpression="SET #status = :closed",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={':closed': 'Closed'}
        )
        def generate_action_id():
            prefix = "UA"
            number = ''.join(random.choices(string.digits, k=5))  # Random 9-digit number
            return f"{prefix}{number}"
        # Insert the action for closing the old complaint
        action_id = generate_action_id()  # Auto-generate ActionId
        action_created_at = now().isoformat()  # Get current timestamp
        action_description = "Complaint closed by user"
        action_type = "System Process"
        status = "Closed"
        user_id = request.user.first_name # This is now the 'sub' UUID
        print(f"User ID (sub): {user_id}")
        
        actions_table.put_item(
            Item={
                'ActionId': action_id,
                'ComplaintId': old_complaint_id,
                'ActionCreatedAt': action_created_at,
                'ActionDescription': action_description,
                'ActionType': action_type,
                'Status': status,
                'UserId': user_id
            }
        )
        
        print(f"Old complaint {old_complaint_id} closed successfully.")

    except Exception as e:
        print(f"Error closing old complaint {old_complaint_id}: {str(e)}")
        return redirect('error_page')  # Redirect to an error page if failure

    # Register the new complaint using the existing logic
    new_complaint = ComplaintForm(request.POST)
    if new_complaint.is_valid():
        # Use the same complaint registration logic to register a new complaint
        description = new_complaint.cleaned_data['description']  # Get description from form
        agreement_no = generate_agreement_no()  # Generate a new Agreement Number

        complaint_data = {
            'Agreement_no': agreement_no,
            'Description': description,
            'Status': 'Created',
            'Created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'UserName': request.user.first_name, # This is now the 'sub' UUID
            'Tags': store_tags(request),  # Assuming you're using tags
        }

        # Save new complaint data to the Complaints table
        complaints_table.put_item(
            Item=complaint_data
        )

@cognito_required
def close_complaint(request, old_complaint_id):
    """
    Closes a complaint and redirects the user.
    """
    try:
        # Update the status of the complaint to "Closed"
        complaints_table.update_item(
             Key={'ComplaintId': old_complaint_id},
            UpdateExpression="SET #status = :closed",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={':closed': 'Closed'}
        )
        # Create an action logging this closure
        action_id = ''.join(random.choices(string.digits, k=5))
        actions_table.put_item(
            Item={
                'ActionId': f"UA{action_id}",
                'ComplaintId': old_complaint_id,
                'ActionCreatedAt': datetime.now().isoformat(),
                'ActionDescription': "Complaint closed by user via AI Resolution",
                'ActionType': "System Process",
                'Status': "Closed",
                'UserId': request.user.first_name # 'sub' UUID
            }
        )
        print(f"Complaint {old_complaint_id} closed successfully.")
        
    except Exception as e:
        print(f"Error closing complaint {old_complaint_id}: {str(e)}")
        # You might want to handle this error more gracefully
        
    return redirect('user_home')

    # If form is invalid, redirect back to the registration page
    return redirect('track_complaint_detail', complaint_id=old_complaint_id)
    #return redirect('register_complaint')  # Redirect to complaint registration page if form is invalid



@cognito_required
def escalate_complaint(request, complaint_id):
    # Generate a unique ActionId (using UUID in this example)
    action_id = str(uuid.uuid4())

    # Get the current timestamp
    action_created_at = now().isoformat()  # Current time in ISO format

    # Action description and other details
    action_description = "Complaint escalated by user"
    action_type = "System Process"
    status = "Escalated"
    user_id = request.user.first_name  # Assuming you use username as user ID

    # Insert the new action record and update main table
    try:
        # Update main table status
        complaints_table.update_item(
            Key={'ComplaintId': complaint_id},
            UpdateExpression="SET #s = :s",
            ExpressionAttributeNames={'#s': 'Status'},
            ExpressionAttributeValues={':s': 'Escalated'}
        )
        
        response = actions_table.put_item(
            Item={
                'ActionId': action_id,
                'ComplaintId': complaint_id,  # The complaint being escalated
                'ActionCreatedAt': action_created_at,
                'ActionDescription': action_description,
                'ActionType': action_type,
                'Status': status,
                'UserId': user_id
            }
        )
        print("Escalation action recorded successfully:", response)
    except Exception as e:
        print("Error inserting escalation action into DynamoDB:", str(e))

    # After creating the entry, you can redirect to the complaint tracking page or wherever needed
    return redirect('track_complaint_detail', complaint_id=complaint_id)

# Function to extract Complaint ID from description using regex
def get_agreement_id(description):
    match = re.search(r'\bMF\d{9}\b', description)  # Assuming MF followed by 9 digits
    if match:
        return match.group(0)  # Return the matched Complaint ID
    return None  # If no match is found


# Function to check if the complaint is a duplicate
def is_duplicate(new_complaint, existing_complaint):
    similarity_score = fuzz.ratio(new_complaint, existing_complaint)
    return similarity_score > 80  # Consider it a duplicate if similarity is over 80%

# Function to check if the complaint is a status inquiry
def is_status_inquiry(description):
    status_keywords = ['status', 'update', 'pending', 'approved', 'resolved', 'where is my', 'when will']
    return any(keyword in description.lower() for keyword in status_keywords)

# Function to check if the complaint is new (not a duplicate or status inquiry)
def check_complaint_type(description, currentUser):
    # First, check if it's a duplicate complaint
    duplicate_complaint = check_for_duplicate_complaints(currentUser, description)
    if duplicate_complaint:
        return 'duplicate', duplicate_complaint
    
    # Then, check if it's a status inquiry
    if is_status_inquiry(description):
        return 'status_inquiry', None
    
    # If it's neither, it's considered a new complaint
    return 'new_complaint', None

# Function to query DynamoDB for duplicate complaints by the same customer
def check_for_duplicate_complaints(username, complaint_description):
    response = complaints_table.scan(
        FilterExpression="UserName = :username",
        ExpressionAttributeValues={
            ":username": username  # Filter by the ComplaintId
        }
    )
    for complaint in response['Items']:
        # Ignore complaints that are already closed or resolved
        status = complaint.get('Status', '').lower()
        if status in ['closed', 'resolved']:
            continue
            
        if is_duplicate(complaint_description, complaint['Description']):
            return complaint  # Return the existing duplicate complaint
    return None

def get_complaint_status(customer_id, complaint):
    response = complaints_table.scan(
        FilterExpression="AgreementNo = :complaint",
        ExpressionAttributeValues={
            ":complaint": complaint  # Filter by the ComplaintId
        }
    ) 
    print(response)
    if response['Items']:
        return response['Items'][0].get('Status', 'Status not found.')
    return 'Complaint not found.'

# Function to mark a complaint as closed in DynamoDB
@cognito_required
def close_complaint(request, old_complaint_id):
    try:
        # Update the status of the old complaint to "Closed"
        complaints_table.update_item(
            Key={'ComplaintId': old_complaint_id},
            UpdateExpression="SET #status = :closed",
            ExpressionAttributeNames={'#status': 'Status'},
            ExpressionAttributeValues={':closed': 'Closed'}
        )
        def generate_action_id():
            prefix = "UA"
            number = ''.join(random.choices(string.digits, k=5))  # Random 9-digit number
            return f"{prefix}{number}"
        # Insert the action for closing the old complaint
        action_id = generate_action_id()  # Auto-generate ActionId
        action_created_at = now().isoformat()  # Get current timestamp
        action_description = "Complaint closed by user"
        action_type = "System Process"
        status = "Closed"
        user_id = request.user.first_name 
        print(user_id) # Current logged-in user
        
        actions_table.put_item(
            Item={
                'ActionId': action_id,
                'ComplaintId': old_complaint_id,
                'ActionCreatedAt': action_created_at,
                'ActionDescription': action_description,
                'ActionType': action_type,
                'Status': status,
                'UserId': user_id
            }
        )
        
        print(f"Old complaint {old_complaint_id} closed successfully.")

    except Exception as e:
        print(f"Error closing old complaint {old_complaint_id}: {str(e)}")
        return redirect('error_page')  # Redirect to an error page if failure
    return redirect('track_complaint_detail', complaint_id=old_complaint_id)

def generate_polished_response(status):
    # Use AI (Claude/Bedrock) to create a more polished response based on the status
    # For simplicity, we'll return a basic example response
    if status == 'Pending':
        return "Your complaint is still under review. We aim to resolve it within the next 3 business days."
    elif status == 'Resolved':
        return "Your complaint has been successfully resolved. Thank you for your patience!"
    else:
        return "We are currently processing your complaint. Please check back later for updates."

def extract_complaint_id_from_entities(entities):
    for entity in entities:
        if entity['Type'] == 'COMPLAINT_ID':  # Assuming the entity type is 'COMPLAINT_ID'
            return entity['Text']
    return None

# Landing page (Complaints home)
def complaints_home(request):
    return render(request, 'complaints/user_home.html', {
        'show_section': 'landing',  # Default section
    })

@cognito_required
def user_home(request):
    username = request.user.first_name # This is now the 'sub' UUID
    print(f"User ID (sub): {username}")

    # Fetch the most recent 4 complaints from DynamoDB
    response1 = complaints_table.scan(
        FilterExpression="UserName = :username",
        ExpressionAttributeValues={":username": username}
    )
    
    complaints = response1.get('Items', [])

    # Sort complaints by 'CreatedAt' field in descending order (most recent first)
    complaints.sort(key=lambda x: x['CreatedAt'], reverse=True)

    # Only get the first 4 complaints (recent 4 complaints)
    recent_complaints = complaints[:]
    comp=recent_complaints[:3]
    # Format dates
    for complaint in recent_complaints:
        # CreatedAt
        created_at = complaint.get('CreatedAt')
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace('Z', ''))
                complaint['CreatedAt'] = dt.strftime('%d-%b-%y')
            except:
                pass
        
        # Last Updated (updated_at for template)
        raw_updated = complaint.get('LastUpdatedTimestamp') or complaint.get('UpdatedAt') or created_at
        if raw_updated:
            try:
                dt_up = datetime.fromisoformat(raw_updated.replace('Z', ''))
                complaint['updated_at'] = dt_up.strftime('%d-%b-%y')
            except:
                complaint['updated_at'] = raw_updated

    # Additional counts for status categories (can be omitted if not needed)
    open_pending_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['pending'])
    in_progress_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['in progress', 'user created case'])
    closed_resolved_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['closed', 'resolved'])

    # Pass the data to the template
    return render(request, 'complaints/user_home.html', {
        'complaints': recent_complaints,
        '3comp':comp,
        'total_complaints': len(recent_complaints),
        'open_pending_count': open_pending_count,
        'in_progress_count': in_progress_count,
        'closed_resolved_count': closed_resolved_count,
        'first_name':request.user.first_name
    })


def login_page(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        print(f"DEBUG: Attempting login for user: {username}")
        
        try:
            auth_result = cognito_login(username, password)
            print("DEBUG: Cognito login successful")
            # Store tokens in session
            # request.session['access_token'] = auth_result['AccessToken']  # Too large for cookie
            request.session['id_token'] = auth_result['IdToken']
            # request.session['refresh_token'] = auth_result['RefreshToken']
            request.session.modified = True
            
            # Decode ID token to get user role
            id_token_payload = decode_jwt_token(auth_result['IdToken'])
            groups = id_token_payload.get('cognito:groups', [])
            
            print(f"DEBUG: User groups: {groups}")
            
            if 'ADMIN' in groups:
                print("DEBUG: Admin detected, redirecting to admin dashboard")
                return redirect('admin_dashboard')
            elif 'SUPERVISOR' in groups:
                print("DEBUG: Supervisor detected, redirecting to supervisor dashboard")
                return redirect('supervisor_dashboard')
            
            # Check for other groups (Agents)
            # If user has groups other than 'USER', treating them as Agent
            non_customer_groups = [g for g in groups if g != 'USER']
            if non_customer_groups:
                print(f"DEBUG: Agent detected (Groups: {non_customer_groups}), redirecting to agent dashboard")
                return redirect('agent_dashboard')
            
            next_url = request.GET.get('next', 'user_home')
            if next_url == '/logout/': # Prevent loop
                next_url = 'user_home'
                
            print(f"DEBUG: Redirecting to {next_url}")
            return redirect(next_url)
        except Exception as e:
            print(f"DEBUG: Cognito login failed: {str(e)}")
            # In a real app, parse 'e' to give better error messages (e.g. "Incorrect password")
            error_message = str(e)
            if "NotAuthorizedException" in error_message:
                error_message = "Invalid username or password."
            elif "UserNotConfirmedException" in error_message:
                error_message = "User is not confirmed. Please check your email."
                
            return render(request, 'complaints/login.html', {'error': error_message})
            
    success_message = request.GET.get('message')
    return render(request, 'complaints/login.html', {'success_message': success_message})

def register_page(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        name = request.POST.get('name')
        password = request.POST.get('password')
        
        # Use email as username for simplicity in this flow
        username = email
        
        try:
            print(f"DEBUG: Attempting registration for user: {username}")
            sign_up_user(username, password, email, name)
            
            # Add to CUSTOMER group (must exist in Cognito)
            try:
                add_user_to_group(username, 'CUSTOMER')
            except Exception as group_err:
                print(f"DEBUG: Error adding user to group: {group_err}")
                # We can continue if group assignment fails, or handle it
            
            print("DEBUG: Registration successful")
            return redirect('/login/?message=Registration successful! Please log in.')
        except Exception as e:
            print(f"DEBUG: Registration failed: {str(e)}")
            error_message = str(e)
            if 'UsernameExistsException' in error_message:
                error_message = 'User already exists with this email.'
            elif 'InvalidPasswordException' in error_message:
                error_message = 'Password does not meet requirements.'
                
            return render(request, 'complaints/register.html', {'error': error_message})
            
    return render(request, 'complaints/register.html')

@cognito_required
def admin_dashboard(request):
    # Security check: ensure user is ADMIN
    id_token = request.session.get('id_token')
    if id_token:
        try:
            payload = decode_jwt_token(id_token)
            groups = payload.get('cognito:groups', [])
            if 'ADMIN' not in groups:
                 return redirect('user_home')
        except:
            return redirect('login')
    else:
        return redirect('login')

    # Fetch all complaints for stats (with simple caching for performance)
    from django.core.cache import cache
    all_complaints = cache.get('all_complaints_cache')
    if not all_complaints:
        all_complaints_resp = complaints_table.scan()
        all_complaints = all_complaints_resp.get('Items', [])
        cache.set('all_complaints_cache', all_complaints, 30) # Cache for 30 seconds
    
    total_open = 0
    unassigned = 0
    urgent_complaints_all = []
    
    for c in all_complaints:
        status = c.get('Status')
        if status not in ['Closed', 'Resolved']:
            total_open += 1
            if not c.get('AssignedTo'):
                unassigned += 1
            if status in ['Escalated', 'High Priority']:
                urgent_complaints_all.append(c)
    
    urgent_complaints = urgent_complaints_all[:4]
    
    # Format urgent complaints for table
    for comp in urgent_complaints:
        # Mocking time elapsed for now
        comp['TimeElapsed'] = "4h 12m" 
        # Mocking channel
        comp['Channel'] = comp.get('Channel', 'Email')
    
    # Channel Distribution Mock (In real app, calculate from all_complaints)
    channel_dist = {
        'Email': 45,
        'Phone': 30,
        'WebChat': 15,
        'Social': 10
    }
    
    # Group Performance Mock
    group_perf = [
        {'name': 'Tech Support', 'tier': 'Tier 1 & 2', 'open': 156, 'avg_resp': '12m', 'capacity': 85, 'status': 'On Track', 'color': 'primary', 'icon': 'build'},
        {'name': 'Billing Dept', 'tier': 'Finance & Refunds', 'open': 89, 'avg_resp': '4h', 'capacity': 92, 'status': 'At Risk', 'color': 'orange', 'icon': 'payments'},
        {'name': 'Enterprise', 'tier': 'VIP Accounts', 'open': 12, 'avg_resp': '5m', 'capacity': 40, 'status': 'On Track', 'color': 'purple', 'icon': 'domain'},
        {'name': 'Returns', 'tier': 'Logistics', 'open': 234, 'avg_resp': '24h+', 'capacity': 95, 'status': 'Breached', 'color': 'red', 'icon': 'keyboard_return'},
    ]

    print(f"DEBUG: admin_dashboard context - total_open: {total_open}, unassigned: {unassigned}")

    # KPIs and other dynamic data
    context = {
        'total_open_val': total_open,
        'unassigned_count': unassigned,
        'avg_resolution_time': "4h 15m", # Mocked
        'csat_score': 4.8, # Mocked
        'urgent_complaints': urgent_complaints,
        'today_date_str': datetime.now().strftime('%B %d, %Y'),
        'channel_dist': channel_dist,
        'group_perf': group_perf,
        'user_name': payload.get('given_name', payload.get('name', 'Admin')),
        'user_role': 'Administrator',
        'user_team': 'IT Management'
    }
    
    return render(request, 'complaints/admin_dashboard.html', context)

@cognito_required
def manage_users(request):
    # Security check: ensure user is ADMIN or SUPERVISOR
    id_token = request.session.get('id_token')
    user_groups = []
    
    if id_token:
        try:
            payload = decode_jwt_token(id_token)
            user_groups = payload.get('cognito:groups', [])
            if 'ADMIN' not in user_groups and 'SUPERVISOR' not in user_groups:
                 return redirect('user_home')
        except:
            return redirect('login')
    else:
        return redirect('login')

    # Fetch all complaints from DynamoDB to calculate stats
    all_complaints = complaints_table.scan().get('Items', [])
    
    # Fetch users from Cognito and fetch their groups in parallel
    cognito_users = list_cognito_users()
    all_user_groups = get_users_groups_bulk([u['Username'] for u in cognito_users])
    
    # Filter for Supervisor: Define Teams and Check Access
    is_admin = 'ADMIN' in user_groups
    is_supervisor = 'SUPERVISOR' in user_groups
    supervisor_teams = []
    
    if is_supervisor and not is_admin:
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        supervisor_teams = [g for g in user_groups if g in SUPPORT_GROUPS]

    formatted_users = []
    
    for u in cognito_users:
        attrs = {attr['Name']: attr['Value'] for attr in u.get('Attributes', [])}
        username = u.get('Username')
        
        # Get groups for Role (using pre-fetched bulk groups)
        target_user_groups = all_user_groups.get(username, [])
        
        # FILTERING LOGIC:
        # If Supervisor (and not Admin), specific checks:
        if is_supervisor and not is_admin:
            # 1. Must be in one of the supervisor's teams
            in_team = any(g in supervisor_teams for g in target_user_groups)
            if not in_team:
                continue
            # 2. Filter out other Supervisors/Admins?
            # Assuming Supervisor manages only 'Agents' (users without higher privs)
            if 'ADMIN' in target_user_groups or 'SUPERVISOR' in target_user_groups:
                continue

        email = attrs.get('email', 'N/A')
        name = attrs.get('given_name', username)
        
        # Link complaints to agent (Checking both username and email)
        user_complaints = [c for c in all_complaints if c.get('AssignedTo') == email or c.get('AssignedTo') == username]
        
        open_tickets = [c for c in user_complaints if c.get('Status') not in ['Closed', 'Resolved']]
        
        # Calculate SLA Stats (Mocking logic based on Priority and Status)
        sla_on_track = 0
        sla_at_risk = 0
        sla_breached = 0
        
        active_tickets_list = []
        for c in open_tickets:
            priority = c.get('Priority', 'Normal')
            status = c.get('Status', 'Open')
            
            # Simple SLA mock: High priority is "At Risk", Breached if Escalated
            sla_status = "On Track"
            if priority == "High": sla_status = "At Risk"
            if status == "Escalated": sla_status = "Breached"
            
            if sla_status == "On Track": sla_on_track += 1
            elif sla_status == "At Risk": sla_at_risk += 1
            else: sla_breached += 1
            
            # Only keep top 5 for the modal list display
            if len(active_tickets_list) < 5:
                # Format time
                created_date = c.get('Created_at') or c.get('createdAt', 'N/A')
                if created_date != 'N/A':
                    try:
                        dt = datetime.fromisoformat(created_date)
                        created_display = dt.strftime('%b %d, %Y')
                    except:
                        created_display = created_date
                else:
                    created_display = 'N/A'

                active_tickets_list.append({
                    'id': str(c.get('Agreement_no') or c.get('ComplaintId') or '#T-000'),
                    'complaint_id': c.get('ComplaintId'),
                    'subject': c.get('Description', 'No description')[:40] + '...',
                    'priority': priority,
                    'status': status,
                    'sla_status': sla_status,
                    'created': created_display
                })

        # Capacity Logic
        max_capacity = 20 # Default setting
        capacity_pct = int((len(open_tickets) / max_capacity) * 100) if max_capacity > 0 else 0

        role = "Agent"
        if "ADMIN" in target_user_groups:
            role = "Admin"
        elif "SUPERVISOR" in target_user_groups:
            role = "Supervisor"
            
        formatted_users.append({
            'username': username,
            'email': email,
            'name': name,
            'status': u.get('UserStatus', 'Unknown'),
            'enabled': u.get('Enabled', False),
            'created': u.get('UserCreateDate'),
            'role': role,
            'groups': target_user_groups,
            'stats': {
                'total': len(user_complaints),
                'open': len(open_tickets),
                'on_track': sla_on_track,
                'at_risk': sla_at_risk,
                'breached': sla_breached,
                'capacity_pct': min(capacity_pct, 100),
                'max_capacity': max_capacity
            },
            'active_tickets': active_tickets_list
        })
        
    current_name = payload.get('given_name', payload.get('name', 'Staff'))
    current_role = 'Administrator' if 'ADMIN' in user_groups else ('Supervisor' if 'SUPERVISOR' in user_groups else 'Agent')
    
    # Calculate Team Name for the current user
    SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
    current_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
    current_team_display = ", ".join([t.replace('_', ' ').title() for t in current_teams]) if current_teams else "General"

    context = {
        'users': formatted_users,
        'user_count': len(formatted_users),
        'user_name': current_name,
        'user_role': current_role,
        'user_team': current_team_display
    }
    return render(request, 'complaints/manage_user.html', context)

@cognito_required
def all_complaints_view(request):
    id_token = request.session.get('id_token')
    try:
        payload = decode_jwt_token(id_token)
    except:
        return redirect('login')
    
    user_groups = payload.get('cognito:groups', [])
    
    is_admin = 'ADMIN' in user_groups
    is_supervisor = 'SUPERVISOR' in user_groups
    
    if not is_admin and not is_supervisor:
        return redirect('user_home')
        
    all_complaints = complaints_table.scan().get('Items', [])
    display_complaints = []
    
    if is_admin:
        display_complaints = all_complaints
    elif is_supervisor:
        # Supervisor filtering logic
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        supervisor_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
        
        # Parallel fetch users/groups to identify ID's
        cognito_users = list_cognito_users()
        all_user_groups = get_users_groups_bulk([u['Username'] for u in cognito_users])
        
        agent_identifiers = set() 
        for u in cognito_users:
            username = u['Username']
            u_gs = all_user_groups.get(username, [])
            if any(g in supervisor_teams for g in u_gs):
                attrs = {attr['Name']: attr['Value'] for attr in u.get('Attributes', [])}
                if attrs.get('email'): agent_identifiers.add(attrs.get('email'))
                if username: agent_identifiers.add(username)
        
        display_complaints = [c for c in all_complaints if c.get('AssignedTo') in agent_identifiers]
    
    # Process for template (stats and formatting)
    processed_complaints = []
    stats = {'total': len(display_complaints), 'open': 0, 'resolved': 0, 'escalated': 0}
    priority_tickets = []
    
    for c in display_complaints:
        status = c.get('Status', 'Open')
        priority = c.get('Priority', 'Normal')
        
        c['ticket_id'] = c.get('Agreement_no') or c.get('ComplaintId') or '#T-000'
        
        # Date Formatting
        created_date = c.get('Created_at') or c.get('createdAt', 'N/A')
        if created_date != 'N/A':
            try:
                # Simple relative time calculation (mock/approx)
                dt = datetime.fromisoformat(created_date)
                c['created_display'] = dt.strftime('%b %d, %Y')
            except:
                c['created_display'] = created_date
        else:
            c['created_display'] = 'N/A'

        if status not in ['Closed', 'Resolved']:
            stats['open'] += 1
            if priority in ['High', 'Critical'] or status == 'Escalated':
                if status == 'Escalated': stats['escalated'] += 1
                priority_tickets.append(c)
        else:
            stats['resolved'] += 1
            
        processed_complaints.append(c)
        
    # Sort: Priority tickets first for the list, or Date Descending
    processed_complaints.sort(key=lambda x: x.get('Created_at') or '', reverse=True)
    
    # Calculate Team Name
    SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
    u_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
    u_team_display = ", ".join([t.replace('_', ' ').title() for t in u_teams]) if u_teams else ("IT Management" if is_admin else "General")

    context = {
        'complaints': processed_complaints,
        'stats': stats,
        'priority_tickets': priority_tickets, # Show all vital ones in table
        'user_name': payload.get('given_name', 'User'),
        'user_role': 'Administrator' if is_admin else 'Supervisor',
        'user_team': u_team_display
    }
    
    return render(request, 'complaints/all_complaints.html', context)

@cognito_required
def supervisor_dashboard(request):
    # Security check: ensure user is SUPERVISOR
    id_token = request.session.get('id_token')
    try:
        payload = decode_jwt_token(id_token)
    except:
        return redirect('login')
        
    user_groups = payload.get('cognito:groups', [])
    
    if 'SUPERVISOR' not in user_groups and 'ADMIN' not in user_groups:
        return redirect('user_home')

    SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
    supervisor_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
    
    # Fetch all users and their groups in parallel
    cognito_users = list_cognito_users()
    all_user_groups = get_users_groups_bulk([u['Username'] for u in cognito_users])
    
    team_agents = []
    agent_identifiers = set() # usernames and emails for filtering tickets
    
    for u in cognito_users:
        username = u['Username']
        u_groups = all_user_groups.get(username, [])
        
        # If user shares a support group with supervisor, they are in the team
        if any(g in supervisor_teams for g in u_groups):
            attrs = {attr['Name']: attr['Value'] for attr in u.get('Attributes', [])}
            email = attrs.get('email')
            
            given = attrs.get('given_name', '')
            family = attrs.get('family_name', '')
            name = f"{given} {family}".strip() or username
            
            team_agents.append({
                'username': username,
                'email': email,
                'name': name,
                'initial': name[:1].upper() if name else 'U',
                'status': u.get('UserStatus', 'Offline'),
                'open_tickets': 0,
                'csat': round(4.5 + (random.random() * 0.4), 1) # Dynamic mock CSAT 4.5 - 4.9
            })
            if email: agent_identifiers.add(email)
            if username: agent_identifiers.add(username)
            if name: agent_identifiers.add(name)

    # Fetch all complaints
    all_complaints = complaints_table.scan().get('Items', [])
    
    # Filter tickets assigned to team agents
    team_tickets = [c for c in all_complaints if c.get('AssignedTo') in agent_identifiers]
    
    open_tickets = [c for c in team_tickets if c.get('Status') not in ['Closed', 'Resolved']]
    total_open = len(open_tickets)
    
    escalated = [c for c in open_tickets if c.get('Status') == 'Escalated']
    active_escalations = len(escalated)
    
    # SLA Adherence calculation
    sla_on_track_count = 0
    priority_queue = []
    
    # Status distribution
    status_counts = {'Open': 0, 'Escalated': 0, 'Pending': 0, 'In_Progress': 0, 'Resolved': 0, 'Closed': 0, 'Cancelled': 0}
    
    for c in team_tickets:
        status = c.get('Status', 'Open')
        # Map spaced status to underscore status
        template_status = status.replace(' ', '_')
        if template_status in status_counts:
            status_counts[template_status] += 1
        else:
            status_counts['Open'] += 1
            
    # Process detailed stats for each agent (for modal popups)
    for agent in team_agents:
        a_username = agent['username']
        a_email = agent['email']
        a_name = agent['name']
        
        # Link complaints to agent
        agent_complaints = [c for c in team_tickets if c.get('AssignedTo') in [a_email, a_username, a_name]]
        # Sort by creation date descending
        agent_complaints.sort(key=lambda x: x.get('CreatedAt') or x.get('Created_at') or '', reverse=True)
        
        agent_open_tickets = [c for c in agent_complaints if c.get('Status') not in ['Closed', 'Resolved']]
        
        a_sla_on_track = 0
        a_sla_at_risk = 0
        a_sla_breached = 0
        a_active_tickets = []
        
        for c in agent_complaints: # Changed from agent_open_tickets to show all
            priority = c.get('Priority', 'Normal')
            status = c.get('Status', 'Open')
            
            sla_status = "On Track"
            if status == 'Escalated': sla_status = "Breached"
            elif priority == 'High' or priority == 'Critical': sla_status = "At Risk"
            elif status in ['Closed', 'Resolved']: sla_status = "Completed"
            
            # Increment SLA counters ONLY for open tickets
            if status not in ['Closed', 'Resolved']:
                if sla_status == "On Track": a_sla_on_track += 1
                elif sla_status == "At Risk": a_sla_at_risk += 1
                elif sla_status == "Breached": a_sla_breached += 1
            
            if len(a_active_tickets) < 10: # Increased limit to 10
                created_date = c.get('CreatedAt') or c.get('Created_at') or 'N/A'
                if created_date != 'N/A':
                    try:
                        dt = datetime.fromisoformat(created_date)
                        created_display = dt.strftime('%b %d, %Y')
                    except:
                        created_display = created_date
                else:
                    created_display = 'N/A'

                a_active_tickets.append({
                    'id': str(c.get('AgreementNo') or c.get('ComplaintId') or '#T-000'),
                    'complaint_id': c.get('ComplaintId'),
                    'subject': (c.get('Subject') or c.get('Description', 'No Subject'))[:40] + '...',
                    'priority': priority,
                    'status': status,
                    'sla_status': sla_status,
                    'created': created_display
                })
        
        max_cap = 20
        agent['stats'] = {
            'total': len(agent_complaints),
            'open': len(agent_open_tickets),
            'on_track': a_sla_on_track,
            'at_risk': a_sla_at_risk,
            'breached': a_sla_breached,
            'max_capacity': max_cap,
            'capacity_pct': int((len(agent_open_tickets)/max_cap)*100) if max_cap > 0 else 0
        }
        agent['active_tickets'] = a_active_tickets
        agent['open_tickets'] = len(agent_open_tickets)

    # Populate Priority Queue and Overall SLA Adherence
    for c in open_tickets:
        priority = c.get('Priority', 'Normal')
        status = c.get('Status', 'Open')
        
        sla_status = "On Track"
        if status == 'Escalated': sla_status = "Breached"
        elif priority == 'High': sla_status = "At Risk"
        
        if sla_status == "On Track": sla_on_track_count += 1
        
        priority_queue.append({
            'id': str(c.get('AgreementNo') or c.get('ComplaintId') or '#TCK-0000'),
            'complaint_id': c.get('ComplaintId'),
            'subject': (c.get('Subject') or c.get('Description', 'No Subject'))[:35] + '...',
            'priority': priority,
            'sla_status': sla_status,
            'category': c.get('Category') or (c.get('Tags', ['General'])[0] if c.get('Tags') else 'General')
        })

    sla_adherence = int((sla_on_track_count / total_open * 100)) if total_open > 0 else 100
    
    # Sort priority queue: Escalated/Critical first
    priority_order = {'Critical': 0, 'High': 1, 'Normal': 2, 'Low': 3}
    priority_queue.sort(key=lambda x: (x['sla_status'] != 'Breached', priority_order.get(x['priority'], 9)))

    # Calculate percentages for status distribution bar
    total_tickets = len(team_tickets)
    status_pct = {}
    if total_tickets > 0:
        for k, v in status_counts.items():
            status_pct[k] = int((v / total_tickets) * 100)
    else:
        status_pct = {'Open': 0, 'Pending': 0, 'Resolved': 0, 'Escalated': 0}

    # Fallback to sum = 100% logic for CSS grid/width
    
    team_display = ", ".join([t.replace('_', ' ').title() for t in supervisor_teams])
    role_base = 'Senior Supervisor' if 'ADMIN' in user_groups else 'Team Supervisor'
    supervisor_role_formatted = f"{role_base} ({team_display})" if team_display else role_base

    context = {
        'total_open': total_open,
        'sla_adherence': sla_adherence,
        'active_escalations': active_escalations,
        'avg_resolution_time': "4h 12m", # Mocked
        'team_agents': team_agents,
        'priority_queue': priority_queue[:10],
        'status_pct': status_pct,
        'user_name': payload.get('given_name', payload.get('name', 'Supervisor')),
        'user_role': 'Senior Supervisor' if 'ADMIN' in user_groups else 'Supervisor',
        'user_team': team_display if team_display else "General",
        'teams': supervisor_teams
    }
    
    return render(request, 'complaints/supervisor_dashboard.html', context)

@cognito_required
def admin_ticket_detail(request, complaint_id):
    # Security check: ensure user is ADMIN or SUPERVISOR
    id_token = request.session.get('id_token')
    try:
        payload = decode_jwt_token(id_token)
    except:
        return redirect('login')
        
    user_groups = payload.get('cognito:groups', [])
    # Customers don't have groups. If user has no groups, they are redirected to user_home.
    if not user_groups:
        return redirect('user_home')

    # Fetch complaint details
    response = complaints_table.scan(
        FilterExpression="ComplaintId = :id",
        ExpressionAttributeValues={":id": complaint_id}
    )
    items = response.get('Items', [])
    if not items:
        return HttpResponse("Ticket not found", status=404)
    
    complaint = items[0]
    
    # Fetch actions/timeline
    actions_response = actions_table.scan(
        FilterExpression="ComplaintId = :id",
        ExpressionAttributeValues={":id": complaint_id}
    )
    actions = actions_response.get('Items', [])
    actions.sort(key=lambda x: x.get('ActionCreatedAt', ''), reverse=False)
    
    # Format dates and normalize fields
    current_user_name = payload.get('given_name', payload.get('name', 'Staff'))
    current_sub = payload.get('sub', '')
    current_email = payload.get('email', '').lower()
    
    # Store customer identifiers from complaint
    customer_sub = complaint.get('UserSub', '')
    customer_email = (complaint.get('UserEmail') or '').lower()
    
    for action in actions:
        # Normalize fields for template
        if 'ActionBy' not in action and 'UserName' in action:
            action['ActionBy'] = action['UserName']
        
        if 'ActionDescription' not in action and 'Description' in action:
            action['ActionDescription'] = action['Description']
            
        action_by = action.get('ActionBy', '')
        user_id = action.get('UserId', '')
        action_type = action.get('ActionType', '')
        
        # 1. Identity Check
        action['is_me'] = (user_id == current_sub or (current_email and user_id.lower() == current_email) or 
                          (current_email and action_by.lower() == current_email))
        
        # 2. Actor Type Classification
        action_by_lower = action_by.lower().strip()
        action_type_lower = action_type.lower().strip()
        
        if action_type_lower in ['system', 'system process'] or action_by_lower == 'system':
            action['actor_type'] = 'system'
            action['theme'] = 'orange'
            action['icon'] = 'settings'
            action['display_role'] = 'System Process'
        elif (user_id == customer_sub or (customer_email and user_id.lower() == customer_email) or 
              action_type_lower == 'user' or action_by_lower == 'user' or 
              'registered ticket' in action.get('ActionDescription', '').lower()):
            action['actor_type'] = 'customer'
            action['theme'] = 'green'
            action['icon'] = 'person'
            action['display_role'] = 'User'
        else:
            action['actor_type'] = 'staff'
            action['theme'] = 'blue'
            action['icon'] = 'shield'
            # Determine detailed role for display
            display_role = 'Agent' # Default staff role
            if action_type and action_type.lower() in ['admin', 'supervisor', 'staff']:
                display_role = action_type.title()
            elif 'admin' in action_by.lower():
                display_role = 'Admin'
            elif 'supervisor' in action_by.lower():
                display_role = 'Supervisor'
            elif action_type and action_type.lower() not in ['reply', 'note']:
                display_role = action_type
                
            action['display_role'] = display_role
            
        # UI override: "Me" always uses blue theme but keeps its alignment
        if action['is_me']:
            action['theme'] = 'blue'
            action['icon'] = 'shield'
            
        at = action.get('ActionCreatedAt')
        if at:
            try:
                dt = datetime.fromisoformat(at)
                action['formatted_date'] = dt.strftime('%b %d, %I:%M %p')
            except:
                action['formatted_date'] = at
    
    created_at = complaint.get('CreatedAt') or complaint.get('Created_at')
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at)
            complaint['formatted_date'] = dt.strftime('%b %d, %Y')
        except:
            complaint['formatted_date'] = created_at
    
    # Detect category from tags
    tags = complaint.get('Tags', [])
    category = "General"
    if isinstance(tags, list) and len(tags) > 0:
        if isinstance(tags[0], dict):
            category = tags[0].get('Name', 'General')
        else:
            category = tags[0]

    user_role = 'Agent'
    if 'ADMIN' in user_groups:
        user_role = 'Administrator'
    elif 'SUPERVISOR' in user_groups:
        user_role = 'Supervisor'

    # Calculate Team Name
    SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
    user_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
    user_team_display = ", ".join([t.replace('_', ' ').title() for t in user_teams]) if user_teams else "General"

    # Fetch agents for reassignment (only for Admin/Supervisor)
    agents_list = []
    if user_role in ['Administrator', 'Supervisor']:
        try:
            print(f"DEBUG: Starting optimized agent fetch for reassignment...")
            
            # 1. Identify Supervisor's Team(s)
            my_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
            if not my_teams and user_role == 'Administrator':
                # Admin sees all teams by default
                my_teams = SUPPORT_GROUPS

            # 2. Fetch agents ONLY for My Teams
            my_team_agents = []
            
            # Fetch all complaints for workload calc (optional but helpful)
            all_complaints_resp = complaints_table.scan() 
            all_complaints_list = all_complaints_resp.get('Items', [])
            
            current_assignee = (complaint.get('AssignedTo') or '').strip().lower()
            
            for team in my_teams:
                team_users = list_users_in_group(team)
                
                # Pre-fetch groups for all users in this team to determine roles
                team_usernames = [u.get('Username') for u in team_users if u.get('Username')]
                team_user_groups = get_users_groups_bulk(team_usernames)

                for u in team_users:
                    username = u.get('Username')
                    if not username: continue
                    
                    given_name = ''
                    family_name = ''
                    email = ''
                    for attr in u.get('Attributes', []):
                        if attr['Name'] == 'email': email = attr['Value']
                        if attr['Name'] == 'given_name': given_name = attr['Value']
                        if attr['Name'] == 'family_name': family_name = attr['Value']
                    
                    name = f"{given_name} {family_name}".strip() or username
                    
                    # Robust exclusion of current assignee
                    user_email_lower = email.strip().lower()
                    user_name_lower = name.strip().lower()
                    
                    if user_email_lower == current_assignee or user_name_lower == current_assignee:
                        print(f"DEBUG: Skipping current owner: {name} ({email})")
                        continue
                        
                    # Identify role from groups
                    user_groups_list = team_user_groups.get(username, [])
                    display_role = "Agent"
                    if "ADMIN" in user_groups_list:
                        display_role = "Admin"
                    elif "SUPERVISOR" in user_groups_list:
                        display_role = "Supervisor"
                    
                    # Calculate workload
                    workload = sum(1 for c in all_complaints_list if (c.get('AssignedTo') or '').strip().lower() in [user_email_lower, user_name_lower])
                    
                    my_team_agents.append({
                        'name': name,
                        'email': email,
                        'workload': workload,
                        'role': display_role
                    })

            # Add to agents_list with team labels
            if my_team_agents:
                # Use the first team as the display name, or handle multiple
                team_display = my_teams[0].replace('_', ' ').title() if my_teams else "My Team"
                agents_list.append({'team': team_display, 'agents': my_team_agents, 'is_my_team': True})
            
            print(f"DEBUG: Processed {len(my_team_agents)} agents for My Team.")
        except Exception as e:
            print(f"ERROR fetching agents for reassignment: {str(e)}")
            import traceback
            traceback.print_exc()

    # Process Attachments for Pre-signed URLs
    attachments = complaint.get('Attachments', [])
    agreement_no = complaint.get('AgreementNo') or complaint.get('Agreement_no') or complaint_id
    processed_attachments = []
    
    for attachment in attachments:
        filename = ""
        if isinstance(attachment, dict):
            filename = attachment.get('filename', '')
        else:
            filename = str(attachment)
            
        # S3 Key structure: complaints/{agreement_no}/{filename}
        # If it's already a full path, use it, otherwise construct it
        if "/" in filename:
            s3_key = filename
            display_name = filename.split('/')[-1]
        else:
            s3_key = f"complaints/{agreement_no}/{filename}"
            display_name = filename
            
        url = generate_s3_presigned_url('complaint-attachments-tcs', s3_key)
        
        ext = display_name.split('.')[-1].lower() if '.' in display_name else ''
        
        processed_attachments.append({
            'filename': display_name,
            'url': url,
            'extension': ext
        })
    
    complaint['processed_attachments'] = processed_attachments

    context = {
        'complaint': complaint,
        'category': category,
        'actions': actions,
        'agents': agents_list,
        'user_name': payload.get('given_name', payload.get('name', 'Staff')),
        'user_role': user_role,
        'user_team': user_team_display
    }
    
    return render(request, 'complaints/admin_ticket_detail.html', context)

@cognito_required
def reassign_ticket(request, complaint_id):
    if request.method == 'POST':
        agent_email = request.POST.get('agent_email')
        agent_name = request.POST.get('agent_name')
        
        reassign_type = request.POST.get('reassign_type', 'Reassign')
        
        # Get user info for action log
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_name = payload.get('given_name', payload.get('name', 'Staff'))
        
        # Update complaint
        complaints_table.update_item(
            Key={'ComplaintId': complaint_id},
            UpdateExpression="set AssignedTo = :a, AssignedAgentName = :n",
            ExpressionAttributeValues={':a': agent_email, ':n': agent_name}
        )
        
        # Determine description based on type
        desc = f"Ticket reassigned to {agent_name} ({agent_email})"
        if reassign_type == 'Transfer':
             desc = f"Ticket transferred to {agent_name} ({agent_email})"

        # Record action
        actions_table.put_item(
            Item={
                'ActionId': str(uuid.uuid4()),
                'ComplaintId': complaint_id,
                'ActionCreatedAt': now().isoformat(),
                'ActionDescription': desc,
                'ActionType': "System",
                'Status': request.POST.get('current_status', 'Open'),
                'UserId': payload.get('email', ''),
                'ActionBy': user_name
            }
        )
        
    return redirect('admin_ticket_detail', complaint_id=complaint_id)

@cognito_required
def reopen_ticket(request, complaint_id):
    # Security: check if ticket is resolved
    response = complaints_table.scan(
        FilterExpression="ComplaintId = :id",
        ExpressionAttributeValues={":id": complaint_id}
    )
    items = response.get('Items', [])
    if items and items[0].get('Status') == 'Resolved':
        # Update status to In Progress or Open?
        # User said "only the user can reopen the tickets which are in resolved status"
        # We'll set it back to "In Progress" or "Open"
        complaints_table.update_item(
            Key={'ComplaintId': complaint_id},
            UpdateExpression="set #s = :s",
            ExpressionAttributeNames={'#s': 'Status'},
            ExpressionAttributeValues={':s': 'In Progress'}
        )
        # Record action
        actions_table.put_item(
            Item={
                'ActionId': str(uuid.uuid4()),
                'ComplaintId': complaint_id,
                'ActionCreatedAt': now().isoformat(),
                'ActionDescription': "Ticket reopened by customer",
                'ActionType': "User",
                'Status': 'In Progress',
                'UserId': request.user.email if hasattr(request.user, 'email') else 'Customer'
            }
        )
    return redirect('track_complaint_detail', complaint_id=complaint_id)

@cognito_required
@require_POST
def add_comment(request, complaint_id):
    """Allows a user to add a comment or reply to their ticket timeline."""
    comment = request.POST.get('comment', '').strip()
    if not comment:
        return redirect('track_complaint_detail', complaint_id=complaint_id)
    
    # 1. Fetch current complaint to maintain current status
    response = complaints_table.scan(
        FilterExpression="ComplaintId = :id",
        ExpressionAttributeValues={":id": complaint_id}
    )
    items = response.get('Items', [])
    current_status = items[0].get('Status', 'Open') if items else 'Open'

    # If ticket was Pending, auto-update to In Progress when customer replies
    if current_status == 'Pending':
        try:
            complaints_table.update_item(
                Key={'ComplaintId': complaint_id},
                UpdateExpression="set #s = :s, PendingActionHeading = :none",
                ExpressionAttributeNames={'#s': 'Status'},
                ExpressionAttributeValues={':s': 'In Progress', ':none': None}
            )
            current_status = 'In Progress'
        except Exception as e:
            print(f"Auto-update error: {e}")

    # 2. Get user info from token
    id_token = request.session.get('id_token')
    user_name = "Customer"
    user_email = ""
    try:
        payload = decode_jwt_token(id_token)
        user_name = payload.get('given_name', payload.get('name', 'Customer'))
        user_email = payload.get('email', '')
    except:
        pass

    # 3. Record the comment as a 'User' action
    actions_table.put_item(
        Item={
            'ActionId': str(uuid.uuid4()),
            'ComplaintId': complaint_id,
            'ActionCreatedAt': now().isoformat(),
            'ActionDescription': comment,
            'ActionType': "User",
            'Status': current_status, # Does not change the status
            'UserId': user_email,
            'ActionBy': user_name
        }
    )
    return redirect('track_complaint_detail', complaint_id=complaint_id)

@cognito_required
def update_ticket_action(request, complaint_id):
    if request.method == 'POST':
        action_type = request.POST.get('action_type', 'Reply') # 'Reply' or 'Note'
        comment = request.POST.get('comment', '')
        new_status = request.POST.get('status')
        
        # Get user info from token
        id_token = request.session.get('id_token')
        try:
            payload = decode_jwt_token(id_token)
        except:
            return redirect('login')
            
        user_name = payload.get('given_name', payload.get('name', 'Staff'))
        user_email = payload.get('email', '')
        user_groups = payload.get('cognito:groups', [])
        
        # Fetch current complaint to check current status
        response = complaints_table.scan(
            FilterExpression="ComplaintId = :id",
            ExpressionAttributeValues={":id": complaint_id}
        )
        items = response.get('Items', [])
        if not items:
            return HttpResponse("Ticket not found", status=404)
        
        complaint = items[0]
        current_status = complaint.get('Status', 'Open')
        
        # Security/Business Logic Constraints
        is_staff = any(g in ['ADMIN', 'SUPERVISOR'] for g in user_groups)
        
        # Only Admin/Supervisor can cancel
        if new_status == 'Cancelled' and not is_staff:
             new_status = current_status # Reset to current if not authorized
        
        # User cannot update same status twice (logic mentioned by user)
        # If status is same, we allow the comment but don't re-trigger status change log in some systems, 
        # but here we follow the user's specific rule: "if the compalint is in escalate status, 
        # then again they can update with stauts only they can add notes in that case."
        
        status_changed = (new_status != current_status)
        
        # If they selected the same status, we treat the action as a Note or just a comment without status change
        # but the prompt implies we should only allow adding notes if status is same.
        
        if status_changed:
            update_data = {
                '#s': 'Status'
            }
            update_vals = {
                ':s': new_status,
                ':ts': datetime.now().isoformat()
            }
            update_expr = "set #s = :s, LastUpdatedTimestamp = :ts"
            
            # If status is Pending, generate a catchy heading and message via AI (Nova Pro)
            if new_status == 'Pending' and comment:
                try:
                    # Specific prompt for Nova Pro to return JSON
                    ai_prompt = f"""
                    You are a professional customer support AI assistant. An agent is requesting something from a customer: "{comment}"
                    
                    Please generate:
                    1. A short, catchy 'heading' (max 4-5 words) summarizing the requirement.
                    2. A supportive and clear 'message' (max 2 sentences) telling the customer exactly what to provide.
                    
                    Response format (JSON only):
                    {{"heading": "...", "message": "..."}}
                    """
                    # Use callAIForIntent for raw response
                    ai_raw = callAIForIntent(ai_prompt)
                    # Try to find JSON in the response if the model included extra text
                    match = re.search(r'\{.*\}', ai_raw, re.DOTALL)
                    if match:
                        ai_data = json.loads(match.group())
                        pending_heading = ai_data.get('heading', 'Update Required')
                        pending_message = ai_data.get('message', 'Our agent is waiting for your response to move forward.')
                        
                        update_vals[':ph'] = pending_heading
                        update_vals[':pm'] = pending_message
                        update_expr += ", PendingActionHeading = :ph, PendingActionMessage = :pm"
                except Exception as e:
                    print(f"AI Nova Pro Summary Error: {e}")

            complaints_table.update_item(
                Key={'ComplaintId': complaint_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=update_data,
                ExpressionAttributeValues=update_vals
            )

            # Auto-complete pending insurance transaction if ticket is Resolved/Closed
            if new_status in ['Resolved', 'Closed']:
                try:
                    agreement_no = complaint.get('AgreementNo') or complaint.get('Agreement_no')
                    print(f"DEBUG: Auto-completing transactions for Ticket: {complaint_id} / Agreement: {agreement_no}")
                    
                    # Search for transactions matching EITHER ComplaintId (UUID) or AgreementNo
                    filter_expr = (Attr('ComplaintID').eq(complaint_id) | Attr('ComplaintID').eq(agreement_no)) & Attr('TransactionStatus').eq('Pending')
                    
                    txn_resp = transaction_table.scan(
                        FilterExpression=filter_expr
                    )
                    found_txns = txn_resp.get('Items', [])
                    print(f"DEBUG: Found {len(found_txns)} pending transactions to auto-complete")
                    
                    for txn in found_txns:
                        transaction_table.update_item(
                            Key={'TransactionID': txn['TransactionID']},
                            UpdateExpression="set TransactionStatus = :ns, LastUpdatedTimestamp = :ts",
                            ExpressionAttributeValues={
                                ':ns': 'Completed',
                                ':ts': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
                            }
                        )
                        print(f"Auto-completed transaction {txn['TransactionID']} for complaint {complaint_id}")
                except Exception as e:
                    print(f"Error updating transaction status: {e}")
        else:
            # If status hasn't changed, the action type is effectively a Note/Internal even if they clicked Reply?
            # User said: "only they can add notes in that case"
            # We'll just record it with the existing status.
            pass

        # Record action in ComplaintActions
        action_id = str(uuid.uuid4())
        action_item = {
            'ActionId': action_id,
            'ComplaintId': complaint_id,
            'ActionCreatedAt': now().isoformat(),
            'ActionDescription': comment,
            'ActionType': action_type,
            'Status': new_status if status_changed else current_status,
            'UserId': user_email,
            'ActionBy': user_name,
            'IsInternal': (action_type == 'Note')
        }
        
        # Attach AI-generated summary if it exists
        if 'pending_heading' in locals():
            action_item['PendingActionHeading'] = pending_heading
            action_item['PendingActionMessage'] = pending_message
            
        actions_table.put_item(Item=action_item)
        
        return redirect('admin_ticket_detail', complaint_id=complaint_id)
    
    return redirect('admin_ticket_detail', complaint_id=complaint_id)

def logout_page(request):
    """Logs out the user and redirects to the login page."""
    request.session.flush() # Clears entire session
    return redirect('login') 

 # Your DynamoDB table name

def generate_agreement_no():
    prefix = "MF"
    number = ''.join(random.choices(string.digits, k=9))  # Random 9-digit number
    return f"{prefix}{number}"

def process_uploaded_files(request, agreement_no: str) -> Tuple[List[str], List[str]]:
    """
    Reads all files from the 'complaint_files' input, saves to a temp path,
    uploads to S3 via `upload_files`, and returns (attachments, s3_keys).

    attachments: list of original uploaded file names
    s3_keys:     list of S3 keys returned by upload_files()
    """
    files = request.FILES.getlist('complaint_files')
    attachments: List[str] = []
    s3_keys: List[str] = []

    if not files:
        print("⚠️ No files found in request.FILES['complaint_files']")
        return attachments, s3_keys

    for uploaded in files:
        print("📦 Processing file:", uploaded.name, uploaded.size, "bytes")

        # Create a secure temp file (mkstemp returns a file descriptor and path)
        fd, temp_path = tempfile.mkstemp(suffix=f"_{uploaded.name}")
        try:
            with os.fdopen(fd, "wb") as tmp:
                for chunk in uploaded.chunks():
                    tmp.write(chunk)
            print("💾 Saved to temp:", temp_path)

            # Upload to S3 (your existing helper)
            agreement_no1 = request.POST.get('agreement_no', agreement_no)
            print("🚀 Uploading to S3 using upload_files() ...")
            s3_key = upload_files(temp_path, agreement_no1)  # <- your helper
            print("🎯 S3 key:", s3_key)

            attachments.append(uploaded.name)
            s3_keys.append(s3_key)
        except Exception as e:
            print(f"❌ Error processing {uploaded.name}: {e}")
        finally:
            # Always try to remove the temp file
            try:
                os.remove(temp_path)
                print("🧹 Temp file removed:", temp_path)
            except OSError:
                print("⚠️ Temp file missing or cannot delete:", temp_path)

    return attachments, s3_keys



    
@csrf_exempt
@cognito_required
def get_ai_estimate(request):
    """
    AJAX view to perform real-time AI damage adjustment.
    Refined logic: Verifies specific vehicle policy, coverage, and status.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=400)
    
    try:
        from .media_analyzer import analyze_image_for_payout, extract_license_plate
        
        # 1. Get Inputs
        description = request.POST.get('description', '')
        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            return JsonResponse({'success': False, 'error': 'No image provided.'})
            
        file_bytes = uploaded_file.read()
        
        # 2. Extract Vehicle ID (Plate)
        # Try image first, then description regex
        car_number = extract_license_plate(file_bytes)
        if not car_number:
            plate_match = re.search(r'[A-Z]{2}[0-9]{1,2}[A-Z]{1,2}[0-9]{4}', description.replace(" ", "").upper())
            if plate_match: car_number = plate_match.group()
            
        # 3. Fetch User Policies
        username = request.user.first_name # sub UUID
        policy_res = policy_table.query(
            KeyConditionExpression='Username = :username',
            ExpressionAttributeValues={':username': username}
        )
        all_policies = policy_res.get('Items', [])
        
        # Match specific vehicle if car_number is found
        policy = None
        if car_number:
            for p in all_policies:
                if p.get('CarNumber', '').replace(" ", "").upper() == car_number:
                    policy = p
                    break
        
        # If a car number was detected but no policy matches it, reject the claim
        if car_number and not policy:
            print(f"Vehicle {car_number} detected but not found in user's policies {all_policies}")
            return JsonResponse({
                'success': True,
                'part': 'N/A',
                'severity': 'N/A',
                'reason': f'Vehicle {car_number} was detected from the evidence, but it is not linked to any active policy in your account.',
                'base_cost': '0.00',
                'taxes': '0.00',
                'other_charges': '0.00',
                'total_estimate': '0.00',
                'deductible': '0.00',
                'payout': '0.00',
                'verdict': f'REJECTED: Vehicle {car_number} is not covered in your policy.',
                'status': 'Uncovered',
                'car_number': car_number
            })

        # Fallback to first policy ONLY if no car number was detected at all
        if not policy and all_policies:
            policy = all_policies[0]
            car_number = policy.get('CarNumber', 'Unknown')
            
        if not policy:
            return JsonResponse({'success': False, 'error': 'No active policy record found in your account to process this adjustment.'})

        # 4. Check Policy Health & Coverage
        policy_status = policy.get('PolicyStatus', 'Unknown')
        covered_parts = policy.get('CoverageDetails', '') # String of covered parts
        deductible = float(policy.get('Deductible', 500))
        part_limits = policy.get('PartLimits', {})
        total_coverage = float(policy.get('TotalCoverage', 10000))

        # 5. Get AI Damage Analysis
        ai_result = analyze_image_for_payout(file_bytes) 
        if 'error' in ai_result:
            return JsonResponse({'success': False, 'error': ai_result['error']})
            
        detected_part = ai_result.get('part', 'Unknown').strip()
        severity = ai_result.get('severity', 'Moderate').strip()
        reason = ai_result.get('reason', 'Damage detected.')

        # 6. Correlate AI with DB
        is_covered = False
        if detected_part.lower() in covered_parts.lower() or any(p.lower() in detected_part.lower() for p in covered_parts.split(',')):
            is_covered = True
            
        is_total_loss = severity.lower() in ['severe', 'total', 'total loss'] and \
                       ('total' in reason.lower() or 'full' in reason.lower() or 'frame' in reason.lower())
        
        # Financial Math (Based on DB Limits)
        multipliers = {"Minor": 0.3, "Moderate": 0.6, "Severe": 1.0, "Total Loss": 1.0}
        factor = multipliers.get(severity, 0.6)
        
        # Get limit for part or total
        base_limit = 1000 # Default fallback
        if is_total_loss:
            base_limit = total_coverage
            severity = "Total Loss"
        else:
            # Map detected part to DB PartLimits key
            for part_key, limit in part_limits.items():
                if part_key.lower() in detected_part.lower() or detected_part.lower() in part_key.lower():
                    base_limit = float(limit)
                    break
        
        subtotal = base_limit * factor
        taxes = subtotal * 0.05
        other_charges = subtotal * 0.10
        
        # New Logic: Deduct taxes and charges from approved amount
        # These charges BECOME the deductible
        applied_deductible = taxes + other_charges
        total_estimate = subtotal # The total approved amount
        payout = max(0, total_estimate - applied_deductible)
        
        # Apply Policy Status Logic
        final_verdict = f"{severity} Damage detected"
        
        if policy_status.lower() == 'lapsed':
            payout = 0
            final_verdict = f"REJECTED: Policy Lapsed ({car_number})"
        elif not is_covered:
            payout = 0
            final_verdict = f"REJECTED: {detected_part} not covered for {car_number}"
        elif is_total_loss:
            final_verdict = f"Total Loss Approved for {car_number}"

        return JsonResponse({
            'success': True,
            'part': detected_part,
            'severity': severity,
            'reason': reason,
            'base_cost': f"{subtotal:.2f}",
            'taxes': f"{taxes:.2f}",
            'other_charges': f"{other_charges:.2f}",
            'total_estimate': f"{total_estimate:.2f}",
            'deductible': f"{applied_deductible:.2f}",
            'payout': f"{payout:.2f}",
            'verdict': final_verdict,
            'status': policy_status,
            'car_number': car_number
        })
        
    except Exception as e:
        print(f"Estimate View Error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ... your other imports and globals (API_GATEWAY_URL, policy_table, upload_files, etc.)

@cognito_required
def register_complaint(request):
    agreement_no = generate_agreement_no()
    print(f"Generated Agreement Number (initial): {agreement_no}")

    if request.method == 'POST':
        form = ComplaintForm(request.POST)
        if not form.is_valid():
            # Show form with errors
            return render(request, 'complaints/register_complaint.html', {
                'show_section': 'register',
                'form': form,
                'agreement_no': agreement_no,
                'description': request.POST.get('description', '')
            })

        description = form.cleaned_data['description']
        tags_from_session = store_tags(request)
        currentUser = request.user.first_name  # This is now the 'sub' UUID

        # Determine complaint type
        complaint_type, existing_complaint = check_complaint_type(description, currentUser)

        # --- Duplicate complaint ---
        if complaint_type == 'duplicate':
            print("⚠️ Duplicate complaint detected!")
            return render(request, 'complaints/complaint_conformation_new.html', {
                'show_section': 'duplicate_popup',
                'complaint_id': existing_complaint['AgreementNo'],
                'oldCompID': existing_complaint['ComplaintId'],
                'resolution_text': (
                    f"Your complaint has been flagged as a duplicate. "
                    f"Here is the status of your original complaint: {existing_complaint['ComplaintId']}"
                ),
            })

        # --- Status inquiry ---
        if complaint_type == 'status_inquiry':
            # Only treat as status inquiry if we actually find a Ticket ID
            complaint_id = get_agreement_id(description)
            if complaint_id:
                print("ℹ️ Status inquiry detected!")
                status = get_complaint_status(request.user.first_name, complaint_id)
                resolution = generate_polished_response(status)
                return render(request, 'complaints/complaints.html', {
                    'show_section': 'status_inquiry',
                    'status_text': resolution,
                    'complaint_id': complaint_id,
                    'status': status,
                    'agreement_no': agreement_no,
                })
            else:
                # If no ID found, downgrade to new complaint
                complaint_type = 'new_complaint'

        # --- New complaint ---
        if complaint_type == 'new_complaint':
            print("✅ New complaint detected!")
            insurance_claim_id = None
            insurance_txn_id = None
            
            # 1. Extract Details
            policy_number, car_number, damage_type = extract_insurance_details(description)
            insurance_keywords = ['insurance', 'claim', 'accident', 'collision', 'policy']
            is_insurance = "Insurance Claim" in [t['name'] for t in tags_from_session] or any(k in description.lower() for k in insurance_keywords)
            transaction_keywords = ['transaction', 'payment', 'charge', 'refund', 'subscription', 'billing', 'statement', 'stmt', 'txn']
            is_transaction = "Transaction Issue" in [t['name'] for t in tags_from_session] or any(k in description.lower() for k in transaction_keywords) or "txn-" in description.lower()
            
            resolution = None
            is_error_flag = False
            assigned_team = "Support"
            custom_tags = tags_from_session

            # 2. Insurance Logic Path
            bypass_insurance = request.POST.get('bypass_insurance') == 'true'
            ai_is_approved = (request.POST.get('ai_is_approved') == 'true')

            if is_insurance and not bypass_insurance:
                print(f"Processing Insurance Path. AI Approved: {ai_is_approved}")
                assigned_team = "Insurance Team"
                custom_tags = [{"name": "Insurance Claim", "score": "1.0"}]
                
                if ai_is_approved:
                    # AI FAST-TRACK: Skip all intermediate validation
                    print("Bypassing manual validation for AI-approved estimate.")
                    car_number = request.POST.get('ai_car_number')
                    custom_payout = request.POST.get('ai_payout', '10.00')

                    # Lookup actual policy number to link correctly
                    try:
                        pol_lookup_res = policy_table.scan(FilterExpression="CarNumber = :c", ExpressionAttributeValues={":c": str(car_number or "").strip().upper()})
                        items = pol_lookup_res.get('Items', [])
                        policy_number = items[0].get('PolicyNo') if items else "AI-V-TRANS"
                    except:
                        policy_number = "AI-V-TRANS"
                    
                    claim_id = f"CLM-AI-{random.randint(100000, 999999)}"
                    insurance_claim_id = claim_id
                    part_detected = request.POST.get('ai_part_detected', 'Detected Part')
                    resolution = f"Insurance adjustment pre-approved by AI for {part_detected}. Claim: {claim_id}"
                    is_error_flag = False
                    
                    # Create transaction with AI amount
                    insurance_txn_id = create_insurance_transaction(agreement_no, claim_id, request.user.first_name, policy_number, amount=custom_payout)
                
                else:
                    # ORIGINAL MANUAL LOGIC: Strict Validation
                    if not policy_number and car_number:
                        try:
                            pol_lookup_res = policy_table.scan(FilterExpression="CarNumber = :c", ExpressionAttributeValues={":c": car_number.strip().upper()})
                            lookup_items = pol_lookup_res.get('Items', [])
                            if lookup_items:
                                pol_rec = lookup_items[0]
                                policy_number = pol_rec.get('PolicyNo')
                        except: pass

                    # Missing Info Check
                    if not policy_number or not car_number or damage_type == "Unknown":
                        user_policies = []
                        try:
                            username = request.user.first_name
                            policy_res = policy_table.query(KeyConditionExpression='Username = :username', ExpressionAttributeValues={':username': username})
                            user_policies = [p for p in policy_res.get('Items', []) if p.get('PolicyStatus', '').lower() == 'active']
                        except: pass
                        
                        missing_label = "Vehicle Number" if not car_number else ("Damage Details" if damage_type == "Unknown" else "Policy Number")
                        car_not_found = (car_number and not any(p.get('CarNumber') == car_number.strip().upper() for p in user_policies))
                        
                        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                            return JsonResponse({
                                'show_validation_error': True, 'missing_field': missing_label, 
                                'user_policies': user_policies, 'car_not_found': car_not_found, 
                                'detected_car': car_number, 'description': description
                            })
                            
                        return render(request, 'complaints/register_complaint.html', {
                            'show_validation_error': True, 'form': form, 'agreement_no': agreement_no,
                            'missing_field': missing_label, 'description': description, 'user_policies': user_policies,
                            'car_not_found': car_not_found, 'detected_car': car_number
                        })

                    # Policy/Vehicle Mismatch Check
                    try:
                        pol_res = policy_table.scan(FilterExpression="PolicyNo = :p", ExpressionAttributeValues={":p": policy_number.strip().upper()})
                        policies = pol_res.get('Items', [])

                        if not policies:
                            is_insurance = False
                            assigned_team = "Support"
                            resolution = "Policy verification failed. Proceeding as a standard complaint."
                            is_error_flag = True
                        else:
                            pol_rec = policies[0]
                            db_car_no = pol_rec.get('CarNumber', '')

                            def cp(p): return re.sub(r'[^A-Z0-9]', '', str(p).upper())
                            if cp(car_number) != cp(db_car_no):
                                err_msg = f'Policy {policy_number} is for vehicle "{db_car_no}", but description mentions "{car_number}".'
                                if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                    return JsonResponse({'show_validation_error': True, 'error_message': err_msg, 'description': description})
                                return render(request, 'complaints/register_complaint.html', {
                                    'show_validation_error': True, 'form': form, 'agreement_no': agreement_no,
                                    'error_message': err_msg, 'description': description
                                })

                            if pol_rec.get('PolicyStatus', '').lower() != 'active':
                                resolution = f"Policy status is {pol_rec.get('PolicyStatus')}. Manual review assigned."
                                is_error_flag = True
                            else:
                                # Coverage Matching
                                damage_keywords = set(damage_type.lower().replace(',', ' ').split())
                                coverage_lower = pol_rec.get('CoverageDetails', '').lower()
                                is_covered = any(k in coverage_lower for k in damage_keywords)
                                
                                if is_covered:
                                    claim_id = f"CLM-{random.randint(100000, 999999)}"
                                    insurance_claim_id = claim_id
                                    resolution = f"Coverage confirmed. Claim submitted: {claim_id}"
                                    is_error_flag = False
                                    insurance_txn_id = create_insurance_transaction(agreement_no, claim_id, request.user.first_name, policy_number, amount="10.00")
                                else:
                                    resolution = "Damage type mismatch with coverage. Manual review required."
                                    is_error_flag = True
                    except Exception as e:
                        print(f"Old Logic Error: {e}")
                        resolution = "Manual verification required due to lookup error."
                        is_error_flag = True

            # 3. Transaction Logic Path (Using Lambda via API Gateway)
            elif is_transaction:
                print("Processing Transaction Flow via Lambda...")
                assigned_team = "Banking Team"
                custom_tags = [{"name": "Transaction Issue", "score": "1.0"}]
                txn_id = extract_transaction_id(description)
                
                # Get CustomerID
                customer_id = request.user.first_name # The 'sub' UUID
                
                # Check if user wants to bypass transaction validation
                bypass_transaction = request.POST.get('bypass_transaction', 'false') == 'true'
                
                if bypass_transaction:
                    print("User chose to bypass transaction validation. Processing as general complaint.")
                    is_transaction = False
                    assigned_team = "Support"
                    resolution = "Transaction validation bypassed. This request will be handled as a general complaint."
                    is_error_flag = False
                
                elif not txn_id:
                    # Missing Transaction ID - Show modal with user's transactions
                    print("⚠️ No Transaction ID found. Fetching user's transactions...")
                    user_txns = []
                    try:
                        txn_res = transaction_table.scan(
                            FilterExpression=Attr('CustomerID').eq(customer_id)
                        )
                        user_txns = txn_res.get('Items', [])
                        user_txns.sort(key=lambda x: str(x.get('TransactionDate') or ''), reverse=True)
                    except Exception as e:
                        print(f"Transaction Fetch Error: {e}")
                    
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return JsonResponse({
                            'show_validation_error': True,
                            'missing_field': 'Transaction ID',
                            'user_transactions': user_txns,
                            'description': description
                        })
                    
                    return render(request, 'complaints/register_complaint.html', {
                        'show_validation_error': True, 'form': form, 'agreement_no': agreement_no,
                        'missing_field': 'Transaction ID', 'description': description, 'user_transactions': user_txns
                    })
                
                else:
                    # Transaction ID provided - Call Lambda via API Gateway
                    try:
                        print(f"Calling Lambda for TransactionID: {txn_id}, CustomerID: {customer_id}")
                        
                        payload = {
                            "transaction_id": txn_id,
                            "customer_id": customer_id,
                            "description": description
                        }
                        
                        api_res = requests.post(API_GATEWAY_TRANSACTION_URL, json=payload, timeout=10)
                        
                        if api_res.status_code == 200:
                            res_data = api_res.json()
                            
                            # Handle Lambda response format
                            if 'body' in res_data and isinstance(res_data['body'], str):
                                res_body = json.loads(res_data['body'])
                            else:
                                res_body = res_data
                            
                            lambda_status = res_body.get('status', 'Pending')
                            lambda_message = res_body.get('message', 'Transaction processed.')
                            
                            # Map Lambda status to our system status
                            if lambda_status == 'Resolved':
                                resolution = lambda_message
                                is_error_flag = False
                            elif lambda_status == 'Escalated':
                                resolution = lambda_message
                                is_error_flag = True  # Triggers Escalated status
                            elif lambda_status == 'Pending':
                                resolution = lambda_message
                                is_error_flag = False
                            elif lambda_status == 'Error':
                                # Lambda returned an error
                                print(f"Lambda returned error: {lambda_message}")
                                is_transaction = False
                                assigned_team = "Support"
                                resolution = f"Transaction {txn_id} could not be verified. This request will be handled as a standard complaint."
                                is_error_flag = True
                            else:
                                # Unknown status
                                resolution = lambda_message
                                is_error_flag = False
                            
                            print(f"Lambda Response - Status: {lambda_status}, Message: {lambda_message}")
                        
                        elif api_res.status_code == 404:
                            # Transaction not found
                            print(f"Transaction {txn_id} not found (404). Fetching user's transactions...")
                            user_txns = []
                            try:
                                txn_res = transaction_table.scan(
                                    FilterExpression=Attr('CustomerID').eq(customer_id)
                                )
                                user_txns = txn_res.get('Items', [])
                                user_txns.sort(key=lambda x: str(x.get('TransactionDate') or ''), reverse=True)
                            except Exception as e:
                                print(f"Transaction Fetch Error: {e}")

                            if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                                return JsonResponse({
                                    'show_validation_error': True,
                                    'missing_field': 'Transaction ID',
                                    'error_message': f"Transaction {txn_id} not found. Please select from your recent transactions.",
                                    'user_transactions': user_txns,
                                    'description': description
                                })
                            
                            return render(request, 'complaints/register_complaint.html', {
                                'show_validation_error': True, 'form': form, 'agreement_no': agreement_no,
                                'missing_field': 'Transaction ID', 'description': description, 'user_transactions': user_txns,
                                'error_message': f"Transaction {txn_id} not found."
                            })
                        
                        else:
                            # API error
                            print(f"Lambda API error: Status {api_res.status_code}")
                            resolution = f"Transaction verification service returned an error. Escalating to Banking Team for manual review of {txn_id}."
                            is_error_flag = True
                    
                    except requests.exceptions.Timeout:
                        print(f"Lambda API Timeout")
                        resolution = f"Transaction verification service timed out. Escalating to Banking Team for manual review of {txn_id}."
                        is_error_flag = True
                    
                    except requests.exceptions.RequestException as e:
                        print(f"Lambda API Request Error: {e}")
                        resolution = f"Error connecting to transaction verification service. Escalating to Banking Team for manual review of {txn_id}."
                        is_error_flag = True
                    
                    except Exception as e:
                        error_type = type(e).__name__
                        print(f"Transaction Processing Error ({error_type}): {e}")
                        resolution = f"Error processing transaction {txn_id}. Escalating for manual review."
                        is_error_flag = True


            # 4. Final Submission Logic (Unified for all paths)
            subject = generate_complaint_subject(description)
            attachments, _ = process_uploaded_files(request, agreement_no)
            
            if not resolution:
                category = tags_from_session[0]['name'] if tags_from_session else 'General'
                resolution = generate_ai_resolution(description, category)
            
            payload = {
                'description': description,
                'user_name': currentUser,
                'agreement_no': agreement_no,
                'attachments': attachments,
                'tags': custom_tags,
                'subject': subject
            }

            try:
                api_res = requests.post(API_GATEWAY_URL, json=payload)
                if api_res.status_code == 200:
                    resp_data = json.loads(api_res.json().get('body', '{}'))
                    comp_id = resp_data.get('complaintId', agreement_no)
                    
                    # Capture AI Estimates if passed from the interactive modal
                    final_payout = request.POST.get('ai_payout', '10.00')
                    ai_part = request.POST.get('ai_part_detected', '')
                    
                    if ai_part and resolution:
                        resolution = f"AI Adjustment: {resolution}"

                    return render(request, 'complaints/complaint_conformation_new.html', {
                        'show_section': 'confirmation',
                        'complaint_id': comp_id,
                        'insurance_claim_id': insurance_claim_id,
                        'insurance_txn_id': insurance_txn_id,
                        'resolution_text': resolution,
                        'agreement_no': agreement_no,
                        'is_error': is_error_flag,
                        'category': custom_tags[0]['name'] if custom_tags else 'General',
                        'subject': subject,
                        'description': description,
                        'submitted': datetime.now().strftime('%d-%b-%y'),
                        'assigned_to': resp_data.get('assignedTo', 'Pending'),
                        'assigned_team': resp_data.get('assignedTeam', assigned_team),
                        'payout_amount': final_payout
                    })
                else:
                    return render(request, 'complaints/complaints.html', {
                        'show_section': 'error', 'error_message': f"Submission failed: {api_res.status_code}"
                    })
            except Exception as e:
                print(f"Final Submit Error: {e}")
                return render(request, 'complaints/complaints.html', { 'show_section': 'error', 'error_message': str(e) })

    # GET: render register form
    else:
        form = ComplaintForm()

    return render(request, 'complaints/register_complaint.html', {
        'show_section': 'register',
        'form': form,
        'agreement_no': agreement_no,
        'description': ''
    })


@cognito_required
def track_complaints(request):
    username = request.user.first_name # This is now the 'sub' UUID
    print(f"User ID (sub): {username}")

    # Fetch the most recent 4 complaints from DynamoDB
    response1 = complaints_table.scan(
        FilterExpression="UserName = :username",
        ExpressionAttributeValues={":username": username}
    )
    
    complaints = response1.get('Items', [])

    # Sort complaints by 'CreatedAt' field in descending order (most recent first)
    complaints.sort(key=lambda x: x['CreatedAt'], reverse=True)

    # Only get the first 4 complaints (recent 4 complaints)
    recent_complaints = complaints[:]

    # Format 'CreatedAt' field to 'DD-MMM-YY'
    for complaint in recent_complaints:
        created_at = complaint['CreatedAt']
        dt = datetime.fromisoformat(created_at)  # Convert to datetime
        complaint['CreatedAt'] = dt.strftime('%d-%b-%y')  # Format to 'DD-MMM-YY'

    # Additional counts for status categories (can be omitted if not needed)
    open_pending_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['Open', 'pending'])
    in_progress_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['in progress', 'User created case'])
    closed_resolved_count = sum(1 for c in recent_complaints if c['Status'].lower() in ['closed', 'resolved'])

    # Pass the data to the template
    return render(request, 'complaints/track_home.html', {
        'complaints': recent_complaints,
        'total_complaints': len(recent_complaints),
        'open_pending_count': open_pending_count,
        'in_progress_count': in_progress_count,
        'closed_resolved_count': closed_resolved_count,
        'first_name':username
    })

def store_tags(request, tags=None):
    """
    Store tags in the request object. If no tags are provided, return the current tags.
    This function is useful for storing and retrieving tags for the current request.
    """
    if tags is not None:
        # Store the tags in the request object for the current session
        request.session['tags'] = tags  # Use session to store tags temporarily
    else:
        # Retrieve the tags from the request session
        return request.session.get('tags', [])



@require_POST
def generate_intent(request):
    try:
        payload = json.loads(request.body.decode('utf-8'))
        description = payload.get('description', '') or ''
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")
    category=[ 'Status Enquiry', 'Insurance Claim', 'Payment issue', 'Transaction Issue', 'Dispute', 'Fraud alert']
    prompt = f"""
    Description: {description}
    Based on the attached description, generate intent tags to classify the complaint under any of the category {category}.
    """
    # Call Comprehend (real) — it will return list of dicts
    tags_from_comprehend = generate_intent_tags(description)
    #tags_from_comprehend = [callAIForIntent(prompt)]
    print("comprehend tags:", tags_from_comprehend)
    # Only send the first tag to the UI
    top_tags = tags_from_comprehend[:1] if tags_from_comprehend else []
    store_tags(request, top_tags) 
    print("Intent tags stored")
    return JsonResponse({"success": True, "tags": top_tags})

def get_relative_time_string(timestamp_str):
    if not timestamp_str:
        return "Unknown"
    
    try:
        dt = datetime.fromisoformat(timestamp_str)
        # Handle timezone-aware/naive datetimes
        if dt.tzinfo:
            from django.utils.timezone import now as tz_now
            current_time = tz_now()
        else:
            current_time = datetime.now()
            
        diff = current_time - dt
        
        seconds = diff.total_seconds()
        if seconds < 0:
            return "Just now"
            
        if seconds < 3600:
            minutes = int(seconds // 60)
            return f"{minutes}min ago" if minutes > 0 else "Just now"
        elif seconds < 86400:
            hours = int(seconds // 3600)
            return f"{hours}hr ago"
        else:
            days = int(seconds // 86400)
            return f"{days} day ago" if days == 1 else f"{days} days ago"
    except Exception as e:
        print(f"Error calculating relative time: {e}")
        return "Unknown"

# views.py (add these imports near top of file)


@cognito_required
def track_complaint_detail(request, complaint_id):
    # Query DynamoDB for actions related to this ComplaintId

    response1 = complaints_table.scan(
        FilterExpression="ComplaintId = :complaint_id",
        ExpressionAttributeValues={":complaint_id": complaint_id}
    )
    
    raw_complaints = response1.get('Items', [])
    if not raw_complaints:
        return redirect('track_complaints')

    # Get raw CreatedAt for last_updated calculation
    raw_created_at = raw_complaints[0].get('CreatedAt')
    
    Status = raw_complaints[0].get('Status', 'Unknown')
    tags = raw_complaints[0].get('Tags', [])
    description = raw_complaints[0].get('Description', '')
    attachments = raw_complaints[0].get('Attachments', [])

    # Format CreatedAt for display
    display_created_at = ""
    if raw_created_at:
        try:
            dt = datetime.fromisoformat(raw_created_at)
            display_created_at = dt.strftime('%d-%b-%y')
        except:
            display_created_at = raw_created_at

    response = actions_table.scan(
        FilterExpression="ComplaintId = :complaint_id",
        ExpressionAttributeValues={":complaint_id": complaint_id}
    )
    actions = [a for a in response.get('Items', []) if not a.get('IsInternal', False)]
    
    # Calculate Last Updated relative time (Ignoring user actions for escalation logic)
    last_updated_str = "Unknown"
    elapsed_hours = 0
    
    # Filter for official actions (not from the user)
    official_actions = [a for a in actions if a.get('ActionType', '').lower() != 'user']
    
    latest_timestamp = raw_created_at
    if official_actions:
        latest_official_action = max(official_actions, key=itemgetter('ActionCreatedAt'))
        latest_timestamp = latest_official_action['ActionCreatedAt']
    
    last_updated_str = get_relative_time_string(latest_timestamp)
    
    # Calculate elapsed hours for logic
    try:
        dt = datetime.fromisoformat(latest_timestamp)
        if dt.tzinfo:
            from django.utils.timezone import now as tz_now
            current_time = tz_now()
        else:
            current_time = datetime.now()
        diff = current_time - dt
        elapsed_hours = diff.total_seconds() / 3600.0
    except:
        elapsed_hours = 0

    # Sort actions by date ascending for timeline (Oldest at Top / Chronological Flow)
    actions_sorted = sorted(actions, key=itemgetter('ActionCreatedAt'), reverse=False)
    for action in actions_sorted:
        a_at = action.get('ActionCreatedAt')
        if a_at:
            try:
                dt = datetime.fromisoformat(a_at)
                action['ActionCreatedAt'] = dt.strftime('%b %d, %I:%M%p')
            except:
                pass

    unique_dates = list(set([action['ActionCreatedAt'][:10] for action in actions_sorted if action.get('ActionCreatedAt')]))

    # Process tags to extract meaningful names if they are stored as JSON strings/dicts
    processed_tags = []
    for tag in tags:
        val = None
        if isinstance(tag, dict):
            # If it's already a dictionary
            val = tag.get('name') or tag.get('NAME')
        elif isinstance(tag, str) and '{' in tag:
            # If it's a string representation of a dictionary
            try:
                name_match = re.search(r"['\"]name['\"]:\s*['\"]([^'\"]*)['\"]", tag, re.IGNORECASE)
                if name_match:
                    val = name_match.group(1)
                else:
                    tag_fixed = tag.replace("'", '"')
                    tag_data = json.loads(tag_fixed)
                    val = tag_data.get('name') or tag_data.get('NAME')
            except:
                pass
        
        # If we successfully extracted a value, format it. Otherwise keep original.
        if val:
            processed_tags.append(str(val).title())
        else:
            processed_tags.append(str(tag).title() if isinstance(tag, str) else str(tag))

    return render(request, 'complaints/track_detail.html', {
        'complaints': raw_complaints,
        'complaint': raw_complaints[0],
        'Status': Status,
        'CreatedAt': display_created_at,
        'Description': description,
        'Subject': raw_complaints[0].get('Subject', 'Ticket Update'),
        'complaint_id': complaint_id,
        'actions': actions_sorted,
        'unique_dates': unique_dates,
        'status_updates': actions_sorted,
        'attachments': attachments,
        'attachments_count': len(attachments),
        'tags': processed_tags,
        'last_updated': last_updated_str,
        'elapsed_hours': elapsed_hours,
        'AssignedTo': get_user_name_by_sub(raw_complaints[0].get('AssignedTo', 'Unassigned')),
        'PendingActionHeading': raw_complaints[0].get('PendingActionHeading', ''),
        'PendingActionMessage': raw_complaints[0].get('PendingActionMessage', '')
    })


# views.py



# def login_page(request):
#     """Redirects the user to Cognito's Hosted UI for authentication."""
#     cognito_domain = "us-east-150fr3bbo2.auth.us-east-1.amazoncognito.com"
#     redirect_uri = "http://localhost:8000/callback"  # This must match the Callback URL in Cognito
#     client_id = '4bdntr8dv2s0rj27cspfamm85j'
#     response_type = "code"
#     scope = "openid email profile"
#     auth_url = f"https://{cognito_domain}/login?client_id={client_id}&response_type={response_type}&scope={scope}&redirect_uri={redirect_uri}"
#     return redirect(auth_url)


def callback(request):
    """Handles the callback after Cognito authentication."""
    code = request.GET.get('code')

    if not code:
        return redirect('/login')  # If no code is provided, redirect back to login.

    # Exchange the code for an access token
    token_url = f"https://cognito-idp.us-east-1.amazonaws.com/us-east-1_50fR3bBO2/oauth2/token"
    payload = {
        'grant_type': 'authorization_code',
        'client_id': settings.COGNITO_APP_CLIENT_ID,
        'code': code,
        'redirect_uri': 'http://localhost:8000',
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    response = requests.post(token_url, data=payload, headers=headers)
    tokens = response.json()

    access_token = tokens.get('access_token')
    id_token = tokens.get('id_token')

    if not access_token:
        return redirect('/login')  # If no token is found, redirect to login

    # Now, you can store the token (e.g., in the session or as a cookie)
    request.session['access_token'] = access_token  # Save the access token in the session

    # Decode the token to verify the user and extract user information (like username)
    try:
        payload = decode_jwt_token(id_token)  # Decode the ID token
        request.user = payload  # Store the user info in the request object
        return redirect('/')  # Redirect to the home page or dashboard
    except Exception as e:
        return redirect('/login')  # If token is invalid, go back to login


# def logout_page(request):
#     """Log the user out and redirect to home page."""
#     # Simply redirect to the Cognito logout URL
#     cognito_domain = "your-cognito-domain.auth.us-east-1.amazoncognito.com"
#     redirect_uri = "http://localhost:8000"
#     logout_url = f"https://{cognito_domain}/logout?client_id={settings.COGNITO_APP_CLIENT_ID}&logout_uri={redirect_uri}"
#     return redirect(logout_url)
@csrf_exempt
def analyze_media(request):
    """
    Analyzes uploaded media files (images/videos) using AWS services.
    Returns extracted details and description to populate in the complaint description.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST method is allowed'}, status=400)
    
    try:
        # Import the media analyzer
        from .media_analyzer import analyze_image, analyze_video, extract_complaint_details_from_media
        
        # Get the file from the request
        if 'file' not in request.FILES:
            return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
        
        uploaded_file = request.FILES['file']
        file_type = request.POST.get('file_type', 'image')  # 'image' or 'video'
        
        # Read file bytes
        file_bytes = uploaded_file.read()
        
        # Analyze based on file type
        if file_type == 'video' or uploaded_file.content_type.startswith('video/'):
            analysis_results = analyze_video(file_bytes)
        else:
            analysis_results = analyze_image(file_bytes)
        
        # Extract complaint-relevant details
        complaint_details = extract_complaint_details_from_media(analysis_results)
        
        # Return the results
        return JsonResponse({
            'success': True,
            'description': analysis_results.get('description', ''),
            'detected_text': analysis_results.get('text', ''),
            'labels': [label.get('name', label) if isinstance(label, dict) else label 
                      for label in analysis_results.get('labels', [])[:10]],
            'details': complaint_details
        })
        
    except Exception as e:
        print(f"Error analyzing media: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({
            'success': False,
            'error': f'Error analyzing media: {str(e)}'
        }, status=500)

@cognito_required
def agent_dashboard(request):
    """
    Agent Dashboard View:
    - Lists tickets assigned to the logged-in agent.
    - Allows switching tabs (My Tickets, Team Unassigned, Resolved).
    - Includes search filtering.
    """
    id_token = request.session.get('id_token')
    try:
        payload = decode_jwt_token(id_token)
    except:
        return redirect('login')
        
    # Use email for database lookup since the AI assignment uses emails
    username = payload.get('email', payload.get('preferred_username', payload.get('username', 'Unknown'))).lower()
    user_sub = payload.get('sub', '')
    
    given = payload.get('given_name', '')
    family = payload.get('family_name', '')
    full_name = f"{given} {family}".strip()
    
    # Expand identifiers for robust matching (DynamoDB is case-sensitive)
    identifiers = [username, username.capitalize(), user_sub]
    if full_name:
        identifiers.append(full_name)
        identifiers.append(full_name.lower())
        identifiers.append(full_name.title())
    
    # Add given_name and username-only variations if they exist
    if given: identifiers.append(given)
    if 'username' in payload: identifiers.append(payload['username'])
    
    # Clean up duplicates
    identifiers = list(set([i for i in identifiers if i]))

    # Extract Team/Group from Cognito Groups
    groups = payload.get('cognito:groups', [])
    
    # Mapping logic: Assuming groups like 'CUSTOMER_SUPPORT', 'TECH_SUPPORT' etc.
    # We take the first group that isn't 'USER' or 'ADMIN' as the team, or default to general.
    user_team = "General"
    for g in groups:
        if g not in ['USER', 'ADMIN', 'SUPERVISOR']:
            user_team = g
            break
            
    # Default tab
    tab = request.GET.get('tab', 'assigned')
    query = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')

    all_tickets = []
    
    try:
        if tab == 'assigned':
            # 1. My Open Tickets (Exclude Closed AND Resolved)
            scan_kwargs = {
                'FilterExpression': Attr('AssignedTo').is_in(identifiers) & ~Attr('Status').is_in(['Closed', 'Resolved'])
            }
        elif tab == 'team':
            # 2. Unassigned tickets for my team
            scan_kwargs = {
                'FilterExpression': Attr('AssignedTeam').eq(user_team) & Attr('AssignedTo').eq('Unassigned') & ~Attr('Status').is_in(['Closed', 'Resolved'])
            }
        elif tab == 'resolved':
            # 3. Tickets resolved by me (Status=Closed/Resolved)
            scan_kwargs = {
                'FilterExpression': Attr('AssignedTo').is_in(identifiers) & Attr('Status').is_in(['Closed', 'Resolved'])
            }
        else:
            scan_kwargs = {
                'FilterExpression': Attr('AssignedTo').is_in(identifiers)
            }

        # Apply Search Filter if exists
        if query:
            if 'FilterExpression' in scan_kwargs:
                scan_kwargs['FilterExpression'] = scan_kwargs['FilterExpression'] & (Attr('ComplaintId').contains(query) | Attr('Subject').contains(query))
            else:
                scan_kwargs['FilterExpression'] = (Attr('ComplaintId').contains(query) | Attr('Subject').contains(query))

        # Apply Status Filter if exists
        if status_filter and status_filter != 'All':
             if 'FilterExpression' in scan_kwargs:
                scan_kwargs['FilterExpression'] = scan_kwargs['FilterExpression'] & Attr('Status').eq(status_filter)
             else:
                scan_kwargs['FilterExpression'] = Attr('Status').eq(status_filter)

        # Apply Priority Filter if exists
        if priority_filter:
             if 'FilterExpression' in scan_kwargs:
                scan_kwargs['FilterExpression'] = scan_kwargs['FilterExpression'] & Attr('Priority').eq(priority_filter)
             else:
                scan_kwargs['FilterExpression'] = Attr('Priority').eq(priority_filter)

        response = complaints_table.scan(**scan_kwargs)
        all_tickets = response.get('Items', [])
        
        # Sort by Date (Newest First)
        all_tickets.sort(key=lambda x: x.get('CreatedAt', ''), reverse=True)

    except Exception as e:
        print(f"Error fetching tickets for agent dashboard: {e}")
        all_tickets = []

    # Calculate Stats for Cards (Separate fast queries or aggregations)
    # Ideally should use a summary table or index. For MVP, we scan or reuse.
    # Here we just mock or do a separate quick scan for COUNTS only if needed.
    # Let's count from the 'assigned' scan results? No, stats need global context for the user.
    
    # Simple Stats Fetch (Status-based for this agent)
    # Using a simple loop over a larger scan of this agent's tickets is better than multiple DB calls.
    # Let's fetch ALL tickets for this agent once for stats.
    
    stats = {
        'my_open': 0,
        'high_priority': 0,
        'resolved_lifetime': 0,
        'pending': 0
    }
    
    try:
        # Scan ALL tickets for this agent to compute stats
        # NOTE: Removed ProjectionExpression because 'Status' is a reserved keyword in DynamoDB
        stat_resp = complaints_table.scan(
            FilterExpression=Attr('AssignedTo').is_in(identifiers)
        )
        agent_all_tickets = stat_resp.get('Items', [])
        
        for t in agent_all_tickets:
            status = t.get('Status', 'Open')
            
            if status not in ['Closed', 'Resolved']:
                stats['my_open'] += 1
                if t.get('Priority') == 'High': 
                    stats['high_priority'] += 1
                if status == 'Pending':
                    stats['pending'] += 1
            else:
                stats['resolved_lifetime'] += 1
                      
    except Exception as e:
        print(f"Error stats: {e}")

    # Resolve customer names for the current page of tickets
    for ticket in all_tickets:
        customer_id = ticket.get('UserID', ticket.get('CustomerID', ''))
        customer_name = "Unknown"
        
        if customer_id:
            resolved_name = get_user_name_by_sub(customer_id)
            if resolved_name != customer_id: # get_user_name_by_sub returns sub if not found usually
                customer_name = resolved_name
            elif ticket.get('UserName'):
                customer_name = ticket.get('UserName')
        elif ticket.get('UserName'):
            customer_name = ticket.get('UserName')
            
        ticket['CustomerName'] = customer_name

    # Pagination
    paginator = Paginator(all_tickets, 10) # 10 per page
    page = request.GET.get('page')
    try:
        tickets = paginator.page(page)
    except:
        tickets = paginator.page(1)

    # Determine if any filter is active (including non-default tabs)
    is_filtered = bool(query or (status_filter and status_filter != 'All') or priority_filter or tab != 'assigned')

    context = {
        'tickets': tickets,
        'active_tab': tab,
        'user_name': payload.get('given_name', payload.get('name', username)),
        'user_role': 'Agent',
        'user_team': user_team.replace('_', ' ').title() if user_team else "General",
        'current_date': datetime.now().strftime('%A, %b %d • %I:%M %p'),
        'my_open_tickets_count': stats['my_open'],
        'high_priority_count': stats['high_priority'],
        'resolved_lifetime_count': stats['resolved_lifetime'],
        'pending_count': stats['pending'],
        'is_filtered': is_filtered
    }
    
    return render(request, 'complaints/agent_dashboard.html', context)

@cognito_required
def get_transfer_agents(request):
    """
    API View to fetch agents from OTHER teams (not the user's team).
    Used for the 'Transfer' button dropdown/modal.
    """
    try:
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_groups = payload.get('cognito:groups', [])
        
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        my_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
        
        # We need to fetch agents from ALL OTHER teams
        other_teams = [g for g in SUPPORT_GROUPS if g not in my_teams]
        print(f"DEBUG: get_transfer_agents - User Groups: {user_groups}")
        print(f"DEBUG: get_transfer_agents - My Teams: {my_teams}")
        print(f"DEBUG: get_transfer_agents - Other Teams: {other_teams}")
        
        grouped_agents = [] # List of {team: name, agents: []}
        
        # Scan stats only once if possible, or skip workload for transfer (faster)
        # We'll skip workload for transfer to keep it fast as per "keep it simple... fast"
        
        current_assignee = (request.GET.get('current_assignee') or '').strip().lower()
        
        for team in other_teams:
            users = list_users_in_group(team)
            
            # Pre-fetch groups for all users in this team to determine roles
            team_usernames = [u.get('Username') for u in users if u.get('Username')]
            team_user_groups = get_users_groups_bulk(team_usernames)

            team_agents = []
            for u in users:
                username = u.get('Username')
                email = ''
                name = username
                for attr in u.get('Attributes', []):
                    if attr['Name'] == 'email': email = attr['Value']
                    if attr['Name'] == 'given_name': name = attr['Value']
                
                # Exclude current assignee
                user_email_lower = email.strip().lower()
                user_name_lower = name.strip().lower()
                if user_email_lower == current_assignee or user_name_lower == current_assignee:
                    continue

                # Identify role from groups
                user_groups_list = team_user_groups.get(username, [])
                display_role = "Agent"
                if "ADMIN" in user_groups_list:
                    display_role = "Admin"
                elif "SUPERVISOR" in user_groups_list:
                    display_role = "Supervisor"

                team_agents.append({
                    'name': name,
                    'email': email,
                    'role': display_role
                })
            
            if team_agents:
                grouped_agents.append({
                    'team': team.replace('_', ' ').title(),
                    'agents': team_agents
                })
                
        return JsonResponse({'success': True, 'groups': grouped_agents})

    except Exception as e:
        print(f"DEBUG: Error in get_transfer_agents: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def create_insurance_transaction(complaint_id, claim_id, user_uuid, policy_no, amount="10.00"):
    """
    Creates a transaction record for an approved insurance claim.
    """
    txn_id = f"TXN-{random.randint(10000, 99999)}"
    now_iso = datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # Item structure
    item = {
        "TransactionID": txn_id,
        "CustomerID": user_uuid, 
        "ComplaintID": complaint_id, 
        "Currency": "USD",
        "Description": f"Insurance Claim Payout for Policy {policy_no}",
        "ErrorMessage": "",
        "EscalationStatus": "Not Escalated",
        "FraudFlag": "false",
        "LastUpdatedTimestamp": now_iso,
        "MerchantID": "INS-CLAIM-DEPT",
        "MerchantName": "Insurance Claims Dept",
        "PaymentMethod": "Bank Transfer",
        "ResolutionNotes": "Pending Agent Approval",
        "RetryCount": "0",
        "TransactionAmount": str(amount), 
        "TransactionDate": now_iso,
        "TransactionReceiptURL": "",
        "TransactionReferenceNumber": claim_id, 
        "TransactionReferenceNumber": claim_id, 
        "TransactionStatus": "Pending",
        "TransactionType": "Insurance Claim"
    }

    try:
        transaction_table.put_item(Item=item)
        print(f"Created Transaction {txn_id} for Claim {claim_id} Amount ${amount}")
        
        # Internal Action Log
        action_id = str(uuid.uuid4())
        actions_table.put_item(Item={
            'ActionId': action_id,
            'ComplaintId': complaint_id,
            'ActionType': 'System',
            'ActionDescription': (
                f"Generated Transaction {txn_id} for Claim {claim_id}. "
                f"Amount: ${amount}. "
                f"Reason: Automated Policy Coverage Verification ({policy_no}). "
                f"Status: Pending Agent Approval."
            ),
            'Timestamp': datetime.now().isoformat(),
            'PerformedBy': 'System (AI)'
        })
        return txn_id
        
    except Exception as e:
        print(f"Error creating transaction: {e}")
        return None

@cognito_required
def user_transactions(request):
    # In this project, request.user.first_name is mapped to the Cognito 'sub' (UUID)
    # request.user.name contains the human-readable given_name
    CustomerID = request.user.first_name 
    display_name = getattr(request.user, 'name', request.user.first_name)
    
    print(f"DEBUG: Fetching transactions for CustomerID (sub): {CustomerID}")
    
    try:
        # Use scan with Attr filter for consistent behavior
        response = transaction_table.scan(
            FilterExpression=Attr('CustomerID').eq(CustomerID)
        )
        transactions = response.get('Items', [])
        print(f"DEBUG: Found {len(transactions)} transactions")
        
        # Sort by date desc
        transactions.sort(key=lambda x: str(x.get('TransactionDate') or ''), reverse=True)
    except Exception as e:
        print(f"Error fetching transactions: {e}")
        transactions = []

    return render(request, 'complaints/user_transactions.html', {
        'transactions': transactions,
        'first_name': display_name
    })

@cognito_required
def transaction_detail(request, transaction_id):
    print(f"DEBUG: Accessing transaction_detail for ID: {transaction_id}")
    try:
        # Fetch transaction - Using scan to handle cases where TransactionID might not be the partition key
        response = transaction_table.scan(
            FilterExpression=Attr('TransactionID').eq(transaction_id)
        )
        
        items = response.get('Items', [])
        transaction = items[0] if items else None
        
        if not transaction:
            print(f"DEBUG: Transaction {transaction_id} not found in DB")
            return HttpResponse("Transaction not found", status=404)
        
        complaint_id = transaction.get('ComplaintID')
        print(f"DEBUG: Associated ComplaintID from transaction: {complaint_id}")
        
        complaint = {}
        if complaint_id:
            # First try to fetch complaint by ComplaintId
            comp_resp = complaints_table.scan(
                FilterExpression="ComplaintId = :id",
                ExpressionAttributeValues={":id": complaint_id}
            )
            complaints_list = comp_resp.get('Items', [])
            
            # If not found, the transaction might store AgreementNo instead of ComplaintId
            # Try searching by AgreementNo
            if not complaints_list:
                print(f"DEBUG: Complaint not found by ComplaintId, trying AgreementNo...")
                comp_resp = complaints_table.scan(
                    FilterExpression="AgreementNo = :agr",
                    ExpressionAttributeValues={":agr": complaint_id}
                )
                complaints_list = comp_resp.get('Items', [])
            
            complaint = complaints_list[0] if complaints_list else {}
            print(f"DEBUG: Found complaint: {complaint.get('ComplaintId', 'Not found')}")
        
        # Policy info
        desc = transaction.get('Description', '')
        policy_no = desc.split('Policy ')[-1] if 'Policy ' in desc else ''
        policy = {}
        if policy_no:
             # Use scan for policy too to avoid schema mismatches
             pol_resp = policy_table.scan(
                 FilterExpression=Attr('PolicyNo').eq(policy_no.strip())
             )
             policies = pol_resp.get('Items', [])
             policy = policies[0] if policies else {}

        # Actions for timeline - use actual ComplaintId from complaint record
        actions = []
        actual_complaint_id = complaint.get('ComplaintId') if complaint else None
        if actual_complaint_id:
            act_resp = actions_table.scan(FilterExpression=Attr('ComplaintId').eq(actual_complaint_id))
            actions = [a for a in act_resp.get('Items', []) if not a.get('IsInternal', False)]
        
        # Sort and Format actions
        actions.sort(key=lambda x: str(x.get('ActionCreatedAt') or x.get('Timestamp') or ''), reverse=True)
        for action in actions:
            a_at = action.get('ActionCreatedAt') or action.get('Timestamp')
            if a_at:
                try:
                    dt = datetime.fromisoformat(str(a_at))
                    action['display_date'] = dt.strftime('%b %d, %Y - %I:%M %p')
                except:
                    action['display_date'] = str(a_at)

        # AI Resolution from actions
        ai_action = next((a for a in actions if 'AI' in (a.get('ActionBy') or a.get('PerformedBy') or '')), None)
        ai_resolution = ai_action.get('ActionDescription', '') if ai_action else ""

        # Format Complaint CreatedAt
        c_at = complaint.get('CreatedAt')
        display_created_at = ""
        if c_at:
            try:
                dt = datetime.fromisoformat(c_at)
                display_created_at = dt.strftime('%d-%b-%y')
            except:
                display_created_at = c_at

        # Extract damage type from complaint tags or description
        damage_type = "N/A"
        tags = complaint.get('Tags', [])
        if tags:
            # Look for damage-related tags
            for tag in tags:
                tag_name = tag.get('name', tag) if isinstance(tag, dict) else str(tag)
                if any(keyword in tag_name.lower() for keyword in ['damage', 'accident', 'collision', 'theft', 'fire', 'flood', 'vandalism']):
                    damage_type = tag_name
                    break
            # If no damage tag found, use the first tag as category
            if damage_type == "N/A" and tags:
                first_tag = tags[0]
                damage_type = first_tag.get('name', first_tag) if isinstance(first_tag, dict) else str(first_tag)
        
        # Try to extract from description if still N/A
        if damage_type == "N/A":
            desc_lower = (complaint.get('Description', '') or '').lower()
            if 'accident' in desc_lower or 'collision' in desc_lower:
                damage_type = "Collision/Accident"
            elif 'theft' in desc_lower or 'stolen' in desc_lower:
                damage_type = "Theft"
            elif 'fire' in desc_lower:
                damage_type = "Fire Damage"
            elif 'flood' in desc_lower or 'water' in desc_lower:
                damage_type = "Water/Flood Damage"
            elif 'scratch' in desc_lower or 'dent' in desc_lower:
                damage_type = "Body Damage"

        # actual_complaint_id is already defined above when fetching actions

        return render(request, 'complaints/transaction_track_detail.html', {
            'transaction': transaction,
            'complaint': complaint,
            'policy': policy,
            'actions': actions,
            'ai_resolution': ai_resolution,
            'complaint_id': actual_complaint_id,
            'display_created_at': display_created_at,
            'attachments': complaint.get('Attachments') or [],
            'damage_type': damage_type,
            'first_name': request.user.first_name
        })
    except Exception as e:
        import traceback
        print(f"Error in transaction_detail: {e}")
        traceback.print_exc()
        return HttpResponse(f"Internal Server Error: {e}", status=500)


# ================================
# Reassignment Request Functions
# ================================

@cognito_required
def create_reassignment_request(request, complaint_id):
    """
    Agent creates a reassignment request for a ticket.
    Stores request in ReassignmentRequests DynamoDB table.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST method allowed'}, status=405)
    
    try:
        # Get user info
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_email = payload.get('email', '')
        user_name = payload.get('given_name', payload.get('name', 'Agent'))
        user_groups = payload.get('cognito:groups', [])
        
        # Get request data
        reason = request.POST.get('reason', '').strip()
        comments = request.POST.get('comments', '').strip()
        
        if not reason:
            return JsonResponse({'success': False, 'error': 'Reason is required'}, status=400)
        
        # Fetch complaint details
        complaint_response = complaints_table.scan(
            FilterExpression="ComplaintId = :id",
            ExpressionAttributeValues={":id": complaint_id}
        )
        complaints = complaint_response.get('Items', [])
        if not complaints:
            return JsonResponse({'success': False, 'error': 'Complaint not found'}, status=404)
        
        complaint = complaints[0]
        
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        
        # Determine supervisor team (from agent's groups)
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        agent_team = next((g for g in user_groups if g in SUPPORT_GROUPS), 'GENERAL')
        
        # Create reassignment request
        reassignment_request = {
            'RequestId': request_id,
            'ComplaintId': complaint_id,
            'TicketSubject': complaint.get('Subject', 'No Subject'),
            'TicketPriority': complaint.get('Priority', 'Normal'),
            'RequestedBy': user_email,
            'RequestedByName': user_name,
            'AgentTeam': agent_team,
            'Reason': reason,
            'AgentComments': comments,
            'Status': 'Pending',  # Pending, Approved, Rejected
            'CreatedAt': datetime.now().isoformat(),
            'UpdatedAt': datetime.now().isoformat(),
            'SupervisorEmail': None,
            'SupervisorName': None,
            'SupervisorComments': None,
            'ProcessedAt': None
        }
        
        # Save to DynamoDB
        reassignment_requests_table.put_item(Item=reassignment_request)
        
        # Log action in ComplaintActions
        actions_table.put_item(
            Item={
                'ActionId': str(uuid.uuid4()),
                'ComplaintId': complaint_id,
                'ActionCreatedAt': datetime.now().isoformat(),
                'ActionDescription': f"Reassignment requested by {user_name}. Reason: {reason}",
                'ActionType': 'System',
                'Status': 'Reassignment Requested',
                'UserId': user_email,
                'ActionBy': user_name,
                'IsInternal': True
            }
        )
        
        return JsonResponse({
            'success': True, 
            'message': 'Reassignment request submitted successfully',
            'request_id': request_id
        })
        
    except Exception as e:
        print(f"Error creating reassignment request: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@cognito_required
def get_pending_reassignment_requests(request):
    """
    Supervisor/Admin fetches pending reassignment requests for their team.
    Returns JSON list of pending requests.
    """
    try:
        # Get user info and validate role
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_groups = payload.get('cognito:groups', [])
        
        # Only ADMIN or SUPERVISOR can view requests
        if 'ADMIN' not in user_groups and 'SUPERVISOR' not in user_groups:
            return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
        
        # Determine which teams the supervisor manages
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        supervisor_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
        is_admin = 'ADMIN' in user_groups
        
        # Fetch all pending requests
        response = reassignment_requests_table.scan(
            FilterExpression="attribute_exists(#s) AND #s = :pending",
            ExpressionAttributeNames={'#s': 'Status'},
            ExpressionAttributeValues={':pending': 'Pending'}
        )
        
        all_requests = response.get('Items', [])
        
        # Filter by team (unless admin)
        if is_admin:
            filtered_requests = all_requests
        else:
            filtered_requests = [r for r in all_requests if r.get('AgentTeam') in supervisor_teams]
        
        # Sort by creation date (newest first)
        filtered_requests.sort(key=lambda x: x.get('CreatedAt', ''), reverse=True)
        
        # Format for response
        formatted_requests = []
        for req in filtered_requests:
            created_at = req.get('CreatedAt', '')
            formatted_date = ''
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', ''))
                    formatted_date = dt.strftime('%b %d, %Y %H:%M')
                except:
                    formatted_date = created_at
            
            formatted_requests.append({
                'request_id': req.get('RequestId'),
                'complaint_id': req.get('ComplaintId'),
                'ticket_subject': req.get('TicketSubject', 'No Subject'),
                'ticket_priority': req.get('TicketPriority', 'Normal'),
                'requested_by': req.get('RequestedByName', req.get('RequestedBy', 'Unknown')),
                'requested_by_email': req.get('RequestedBy', ''),
                'agent_team': req.get('AgentTeam', 'General').replace('_', ' ').title(),
                'reason': req.get('Reason', ''),
                'comments': req.get('AgentComments', ''),
                'created_at': formatted_date,
                'status': req.get('Status', 'Pending')
            })
        
        return JsonResponse({
            'success': True,
            'requests': formatted_requests,
            'count': len(formatted_requests)
        })
        
    except Exception as e:
        print(f"Error fetching reassignment requests: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@cognito_required
def process_reassignment_request(request, request_id):
    """
    Supervisor approves or rejects a reassignment request.
    Action: 'approve' or 'reject'
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST method allowed'}, status=405)
    
    try:
        # Get user info and validate role
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_groups = payload.get('cognito:groups', [])
        supervisor_email = payload.get('email', '')
        supervisor_name = payload.get('given_name', payload.get('name', 'Supervisor'))
        
        # Only ADMIN or SUPERVISOR can process requests
        if 'ADMIN' not in user_groups and 'SUPERVISOR' not in user_groups:
            return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
        
        # Get action and comments
        action = request.POST.get('action', '').lower()  # 'approve' or 'reject'
        supervisor_comments = request.POST.get('comments', '').strip()
        new_agent_email = request.POST.get('new_agent_email', '')  # For approval, assign to new agent
        new_agent_name = request.POST.get('new_agent_name', '')
        
        if action not in ['approve', 'reject']:
            return JsonResponse({'success': False, 'error': 'Invalid action. Use approve or reject.'}, status=400)
        
        # Fetch the request
        response = reassignment_requests_table.scan(
            FilterExpression="RequestId = :id",
            ExpressionAttributeValues={":id": request_id}
        )
        
        requests_list = response.get('Items', [])
        if not requests_list:
            return JsonResponse({'success': False, 'error': 'Request not found'}, status=404)
        
        reassign_req = requests_list[0]
        complaint_id = reassign_req.get('ComplaintId')
        
        # Update request status
        new_status = 'Approved' if action == 'approve' else 'Rejected'
        
        reassignment_requests_table.update_item(
            Key={'RequestId': request_id},
            UpdateExpression="""
                SET #s = :status, 
                    SupervisorEmail = :sup_email, 
                    SupervisorName = :sup_name, 
                    SupervisorComments = :sup_comments, 
                    ProcessedAt = :processed_at,
                    UpdatedAt = :updated_at
            """,
            ExpressionAttributeNames={'#s': 'Status'},
            ExpressionAttributeValues={
                ':status': new_status,
                ':sup_email': supervisor_email,
                ':sup_name': supervisor_name,
                ':sup_comments': supervisor_comments,
                ':processed_at': datetime.now().isoformat(),
                ':updated_at': datetime.now().isoformat()
            }
        )
        
        # If approved, update the complaint's assignment (if new agent provided)
        if action == 'approve' and new_agent_email:
            complaints_table.update_item(
                Key={'ComplaintId': complaint_id},
                UpdateExpression="set AssignedTo = :a, AssignedAgentName = :n",
                ExpressionAttributeValues={':a': new_agent_email, ':n': new_agent_name or new_agent_email}
            )
        
        # Log action in ComplaintActions
        action_desc = f"Reassignment request {new_status.lower()} by {supervisor_name}."
        if supervisor_comments:
            action_desc += f" Comments: {supervisor_comments}"
        if action == 'approve' and new_agent_email:
            action_desc += f" Ticket reassigned to {new_agent_name or new_agent_email}."
        
        actions_table.put_item(
            Item={
                'ActionId': str(uuid.uuid4()),
                'ComplaintId': complaint_id,
                'ActionCreatedAt': datetime.now().isoformat(),
                'ActionDescription': action_desc,
                'ActionType': 'System',
                'Status': f'Reassignment {new_status}',
                'UserId': supervisor_email,
                'ActionBy': supervisor_name,
                'IsInternal': True
            }
        )
        
        return JsonResponse({
            'success': True, 
            'message': f'Reassignment request {new_status.lower()} successfully',
            'status': new_status
        })
        
    except Exception as e:
        print(f"Error processing reassignment request: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@cognito_required
def get_agent_reassignment_requests(request):
    """
    Agent fetches their own reassignment requests (to see status).
    """
    try:
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_email = payload.get('email', '')
        
        # Fetch all requests by this agent
        response = reassignment_requests_table.scan(
            FilterExpression="RequestedBy = :email",
            ExpressionAttributeValues={":email": user_email}
        )
        
        requests_list = response.get('Items', [])
        
        # Sort by creation date (newest first)
        requests_list.sort(key=lambda x: x.get('CreatedAt', ''), reverse=True)
        
        # Format for response
        formatted_requests = []
        for req in requests_list[:10]:  # Limit to 10 most recent
            created_at = req.get('CreatedAt', '')
            formatted_date = ''
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', ''))
                    formatted_date = dt.strftime('%b %d, %Y %H:%M')
                except:
                    formatted_date = created_at
            
            formatted_requests.append({
                'request_id': req.get('RequestId'),
                'complaint_id': req.get('ComplaintId'),
                'ticket_subject': req.get('TicketSubject', 'No Subject'),
                'reason': req.get('Reason', ''),
                'status': req.get('Status', 'Pending'),
                'supervisor_comments': req.get('SupervisorComments', ''),
                'created_at': formatted_date,
                'processed_at': req.get('ProcessedAt', '')
            })
        
        return JsonResponse({
            'success': True,
            'requests': formatted_requests
        })
        
    except Exception as e:
        print(f"Error fetching agent reassignment requests: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
@cognito_required
def get_ticket_reassignment_status(request, complaint_id):
    """
    Checks if a specific ticket has a pending reassignment request.
    """
    try:
        response = reassignment_requests_table.scan(
            FilterExpression="ComplaintId = :cid AND #s = :pending",
            ExpressionAttributeNames={'#s': 'Status'},
            ExpressionAttributeValues={':cid': complaint_id, ':pending': 'Pending'}
        )
        items = response.get('Items', [])
        
        if items:
            req = items[0]
            return JsonResponse({
                'success': True,
                'has_pending': True,
                'request': {
                    'request_id': req.get('RequestId'),
                    'requested_by': req.get('RequestedByName', req.get('RequestedBy', 'Unknown')),
                    'reason': req.get('Reason', ''),
                    'comments': req.get('AgentComments', ''),
                    'created_at': req.get('CreatedAt', '')
                }
            })
        
        return JsonResponse({'success': True, 'has_pending': False})
        
    except Exception as e:
        print(f"Error checking ticket reassignment status: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
@cognito_required
def get_all_reassignment_requests(request):
    """
    Supervisor/Admin fetches ALL reassignment requests for their team (Pending, Approved, Rejected).
    """
    try:
        id_token = request.session.get('id_token')
        payload = decode_jwt_token(id_token)
        user_groups = payload.get('cognito:groups', [])
        
        if 'ADMIN' not in user_groups and 'SUPERVISOR' not in user_groups:
            return JsonResponse({'success': False, 'error': 'Unauthorized'}, status=403)
        
        SUPPORT_GROUPS = ["TECH_SUPPORT", "CUSTOMER_SUPPORT", "FINANCE_SUPPORT", "INSURANCE_TEAM", "BANKING_SUPPORT"]
        supervisor_teams = [g for g in user_groups if g in SUPPORT_GROUPS]
        is_admin = 'ADMIN' in user_groups
        
        # Scan all requests (no Status filter initially to get history)
        response = reassignment_requests_table.scan()
        all_requests = response.get('Items', [])
        
        # Filter by team (unless admin)
        if is_admin:
            filtered_requests = all_requests
        else:
            filtered_requests = [r for r in all_requests if r.get('AgentTeam') in supervisor_teams]
        
        # Sort by creation date (newest first)
        filtered_requests.sort(key=lambda x: x.get('CreatedAt', ''), reverse=True)
        
        formatted_requests = []
        for req in filtered_requests:
            created_at = req.get('CreatedAt', '')
            formatted_date = ''
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at.replace('Z', ''))
                    formatted_date = dt.strftime('%b %d, %H:%M')
                except:
                    formatted_date = created_at
            
            formatted_requests.append({
                'request_id': req.get('RequestId'),
                'complaint_id': req.get('ComplaintId'),
                'ticket_subject': req.get('TicketSubject', 'No Subject'),
                'requested_by': req.get('RequestedByName', req.get('RequestedBy', 'Unknown')),
                'agent_team': req.get('AgentTeam', 'General').replace('_', ' ').title(),
                'reason': req.get('Reason', ''),
                'status': req.get('Status', 'Pending'),
                'created_at': formatted_date
            })
        
        return JsonResponse({
            'success': True,
            'requests': formatted_requests,
            'pending_count': len([r for r in formatted_requests if r['status'] == 'Pending'])
        })
        
    except Exception as e:
        print(f"Error fetching all reassignment requests: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
