from django.core.management.base import BaseCommand

from apps.intelligence.views import evaluate_workflow_rules


class Command(BaseCommand):
    help = "Evaluate enabled factory workflow rules and send deduplicated notifications."

    def handle(self, *args, **options):
        count = evaluate_workflow_rules()
        self.stdout.write(self.style.SUCCESS(f"Workflow evaluation complete: {count} new alert(s)."))
