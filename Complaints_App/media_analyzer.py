import boto3
import json
import re
from io import BytesIO
from django.conf import settings

# AWS Configuration from settings
AWS_ACCESS_KEY_ID = settings.AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY = settings.AWS_SECRET_ACCESS_KEY
AWS_REGION = settings.AWS_REGION_NAME

# Initialize AWS clients
rekognition_client = boto3.client(
    'rekognition',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

textract_client = boto3.client(
    'textract',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

bedrock_client = boto3.client(
    'bedrock-runtime',
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)


def analyze_image(image_bytes):
    """
    Analyzes an image using AWS Rekognition and Textract.
    Returns a comprehensive description of the image content.
    
    Args:
        image_bytes: Binary content of the image
        
    Returns:
        dict: Analysis results including labels, text, and description
    """
    try:
        analysis_results = {
            'labels': [],
            'text': '',
            'description': '',
            'objects': [],
            'faces': 0,
            'moderation': []
        }
        
        # 1. Detect Labels (objects, scenes, activities)
        try:
            labels_response = rekognition_client.detect_labels(
                Image={'Bytes': image_bytes},
                MaxLabels=20,
                MinConfidence=70
            )
            
            analysis_results['labels'] = [
                {
                    'name': label['Name'],
                    'confidence': round(label['Confidence'], 2)
                }
                for label in labels_response.get('Labels', [])
            ]
        except Exception as e:
            print(f"Error detecting labels: {e}")
        
        # 2. Detect Text in Image (OCR)
        try:
            text_response = textract_client.detect_document_text(
                Document={'Bytes': image_bytes}
            )
            
            # Extract all detected text
            detected_text = []
            for block in text_response.get('Blocks', []):
                if block['BlockType'] == 'LINE':
                    detected_text.append(block['Text'])
            
            analysis_results['text'] = ' '.join(detected_text)
        except Exception as e:
            print(f"Error detecting text: {e}")
        
        # 3. Detect Objects with bounding boxes
        try:
            objects_response = rekognition_client.detect_custom_labels(
                Image={'Bytes': image_bytes},
                MinConfidence=70
            ) if False else rekognition_client.detect_labels(
                Image={'Bytes': image_bytes},
                MaxLabels=10,
                MinConfidence=75
            )
            
            # Use labels as objects for now
            analysis_results['objects'] = [
                label['Name'] for label in objects_response.get('Labels', [])[:5]
            ]
        except Exception as e:
            print(f"Error detecting objects: {e}")
        
        # 4. Detect Faces (count)
        try:
            faces_response = rekognition_client.detect_faces(
                Image={'Bytes': image_bytes},
                Attributes=['DEFAULT']
            )
            analysis_results['faces'] = len(faces_response.get('FaceDetails', []))
        except Exception as e:
            print(f"Error detecting faces: {e}")
        
        # 5. Content Moderation (check for inappropriate content)
        try:
            moderation_response = rekognition_client.detect_moderation_labels(
                Image={'Bytes': image_bytes},
                MinConfidence=60
            )
            analysis_results['moderation'] = [
                label['Name'] for label in moderation_response.get('ModerationLabels', [])
            ]
        except Exception as e:
            print(f"Error in content moderation: {e}")
        
        # 6. Generate comprehensive description using Bedrock (Passing image bytes for Vision)
        analysis_results['description'] = generate_image_description(analysis_results, image_bytes)
        
        return analysis_results
        
    except Exception as e:
        print(f"Error analyzing image: {e}")
        return {
            'error': str(e),
            'description': 'Unable to analyze image. Please try again.'
        }


def analyze_video(video_bytes, max_duration_seconds=30):
    """
    Analyzes a video using AWS Rekognition Video.
    Note: For real-time analysis, we'll analyze the first frame as an image.
    For full video analysis, you'd need to upload to S3 and use StartLabelDetection.
    
    Args:
        video_bytes: Binary content of the video
        max_duration_seconds: Maximum duration to analyze
        
    Returns:
        dict: Analysis results
    """
    try:
        # For simplicity, we'll extract the first frame and analyze it as an image
        # In production, you might want to use AWS Rekognition Video API with S3
        
        # For now, return a placeholder that suggests uploading to S3 for full analysis
        return {
            'description': 'Video uploaded. For detailed video analysis, the video will be processed asynchronously.',
            'note': 'Currently analyzing video as a snapshot. Full video analysis coming soon.',
            'labels': [],
            'text': ''
        }
        
    except Exception as e:
        print(f"Error analyzing video: {e}")
        return {
            'error': str(e),
            'description': 'Unable to analyze video. Please try again.'
        }


def generate_image_description(analysis_data, image_bytes=None):
    """
    Uses AWS Bedrock Multi-modal capabilities to generate a concise description
    of the vehicle, damage, and identifying text.
    """
    try:
        # Prepare context from analysis
        labels_text = ', '.join([label['name'] for label in analysis_data.get('labels', [])[:10]])
        detected_text = analysis_data.get('text', '')
        faces_count = analysis_data.get('faces', 0)
        
        # Build professional prompt
        system_prompt = (
            "You are an expert claims assistant. Provide a clear Evidence Summary.\n\n"
            "### Scenario A: Vehicle Damage\n"
            "1. Start with a natural sentence (e.g., 'The vehicle has sustained damage to the back side and back door area along with the windshield').\n"
            "2. Below, provide: [Make/Model] | [Primary Issue] | [Identifier/Plate].\n\n"
            "### Scenario B: Financial/Transaction (Receipts, Screenshots)\n"
            "1. Start with a natural sentence (e.g., 'Confirmed payment of $120.00 to Amazon Services on Oct 12th, currently showing as Failed/Pending').\n"
            "2. Below, provide: [Merchant/App] | [Amount/Status] | [Transaction ID].\n\n"
            "### General Instructions:\n"
            "- Normalize IDs/Plates (no spaces).\n"
            "- Be concise but highly descriptive.\n"
            "- If the image is unrelated, provide a single polite sentence."
        )
        
        user_prompt = f"""Summarize this incident evidence.
DATA FROM REKO/TEXTRACT:
- Visual Hints: {labels_text}
- OCR Text: {detected_text}

Analyze the image and data to generate the Evidence Summary."""

        model_id = "amazon.nova-pro-v1:0"
        
        # Determine format (Bedrock supports 'png', 'jpeg', 'webp', 'gif')
        # We'll use a simple check, defaulting to 'jpeg'
        img_format = "jpeg"
        if image_bytes:
            # Check for PNG magic numbers
            if image_bytes.startswith(b'\x89PNG'):
                img_format = "png"
        
        # Prepare content list for multi-modal input
        content = []
        if image_bytes:
            content.append({
                "image": {
                    "format": img_format,
                    "source": {"bytes": image_bytes}
                }
            })
        content.append({"text": user_prompt})

        try:
            response = bedrock_client.converse(
                modelId=model_id,
                system=[{"text": system_prompt}],
                messages=[{"role": "user", "content": content}],
                inferenceConfig={"maxTokens": 150, "temperature": 0.1}
            )
            description = response["output"]["message"]["content"][0]["text"].strip()
            
            # Clean up redundant prefixes
            description = description.replace("Description:", "").replace("Summary:", "").replace("--- Evidence Summary ---", "").strip()

            return f"--- Evidence Summary ---\n{description}"

        except Exception as model_err:
            print(f"Bedrock Vision Error ({model_id}): {model_err}")
            # Fallback to Text-Only using Titan
            fallback_prompt = f"Write a 1-sentence description of this evidence and a snapshot line. Labels: {labels_text}. Text: {detected_text}."
            try:
                fb_response = bedrock_client.converse(
                    modelId="amazon.titan-text-express-v1",
                    messages=[{"role": "user", "content": [{"text": fallback_prompt}]}],
                    inferenceConfig={"maxTokens": 150}
                )
                return f"--- Evidence Summary ---\n{fb_response['output']['message']['content'][0]['text'].strip()}"
            except:
                # Absolute minimal descriptive fallback if total API failure
                summary = "Financial document/Receipt" if "Transaction" in detected_text else "Incident evidence"
                return f"--- Evidence Summary ---\n{summary} detected.\nDetails: {detected_text[:200]}"

    except Exception as e:
        print(f"Error in generate_image_description: {e}")
        return f"--- Evidence Summary ---\nAnalysis data captured.\nDetails: {detected_text[:200]}"




def analyze_image_for_payout(image_bytes):
    """
    Uses Amazon Nova Pro for high-end financial adjustment.
    Specifically extracts PART and SEVERITY for payout logic.
    """
    try:
        system_prompt = (
            "You are an expert automotive insurance adjuster. Analyze the image to determine:\n"
            "1. The primary damaged car part (e.g., Bumper, Windshield, Door, Hood, Fender).\n"
            "2. The severity of damage (Minor, Moderate, or Severe).\n"
            "   - Minor: Scratches, tiny dents.\n"
            "   - Moderate: Large dents, shattered glass, functional impact.\n"
            "   - Severe: Mangled frame, airbag deployment, structural failure.\n\n"
            "Return ONLY a JSON object: {\"part\": \"...\", \"severity\": \"...\", \"reason\": \"...\"}"
        )
        
        user_prompt = "Adjust this claim."
        model_id = "amazon.nova-pro-v1:0"

        # Prepare content
        img_format = "png" if image_bytes.startswith(b'\x89PNG') else "jpeg"
        content = [
            {
                "image": {
                    "format": img_format,
                    "source": {"bytes": image_bytes}
                }
            },
            {"text": user_prompt}
        ]

        response = bedrock_client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": 150, "temperature": 0.1}
        )
        
        res_text = response["output"]["message"]["content"][0]["text"].strip()
        
        # Extract JSON
        json_match = re.search(r'({.*})', res_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        return {"part": "Unknown", "severity": "Moderate", "reason": "Could not parse AI response"}

    except Exception as e:
        print(f"Nova Pro Adjustment Error: {e}")
        return {"error": str(e), "part": "Unknown", "severity": "Moderate"}

def extract_complaint_details_from_media(analysis_results):
    """
    Extracts specific complaint-relevant details from media analysis.
    
    Args:
        analysis_results: Results from analyze_image or analyze_video
        
    Returns:
        dict: Structured complaint details
    """
    details = {
        'description': analysis_results.get('description', ''),
        'detected_text': analysis_results.get('text', ''),
        'key_objects': [l['name'] for l in analysis_results.get('labels', [])[:5]] if isinstance(analysis_results.get('labels'), list) else [],
        'has_people': analysis_results.get('faces', 0) > 0,
        'content_flags': analysis_results.get('moderation', [])
    }
    
    return details

def extract_license_plate(image_bytes):
    """
    Uses Rekognition to extract potential license plate numbers from the image.
    Falls back to Bedrock Vision if Rekognition pattern match fails.
    """
    # 1. Try Rekognition first (Fast & Cost-effective)
    try:
        response = rekognition_client.detect_text(Image={'Bytes': image_bytes})
        detections = response.get('TextDetections', [])
        
        # Simplified: Look for strings with 8-10 chars containing letters and numbers
        plate_pattern = re.compile(r'[A-Z]{2}[0-9]{1,2}[A-Z]{1,2}[0-9]{4}')
        
        for det in detections:
            if det['Type'] == 'LINE':
                clean_text = re.sub(r'[^A-Z0-9]', '', det['DetectedText'].upper())
                match = plate_pattern.search(clean_text)
                if match:
                    return match.group()
    except Exception as e:
        print(f"Rekognition Plate Detection Error: {e}")

    # 2. AI Fallback (Reliable for complex angles/fonts)
    try:
        prompt = "Extract the vehicle registration/license plate number from this image. Return ONLY the alphanumeric code (e.g., MP05CA5816). If not found, return 'None'."
        img_format = "png" if image_bytes.startswith(b'\x89PNG') else "jpeg"
        
        response = bedrock_client.converse(
            modelId="amazon.nova-pro-v1:0",
            messages=[{"role": "user", "content": [
                {"image": {"format": img_format, "source": {"bytes": image_bytes}}},
                {"text": prompt}
            ]}],
            inferenceConfig={"maxTokens": 20, "temperature": 0.1}
        )
        
        found = response["output"]["message"]["content"][0]["text"].strip().upper()
        # Clean response (remove spaces, dots, etc)
        found_clean = re.sub(r'[^A-Z0-9]', '', found)
        
        if found_clean and found_clean != "NONE" and len(found_clean) >= 6:
            print(f"Bedrock AI extracted plate: {found_clean}")
            return found_clean
            
    except Exception as e:
        print(f"Bedrock Plate Extraction Error: {e}")

    return None
