import stripe

from celery import shared_task
from django.conf import settings
from django.core import mail
from django.core.urlresolvers import reverse
from django.template import Template, Context

from foodapp import models

stripe.api_key = settings.STRIPE_API_SECRET_KEY

notification_email_msg = Template("""
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
def create_invoices_and_send_notifications():

    customers = models.StripeCustomer.objects.all()

    # Create invoices
    for customer in customers:
        uninvoiced_orders = models.Order.objects.filter(user=customer.user, is_invoiceable=True, invoice_item=None)

        for order in uninvoiced_orders:
            customer_id = customer.customer_id

            order.invoiceitem = stripe.InvoiceItem.create(
                customer=customer_id,
                # amount must be in cents
                amount=(int(100 * order.item.cost * order.quantity)),
                currency="usd",
                description=order.item.name,
                metadata={"quantity": order.quantity, "date": order.date},
            )
            order.save()

    # Send reminders
    if settings.FOODAPP_SEND_INVOICE_REMINDERS:
        connection = mail.get_connection(fail_silently=False)

        for customer in customers:
            # Calculate total cost
            all_invoice_items = stripe.InvoiceItem.all(customer=customer.customer_id).get('data')
            total_cost = 0

            if len(all_invoice_items) == 0:
                continue

            for data in all_invoice_items:
                if data['invoice'] is None:
                    total_cost += int(data['amount'])

            total_cost = '$%.2f' % (total_cost / 100.00)
            url = settings.SITE_URL + reverse('foodapp:stripe_invoices')

            # Send email
            user = customer.user
            subj = "[Foodapp] Payment required - you have unpaid items on Foodapp"
            from_email = settings.DEFAULT_FROM_EMAIL
            recipients = [user.email]

            msg = notification_email_msg.render(
                Context({"total_cost": total_cost, "url": url})
            )
            email = mail.EmailMessage(subj, msg, from_email, recipients)

            email.connection = connection
            email.send()
