from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

# Create your models here.

class Listing(models.Model):
    LISTING_TYPE_CHOICES = [
        ('sale', 'For Sale'),
        ('rent', 'For Rent'),
    ]

    PROPERTY_TYPE_CHOICES = [
        ('apartment', 'Apartment'),
        ('house', 'House'),
        ('land', 'Land'),
        ('office', 'Office'),
    ]

    MODERATION_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    AVAILABILITY_CHOICES = [
        ('available', 'Available'),
        ('sold', 'Sold'),
        ('rented', 'Rented'),
    ]

    HEATING_CHOICES = [
        ('none', 'None'),
        ('central', 'Central'),
        ('forced_air', 'Forced Air'),
        ('radiant', 'Radiant'),
        ('other', 'Other'),
    ]

    COOLING_CHOICES = [
        ('none', 'None'),
        ('central_air', 'Central Air'),
        ('window_unit', 'Window Unit'),
        ('other', 'Other'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="listings")
    title = models.CharField(max_length=200)
    description = models.TextField()
    listing_type = models.CharField(max_length=10, choices=LISTING_TYPE_CHOICES)
    property_type = models.CharField(max_length=20, choices=PROPERTY_TYPE_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    city = models.CharField(max_length=100)
    district = models.CharField(max_length=100)
    area_sqm = models.PositiveIntegerField(help_text="Area in square meters")
    year_built = models.PositiveIntegerField(null=True, blank=True, help_text="Year the property was build")
    rooms = models.PositiveIntegerField(null=True, blank=True)
    bathrooms = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    moderation_status = models.CharField(max_length=10, choices=MODERATION_CHOICES, default='pending')
    moderation_note = models.TextField(blank=True)

    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    availability = models.CharField(max_length=10, choices=AVAILABILITY_CHOICES, default="available")
    tour_url = models.URLField(blank=True, help_text="Matterport, Kuula or similar 3D tour link")
    video = models.FileField(upload_to="listing_videos/", null=True, blank=True, help_text="A short walkthrought video (mp4 recommended)")
    floor_plan = models.ImageField(upload_to="listing_floorplans/", null=True, blank=True, help_text="Floor plan image (optional)")
    heating = models.CharField(max_length=20, choices=HEATING_CHOICES, default="none", blank=True)
    cooling = models.CharField(max_length=20, choices=COOLING_CHOICES, default="none", blank=True)
    has_fireplace = models.BooleanField(default=False)
    hoa_fee = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, help_text="Montly HOA fee, if applicable")

    view_count = models.PositiveIntegerField(default=0)
    walk_score = models.PositiveIntegerField(null=True, blank=True)
    nearby_places = models.JSONField(default=dict, blank=True)
    slug = models.SlugField(max_length=200, blank=True)

    is_off_plan = models.BooleanField(default=False)
    developer_name = models.CharField(max_length=150, blank=True)
    expected_completion = models.DateField(null=True, blank=True, help_text="Expected handover/completion date")
    payment_plan = models.TextField(blank=True, help_text="e.g 20% down payment, 80% on handover")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)[:220]
        super().save(*args, **kwargs)
    
    def __str__(self):
        return self.title

    @property
    def favorite_count(self):
        return self.favorited_by.count()

class ListingImage(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="listing_images/")
    is_cover = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_aerial = models.BooleanField(default=False, help_text="Drone/aerial shot")

    class Meta:
        ordering = ['order', 'uploaded_at']

    def __str__(self):
        return f"Image for {self.listing.title}"

class Conversation(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, null=True, blank=True, related_name="conversations")
    participants = models.ManyToManyField(User, related_name="conversations")
    created_at = models.DateTimeField(auto_now_add=True)
    is_archived = models.BooleanField(default=False)
    is_reported = models.BooleanField(default=False)

class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    body = models.TextField(blank=True)
    attachment = models.FileField(upload_to="message_attachments/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

class BlockedUser(models.Model):
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="blocking")
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name="blocked_by")
    created_at = models.DateTimeField(auto_now_add=True)

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    is_agency = models.BooleanField(default=False)
    company_name = models.CharField(max_length=150, blank=True)
    agency_phone = models.CharField(max_length=30, blank=True)
    is_verified = models.BooleanField(default=False, help_text="Verified licensed agency (Selverio-style badge)")
    license_number = models.CharField(max_length=50, blank=True, help_text="Real estate license/registration number")

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    text = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class NotificationPrefence(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notifications_prefence")
    email_new_message = models.BooleanField(default=False)
    email_listing_status = models.BooleanField(default=False)

class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorites")
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'listing')

class SavedSearch(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_searches")
    name = models.CharField(max_length=100, blank=True)

    q = models.CharField(max_length=200, blank=True)
    listing_type = models.CharField(max_length=10, blank=True)
    property_type = models.CharField(max_length=20, blank=True)
    city = models.CharField(max_length=100, blank=True)
    min_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    rooms = models.PositiveIntegerField(null=True, blank=True)

    email_alerts = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_notified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name or f"Saved search #{self.pk}"

    def matches(self, listing):
        if self.q and self.q.lower() not in (listing.title + " " + listing.description).lower():
            return False
        if self.listing_type and listing.listing_type != self.listing_type:
            return False
        if self.property_type and listing.property_type != self.property_type:
            return False
        if self.city and self.city.lower() not in listing.city.lower():
            return False
        if self.min_price and listing.price < self.min_price:
            return False
        if self.max_price and listing.price > self.max_price:
            return False
        if self.rooms and (listing.rooms is None or listing.rooms > self.rooms):
            return False
        return True

class AgencyReview(models.Model):
    agency = models.ForeignKey(User, on_delete=models.CASCADE, related_name="agency_reviews")
    reviewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="written_reviews")
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('agency', 'reviewer')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.reviewer.username} → {self.agency.username} ({self.rating}★)"

class ListingReport(models.Model):
    AI_VERDICT_CHOICES = [
        ('pending', 'Pending'),
        ('valid', 'Valid'),
        ('invalid', 'Invalid'),
    ]

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="listing_reports")
    reason = models.TextField()
    ai_verdict = models.CharField(max_length=10, choices=AI_VERDICT_CHOICES, default='pending')
    ai_notes = models.TextField(blank=True)
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class AppointmentRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ]

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="appointment_requests")
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name="appointment_requests")
    requested_date = models.DateField()
    requested_time = models.TimeField()
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.requested.username} → {self.listing.title} ({self.requested_date} {self.requested_time})"

class PriceHistory(models.Model):
    EVENT_CHOICES = [
        ('listed', 'Listed'),
        ('price_change', 'Price Change'),
        ('sold', 'Sold'),
        ('rented', 'Rented'),
        ('realisted', 'Realisted'),
    ]

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="price_history")
    event = models.CharField(max_length=20, choices=EVENT_CHOICES)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.listing.title} - {self.get_event_display()} (${self.price})"