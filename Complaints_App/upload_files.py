import boto3
from botocore.client import Config
import requests
from django.conf import settings

# -------------------------------
# AWS SETTINGS FROM DJANGO
# -------------------------------
aws_access_key_id = settings.AWS_ACCESS_KEY_ID
aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_REGION_NAME

# -------------------------------
# CONFIGURE YOUR S3 BUCKET & FILE
# -------------------------------
BUCKET_NAME = "complaint-attachments-tcs"

# Set your complaint folder and file name
COMPLAINT_ID = "12345"
#LOCAL_FILE_PATH = "register.png"    # <-- change here
FILE_NAME_IN_S3 = "registerS3v1.png"                             # <-- change here

# S3 object key
S3_KEY = f"complaints/{COMPLAINT_ID}/{FILE_NAME_IN_S3}"

# -------------------------------
# STEP 1: CREATE PRESIGNED POST
# -------------------------------
def create_presigned_post(bucket, key):
    s3 = boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        config=Config(signature_version="s3v4")
    )

    presigned_post = s3.generate_presigned_post(
        Bucket=bucket,
        Key=key,
        Fields={"Content-Type": "image/png"},
        Conditions=[
            {"Content-Type": "image/png"},
            ["content-length-range", 1, 50_000_000]  # max 50 MB
        ],
        ExpiresIn=3600
    )

    return presigned_post


# -------------------------------
# STEP 2: UPLOAD USING PRESIGNED POST
# -------------------------------
def upload_file_to_s3(presigned_post, local_path):
    with open(local_path, "rb") as f:
        files = {"file": (local_path, f, "image/png")}
        response = requests.post(presigned_post["url"],
                                 data=presigned_post["fields"],
                                 files=files)

    if response.status_code in (200, 204):
        print("✅ File uploaded successfully!")
        print("📁 S3 Path:", presigned_post["fields"]["key"])
        return(presigned_post["fields"]["key"])
    else:
        print("❌ Upload failed:", response.status_code)
        return(response.text)


# -------------------------------
# MAIN EXECUTION
# -------------------------------
def upload_files(local_file_path, complaint_id):
    print("📤 upload_files() triggered")
    print("➡ local_file_path:", local_file_path)
    print("➡ complaint_id:", complaint_id)

    file_name = local_file_path.split('\\')[-1]
    print("📄 File name extracted:", file_name)

    new_file_name = f"{complaint_id}_{file_name}"  # Combine complaint ID and original file name
    print(f"New file name: {new_file_name}")

    s3_key = f"complaints/{complaint_id}/{new_file_name}"
    print("🪣 S3 key will be:", s3_key)

    presigned_post = create_presigned_post(BUCKET_NAME, s3_key)
    print("🔑 Presigned POST generated")

    uploaded_key = upload_file_to_s3(presigned_post, local_file_path)
    print("✅ upload_file_to_s3 returned:", uploaded_key)

    return uploaded_key

