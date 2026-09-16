from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import user_passes_test
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from datetime import date, datetime
import csv
from io import StringIO
from django.core.management import call_command

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
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
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
            
    # Calculate attendance stats for the nearest Sunday
    ref_date = get_current_sunday_reference()
    total_active = Worker.objects.filter(status='ACTIVE').count()
    present_today = CheckIn.objects.filter(sunday_reference=ref_date).count()
    absent_today = total_active - present_today
    
    # Approvals list
    pending_workers = Worker.objects.filter(status='PENDING').order_by('-date_joined')
    
    # Welfare Lists (Yellow and Red Flags)
    active_workers = Worker.objects.filter(status='ACTIVE')
    yellow_flag_workers = []
    red_flag_workers = []
    
    for worker in active_workers:
        # Check if already checked in today
        checked_in_today = CheckIn.objects.filter(worker=worker, sunday_reference=ref_date).exists()
        if checked_in_today:
            continue
            
        misses = calculate_consecutive_misses(worker)
        if misses == 2:
            yellow_flag_workers.append({'worker': worker, 'misses': misses})
        elif misses >= 3:
            red_flag_workers.append({'worker': worker, 'misses': misses})
            
    # Attendance by department stats
    departments = Department.objects.all()
    dept_stats = []
    for dept in departments:
        dept_active = Worker.objects.filter(department=dept, status='ACTIVE').count()
        dept_present = CheckIn.objects.filter(worker__department=dept, sunday_reference=ref_date).count()
        percentage = int((dept_present / dept_active * 100)) if dept_active > 0 else 0
        dept_stats.append({
            'department': dept,
            'active': dept_active,
            'present': dept_present,
            'percentage': percentage
        })

    context = {
        'sunday_pin': pin_setting.value,
        'ref_date': ref_date,
        'total_active': total_active,
        'present_today': present_today,
        'absent_today': absent_today,
        'pending_workers': pending_workers,
        'yellow_flag_workers': yellow_flag_workers,
        'red_flag_workers': red_flag_workers,
        'dept_stats': dept_stats,
    }
    
    return render(request, 'management/admin_dashboard.html', context)

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

# Trigger Automated Reminders Action
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


def _summarise_command_output(raw_output):
    """Pull the command's final summary line out for the dashboard message."""
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    return lines[-1] if lines else 'See server logs for details.'
