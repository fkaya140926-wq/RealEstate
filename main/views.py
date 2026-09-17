from django.shortcuts import render, redirect, get_object_or_404
from .forms import NewUserForm, ListingForm, AppointmentRequestForm
from django.contrib.auth import login, authenticate, logout, update_session_auth_hash
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, PasswordChangeForm
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.models import User
from .models import Listing, ListingImage, Conversation, Message, BlockedUser, Profile, Notification, NotificationPrefence, Favorite, SavedSearch, AgencyReview, ListingReport, AppointmentRequest, PriceHistory
from groq import Groq
from django.conf import settings
import time, requests, re, json, math
from django.http import Http404, JsonResponse, HttpResponse
from django.db.models import Q, Prefetch, Count, F, Avg, Count, Sum
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.utils.timesince import timesince
from django_q.tasks import async_task
from django.db.models.functions import TruncMonth
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited

# Create your views here.

def homepage(request):
    return render(request=request, template_name="main/home.html")

@ratelimit(key="ip", rate="5/m", block=True)
def register_request(request):
    if request.method == "POST":
        form = NewUserForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False
            user.save()

            Profile.objects.create(user=user)
            NotificationPrefence.objects.create(user=user, email_new_message=True, email_listing_status=True)

            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            verify_url = request.build_absolute_uri(
                reverse("main:verify_email", kwargs={"uidb64": uid, "token":token})
            )

            send_mail(
                subject="Verify your email",
                message=f"Hi {user.username},\n\nPlease click the link below to verify your email: \n{verify_url}",
                from_email="noreply@example.com",
                recipient_list=[user.email],
            )

            messages.success(request, "Registration successful.")
            return redirect("main:homepage")
        messages.error(request, "Unsucessful registration. Invalid information.")
    form = NewUserForm()
    return render(request=request, template_name="main/register.html", context={"register_form":form})

def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        if user.is_active:
            messages.info(request, "This account is already verified.")
        else:
            user.is_active = True
            user.save()
            messages.success(request, "Your email has been verified. You can now log in.")
    else:
        messages.error(request, "The verification link is invalid or has expired.")
    return redirect("main:login")

@ratelimit(key="ip", rate="5/m", block=True)
def login_request(request):
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            user = authenticate(username=username, password=password)
            if user is not None:
                login(request, user)
                messages.info(request, f"You are now logged in as {username}.")
                return redirect("main:homepage")
            else:
                messages.error(request, "Invalid username or password.")
        else:
            messages.error(request, "Invalid username or password.")
    form = AuthenticationForm()
    return render(request=request, template_name="main/login.html", context={"login_form":form})

def logout_request(request):
    logout(request)
    messages.info(request, "You have successfully logged out.")
    return redirect("main:homepage")

def password_reset_request(request):
    if request.method == "POST":
        email = request.POST.get('email')
        user = User.objects.filter(email=email).first()

        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = request.build_absolute_uri(
                reverse("main:password_reset_confirm", kwargs={"uidb64": uid, "token":token})
            )

            send_mail(
                subject="Reset your password",
                message=f"Hi {user.username},\n\nClick the link below to reset your password:\n{reset_url}",
                from_email="noreply@example.com",
                recipient_list=[user.email],
            )

            messages.success(request, "If an account with that email exists a password reset link has been sent.")
            return redirect("main:login")
    return render(request=request, template_name="main/password/password_reset_request.html")

