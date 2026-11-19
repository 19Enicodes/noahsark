from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from datetime import date, datetime
import csv
from io import StringIO
from django.core.management import call_command
from django.db.models import Q

from .models import Worker, Department, CheckIn, SystemSetting
from .attendance import get_current_sunday_reference, calculate_consecutive_misses
from . import notifications

# Helper: Check if superuser exists
def has_superuser():
    return User.objects.filter(is_superuser=True).exists()

# Home / Landing Page
def home(request):
    departments = Department.objects.all()
    if request.method == 'POST':
        full_name = request.POST.get('full_name')
        phone = request.POST.get('phone')
        email = request.POST.get('email')
        dept_id = request.POST.get('department')
        
        birthday_day = request.POST.get('birthday_day')
        birthday_month = request.POST.get('birthday_month')
        birthday = None
        if birthday_day and birthday_month:
            try:
                birthday = date(1900, int(birthday_month), int(birthday_day))
            except ValueError:
                birthday = None
                
        department = Department.objects.get(id=dept_id) if dept_id else None
        
        # Simple validation
        if not full_name or not phone:
            messages.error(request, "Name and Phone Number are required.")
            return render(request, 'management/worker_register.html', {'departments': departments})
            
        if Worker.objects.filter(phone_number=phone).exists():
            messages.error(request, "A worker with this phone number is already registered.")
            return render(request, 'management/worker_register.html', {'departments': departments})
            
        worker = Worker.objects.create(
            full_name=full_name,
            phone_number=phone,
            email=email,
            department=department,
            birthday=birthday,
            status='PENDING'
        )
         
        request.session['new_worker_code'] = worker.nark_code
        return redirect('registration_success')
        
    return render(request, 'management/worker_register.html', {'departments': departments})

# Redirect /register/ to home landing
def worker_registration(request):
    return redirect('home')

def registration_success(request):
    code = request.session.get('new_worker_code', 'NARK-0000')
    return render(request, 'management/registration_success.html', {'nark_code': code})

# Find My Code Kiosk
def find_code(request):
    worker = None
    searched = False
    if request.method == 'POST':
        phone = request.POST.get('phone')
        if phone:
            # Try exact match or suffix match
            worker = Worker.objects.filter(phone_number__contains=phone).first()
            searched = True
    return render(request, 'management/find_code.html', {'worker': worker, 'searched': searched})

# Usher Login (PIN-based gateway)
def usher_login(request):
    pin_setting, _ = SystemSetting.objects.get_or_create(key='sunday_pin', defaults={'value': '1234'})
    
    if request.method == 'POST':
        entered_pin = request.POST.get('pin')
        if entered_pin == pin_setting.value:
            request.session['usher_authorized'] = True
            return redirect('usher_checkin')
        else:
            messages.error(request, "Invalid Sunday PIN. Please try again.")
            
    return render(request, 'management/usher_login.html')

# Usher Check-in Console
def usher_checkin(request):
    if not request.session.get('usher_authorized'):
        return redirect('usher_login')
        
    # AJAX Search endpoint
    q = request.GET.get('q', '')
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or q:
        workers = Worker.objects.filter(status='ACTIVE', full_name__icontains=q)[:5]
        results = [{'nark_code': w.nark_code, 'full_name': w.full_name, 'dept': w.department.name if w.department else 'None'} for w in workers]
        return JsonResponse({'results': results})

    if request.method == 'POST':
        nark_code = request.POST.get('nark_code', '').strip().upper()
        worker = Worker.objects.filter(nark_code=nark_code, status='ACTIVE').first()
        
        if worker:
            ref_date = get_current_sunday_reference()
            check_in, created = CheckIn.objects.get_or_create(
                worker=worker,
                sunday_reference=ref_date,
                defaults={'usher_id': 'Sunday Usher'}
            )
            if created:
                worker.last_check_in = datetime.now()
                worker.missed_sundays_count = 0
                worker.save()
                # Sent on a background thread: an SMS call plus an SMTP
                # handshake is seconds of latency and the usher has a queue.
                notifications.notify_in_background(
                    worker,
                    notifications.CHECKIN_CONFIRM,
                    {'date': date.today().strftime('%A, %d %B %Y')},
                )
                messages.success(request, f"✅ {worker.full_name} checked in successfully.")
            else:
                messages.info(request, f"ℹ️ {worker.full_name} is already checked in for this Sunday.")
        else:
            messages.error(request, "❌ Code not found or worker is not approved/active.")
            
    return render(request, 'management/sunday_checkin.html')

