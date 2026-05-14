import os
import boto3
import json
import re
from django.conf import settings

# Read AWS creds from settings
aws_access_key_id = settings.AWS_ACCESS_KEY_ID
aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_REGION_NAME
#COMPREHEND_ENDPOINT_ARN = ''

# Flag to toggle between pre-trained and custom model (real-time endpoint)
USE_REALTIME_MODEL = False

# Create boto3 client (will use env-based credentials if keys empty)
comprehend = boto3.client(
    "comprehend",
    region_name=AWS_REGION,
    aws_access_key_id=aws_access_key_id,
    aws_secret_access_key=aws_secret_access_key,
)

def generate_intent_tags(complaint_description):
    try:
        # Replace this with your endpoint ARN
        endpoint_arn = settings.comprehendEndpointArn#"arn:aws:comprehend:us-east-1:689978033638:document-classifier-endpoint/v6Endpoint"

        # Call Amazon Comprehend to classify the complaint using the custom model
        response = comprehend.classify_document(
            Text=complaint_description,
            EndpointArn=endpoint_arn,  # Use the Endpoint ARN here
        )

        # Print the full response for debugging
        print("Comprehend Response:", json.dumps(response, indent=4))

        # Extract the predicted intents (Classes)
        classes = response.get("Classes", [])
        
        # Convert to list of dicts with name and score
        tags = [{"name": c["Name"], "score": str(c["Score"])} for c in classes]
        
        return tags

    except Exception as e:
        print(f"Error occurred while calling Comprehend: {str(e)}")
        # Better fallback: Use Bedrock to classify if Comprehend fails
        return classify_intent_with_bedrock(complaint_description)

def classify_intent_with_bedrock(description):
    """
    Uses Bedrock to classify the complaint into specific categories for higher accuracy.
    """
    try:
        bedrock = boto3.client(
            service_name='bedrock-runtime', 
            region_name=AWS_REGION,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        
        categories = ['Status Enquiry', 'Insurance Claim', 'Payment issue', 'Transaction Issue', 'Dispute', 'Fraud alert', 'General']
        
        prompt = f"""
        Classify the following customer complaint into exactly ONE of these categories: {', '.join(categories)}.
        Return ONLY the category name.
        
        Complaint: "{description}"
        Category:"""
        
        model_id = "amazon.nova-pro-v1:0"
        
        conversation = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        response = bedrock.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=conversation,
            inferenceConfig={"maxTokens": 20, "temperature": 0.1, "topP": 0.9},
        )

        category_name = response["output"]["message"]["content"][0]["text"].strip()
        
        # Validate category_name is in our list
        final_category = "General"
        for cat in categories:
            if cat.lower() in category_name.lower():
                final_category = cat
                break
                
        return [{"name": final_category, "score": "1.0"}]

    except Exception as e:
        print(f"Error classifying with Bedrock: {str(e)}")
        return [{"name": "General", "score": "0.0"}]

def generate_ai_resolution(description, category):
    """
    Generates a helpful resolution/response for the customer using AWS Bedrock.
    """
    try:
        bedrock = boto3.client(
            service_name='bedrock-runtime', 
            region_name=AWS_REGION,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        
        prompt = f"""
        You are an expert customer support AI for a financial and service portal named CompResolve. 
        A customer has submitted a complaint in the category "{category}".
        
        Customer Complaint: "{description}"
        
        Task:
        1. Analyze the customer's problem.
        2. Provide a professional, empathetic, and highly accurate first-step resolution.
        3. If it involves a bank statement error (like incorrect entries), explain that we will initiate a reconciliation process and suggest they double-check their transaction history.
        4. If it's a technical login issue, suggest clearing cookies or using a different browser.
        5. Keep it concise (max 100 words), actionable, and friendly.
        6. Do not use generic placeholders.
        
        Resolution:"""
        
        model_id = "amazon.nova-pro-v1:0"
        
        conversation = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        response = bedrock.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=conversation,
            inferenceConfig={"maxTokens": 200, "temperature": 0.6, "topP": 0.9},
        )

        resolution = response["output"]["message"]["content"][0]["text"].strip()
        return resolution

    except Exception as e:
        print(f"Error generating resolution with Bedrock: {str(e)}")
        return "Thank you for bringing this to our attention. Our specialized support team has been assigned to your case and will conduct a thorough review of your account and the details provided. We aim to provide a comprehensive update or resolution within 24 hours."

