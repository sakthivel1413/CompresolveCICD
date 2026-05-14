import boto3
import time
import os
import requests
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

# AWS credentials and region
aws_access_key_id = settings.AWS_ACCESS_KEY_ID
aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
region_name = settings.AWS_REGION_NAME

# Initialize AWS clients
s3_client = boto3.client('s3', region_name=region_name, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
transcribe_client = boto3.client('transcribe', region_name=region_name, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)

# S3 bucket details
bucket_name = 'complaint-attachments-tcs'  # Replace with your S3 bucket name

# Upload a file to S3
def upload_to_s3(local_file_path, s3_bucket_name):
    print(f"Uploading {local_file_path} to S3 bucket {s3_bucket_name}...")
    try:
        s3_client.upload_file(local_file_path, s3_bucket_name, os.path.basename(local_file_path))
        s3_uri = f"s3://{s3_bucket_name}/{os.path.basename(local_file_path)}"
        print(f"File uploaded successfully. S3 URI: {s3_uri}")
        return s3_uri
    except Exception as e:
        print(f"Error uploading file: {e}")
        return None

# Start the transcription job
def start_transcription_job(audio_file_uri, job_name):
    print(f"Starting transcription job: {job_name} for audio file: {audio_file_uri}")
    try:
        response = transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode='en-US',  # Adjust language code as necessary
            Media={'MediaFileUri': audio_file_uri},
            MediaFormat='webm',  # Change format based on your file type (e.g., 'mp3', 'webm')
            OutputBucketName=bucket_name,  # Replace with your S3 output bucket
        )
        print(f"Transcription job {job_name} started successfully.")
        return response
    except Exception as e:
        print(f"Error starting transcription job: {e}")
        return None

# Check transcription job status
def check_transcription_job_status(job_name):
    print(f"Checking status for transcription job: {job_name}")
    try:
        response = transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
        status = response['TranscriptionJob']['TranscriptionJobStatus']
        print(f"Job {job_name} status: {status}")
        return status, response
    except Exception as e:
        print(f"Error checking transcription job status: {e}")
        return None, None

# Generate presigned URL for downloading the transcript
def download_transcript_with_presigned_url(bucket_name, object_key):
    print(f"Generating presigned URL for transcription file: {object_key}")
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': object_key},
            ExpiresIn=3600  # URL expires in 1 hour
        )
        print(f"Generated presigned URL: {url}")
        return url
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None

# Extract the transcript from JSON data
def extract_transcript(json_data):
    try:
        transcript = json_data['results']['transcripts'][0]['transcript']
        return transcript
    except KeyError:
        print("Error: Transcript not found in the response.")
        return None

# Django view for handling transcription request
@csrf_exempt
def transcribe_audio(request):
    if request.method == 'POST' and request.FILES.get('audio_file'):
        audio_file = request.FILES['audio_file']
        # Get the base directory of the project
        BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # Ensure the 'temp' folder exists within the base directory
        temp_dir = os.path.join(BASE_DIR, 'temp')

        # Check if the temp directory exists; if not, create it
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        # Save the uploaded file in the temp directory
        file_path = os.path.join(temp_dir, audio_file.name)
        with open(file_path, 'wb') as f:
            for chunk in audio_file.chunks():
                f.write(chunk)
        
        # Upload the file to S3
        s3_uri = upload_to_s3(file_path, bucket_name)
        if not s3_uri:
            return JsonResponse({'error': 'Failed to upload file to S3'}, status=500)
        
        job_name = f'transcription-job-{int(time.time())}'  # Unique job name
        print("\nStarting transcription process...\n")
        
        # Start the transcription job
        start_response = start_transcription_job(s3_uri, job_name)
        if not start_response:
            return JsonResponse({'error': 'Failed to start transcription job'}, status=500)
        
        # Check job status
        status = 'IN_PROGRESS'
        while status == 'IN_PROGRESS':
            print("\nWaiting for transcription to complete...")
            time.sleep(5)  # Reduced wait time to 5 seconds before checking again
            status, response = check_transcription_job_status(job_name)
        
        if status == 'COMPLETED':
            print("\nTranscription completed successfully!")
            transcription_uri = response['TranscriptionJob']['Transcript']['TranscriptFileUri']
            print(f"Transcription is available at: {transcription_uri}")
            
            # Generate presigned URL for downloading the transcription file
            transcript_key = transcription_uri.split('/')[-1]  # Extract the object key from the URL
            presigned_url = download_transcript_with_presigned_url(bucket_name, transcript_key)
            
            # Download the transcript
            if not presigned_url:
                return JsonResponse({'error': 'Failed to generate presigned URL'}, status=500)
            
            response = requests.get(presigned_url)
            if response.status_code == 200:
                transcript_json = response.json()
                transcript_text = extract_transcript(transcript_json)
                if transcript_text:
                    return JsonResponse({'transcription': transcript_text})
                else:
                    return JsonResponse({'error': 'Failed to extract transcript from the response'}, status=500)
            else:
                return JsonResponse({'error': f'Failed to download the transcript: {response.status_code}'}, status=500)
        else:
            return JsonResponse({'error': f'Transcription job failed with status: {status}'}, status=500)
    else:
        return JsonResponse({'error': 'No audio file uploaded'}, status=400)