# Admin Setup (The "Keys" Phase)
def admin_setup(request):
    if has_superuser():
        return redirect('admin_login')

    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
        else:
            User.objects.create_superuser(username, email, password)
            messages.success(request, "Super Admin created successfully!")
            return redirect('admin_login')

    return render(request, 'management/admin_setup.html')

# Admin Login (The "Stewardship" Phase)
def admin_login(request):
    if not has_superuser():
        return redirect('admin_setup')
        
    if request.method == 'POST':
        raw_login = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''

        # Support login with either username or email (case-insensitive) and strip spaces
        user_obj = User.objects.filter(
            Q(username__iexact=raw_login) | Q(email__iexact=raw_login)
        ).first()
        resolved_username = user_obj.username if user_obj else raw_login

        user = authenticate(request, username=resolved_username, password=password)
        
        if user is not None and user.is_superuser:
            auth_login(request, user)
            return redirect('admin_dashboard')
        else:
            messages.error(request, "Invalid Admin Credentials.")
            
    return render(request, 'management/admin_login.html')

# Admin Logout
def admin_logout(request):
    auth_logout(request)
    return redirect('admin_login')

# Superuser Required Check
def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_superuser, login_url='admin_login')(view_func)

def _resolve_sunday_param(request, current_ref_date):
    """
    Safely resolves the requested Sunday date from request.GET, handling:
    - Single date parameter (?date=2026-09-20)
    - Duplicate date parameters (?date=2026-09-20&date=2026-11-22) by prioritizing
      any historical / explicitly chosen date over the default/current reference date.
    - Invalid date formats (falls back to current_ref_date).
    """
    raw_dates = [d.strip() for d in request.GET.getlist('date') if d.strip()]
    if not raw_dates:
        return current_ref_date

    parsed_dates = []
    for raw in raw_dates:
        try:
            parsed_dates.append(datetime.strptime(raw, '%Y-%m-%d').date())
        except ValueError:
            continue

    if not parsed_dates:
        return current_ref_date

    # If multiple dates were passed, prefer any date that differs from current_ref_date
    for d in parsed_dates:
        if d != current_ref_date:
            return d

    return parsed_dates[0]