def extract_insurance_details(description):
    """
    Extracts policy number, vehicle number, and damage details from the description using AWS Bedrock.
    Falls back to Regex if Bedrock fails.
    """
    policy_number = None
    vehicle_number = None
    damage_type = "Unknown"

    # 1. Try extraction with Bedrock (highly accurate for unstructured text)
    try:
        bedrock = boto3.client(
            service_name='bedrock-runtime', 
            region_name=AWS_REGION,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        
        prompt = f"""
        Extract the following insurance details from the text. 
        
        Text: "{description}"
        
        Fields to extract:
        1. Policy Number: (Numbers starting with POL)
        2. Vehicle/Registration Number: (Registration numbers like TN02BK1721)
        3. Damage: (Look for the SPECIFIC PART of the car damaged, e.g., 'door', 'bumper', 'windshield', 'glass', 'mirror'. If NO specific damage or affected part is mentioned, return 'None')
        
        Return ONLY a JSON object with keys "policy_number", "vehicle_number", and "damage_type". 
        Use 'None' as a string if a field is not found.
        """
        
        conversation = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        response = bedrock.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=conversation,
            inferenceConfig={"maxTokens": 100, "temperature": 0.1, "topP": 0.9},
        )

        res_text = response["output"]["message"]["content"][0]["text"].strip()
        json_match = re.search(r'({.*})', res_text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(1))
            policy_number = data.get("policy_number") if data.get("policy_number") != "None" else None
            vehicle_number = data.get("vehicle_number") if data.get("vehicle_number") != "None" else None
            
            # Use 'Unknown' if AI returns 'None' to trigger the frontend prompt
            damage_val = data.get("damage_type")
            damage_type = damage_val if damage_val != "None" else "Unknown"

    except Exception as e:
        print(f"Bedrock Extraction Failed: {e}")

    # 2. Regex Fallback (Improved)
    if not policy_number:
        policy_match = re.search(r'(?:policy|pol|plan)\s*(?:no\.?|number|#|id)?\s*[:\s-]*([A-Z0-9]*\d+[A-Z0-9]*)', description, re.IGNORECASE)
        if policy_match:
            policy_number = policy_match.group(1).upper()
        else:
            pol_prefix_match = re.search(r'\b(POL\d+)\b', description, re.IGNORECASE)
            if pol_prefix_match:
                policy_number = pol_prefix_match.group(1).upper()
    
    if not vehicle_number:
        car_match = re.search(r'(?:car|vehicle|reg|registration|plate)\s*(?:no\.?|number|#)?\s*[:\s-]*([A-Z0-9]{2,}\s*[A-Z0-9]+)', description, re.IGNORECASE)
        if car_match:
            vehicle_number = car_match.group(1).upper().replace(" ", "")
            
    # Simple part-based fallback if Bedrock failed or returned Unknown
    if damage_type == "Unknown":
        damage_keywords = ['door', 'bumper', 'windshield', 'glass', 'mirror', 'headlight', 'tail', 'bonnet', 'tyre', 'wheel']
        for keyword in damage_keywords:
            if keyword in description.lower():
                damage_type = keyword
                break

    return policy_number, vehicle_number, damage_type

def generate_complaint_subject(description):
    """
    Generates a concise subject/title for the complaint description using AWS Bedrock.
    """
    try:
        bedrock = boto3.client(
            service_name='bedrock-runtime', 
            region_name=AWS_REGION,
            aws_access_key_id=aws_access_key_id or None,
            aws_secret_access_key=aws_secret_access_key or None,
        )
        
        prompt = f"Extract a very concise, professional subject title (max 5-7 words) from this customer complaint. Do not use quotes or prefixes. Description: {description}"
        
        model_id = "amazon.titan-text-express-v1" 
        
        conversation = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        response = bedrock.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=conversation,
            inferenceConfig={"maxTokens": 30, "temperature": 0.3, "topP": 0.9},
        )

        subject = response["output"]["message"]["content"][0]["text"].strip().replace('"', '')
        return subject

    except Exception as e:
        print(f"Error generating subject with Bedrock: {str(e)}")
        # Basic heuristic fallback
        return (description[:50] + '...') if len(description) > 50 else description

def extract_transaction_id(description):
    """
    Extracts Transaction ID tokens (e.g., TXN-12345) from the text.
    Returns the first found ID or None.
    """
    # Pattern to match TXN- followed by alphanumeric characters
    match = re.search(r'(TXN-[\w\d]+)', description)
    if match:
        return match.group(1)
    return None


def extract_transaction_amount(description):
    """
    Extracts numeric amounts from the description.
    Looks for sequences like $100, £50, 200 USD, or just 300.50.
    """
    # Look for currency symbols or just digits with decimals
    match = re.search(r'[\$£€¥]?\s?(\d+(?:[.,]\d{1,2})?)', description)
    if match:
        try:
            val = float(match.group(1).replace(',', ''))
            return val
        except:
            return 0.0
    return 0.0

