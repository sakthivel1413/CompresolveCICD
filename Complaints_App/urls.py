from django.urls import path
from . import views
from .geminiAI import transcribe_audio

from django.contrib.auth import views as auth_views


urlpatterns = [ 
    path('reassign_ticket/<str:complaint_id>/', views.reassign_ticket, name='reassign_ticket'),
    path('login/', views.login_page, name='login'),  # Redirect to Cognito Hosted UI
    path('register/', views.register_page, name='register'),
    path('callback/', views.callback, name='callback'),  # Handle the Cognito callback
    path('logout/', views.logout_page, name='logout'),
    path('', views.user_home, name='user_home'),  
    path('home', views.user_home),
    path('track_complaints/', views.track_complaints, name='track_complaints'),
    path('register_complaint/', views.register_complaint, name='register_complaint'),  # Register section
    path('generate_intent/', views.generate_intent, name='generate_intent'),
    path('get_ai_estimate/', views.get_ai_estimate, name='get_ai_estimate'),
    path('transcribe/', views.transcribe_view, name='transcribe'),
    path('close_complaint/<str:old_complaint_id>/', views.close_complaint, name='close_complaint'),
    path('transcribe_audio/', transcribe_audio, name='transcribe_audio'),
    path('start_transcription/', views.start_transcription, name='start_transcription'),
    path('check_transcription_status/<str:job_name>/', views.check_transcription_status, name='check_transcription_status'),
    path('escalate_complaint/<str:complaint_id>/', views.escalate_complaint, name='escalate_complaint'),
    path('track_complaint_detail/<str:complaint_id>/', views.track_complaint_detail, name='track_complaint_detail'),
    path('reopen_ticket/<str:complaint_id>/', views.reopen_ticket, name='reopen_ticket'),
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('manage_users/', views.manage_users, name='manage_users'),
    path('all_complaints/', views.all_complaints_view, name='all_complaints'),
    path('supervisor_dashboard/', views.supervisor_dashboard, name='supervisor_dashboard'),
    path('close_and_register_new_complaint/<str:old_complaint_id>/', views.close_and_register_new_complaint, name='close_and_register_new_complaint'),
    path('admin_ticket_detail/<str:complaint_id>/', views.admin_ticket_detail, name='admin_ticket_detail'),
    path('analyze_media/', views.analyze_media, name='analyze_media'),
    path('update_ticket_action/<str:complaint_id>/', views.update_ticket_action, name='update_ticket_action'),
    path('agent_dashboard/', views.agent_dashboard, name='agent_dashboard'),
    path('api/get_transfer_agents/', views.get_transfer_agents, name='get_transfer_agents'),
    path('add_comment/<str:complaint_id>/', views.add_comment, name='add_comment'),
    path('transactions/', views.user_transactions, name='user_transactions'),
    path('transaction_detail/<str:transaction_id>/', views.transaction_detail, name='transaction_detail'),
    
    # Reassignment Request APIs
    path('api/reassignment_request/<str:complaint_id>/', views.create_reassignment_request, name='create_reassignment_request'),
    path('api/pending_reassignment_requests/', views.get_pending_reassignment_requests, name='get_pending_reassignment_requests'),
    path('api/process_reassignment_request/<str:request_id>/', views.process_reassignment_request, name='process_reassignment_request'),
    path('api/agent_reassignment_requests/', views.get_agent_reassignment_requests, name='get_agent_reassignment_requests'),
    path('api/ticket_reassignment_status/<str:complaint_id>/', views.get_ticket_reassignment_status, name='get_ticket_reassignment_status'),
    path('api/all_reassignment_requests/', views.get_all_reassignment_requests, name='get_all_reassignment_requests'),
]