def password_reset_confirm(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None or not default_token_generator.check_token(user, token):
        messages.error(request, "The password reset link is invalid or has expired.")
        return redirect("main:login")

    if request.method == "POST":
        form = SetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            return redirect("main:password_reset_complete")
    else:
        form = SetPasswordForm(user)
    return render(request=request, template_name="main/password/password_reset_confirm.html", context={"form":form})

def password_reset_done(request):
    return render(request=request, template_name="main/password/password_reset_done.html")

def password_reset_complete(request):
    return render(request=request, template_name="main/password/password_reset_complete.html")

@login_required
def create_listing(request):
    if request.method == "POST":
        form = ListingForm(request.POST, request.FILES)
        if form.is_valid():
            listing = form.save(commit=False)
            listing.owner = request.user
            listing.save()

            lat, lng = geocode_address(listing.address, listing.district, listing.city)
            listing.latitude = lat
            listing.longitude = lng
            listing.save()

            async_task(calculate_walk_score_delayed, listing.pk)

            PriceHistory.objects.create(listing=listing, event='listed', price=listing.price)

            images = request.FILES.getlist('images')
            for image in images:
                ListingImage.objects.create(listing=listing, image=image)

            async_task(run_moderation_delayed, listing.pk)
                        
            messages.info(request, "Listing submitted for manual review")

            return redirect("main:listing_detail", slug=listing.slug, pk=listing.pk)
    else:
        form = ListingForm()
    return render(request=request, template_name="main/listing_form.html", context={"form":form})

def listing_list(request):
    listings = Listing.objects.filter(is_active=True, moderation_status="approved", availability="available").order_by('-created_at')

    SORT_OPTIONS = {
        'newest': '-created_at',
        'oldest': 'created_at',
        'price_asc': 'price',
        'price_desc': '-price',
    }

    q = request.GET.get('q', '').strip()
    listing_type = request.GET.get('listing_type')
    property_type = request.GET.get('property_type')
    city = request.GET.get('city')
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    rooms = request.GET.get('rooms')
    sort = request.GET.get('sort', 'newest')
    min_area = request.GET.get('min_area')
    max_area = request.GET.get('max_area')
    off_plan = request.GET.get('off_plan')

    if q:
        q_lower = q.lower()
        type_filter = Q(title__icontains=q) | Q(description__icontains=q)
        for value, label in Listing.PROPERTY_TYPE_CHOICES:
            if q_lower in label.lower() or q_lower == value.lower():
                type_filter |= Q(property_type=value)
        for value, label in Listing.LISTING_TYPE_CHOICES:
            if q_lower in label.lower() or q_lower == value.lower():
                type_filter |= Q(listing_type=value)

        numbers = re.findall(r'\d+', q)
        for num in numbers:
            num = int(num)
            type_filter |= Q(area_sqm=num)
            type_filter |= Q(rooms=num)
            type_filter |= Q(price=num)
        listings = listings.filter(type_filter)
    if listing_type:
        listings = listings.filter(listing_type=listing_type)
    if property_type:
        listings = listings.filter(property_type=property_type)
    if city:
        listings = listings.filter(city__icontains=city)
    if min_price:
        listings = listings.filter(price__gte=min_price)
    if max_price:
        listings = listings.filter(price__lte=max_price)
    if rooms:
        listings = listings.filter(rooms__gte=rooms)
    if min_area:
        listings = listings.filter(area_sqm__gte=min_area)
    if max_area:
        listings = listings.filter(area_sqm__lte=max_area)
    if off_plan:
        listings = listings.filter(is_off_plan=True)

    listings = listings.order_by(SORT_OPTIONS.get(sort, '-created_at'))

    paginator = Paginator(listings, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    favorited_ids = []

    map_data = [
        {
            "id": l.pk,
            "title": l.title,
            "price": str(l.price),
            "lat": l.latitude,
            "lng": l.longitude,
            "url": f"/listings/{l.pk}/",
            "city": l.city,
        }
        for l in listings if l.latitude and l.longitude
    ]
    map_data_json = json.dumps(map_data)

    if request.user.is_authenticated:
        favorited_ids = list(Favorite.objects.filter(user=request.user).values_list('listing_id', flat=True))

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request=request, template_name="main/listing_results.html", context={"listings":page_obj, "q":q, "listing_type":listing_type, "property_type":property_type, "city":city, "min_price":min_price, "max_price":max_price, "rooms":rooms, "sort": sort, "sort_options": SORT_OPTIONS, "favorited_ids":favorited_ids, "min_area":min_area, "max_area":max_area, 'off_plan':off_plan, "map_data_json": map_data_json, "page_obj":page_obj, "total_count": paginator.count,})

    return render(request=request, template_name="main/listing_list.html", context={"listings":page_obj, "q":q, "listing_type":listing_type, "property_type":property_type, "city":city, "min_price":min_price, "max_price":max_price, "rooms":rooms, "sort": sort, "sort_options": SORT_OPTIONS, "favorited_ids":favorited_ids, "min_area":min_area, "max_area":max_area, 'off_plan':off_plan, "map_data_json": map_data_json, "page_obj":page_obj, "total_count":paginator.count})

def listing_detail(request, pk, slug=None):
    listing = get_object_or_404(Listing, pk=pk)
    is_owner = request.user.is_authenticated and listing.owner == request.user

    if not is_owner and (not listing.is_active or listing.moderation_status != "approved"):
        raise Http404

    if not is_owner:
        viewed_listings = request.session.get('viewed_listings', [])
        if listing.pk not in viewed_listings:
            Listing.objects.filter(pk=listing.pk).update(view_count=F('view_count') + 1)
            listing.refresh_from_db(fields=['view_count'])
            viewed_listings.append(listing.pk)
            request.session['viewed_listings'] = viewed_listings
    
    is_favorited = False
    if request.user.is_authenticated:
        is_favorited = Favorite.objects.filter(user=request.user, listing=listing).exists()

    price_per_sqm = None
    avg_price_per_sqm = None
    price_diff_percent = None
    similar_listings = Listing.objects.none()

    if listing.area_sqm:
        price_per_sqm = float(listing.price) / listing.area_sqm

        similar_listings = Listing.objects.filter(
            city=listing.city,
            property_type=listing.property_type,
            listing_type=listing.listing_type,
            is_active=True,
            moderation_status="approved",
            area_sqm__gt=0,
        ).exclude(pk=listing.pk)

        per_sqm_values = [
            float(l.price) / l.area_sqm for l in similar_listings if l.area_sqm
        ]

        if per_sqm_values:
            avg_price_per_sqm = sum(per_sqm_values) / len(per_sqm_values)
            price_diff_percent = round(((price_per_sqm - avg_price_per_sqm) / avg_price_per_sqm) * 100, 1)

    price_history_entries = list(listing.price_history.all())
    for i, entry in enumerate(price_history_entries):
        entry.price_direction = None
        if entry.event == "price_change" and i + 1 < len(price_history_entries):
            prev_price = price_history_entries[i + 1].price
            if entry.price > prev_price:
                entry.price_direction = 'up'
            elif entry.price < prev_price:
                entry.price_direction = 'down'

    return render(request=request, template_name="main/listing_detail.html", context={"listing":listing, "is_favorited": is_favorited, "price_per_sqm":price_per_sqm, "avg_price_per_sqm":avg_price_per_sqm, "price_diff_percent":price_diff_percent, "similar_listings": similar_listings, "price_history_entries": price_history_entries})

@login_required
def edit_listing(request, pk, slug=None):
    listing = get_object_or_404(Listing, pk=pk)

    if listing.owner != request.user:
        messages.error(request, "You are not allowed to edit this listing.")
        return redirect("main:listing_detail", slug=listing.slug, pk=listing.pk)

    old_price = listing.price
    old_availability = listing.availability

    if request.method == "POST":
        form = ListingForm(request.POST, request.FILES, instance=listing)
        if form.is_valid():
            content_fields = ["title", "description", "price", "property_type", "listing_type",
                              "city", "district", "address", "area_sqm", "rooms", "bathrooms", "floor_plan"]
            needs_review = any(field in form.changed_data for field in content_fields) or request.FILES.getlist('images')

            listing = form.save(commit=False)

            lat, lng = geocode_address(listing.address, listing.district, listing.city)
            listing.latitude = lat
            listing.longitude = lng

            listing.availability = request.POST.get("availability", listing.availability)

            if needs_review:
                listing.moderation_status = "pending"
                listing.moderation_note = ""
            listing.save()

            async_task(calculate_walk_score_delayed, listing.pk)

            if listing.price != old_price:
                PriceHistory.objects.create(listing=listing, event='price_change', price=listing.price)

            if listing.availability != old_availability:
                if listing.availability == "sold":
                    PriceHistory.objects.create(listing=listing, event="sold", price=listing.price)
                elif listing.availability == "rented":
                    PriceHistory.objects.create(listing=listing, event="rented", price=listing.price)
                elif listing.availability == "available" and old_availability in ('sold', 'rented'):
                    PriceHistory.objects.create(listing=listing, event='relisted', price=listing.price)

            for image in request.FILES.getlist('images'):
                ListingImage.objects.create(listing=listing, image=image)

            if needs_review:
                async_task(run_moderation_delayed, listing.pk)
                messages.info(request, "Listing updated and submitted for review")
            else:
                messages.success(request, "Listing updated.")
            return redirect("main:listing_detail", pk=listing.pk)
    else:
        form = ListingForm(instance=listing)
    return render(request=request, template_name="main/listing_edit.html", context={"form":form, "listing":listing})

@login_required
def delete_listing(request, pk):
    listing =get_object_or_404(Listing, pk=pk)

    if listing.owner != request.user:
        messages.error(request, "You are not allowed to delete this listing.")
        return redirect("main:dashboard")

    if request.method == "POST":
        listing.delete()

    return redirect("main:dashboard")

@login_required
def toggle_listing_status(request, pk):
    listing = get_object_or_404(Listing, pk=pk)

    if listing.owner != request.user:
        messages.error(request, "You are not allowed to modify this listing.")
        return redirect("main:dashboard")

    if request.method == "POST":
        listing.is_active = not listing.is_active
        listing.save()
    return redirect("main:dashboard")

@login_required
def dashboard(request):
    listings = Listing.objects.filter(owner=request.user).order_by('-created_at')

    favorites = Favorite.objects.filter(user=request.user).select_related('listing').order_by('-created_at')
    favorite_listings = [f.listing for f in favorites]

    saved_searches = SavedSearch.objects.filter(user=request.user).order_by('-created_at')

    listing_reports = ListingReport.objects.filter(reporter=request.user).select_related('listing', 'reporter').order_by('-created_at')

    send_appointments = AppointmentRequest.objects.filter(requester=request.user).select_related('listing').order_by('-created_at')
    received_appointments = AppointmentRequest.objects.filter(listing__owner=request.user).select_related('listing', 'requester').order_by('-created_at')

    context = {
        "listings": listings,
        "total_count": listings.count(),
        "active_count": listings.filter(is_active=True).count(),
        "sale_count": listings.filter(listing_type='sale').count(),
        "rent_count": listings.filter(listing_type='rent').count(),
        "favorite_listings": favorite_listings,
        "saved_searches": saved_searches,
        "listing_reports": listing_reports,
        "sent_appointments": send_appointments,
        "received_appointments": received_appointments,
    }
    return render(request=request, template_name="main/dashboard.html", context=context)

def run_moderation_delayed(listing_id):
    time.sleep(60)
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return
    status, reason = moderate_listing(listing)
    listing.moderation_status = status
    listing.moderation_note = reason
    listing.save()

    if status == "approved":
        Notification.objects.create(
            user=listing.owner,
            text=f'Your listing "{listing.title}" has been approved.',
            link=f"/listings/{listing.slug}-{listing.pk}/", 
        )
        notify_matching_saved_searches(listing)

        recent_prices = list(
            listing.price_history.order_by('-created_at').values_list('price', flat=True)[:2]

        )
        if len(recent_prices) == 2 and recent_prices[0] < recent_prices[1]:
            favoriting_users = User.objects.filter(favorites__listing=listing)
            for user in favoriting_users:
                Notification.objects.create(
                    user=user,
                    text=f'Price drop: "{listing.title}" is now ${listing.price:,.0f}',
                    link=f"/listings/{listing.slug}-{listing.pk}/",
                )


        prefs = getattr(listing.owner, 'notifications_prefence', None)
        if prefs and prefs.email_listing_status and listing.owner.email:
            send_mail(
                subject="Your listing has been approved.",
                message=f'Your listing "{listing.title}" has been approved and is now live.\n\nView it: /listings/{listing.slug}-{listing.pk}/',
                from_email="noreply@example.com",
                recipient_list=[listing.owner.email],
                fail_silently=True,
            )
    elif status == "rejected":
        Notification.objects.create(
            user=listing.owner,
            text=f'Your listing "{listing.title}" was rejected: {reason}',
            link="/dashboard/",
        )

        prefs = getattr(listing.owner, 'notifications_prefence', None)
        if prefs and prefs.email_listing_status and listing.owner.email:
            send_mail(
                subject="Your listing was rejected",
                message=f'Your listing "{listing.title}" was rejected.\n\nReason: {reason}\n\nView your dashboard: /dashboard/',
                from_email="noreply@example.com",
                recipient_list=[listing.owner.email],
                fail_silently=True
            )

def moderate_listing(listing):
    client = Groq(api_key=settings.GROQ_API_KEY, timeout=60)

    prompt = f"""Review this real estate listing. Reject if it contains spam, scam patterns, offensive language, or fake/placeholders content unrelated to real estate.
    
Title = {listing.title}
Description = {listing.description}
Property type: {listing.property_type}
Price: {listing.price}

Respond with exactly one word on the first line: APPROVED or REJECTED
Then a short reason on the second line."""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        lines = content.split("\n")
        verdict = lines[0].strip().upper()
        reason = lines[1] if len(lines) > 1 else ""

        if "APPROVED" in verdict:
            return "approved", reason
        return "rejected", reason
    except Exception:
        return "pending", "Moderation check failed, needs manual review"

@login_required
def start_conversation(request, listing_id):
    listing = get_object_or_404(Listing, pk=listing_id)
    if listing.owner == request.user:
        messages.error(request, "You can't message yourself.")
        return redirect("main:listing_detail", pk=listing.pk)

    if BlockedUser.objects.filter(blocker=listing.owner, blocked=request.user).exists():
        messages.error(request, "You can's contact this user")
        return redirect("main:listing_detail", pk=listing.pk)

    conversation = Conversation.objects.filter(participants=request.user).filter(participants=listing.owner).first()
    if not conversation:
        conversation = Conversation.objects.create(listing=listing)
        conversation.participants.add(request.user, listing.owner)
    elif conversation.is_archived:
        conversation.is_archived = False
        conversation.save()

    return redirect("main:conversation_detail", pk=conversation.pk)

@login_required
def inbox(request):
    conversations = build_conversation_list(request.user)
    return render(request=request, template_name="main/inbox.html", context={"conversations":conversations})

@login_required
def conversation_detail(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    other_user = conversation.participants.exclude(pk=request.user.pk).first()

    is_blocked = BlockedUser.objects.filter(
        Q(blocker=request.user, blocked=other_user) | Q(blocker=other_user, blocked=request.user)
    ).exists()

    if request.method == "POST":
        if not is_blocked:
            body = request.POST.get("body", "").strip()
            attachment = request.FILES.get("attachment")
            if body or attachment:
                Message.objects.create(conversation=conversation, sender=request.user, body=body, attachment=attachment)
                prefs = getattr(other_user, 'notifications_prefence', None)
                if prefs and prefs.email_new_message and other_user.email:
                    send_mail(
                        subject=f"New message from {request.user.username}",
                        message=f"{request.user.username} sent you a message.\n\nView it: /inbox/{conversation.pk}/",
                        from_email="noremply@example.com",
                        recipient_list=[other_user.email],
                        fail_silently=True,
                    )
        elif request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            messages.warning(request, "You can't send messages to this user.")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render(request=request, template_name="main/chat_messages.html", context={"active_conversation":conversation})
        return redirect("main:conversation_detail", pk=conversation.pk)

    conversation.messages.exclude(sender=request.user).update(is_read=True)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return render(request=request, template_name="main/chat_messages.html", context={"active_conversation":conversation})

    if is_blocked:
        messages.warning(request, "You can't message this user.")

    conversations = build_conversation_list(request.user)
    return render(request=request, template_name="main/conversation_detail.html", context={"conversations":conversations, "active_conversation":conversation, "other_user":other_user, "is_blocked":is_blocked})

def build_conversation_list(user, archived=False):
    convs = list(
        user.conversations
        .filter(is_archived=archived)
        .distinct()
        .prefetch_related(
            Prefetch('participants', queryset=User.objects.exclude(pk=user.pk), to_attr='other_participants'),
            Prefetch('messages', queryset=Message.objects.order_by('-created_at'), to_attr='ordered_messages'),
            
        )
    )

    conv_ids = [c.pk for c in convs]
    unread_count = dict(
        Message.objects.filter(conversation_id__in=conv_ids, is_read=False)
        .exclude(sender=user)
        .values('conversation_id')
        .annotate(count=Count('id'))
        .values_list('conversation_id', 'count')
    )

    result = []
    for c in convs:
        other = c.other_participants[0] if c.other_participants else None
        last_msg = c.ordered_messages[0] if c.ordered_messages else None
        result.append({
            "conversation": c,
            "other_user": other,
            "last_message": last_msg,
            "unread_count": unread_count.get(c.pk, 0),
        })

    result.sort(
        key=lambda item: item["last_message"].created_at if item["last_message"] else item["conversation"].created_at,
        reverse=True
    )

    return result

def navbar_conversations(request):
    if request.user.is_authenticated:
        unread_count = Message.objects.filter(
            conversation__participants=request.user, is_read=False
        ).exclude(sender=request.user).count()

        raw_conversations = request.user.conversations.filter(is_archived=False).distinct()
        conversations = []
        for c in raw_conversations:
            other = c.participants.exclude(pk=request.user.pk).first()
            last = c.messages.order_by('-created_at').first()
            conversations.append({
                "pk": c.pk,
                "other_user": other,
                "last_message": last,
            })

        conversations.sort(
            key=lambda item: item["last_message"].created_at if item["last_message"] else timezone.now(),
            reverse=True
        )
        conversations = conversations[:5]

        notifications = request.user.notifications.order_by('-created_at')[:5]
        unread_notifications_count = request.user.notifications.filter(is_read=False).count()

        return {
            "navbar_conversations": conversations,
            "navbar_unread_count": unread_count,
            "navbar_notifications": notifications,
            "navbar_unread_notifications_count": unread_notifications_count
        }

    return {}

@login_required
def archive_conversation(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    conversation.is_archived = True
    conversation.save()
    return redirect("main:inbox")

@login_required
def archived_conversations(request):
    conversations = build_conversation_list(request.user, archived=True)
    return render(request=request, template_name="main/inbox.html", context={"conversations":conversations, "active_conversation":None, "viewing_archive":True})

@login_required
def unarchive_conversation(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    conversation.is_archived = False
    conversation.save()
    return redirect("main:archived_conversations")

@login_required
def report_conversation(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    conversation.is_reported = True
    conversation.save()
    messages.success(request, "Conversation reported.")
    return redirect("main:conversation_detail", pk=pk)

@login_required
def unblock_user(request, user_id):
    other = get_object_or_404(User, pk=user_id)
    BlockedUser.objects.filter(blocker=request.user, blocked=other).delete()
    messages.success(request, f"You have unblocked {other.username}.")
    return redirect("main:blocked_users")

@login_required
def blocked_users(request):
    blocked = BlockedUser.objects.filter(blocker=request.user).select_related('blocked')
    return render(request=request, template_name="main/blocked_users.html", context={"blocked":blocked})

@login_required
def block_conversation_user(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    other = conversation.participants.exclude(pk=request.user.pk).first()
    if other:
        BlockedUser.objects.get_or_create(blocker=request.user, blocked=other)
        messages.success(request, f"You have blocked {other.username}.")
    return redirect("main:inbox")

@login_required
def delete_conversation(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, participants=request.user)
    conversation.delete()
    return redirect("main:inbox")

@login_required
def delete_account(request):
    if request.method == "POST":
        request.user.conversations.all().delete()
        request.user.delete()
        messages.info(request, "Your account has been deleted.")
        return redirect("main:homepage")
    return redirect("main:settings_page")

@login_required
def change_password(request):
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed successfully")
            return redirect("main:settings_page")
    else:
        form = PasswordChangeForm(request.user)
    return render(request=request, template_name="main/change_password.html", context={"form":form})

def read_notification(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.is_read = True
    notif.save()
    return redirect(notif.link or "main:dashboard")

def notifications_check(request):
    if not request.user.is_authenticated:
        return JsonResponse({"count": 0, "notifications": []})
    unread = request.user.notifications.filter(is_read=False).order_by('-created_at')[:5]
    data = [
        {
            "id": n.pk,
            "text": n.text,
            "link": n.link or "",
            "timesince": timesince(n.created_at) + " ago",
        }
        for n in unread 
    ]

    message_count = Message.objects.filter(
        conversation__participants=request.user, is_read=False
    ).exclude(sender=request.user).count()

    raw_conversations = request.user.conversations.filter(is_archived=False).distinct()
    conv_items = []
    for c in raw_conversations:
        other = c.participants.exclude(pk=request.user.pk).first()
        last = c.messages.order_by('-created_at').first()
        conv_items.append({
            "pk": c.pk,
            "other_user": other.username if other else "",
            "avatar_letter": other.username[0].upper() if other else "?",
            "last_message": last.body if last else "",
            "sort_key": last.created_at.isoformat() if last else c.created_at.isoformat()
        })
    conv_items.sort(key=lambda item: item["sort_key"], reverse=True)
    conv_items = conv_items[:5]

    return JsonResponse({"count": len(data), "notifications": data, "message_count":message_count, "conversations": conv_items,})

@login_required
def mark_all_notifications_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return JsonResponse({"status": "ok"})

@login_required
def settings_page(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    prefs, _ = NotificationPrefence.objects.get_or_create(user=request.user)
    blocked = BlockedUser.objects.filter(blocker=request.user).select_related('blocked')
    return render(request=request, template_name="main/settings.html", context={"profile":profile, "prefs":prefs, "blocked":blocked,})

@login_required
def update_account_info(request):
    if request.method == "POST":
        profile, _ = Profile.objects.get_or_create(user=request.user)
        new_username = request.POST.get("username", "").strip()
        new_email = request.POST.get("email", "").strip()

        if User.objects.filter(username=new_username).exclude(pk=request.user.pk).exists():
            messages.error(request, "This username or already taken.")
            return redirect("main:settings_page")

        if User.objects.filter(email=new_email).exclude(pk=request.user.pk).exists():
            messages.error(request, "This email is already in use.")
            return redirect("main:settings_page")

        request.user.username = new_username
        request.user.email = new_email
        request.user.save()

        profile.is_agency = request.POST.get('is_agency') == "on"
        profile.company_name = request.POST.get('company_name', "").strip()
        profile.agency_phone = request.POST.get('agency_phone', "").strip()
        profile.save()

        if request.FILES.get('avatar'):
            profile.avatar = request.FILES['avatar']
            profile.save()

        messages.success(request, "Profile updated")
    return redirect("main:settings_page")

@login_required
def update_notification_preferences(request):
    if request.method == "POST":
        prefs, _ = NotificationPrefence.objects.get_or_create(user=request.user)
        prefs.email_new_message = request.POST.get("email_new_message") == "on"
        prefs.email_listing_status = request.POST.get("email_listing_status") == "on"
        prefs.save()
        messages.success(request, "Notification prefences updated.")
    return redirect("main:settings_page")

@login_required
def toggle_favorite(request, pk):
    listing = get_object_or_404(Listing, pk=pk)
    favorite, created = Favorite.objects.get_or_create(user=request.user, listing=listing)

    if not created:
        favorite.delete()

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        is_favorited = Favorite.objects.filter(user=request.user, listing=listing).exists()
        return JsonResponse({"is_favorited": is_favorited})
    return redirect("main:listing_detail", pk=listing.pk)

def geocode_address(address, district, city):
    query = ", ".join(p for p in [address, district, city] if p)

    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "json", "q": query, "limit": 1},
            headers={"User-Agent": "HomelandApp/1.0"},
            timeout=5,
        )
        result = response.json()
        if result:
            return float(result[0]["lat"]), float(result[0]["lon"])
    except Exception:
        return None, None

@login_required
def reorder_images(request, pk):
    listing = get_object_or_404(Listing, pk=pk, owner=request.user)
    if request.method == "POST":
        order_list = request.POST.getlist('image_order[]')
        for index, image_id in enumerate(order_list):
            ListingImage.objects.filter(pk=image_id, listing=listing).update(order=index)
    return JsonResponse({"status": "ok"})

@login_required
def delete_listing_image(request, image_id):
    image = get_object_or_404(ListingImage, pk=image_id)
    if image.listing.owner != request.user:
        messages.error(request, "Not allowed.")
        return redirect("main:dashboard")
    listing_pk = image.listing.pk
    image.delete()
    return redirect("main:edit_listing", pk=listing_pk)

@login_required
def set_cover_image(request, image_id):
    image = get_object_or_404(ListingImage, pk=image_id)
    if image.listing.owner != request.user:
        messages.error(request, "Not allowed.")
        return redirect("main:dashboard")
    ListingImage.objects.filter(listing=image.listing).update(is_cover=False)
    image.is_cover = True
    image.save()
    return redirect("main:edit_listing", pk=image.listing.pk)

@login_required
def save_search(request):
    if request.method == "POST" and request.user.is_authenticated:
        SavedSearch.objects.create(
            user=request.user,
            name=request.POST.get('name', '').strip(),
            q=request.POST.get('q', '').strip(),
            listing_type=request.POST.get('listing_type', ''),
            property_type=request.POST.get('property_type', ''),
            city=request.POST.get('city', ''),
            min_price=request.POST.get('min_price') or None,
            max_price=request.POST.get('max_price') or None,
            rooms=request.POST.get('rooms') or None,
        )
        messages.success(request, "Search saved. We'll notify you when a matching listing is approved.")

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({"status": "ok"})

    return redirect("main:listing_list")

@login_required
def delete_saved_search(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk, user=request.user)
    search.delete()
    messages.info(request, "Saved search removed.")
    return redirect("main:dashboard")

@login_required
def toggle_saved_search_alerts(request, pk):
    search = get_object_or_404(SavedSearch, pk=pk, user=request.user)
    search.email_alerts = not search.email_alerts
    search.save()
    return redirect("main:dashboard")

def notify_matching_saved_searches(listing):
    candidates = SavedSearch.objects.filter(email_alerts=True).exclude(user=listing.owner).select_related('user')

    for search in candidates:
        if not search.matches(listing):
            continue

        Notification.objects.create(
            user=search.user,
            text=f'New listing matching "{search.name or "your saved search"}": {listing.title}',
            link=f"/listings/{listing.slug}-{listing.pk}/",
        )
        search.last_notified_at = timezone.now()
        search.save(update_fields=['last_notified_at'])

        if search.user.email:
            send_mail(
                subject="New listing matches your saved search",
                message=f'A new listing matches your saved search: {listing.title}\n\n'
                        f'View it /listings/{listing.slug}-{listing.pk}/',
                from_email="noreply@example.com",
                recipient_list=[search.user.email],
                fail_silently=True
            )

def agency_profile(request, username):
    agency_user = get_object_or_404(User, username=username)
    profile = getattr(agency_user, 'profile', None)

    if not profile or not profile.is_agency:
        raise Http404

    listings = Listing.objects.filter(
        owner=agency_user, is_active=True, moderation_status="approved"
    ).order_by('-created_at')

    reviews = AgencyReview.objects.filter(agency=agency_user).select_related('reviewer')
    review_count = reviews.count()
    average_rating = 0
    if review_count:
        average_rating = round(sum(r.rating for r in reviews) / review_count, 1)

    my_review = None
    if request.user.is_authenticated:
        my_review = reviews.filter(reviewer=request.user).first()

    all_listings = Listing.objects.filter(owner=agency_user)
    total_views = all_listings.aggregate(total=Sum('view_count'))['total'] or 0 
    sold_count = all_listings.filter(availability='sold').count()
    rented_count = all_listings.filter(availability='rented').count()
    closed_count = sold_count + rented_count
    total_listing_count = all_listings.count()
    success_rate = round((closed_count / total_listing_count) * 100) if total_listing_count else 0

    conversations = Conversation.objects.filter(listing__owner=agency_user, listing__isnull=False).distinct()
    response_times = []
    for convo in conversations:
        first_buyer_msg = convo.messages.exclude(sender=agency_user).order_by('created_at').first()
        if not first_buyer_msg:
            continue
        first_buyer_reply = convo.messages.filter(sender=agency_user, created_at__gt=first_buyer_msg.created_at).order_by('created_at').first()
        if first_buyer_reply:
            delta = first_buyer_reply.created_at - first_buyer_msg.created_at
            response_times.append(delta.total_seconds())

    avg_response_hours = None
    avg_response_display = None
    if response_times:
        avg_seconds = sum(response_times) / len(response_times)
        avg_response_hours = round(avg_seconds / 3600, 1)
        if avg_seconds < 3600:
            avg_response_display = f"{round(avg_seconds / 60)}m"
        else:
            avg_response_display = f"{avg_response_hours}h"

    return render(request=request, template_name="main/agency_profile.html", context={"agency_user":agency_user, "profile":profile, "listings":listings, "listing_count":listings.count(), "reviews":reviews, "review_count":review_count, "average_rating":average_rating, "my_review":my_review,  "total_listings": total_listing_count, "active_listings": listings.count(), "total_views": total_views, "sold_count": sold_count, "rented_count": rented_count, "success_rate": success_rate, "avg_response_hours": avg_response_hours, "avg_response_display":avg_response_display})

@login_required
def submit_agency_review(request, username):
    agency_user = get_object_or_404(User, username=username)
    profile = getattr(agency_user, 'profile', 'None')

    if not profile or not profile.is_agency:
        raise Http404

    if agency_user == request.user:
        messages.error(request, "You can't review yourself.")
        return redirect("main:agency_profile", username=username)

    if request.method == "POST":
        rating = request.POST.get("rating")
        comment = request.POST.get("comment", "").strip()

        if rating and rating.isdigit() and 1 <= int(rating) <= 5:
            AgencyReview.objects.update_or_create(
                agency=agency_user,
                reviewer=request.user,
                defaults={"rating": int(rating), "comment": comment},
            )
            messages.success(request, "Your review has been submitted.")
        else:
            messages.error(request, "Please select a rating between 1 and 5.")
    return redirect("main:agency_profile", username=username)

@ratelimit(key="user_or_ip", rate="15/m", block=True)
def ai_price_estimate(request, pk):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    listing = get_object_or_404(Listing, pk=pk)

    similar_listings = Listing.objects.filter(
        city=listing.city,
        property_type=listing.property_type,
        listing_type=listing.listing_type,
        is_active=True,
        moderation_status="approved",
        area_sqm__gt=0,
    ).exclude(pk=listing.pk)[:15]

    comps_text = ""
    for l in similar_listings:
        comps_text += f"- {l.title}: ${l.price}, {l.area_sqm} sqm, {l.rooms or '?'} rooms, {l.district}"

    if not comps_text:
        comps_text = "No comparable listings avaible in this city/type."

    client = Groq(api_key=settings.GROQ_API_KEY, timeout=60)

    prompt = f"""You are a real estate pricing assistant. Estimate a fair market price range for this listing based on the comparable listing provided.
    
LISTING TO ESTIMATE:
Title: {listing.title}
City: {listing.city}, District: {listing.district}
Property type: {listing.property_type}
Offer type: {listing.get_listing_type_display}
Area: {listing.area_sqm} sqm
Rooms: {listing.rooms or 'N/A'}
Bathrooms: {listing.bathrooms or 'N/A'}
Current listed price: ${listing.price}

COMPARABLE LISTINGS IN THE SAME AREA:
{comps_text}

Respond in exactly this format:
LOW: <low estimate number only, no currency symbol or commas>
HIGH: <high estimate number only, no currency symbol or commas>
REASONING: <one short sentence explaining the estimate>"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()

        low, high, reasoning = None, None, ""
        for line in content.split("\n"):
            if line.upper().startswith("LOW:"):
                low = line.split(":", 1)[1].strip().replace(",", "").replace("$", "")
            elif line.upper().startswith("HIGH:"):
                high = line.split(":", 1)[1].strip().replace(",", "").replace("$", "")
            elif line.upper().startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()
        if not low or not high:
            return JsonResponse({"error": "Could not pase estimate"}, status=500)

        return JsonResponse({
            "low": float(low),
            "high": float(high),
            "reasoning": reasoning,
            "comp_count": similar_listings.count(),
        })
    except Exception as e:
        return JsonResponse({"error": "AI esimate unavailable right now"}, status=500)

@login_required
def report_listing(request, pk):
    listing = get_object_or_404(Listing, pk=pk)

    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if not reason:
            messages.error(request, "Please describe the issue before submitting.")
            return redirect("main:listing_detail", pk=pk)

        
        already_reported_today = ListingReport.objects.filter(
            listing=listing,
            reporter=request.user,
            created_at__date=timezone.localdate(),
        ).exists()
        if already_reported_today:
            messages.error(request, "You have already reported this listing today. Please try again tomorrow.")
            return redirect("main:listing_detail", pk=pk)

        report = ListingReport.objects.create(listing=listing, reporter=request.user, reason=reason)
        async_task(run_report_ai_review, report.pk)
        messages.success(request, "Report submitted. Our AI and team will review it shortly.")
    return redirect("main:listing_detail", pk=pk)

def run_report_ai_review(report_id):
    report = ListingReport.objects.select_related('listing', 'listing__owner').get(pk=report_id)
    listing = report.listing

    client = Groq(api_key=settings.GROQ_API_KEY, timeout=60)

    prompt = f"""You are a content moderation assistant for a real estate platform. A user reported a listing. Decide if the report appers VALID (the listing likely violates policy: scam, fake, offensive, misleading, duplicate, or clearly wrong info) or INVALID (report seems unfounded or is just a prefence complaint).
    
LISTING:
Ttitle: {listing.title}
Description: {listing.description}
Price: {listing.price}
City: {listing.city}

REPORT REASON FROM USER:
{report.reason}

Respond in exactly this format:
VERDICT: <VALID OR INVALID>
NOTES: <one short sentence explaining why>"""

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()

        verdict = "invalid"
        notes = ""
        for line in content.split("\n"):
            if line.upper().startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                verdict = "valid" if "VALID" in v and "INVALID" not in v else "invalid"
            elif line.upper().startswith("NOTES:"):
                notes = line.split(":", 1)[1].strip()

        report.ai_verdict = verdict
        report.ai_notes = notes
        report.resolved = True
        report.save()

        if verdict == "valid":
            listing.moderation_status = "pending"
            listing.is_active = False
            listing.moderation_note = f"Flagged by AI after user report: {notes}"
            listing.save()

            Notification.objects.create(
                user=listing.owner,
                text=f'Your listing "{listing.title}" was flagged for review after a  user report.',
                link=f"/listings/{listing.slug}-{listing.pk}/",
            )

    except Exception:
        report.ai_notes = "AI review failed, needs manual check."
        report.save()

@login_required
def request_appointment(request, pk):
    listing = get_object_or_404(Listing, pk=pk)

    if listing.owner == request.user:
        messages.error(request, "You cannot request an appointment for your own listing.")
        return redirect("main:listing_detail", pk=pk)

    if request.method == "POST":
        has_pending = AppointmentRequest.objects.filter(
            listing=listing,
            requester=request.user,
            status='pending',
        ).exists()
        if has_pending:
            messages.error(request, "You already have a pending appointment request for this listing.")
            return redirect("main:listing_detail", pk=pk)

        form = AppointmentRequestForm(request.POST)
        if form.is_valid():
            appointment = form.save(commit=False)
            appointment.listing = listing
            appointment.requester = request.user
            appointment.save()

            Notification.objects.create(
                user=listing.owner,
                text=f'{request.user.username} requested an appointment for "{listing.title}" on {appointment.requested_date} at {appointment.requested_time.strftime("%H:%M")}.',
                link=f"/dashboard/",
            )

            messages.success(request, "Appointment request sent.")
        else:
            messages.error(request, "Please provide a valid date and time.")
    return redirect("main:listing_detail", pk=pk)

@login_required
def approve_appointment(request, pk):
    appointment = get_object_or_404(AppointmentRequest, pk=pk, listing__owner=request.user)
    appointment.status = 'approved'
    appointment.save()

    Notification.objects.create(
        user=appointment.requester,
        text=f'Your appointment for "{appointment.listing.title}" on {appointment.requested_date} at {appointment.requested_time.strftime("%H:%M")} was approved.',
        link=f"/dashboard/",
    )

    messages.success(request, "Appointment approved.")
    return redirect("main:dashboard")

@login_required
def reject_appointment(request, pk):
    appointment = get_object_or_404(AppointmentRequest, pk=pk, listing__owner=request.user)
    appointment.status = 'rejected'
    appointment.save()

    Notification.objects.create(
        user=appointment.requester,
        text=f'Your appointment for "{appointment.listing.title}" on {appointment.requested_date} at {appointment.requested_time.strftime("%H:%M")} was declined.',
        link=f"/dashboard",
    )

    messages.success(request, "Appointment rejected.")
    return redirect("main:dashboard")

@login_required
def cancel_appointment(request, pk):
    appointment = get_object_or_404(AppointmentRequest, pk=pk, requester=request.user)
    appointment.status = 'cancelled'
    appointment.save()
    messages.success(request,"Appointment cancelled.")
    return redirect("main:dashboard")

def delete_notification(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.delete()
    return JsonResponse({"status": "ok"})

@login_required
def clear_notificatons(request):
    request.user.notifications.all().delete()
    return JsonResponse({"status": "ok"})

def calculate_walk_score(lat, lng):
    if not lat or not lng:
        return None, {}

    query = f"""
    [out:json][timeout:25];
    (
        node["shop"="supermarket"](around:2400,{lat},{lng});
        node["amenity"="restaurant"](around:2400,{lat},{lng});
        node["amenity"="cafe"](around:2400,{lat},{lng});
        node["amenity"="school"](around:2400,{lat},{lng});
        node["amenity"="pharmacy"](around:2400,{lat},{lng});
        node["leisure"="park"](around:2400,{lat},{lng});
        node["highway"="bus_stop"](around:2400,{lat},{lng});
        node["railway"="station"](around:2400,{lat},{lng});
    );
    out body;
    """

    try:
        response = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers={"User-Agent": "HomelandApp/1.0", "Accept": "application/json"},
            timeout=30,
        )
        elements = response.json().get("elements", [])

        def haversine(lat1, lon1, lat2, lon2):
            R = 6371000
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlambda = math.radians(lon2 -lon1)
            a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
            return 2 * R * math.asin(math.sqrt(a))

        total_score = 0
        nearby = {"schools": [], "hospitals":[], "parks": [], "transit": []}

        for el in elements:
            if "lat" not in el or "lon" not in el:
                continue
            dist = haversine(lat, lng, el["lat"], el["lon"])
            if dist <= 400:
                total_score += 5
            elif dist <= 2400:
                total_score += 5 * (1 - (dist - 400) / 2000)

            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue

            entry = {"name": name, "distance_m": round(dist)}

            if tags.get("amenity") == "school" and len(nearby["schools"]) < 5:
                nearby["schools"].append(entry)
            elif tags.get("amenity") == "hospital" and len(nearby["hospitals"]) < 5:
                nearby["hospitals"].append(entry)
            elif tags.get("leisure") == "park" and len(nearby["parks"]) < 5:
                nearby["parks"].append(entry)
            elif (tags.get("highway") == "bus_stop" or tags.get("railway") == "station") and len(nearby["transit"]) < 5:
                nearby["transit"].append(entry)

        for key in nearby:
            nearby[key].sort(key=lambda x: x["distance_m"])

        score =  min(100, round(total_score))
        return score, nearby
    except Exception as e :
        print(f"Walk score calculation failed: {e}")
        return None, {}

def calculate_walk_score_delayed(listing_id):
    try:
        listing = Listing.objects.get(pk=listing_id)
    except Listing.DoesNotExist:
        return
    score, nearby = calculate_walk_score(listing.latitude, listing.longitude)
    listing.walk_score = score
    listing.nearby_places = nearby
    listing.save(update_fields=['walk_score', 'nearby_places'])

@ratelimit(key='user', rate="10/m", block=False)
def ask_listing_ai(request, pk):
    if getattr(request, 'limited', False):
        return JsonResponse({"error": "Too many requests. Please wait a moment and try again."}, status=429)

    if request.method != "POST":
        return JsonResponse({"error": "Invalid request"}, status=400)

    listing = get_object_or_404(Listing, pk=pk)
    question = request.POST.get("question", "").strip()
    history_raw = request.POST.get("history", "[]")

    if not question:
        return JsonResponse({"error": "Question is empty"}, status=400)

    try:
        history = json.loads(history_raw)
    except (ValueError, TypeError):
        history = []

    nearby_text = json.dumps(listing.nearby_places) if listing.nearby_places else "No data available"

    prompt = f"""You are a friendly, knowledgeable real estate assistant helping a user evaluate this specifig listing, in a ongoing conversation.
   
LISTING_DETAILS:
Title: {listing.title}
Description: {listing.description}
Price: ${listing.price}
City/Disctrict: {listing.city}, {listing.district}
Area: {listing.area_sqm} sqm
Rooms: {listing.rooms or 'N/A'}, Bathrooms: {listing.bathrooms or 'N/A'}
Year build: {listing.year_built or 'N/A'}
Heating: {listing.get_heating_display()}, Cooling: {listing.get_cooling_display()}
Has fireplace: {listing.has_fireplace}
HOA fee: {listing.hoa_fee or 'N/A'}
Walk Score: {listing.walk_score or 'N/A'}
Nearby places: {nearby_text}
Listed on: {listing.created_at.strftime('%B %d, %Y')}
     
INSTRUCTIONS: 
- Use the listing details above as context first.
- For question about the neighborhood, investment potential, market conditions, or general real estate advice, use your general real estate knowledge to give a helpful, realistic answer - don't refuse just because it's not explicitly in the data above.
- Be honest that broader assessments (investment potential, area growth, etc.) are general guidance, not guaranteed facts, not a formal appraisal.
- If the user asks something unrelated to this listing or real estate in general, politely say you can only help with questions about this property and real estate topics.
- Never recommend or mention other real estate websites, apps, or platforms(such as Zillow, Realtor.com, Redfln, etc). If the user wants to browse other listings, tell them to ouse this site's search and filters instead.
- Keep answers to 2-4 short sentences, conversational tone.
- Remember what was already discussed earlier in this conversation and stay consisten with it."""

    messages = [{"role": "system", "content": prompt}]
    for msg in history[-10:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content":question})

    client = Groq(api_key=settings.GROQ_API_KEY, timeout=60)

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0,
        )
        answer = response.choices[0].message.content.strip()
        return JsonResponse({"answer":answer})
    except Exception as e:
        return JsonResponse({"error": "AI assistant unavailable right now"}, status=500)

def market_trends(request):
    city = request.GET.get('city', '')

    approved_listings = Listing.objects.filter(is_active=True, moderation_status='approved')

    price_qs = PriceHistory.objects.filter(event__in=['listed', 'price_change'])
    if city:
        price_qs = price_qs.filter(listing__city__iexact=city)

    trend_data = (
        price_qs
        .annotate(month=TruncMonth('created_at'))
        .values('month')
        .annotate(avg_price=Avg('price'), count=Count('id'))
        .order_by('month')
    )

    trend_labels = [entry['month'].strftime('%b %Y') for entry in trend_data if entry['month']]
    trend_values = [float(entry['avg_price']) for entry in trend_data if entry['month']]
    trend_counts = [entry['count'] for entry in trend_data if entry['month']]

    heatmap_qs = (
        Listing.objects.filter(is_active=True, moderation_status='approved', latitude__isnull=False, longitude__isnull=False)
        .values('city')
        .annotate(
            listing_count=Count('id'),
            total_views=Sum('view_count'),
            avg_price=Avg('price'),
            avg_lat=Avg('latitude'),
            avg_lng=Avg('longitude'),
        )
        .order_by('-listing_count')
    )

    heatmap_data = [
        {
            "city": row["city"],
            "lat": row["avg_lat"],
            "lng": row["avg_lng"],
            "listing_count": row["listing_count"],
            "total_views": row["total_views"] or 0,
            "avg_price": round(float(row["avg_price"]), 0) if row["avg_price"] else 0,
        }
        for row in heatmap_qs
    ]

    cities = Listing.objects.filter(is_active=True).values_list('city', flat=True).distinct().order_by('city')

    total_listings = approved_listings.count()
    overall_avg = approved_listings.aggregate(avg=Avg('price'))['avg']
    top_city = heatmap_data[0] if heatmap_data else None

    return render(request=request, template_name="main/market_trends.html", context={"trend_labels_json": json.dumps(trend_labels), "trend_values_json": json.dumps(trend_values), "heatmap_data_json": json.dumps(heatmap_data), "cities": cities, "selected_city":city, "total_listings": total_listings, "overall_avg_price":overall_avg, "top_city":top_city, "trend_counts_json": json.dumps(trend_counts),})

def ratelimited_error(request, exception):
    return JsonResponse({"error": "Too many requests. Please wait a moment and try again."}, status=429)

def robots_txt(request):
    return render(request=request, template_name="main/robots.txt", content_type="text/plain")