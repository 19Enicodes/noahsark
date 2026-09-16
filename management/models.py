
from django.db import models
import random

class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    head = models.CharField(max_length=100, blank=True, null=True)
    
    @property
    def member_count(self):
        return Worker.objects.filter(department=self, status='ACTIVE').count()

    def __str__(self):
        return self.name

class Worker(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Approval'),
        ('ACTIVE', 'Active'),
        ('INACTIVE', 'Inactive'),
    ]

    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True, null=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True)
    nark_code = models.CharField(max_length=12, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    date_joined = models.DateTimeField(auto_now_add=True)
    birthday = models.DateField(null=True, blank=True)
    
    # Welfare Tracking
    last_check_in = models.DateTimeField(null=True, blank=True)
    missed_sundays_count = models.IntegerField(default=0)

    def save(self, *args, **kwargs):
        if not self.nark_code:
            # Safely generate a unique 4-digit code
            while True:
                code = f"NARK-{random.randint(1000, 9999)}"
                # We check the database to avoid collisions
                if not Worker.objects.filter(nark_code=code).exists():
                    self.nark_code = code
                    break
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.nark_code})"

class CheckIn(models.Model):
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE)
    check_in_time = models.DateTimeField(auto_now_add=True)
    sunday_reference = models.DateField()
    usher_id = models.CharField(max_length=50)

    class Meta:
        verbose_name_plural = "Check-In Records"
        unique_together = ('worker', 'sunday_reference')

    def __str__(self):
        return f"{self.worker.full_name} - {self.sunday_reference}"

class AlertLog(models.Model):
    worker = models.ForeignKey(Worker, on_delete=models.CASCADE)
    type = models.CharField(max_length=50) # 'BIRTHDAY', 'CHECKIN_REMINDER', 'WELFARE_RED', 'CHECKIN_CONFIRM'
    sent_at = models.DateTimeField(auto_now_add=True)
    channel = models.CharField(max_length=20, default='SMS') # 'SMS' or 'EMAIL'
    status = models.CharField(max_length=20, default='PENDING') # 'PENDING', 'SENT' or 'FAILED'
    # Provider message id on success, or the failure reason - so "why didn't
    # this send" is answerable from the admin rather than lost to stdout.
    detail = models.TextField(blank=True, default='')

    def __str__(self):
        return f"{self.type} - {self.worker.full_name} ({self.sent_at.date()})"

class SystemSetting(models.Model):
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField()

    def __str__(self):
        return f"{self.key}: {self.value}"