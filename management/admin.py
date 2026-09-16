from django.contrib import admin
from .models import Department, Worker, CheckIn, AlertLog, SystemSetting

@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'head', 'member_count')

@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'nark_code', 'department', 'status', 'missed_sundays_count')
    list_filter = ('status', 'department')
    search_fields = ('full_name', 'nark_code', 'phone_number')

@admin.register(CheckIn)
class CheckInAdmin(admin.ModelAdmin):
    list_display = ('worker', 'sunday_reference', 'check_in_time', 'usher_id')
    list_filter = ('sunday_reference',)
    search_fields = ('worker__full_name', 'worker__nark_code')

@admin.register(AlertLog)
class AlertLogAdmin(admin.ModelAdmin):
    list_display = ('worker', 'type', 'channel', 'status', 'sent_at', 'short_detail')
    list_filter = ('status', 'channel', 'type')
    search_fields = ('worker__full_name', 'worker__nark_code', 'detail')
    readonly_fields = ('worker', 'type', 'channel', 'status', 'sent_at', 'detail')
    date_hierarchy = 'sent_at'

    @admin.display(description='Detail')
    def short_detail(self, obj):
        if not obj.detail:
            return '-'
        return obj.detail if len(obj.detail) <= 80 else obj.detail[:77] + '...'

@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'value')
