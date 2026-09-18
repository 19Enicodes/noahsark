
from django.contrib import admin
from django.urls import path
from management import views

urlpatterns = [
    path('admin-internal/', admin.site.urls), 
    path('', views.home, name='home'),
    path('setup/', views.admin_setup, name='admin_setup'),
    path('login/', views.admin_login, name='admin_login'),
    path('logout/', views.admin_logout, name='admin_logout'),
    
    # Registration Flow
    path('register/', views.worker_registration, name='worker_registration'),
    path('register/success/', views.registration_success, name='registration_success'),
    path('find-code/', views.find_code, name='find_code'),

    # Usher Portal
    path('usher/login/', views.usher_login, name='usher_login'),
    path('usher/checkin/', views.usher_checkin, name='usher_checkin'),

    # Admin Management Center
    path('admin/dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/approve/<int:worker_id>/', views.approve_worker, name='approve_worker'),
    path('admin/export/', views.export_workers, name='export_workers'),
    path('admin/send-birthdays/', views.send_birthday_trigger, name='send_birthday_trigger'),
    path('admin/send-welfare/', views.send_welfare_trigger, name='send_welfare_trigger'),
    path('admin/run-reminders/', views.run_reminders_trigger, name='run_reminders_trigger'),
    path('admin/send-reminders/', views.send_reminders_trigger, name='send_reminders_trigger'),
    path('admin/update-profile/', views.admin_update_profile, name='admin_update_profile'),

    # Automated Cron Webhook Endpoint
    path('api/cron/run-jobs/', views.cron_run_jobs, name='cron_run_jobs'),
]

