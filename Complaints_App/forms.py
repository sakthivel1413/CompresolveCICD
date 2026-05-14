from django import forms
from .models import Complaint

class ComplaintForm(forms.ModelForm):
    # File upload field
    complaint_file = forms.FileField(required=False)
    
    # Custom description field
    description = forms.CharField(
        widget=forms.Textarea(attrs={'placeholder': 'Describe your issue here...', 'rows': 5, 'cols': 50})
    )

    class Meta:
        model = Complaint
        fields = ['description', 'complaint_file']  # Specify the fields you want in the form
