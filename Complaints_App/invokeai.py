import boto3
import json
from botocore.exceptions import ClientError
from django.conf import settings

# AWS Settings
aws_access_key_id = settings.AWS_ACCESS_KEY_ID
aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_REGION_NAME

# Create a Bedrock Runtime client
client = boto3.client(
    "bedrock-runtime", 
    region_name=AWS_REGION,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key
)
description="I have attached my policy details please proced the claim fro my damanged vechiele"
category=['Insurance claim', 'Status Enquiry', 'Payment issue', 'Transaction Issue', 'Dispute', 'Fraud alert', 'General Enquiry']
prompt = f"""
Description: {description}
Based on the attached description, generate intent tags to classify the complaint under a category {category}.
"""
# Set the model ID, e.g., Titan Text Premier.
model_id = "amazon.nova-pro-v1:0"

def callAI(description_for_ai):
    # Define the prompt for the model.
    prompt = f"""
    Complaint Description: {description_for_ai}
    You are a L1 team member who receive comaplint from customer. (if you unable to decide what to say, just wel, we got you comaplint and currently working on, please track for more details)
    Based on the above description, response to customer with a short 2 sentence message.
    """
    
    # Format the conversation payload for the Converse API
    conversation = [
        {
            "role": "user",
            "content": [{"text": prompt}],
        }
    ]

    try:
        # Send the message to the model using client.converse
        response = client.converse(
            modelId=model_id,
            messages=conversation,
            inferenceConfig={"maxTokens": 512, "temperature": 0.5, "topP": 0.9},
        )

        # Extract and print the response text.
        response_text = response["output"]["message"]["content"][0]["text"]
        print("AI Response")
        print(response_text)
        return response_text

    except (ClientError, Exception) as e:
        print(f"ERROR: Can't invoke '{model_id}'. Reason: {e}")
        # Return a friendly error message instead of crashing
        return "I'm sorry, I am currently unable to process your request."

#result=callAI(prompt)


def callAIForIntent(prompt):
    # Define the prompt for the model.
    conversation = [
        {
            "role": "user",
            "content": [{"text": prompt}],
        }
    ]

    try:
        # Send the message to the model using client.converse
        response = client.converse(
            modelId=model_id,
            messages=conversation,
            inferenceConfig={"maxTokens": 512, "temperature": 0.5, "topP": 0.9},
        )

        # Extract and print the response text.
        response_text = response["output"]["message"]["content"][0]["text"]
        print("AI Response")
        print(response_text)
        return response_text

    except (ClientError, Exception) as e:
        print(f"ERROR: Can't invoke '{model_id}'. Reason: {e}")
        return "Error processing intent."
# result1=callAIForIntent(prompt)