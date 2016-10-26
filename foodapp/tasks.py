
from celery import shared_task
from django.conf import settings
from django.core.urlresolvers import reverse
from django.template import Template, Context

from foodapp import models

PAYMENT_REQUIRED_BODY = Template("""
This is your friendly reminder to submit payment for your
recent Foodapp invoice for a total of {{ total_cost }}.  Please
navigate to the following link in order to view and pay this
invoice.

{{ url }}
""")


@shared_task
def reset_rice_cooker():
    cookers = models.RiceCooker.objects.all()
    for cooker in cookers:
        cooker.is_on = False
        cooker.save()


@shared_task
def create_invoices():
    for customer in models.StripeCustomer.objects.all():
        customer.user.orders.generate_invoice_items()


@shared_task
def send_invoice_notifications():
    customers = models.StripeCustomer.objects.all()

    # Ensure invoice items exist
    for customer in customers:
        customer.user.orders.generate_invoice_items()

    # Send reminders after generation in case email sending errors
    if not settings.FOODAPP_SEND_INVOICE_REMINDERS:
        return

    for customer in customers:
        uninvoiced_items = customer.get_uninvoiced_items()

        # skip if no need to notify user
        if len(uninvoiced_items) == 0:
            continue

        total_cost = sum(item.amount for item in uninvoiced_items)

        # Send email
        subject = "[Foodapp] Payment required - you have unpaid items on Foodapp"
        message = PAYMENT_REQUIRED_BODY.render(Context({
            'total_cost': '$%.2f' % (total_cost / 100.00),
            'url': settings.SITE_URL + reverse('foodapp:stripe_invoices')
        }))
        customer.user.email_user(subject, message)