# Admin Dashboard
@superuser_required
def admin_dashboard(request):
    # Retrieve system PIN
    pin_setting, _ = SystemSetting.objects.get_or_create(key='sunday_pin', defaults={'value': '1234'})
    
    if request.method == 'POST' and 'update_pin' in request.POST:
        new_pin = request.POST.get('sunday_pin')
        if new_pin:
            pin_setting.value = new_pin
            pin_setting.save()
            messages.success(request, "Sunday PIN updated successfully.")
            
    # Determine selected Sunday reference date
    current_ref_date = get_current_sunday_reference()
    selected_sunday = _resolve_sunday_param(request, current_ref_date)

    # Get list of all distinct Sundays with check-in records for dropdown
    recorded_sundays = list(
        CheckIn.objects.values_list('sunday_reference', flat=True)
        .distinct()
        .order_by('-sunday_reference')
    )
    if current_ref_date not in recorded_sundays:
        recorded_sundays.insert(0, current_ref_date)
    if selected_sunday not in recorded_sundays:
        recorded_sundays.append(selected_sunday)
        recorded_sundays.sort(reverse=True)

    # Attendees for the selected Sunday
    attendee_checkins = list(
        CheckIn.objects.filter(sunday_reference=selected_sunday)
        .select_related('worker', 'worker__department')
        .order_by('check_in_time')
    )
    present_worker_ids = {c.worker_id for c in attendee_checkins}
    present_today = len(attendee_checkins)

    # Active workers eligible on or before this Sunday
    eligible_workers = list(
        Worker.objects.filter(
            status='ACTIVE',
            date_joined__date__lte=selected_sunday
        ).select_related('department').order_by('full_name')
    )
    total_active = len(eligible_workers)
    if total_active < present_today:
        eligible_workers = list(Worker.objects.filter(status='ACTIVE').select_related('department').order_by('full_name'))
        total_active = len(eligible_workers)

    absent_workers = [w for w in eligible_workers if w.id not in present_worker_ids]
    absent_today = max(0, total_active - present_today)
    
    # Approvals list
    pending_workers = Worker.objects.filter(status='PENDING').order_by('-date_joined')
    
    # Welfare Lists (Yellow and Red Flags) - based on current live cycle
    active_workers = Worker.objects.filter(status='ACTIVE')
    yellow_flag_workers = []
    red_flag_workers = []
    
    for worker in active_workers:
        checked_in_today = CheckIn.objects.filter(worker=worker, sunday_reference=current_ref_date).exists()
        if checked_in_today:
            continue
            
        misses = calculate_consecutive_misses(worker)
        if misses == 2:
            yellow_flag_workers.append({'worker': worker, 'misses': misses})
        elif misses >= 3:
            red_flag_workers.append({'worker': worker, 'misses': misses})
            
    # Attendance by department stats for selected Sunday
    departments = Department.objects.all().order_by('name')
    dept_stats = []
    for dept in departments:
        dept_present_checkins = [c for c in attendee_checkins if c.worker.department_id == dept.id]
        dept_absent_workers = [w for w in absent_workers if w.department_id == dept.id]
        dept_present = len(dept_present_checkins)
        dept_active = dept_present + len(dept_absent_workers)
        percentage = int((dept_present / dept_active * 100)) if dept_active > 0 else 0
        dept_stats.append({
            'department': dept,
            'active': dept_active,
            'present': dept_present,
            'percentage': percentage,
            'present_checkins': dept_present_checkins,
            'absent_workers': dept_absent_workers,
        })

    # If there are workers or attendees without an assigned department
    unassigned_checkins = [c for c in attendee_checkins if not c.worker.department_id]
    unassigned_absent = [w for w in absent_workers if not w.department_id]
    if unassigned_checkins or unassigned_absent:
        unassigned_present = len(unassigned_checkins)
        unassigned_active = unassigned_present + len(unassigned_absent)
        percentage = int((unassigned_present / unassigned_active * 100)) if unassigned_active > 0 else 0
        dept_stats.append({
            'department': {'id': 'unassigned', 'name': 'General / Unassigned'},
            'active': unassigned_active,
            'present': unassigned_present,
            'percentage': percentage,
            'present_checkins': unassigned_checkins,
            'absent_workers': unassigned_absent,
        })

    context = {
        'sunday_pin': pin_setting.value,
        'ref_date': selected_sunday,
        'current_ref_date': current_ref_date,
        'is_historical': (selected_sunday != current_ref_date),
        'recorded_sundays': recorded_sundays,
        'attendee_checkins': attendee_checkins,
        'absent_workers': absent_workers,
        'total_active': total_active,
        'present_today': present_today,
        'absent_today': absent_today,
        'pending_workers': pending_workers,
        'yellow_flag_workers': yellow_flag_workers,
        'red_flag_workers': red_flag_workers,
        'dept_stats': dept_stats,
    }
    
    return render(request, 'management/admin_dashboard.html', context)

