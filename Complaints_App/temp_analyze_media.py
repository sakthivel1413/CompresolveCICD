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
