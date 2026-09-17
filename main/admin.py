from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import(
    Listing, ListingImage, Conversation, Message, BlockedUser, Profile,
    Notification, NotificationPrefence, Favorite, SavedSearch,
    AgencyReview, ListingReport, AppointmentRequest
)

# Register your models here.

class ListingImageInline(admin.TabularInline):
    model = ListingImage
    extra = 0

@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "listing_type", "property_type", "price", "city", "moderation_status", "availability", "is_active", "created_at")
    list_filter = ("moderation_status", "availability", "listing_type", "property_type", "is_active", "city")
    search_fields = ("title", "description", "city", "district", "owner__username", "owner__email")
    list_editable = ("moderation_status", "is_active")
    readonly_fields = ("created_at",)
    inlines = [ListingImageInline]
    date_hierarchy = "created_at"

@admin.register(ListingReport)
class ListingReportAdmin(admin.ModelAdmin):
    list_display = ("listing", "reporter", "ai_verdict", "resolved", "created_at")
    list_filter = ("ai_verdict", "resolved", "created_at")
    search_fields = ("listing__title", "reporter__username", "reason")
    list_editable = ("resolved",)
    readonly_fields = ("created_at",)

admin.register(AppointmentRequest)
class AppointmentRequestAdmin(admin.ModelAdmin):
    list_display = ("listing", "requester", "requested_date", "requested_time", "status", "created_at")
    list_filter = ("status", "requested_date")
    search_fields = ("listing__title", "requester__username")
    readonly_fields = ("created_at",)

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_agency", "company_name", "agency_phone")
    list_filter = ("is_agency",)
    search_fields = ("user__username", "company_name")

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("pk", "listing", "is_archived", "is_reported", "created_at")
    list_filter = ("is_archived", "is_reported")
    readonly_fields = ("created_at",)

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "body", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("body", "sender__username")
    readonly_fields = ("created_at",)

@admin.register(BlockedUser)
class BlockedUserAdmin(admin.ModelAdmin):
    list_display = ("blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    readonly_fields = ("created_at",)

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "text", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("user__username", "text")
    readonly_fields = ("created_at",)

@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "listing", "created_at")
    search_fields = ("user__username", "listing__title")
    readonly_fields = ("created_at",)

@admin.register(SavedSearch)
class SavedSearchAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "city", "email_alerts", "created_at")
    list_filter = ("email_alerts",)
    search_fields = ("user__username", "name", "city")
    readonly_fields = ("created_at", "last_notified_at")

admin.site.register(NotificationPrefence)