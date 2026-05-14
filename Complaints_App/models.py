from django.db import models
from django.contrib.auth.models import User

# Complaint Model
class Complaint(models.Model):
    attachment_s3_path = models.CharField(max_length=500, null=True, blank=True)
    agreement_no = models.CharField(max_length=50)
    description = models.TextField()
    tags = models.CharField(max_length=255, blank=True, null=True)  # Placeholder for AI tags
    status = models.CharField(max_length=50, default="User created case")  # Initial status
    created_at = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    

    def __str__(self):
        return f"Complaint {self.id} - {self.agreement_no}"

# ComplaintAction Model
class ComplaintAction(models.Model):
    ACTION_TYPES = [
        ('MyAction', 'My Action'),
        ('LBG', 'LBG User Action'),
        ('System', 'System Process'),
    ]

    complaint = models.ForeignKey(Complaint, on_delete=models.CASCADE, related_name='actions')
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    elapsed_time = models.IntegerField(blank=True, null=True)  # Optional for timeline display

    def __str__(self):
        return f"Action {self.id} on Complaint {self.complaint.id}"