# Export Specific Sunday Attendance Record as CSV
@superuser_required
def export_sunday_attendance(request):
    current_ref_date = get_current_sunday_reference()
    selected_sunday = _resolve_sunday_param(request, current_ref_date)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sunday_attendance_{selected_sunday}.csv"'
    response.write('\ufeff')  # UTF-8 BOM for Microsoft Excel

    writer = csv.writer(response)
    writer.writerow([
        'Sunday Date',
        'Worker Name',
        'NARK Code',
        'Department',
        'Phone Number',
        'Email',
        'Attendance Status',
        'Check-in Time',
        'Checked In By (Usher)'
    ])

    # Check-ins for this Sunday
    attendee_checkins = list(
        CheckIn.objects.filter(sunday_reference=selected_sunday)
        .select_related('worker', 'worker__department')
        .order_by('check_in_time')
    )
    present_worker_ids = {c.worker_id for c in attendee_checkins}

    # Active workers eligible on or before this Sunday
    eligible_workers = list(
        Worker.objects.filter(
            status='ACTIVE',
            date_joined__date__lte=selected_sunday
        ).select_related('department').order_by('full_name')
    )
    if len(eligible_workers) < len(attendee_checkins):
        eligible_workers = list(Worker.objects.filter(status='ACTIVE').select_related('department').order_by('full_name'))

    absent_workers = [w for w in eligible_workers if w.id not in present_worker_ids]

    present_rows = []
    for c in attendee_checkins:
        w = c.worker
        dept_name = w.department.name if w.department else 'None'
        time_str = c.check_in_time.strftime('%I:%M %p') if c.check_in_time else 'Yes'
        present_rows.append([
            str(selected_sunday),
            w.full_name,
            w.nark_code,
            dept_name,
            w.phone_number,
            w.email or '',
            'PRESENT',
            time_str,
            c.usher_id or 'System'
        ])

    absent_rows = []
    for w in absent_workers:
        dept_name = w.department.name if w.department else 'None'
        absent_rows.append([
            str(selected_sunday),
            w.full_name,
            w.nark_code,
            dept_name,
            w.phone_number,
            w.email or '',
            'ABSENT',
            '-',
            '-'
        ])

    for row in present_rows + absent_rows:
        writer.writerow(row)

    return response

# Approve Worker Action
@superuser_required
def approve_worker(request, worker_id):
    worker = get_object_or_404(Worker, id=worker_id)
    worker.status = 'ACTIVE'
    worker.save()
    messages.success(request, f"Approved worker {worker.full_name}.")
    return redirect('admin_dashboard')

# Export Worker Database as CSV
@superuser_required
def export_workers(request):
    export_type = request.GET.get('type', 'workers')
    
    response = HttpResponse(content_type='text/csv')
    if export_type == 'members':
        response['Content-Disposition'] = f'attachment; filename="nark_members_{date.today()}.csv"'
        workers = Worker.objects.filter(department__name__in=['Member', 'Members']).select_related('department')
    else:
        response['Content-Disposition'] = f'attachment; filename="nark_workers_{date.today()}.csv"'
        workers = Worker.objects.exclude(department__name__in=['Member', 'Members']).select_related('department')
        
    writer = csv.writer(response)
    writer.writerow(['Full Name', 'NARK Code', 'Phone Number', 'Email', 'Department', 'Status', 'Date Joined', 'Birthday'])
    
    for w in workers:
        writer.writerow([
            w.full_name,
            w.nark_code,
            w.phone_number,
            w.email if w.email else '',
            w.department.name if w.department else 'None',
            w.status,
            w.date_joined.strftime('%Y-%m-%d'),
            w.birthday.strftime('%Y-%m-%d') if w.birthday else ''
        ])
        
    return response

# Trigger Birthday Wishes Action
@superuser_required
@require_POST
def send_birthday_trigger(request):
    try:
        output = StringIO()
        call_command('send_birthday_reminders', stdout=output, stderr=output, no_color=True)
        messages.success(
            request,
            f"✅ Birthday check complete. {_summarise_command_output(output.getvalue())}"
        )
    except Exception as e:
        messages.error(request, f"❌ Failed to send birthday wishes: {str(e)}")
    return redirect('admin_dashboard')

# Trigger Welfare Follow-ups Action
@superuser_required
@require_POST
def send_welfare_trigger(request):
    try:
        output = StringIO()
        call_command('send_welfare_reminders', stdout=output, stderr=output, no_color=True)
        messages.success(
            request,
            f"✅ Welfare check complete. {_summarise_command_output(output.getvalue())}"
        )
    except Exception as e:
        messages.error(request, f"❌ Failed to send welfare follow-ups: {str(e)}")
    return redirect('admin_dashboard')

