from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Listing, ListingImage, AppointmentRequest
from django.utils import timezone

# Create your forms here.

class NewUserForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already registered.")
        return email

    def save(self, commit=True):
        user = super(NewUserForm, self).save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return user

class ListingForm(forms.ModelForm):
    class Meta:
        model = Listing
        fields = [
            'title',
            'description',
            'listing_type',
            'property_type',
            'price',
            'city',
            'district',
            'address',
            'area_sqm',
            'year_built',
            'rooms',
            'bathrooms',
            'latitude',
            'longitude',
            'tour_url',
            'video',
            'floor_plan',
            'heating',
            'cooling',
            'has_fireplace',
            'hoa_fee',
            'is_off_plan',
            'developer_name',
            'expected_completion',
            'payment_plan',
        ]
        widgets = {
            'latitude': forms.HiddenInput(),
            'longitude': forms.HiddenInput(),
            'video': forms.ClearableFileInput(attrs={'accept': 'video/*', 'class': 'form-control'}),
            'floor_plan': forms.ClearableFileInput(attrs={'accept': 'image/*', 'class': 'form-control'}),
            'expected_completion': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'payment_plan': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'e.g. 20% down payment, 80% on handover'},)
        }

class AppointmentRequestForm(forms.ModelForm):
    class Meta:
        model = AppointmentRequest
        fields = ['requested_date', 'requested_time', 'note']
        widgets = {
            'requested_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'requested_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'note': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Optional note'},)
        }   

    def clean_requested_date(self):
        date = self.cleaned_data['requested_date']
        if date < timezone.localdate():
            raise forms.ValidationError("You cannot request an appointment for a past date.")
        return date