from django.core.management.base import BaseCommand
from management.models import Department

DEPARTMENTS = [
    'Choir Department',
    'Ushering Department',
    'Prayer Department',
    'Light House Department',
    'Sanitation Department',
    'Junior Church',
    'Greeters Department',
    'Media and Sound Department',
    'Member',
    'Minister',
]

class Command(BaseCommand):
    help = 'Seeds or updates the 10 official church departments in the database.'

    def handle(self, *args, **options):
        self.stdout.write("Seeding church departments...")
        created_count = 0
        for name in DEPARTMENTS:
            dept, created = Department.objects.get_or_create(name=name)
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  + Created: {name}"))
            else:
                self.stdout.write(f"  = Existing: {name}")

        self.stdout.write(self.style.SUCCESS(f"\nDone. {created_count} new departments created. Total in DB: {Department.objects.count()}."))