# Trigger Automated Reminders Action (Legacy combined fallback)
# POST-only: these spend real money, so a refresh, double-click or browser
# prefetch must not be able to re-fire them.
@superuser_required
@require_POST
def run_reminders_trigger(request):
    try:
        output = StringIO()
        # no_color so ANSI escape codes don't leak into the flash message.
        call_command('run_reminders', stdout=output, stderr=output, no_color=True)
        messages.success(
            request,
            f"✅ Birthday & welfare check complete. {_summarise_command_output(output.getvalue())}"
        )
    except Exception as e:
        messages.error(request, f"❌ Failed to run birthday & welfare check: {str(e)}")
    return redirect('admin_dashboard')

# Trigger Sunday Check-in Reminders
@superuser_required
@require_POST
def send_reminders_trigger(request):
    try:
        output = StringIO()
        # --force so the dashboard button still works if leadership needs to
        # send outside a Sunday; the command guards the scheduled path.
        call_command('send_checkin_reminders', force=True, stdout=output, stderr=output, no_color=True)
        messages.success(
            request,
            f"✅ Sunday reminder dispatched. {_summarise_command_output(output.getvalue())}"
        )
    except Exception as e:
        messages.error(request, f"❌ Failed to send Sunday reminders: {str(e)}")
    return redirect('admin_dashboard')

# External Cron / Webhook Endpoint
@csrf_exempt
def cron_run_jobs(request):
    """
    Secured webhook endpoint that can be called by external schedulers (e.g. Railway Cron, cron-job.org).
    Requires a matching token parameter or X-Cron-Key header.
    Usage: GET/POST /api/cron/run-jobs/?token=<CRON_SECRET_KEY>&job=birthdays|welfare|sunday|all
    """
    token = request.GET.get('token') or request.POST.get('token') or request.headers.get('X-Cron-Key')
    expected_token = getattr(settings, 'CRON_SECRET_KEY', 'noahs-ark-cron-secret-2026')

    if not token or token != expected_token:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)

    job = request.GET.get('job') or request.POST.get('job') or 'all'
    job = job.lower()

    from .scheduler import run_birthday_job, run_welfare_job, run_sunday_job

    results = {}
    force = request.GET.get('force', '0') in ('1', 'true') or request.POST.get('force', '0') in ('1', 'true')

    if job in ('birthdays', 'birthday', 'all'):
        results['birthdays'] = run_birthday_job(force=force)
    if job in ('welfare', 'all'):
        results['welfare'] = run_welfare_job(force=force)
    if job in ('sunday', 'all'):
        results['sunday'] = run_sunday_job(force=force)

    return JsonResponse({'status': 'success', 'job': job, 'results': results})


def _summarise_command_output(raw_output):
    """Pull the command's final summary line out for the dashboard message."""
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    return lines[-1] if lines else 'See server logs for details.'


# Update Admin Credentials / Password Action
@superuser_required
@require_POST
def admin_update_profile(request):
    user = request.user
    new_username = request.POST.get('username', '').strip()
    new_email = request.POST.get('email', '').strip()
    current_password = request.POST.get('current_password', '')
    new_password = request.POST.get('new_password', '')
    confirm_password = request.POST.get('confirm_password', '')

    # Verify current password
    if not user.check_password(current_password):
        messages.error(request, "❌ Current password is incorrect. Profile changes were not saved.")
        return redirect('admin_dashboard')

    # Validate and update username
    if new_username and new_username != user.username:
        if User.objects.filter(username=new_username).exclude(id=user.id).exists():
            messages.error(request, f"❌ Username '{new_username}' is already taken.")
            return redirect('admin_dashboard')
        user.username = new_username

    # Update email
    user.email = new_email

    # If changing password
    if new_password:
        if new_password != confirm_password:
            messages.error(request, "❌ New passwords do not match.")
            return redirect('admin_dashboard')
        if len(new_password) < 8:
            messages.error(request, "❌ New password must be at least 8 characters long.")
            return redirect('admin_dashboard')
        user.set_password(new_password)
        user.save()
        # Keep user logged in after password change
        update_session_auth_hash(request, user)
        messages.success(request, "✅ Admin credentials and password updated successfully!")
    else:
        user.save()
        messages.success(request, "✅ Admin profile details updated successfully!")

    return redirect('admin_dashboard')

