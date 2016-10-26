
from datetime import date, datetime
from multiprocessing import Pool

from django.db import models
from django.conf import settings

import stripe
stripe.api_key = settings.STRIPE_API_SECRET_KEY


# necessary for map, as pickling can't process class/instance methods
def create_invoiceitem(item):
    return InvoiceItem.create(item)


# Stripe model stubs
class Invoice(object):
    def __init__(self, data):
        self._data = data

        self.id = data['id']
        self.date = datetime.fromtimestamp(int(data['date']))
        self.paid = data['paid']
        self.attempted = data['attempted']
        self.forgiven = data['forgiven']

        self.items = [InvoiceItem(item) for item in data['lines']]

        self.total = sum(item.amount for item in self.items)

    @property
    def total_usd(self):
        return '$%.2f' % (self.total / 100.00)

    @classmethod
    def list(cls, **kwargs):
        return [
            cls(data) for data
            in stripe.Invoice.list(**kwargs).get('data')
        ]

    @classmethod
    def retrieve(cls, **kwargs):
        return cls(stripe.Invoice.retrieve(**kwargs))

    @classmethod
    def create(cls, stripe_customer, **kwargs):
        kwargs['customer'] = stripe_customer.customer_id
        return cls(stripe.Invoice.create(**kwargs))


class InvoiceItem(object):
    def __init__(self, data):
        self.id = data['id']
        self.invoice_id = data.get('invoice')
        self.description = data['description']
        self.amount = data['amount']

        self.order_id = data['metadata'].get('order_id')
        self.date = data['metadata'].get('date')
        self.quantity = int(data['metadata'].get('quantity'))

    @property
    def amount_usd(self):
        return '$%.2f' % (self.amount / 100.00)

    @property
    def invoiced(self):
        return self.invoice_id is not None

    @classmethod
    def list(cls, **kwargs):
        return [
            cls(data) for data
            in stripe.InvoiceItem.list(**kwargs).get('data')
        ]

    @classmethod
    def retrieve(cls, **kwargs):
        return cls(stripe.InvoiceItem.retrieve(**kwargs))

    @classmethod
    def create(cls, order):
        data = stripe.InvoiceItem.create(
            customer=order.user.stripecustomer.customer_id,
            # amount must be in cents
            amount=(int(100 * order.item.cost * order.quantity)),
            currency='usd',
            description=order.item.name,
            metadata={
                'order_id': order.pk,
                'quantity': order.quantity,
                'date': order.date
            },
        )

        # update the order w/ the invoice
        order.invoiceitem_id = data.id
        order.save(update_fields=['invoiceitem_id'])

        return cls(data)


class StripeCustomerQuerySet(models.QuerySet):
    def create(self, token, **kwargs):
        # create the
        stripe_customer = stripe.Customer.create(source=token)
        kwargs['customer_id'] = stripe_customer.id

        return super(StripeCustomerQuerySet, self).create(**kwargs)


class StripeCustomerManager(models.Manager.from_queryset(StripeCustomerQuerySet)):
    pass


class StripeCustomer(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL)
    customer_id = models.CharField(max_length=18)

    objects = StripeCustomerManager()

    def is_valid(self):
        try:
            customer = stripe.Customer.retrieve(self.customer_id)
            return not getattr(customer, 'deleted', False)
        except stripe.InvalidRequestError:
            return None

    def get_customer(self):
        try:
            return stripe.Customer.retrieve(self.customer_id)
        except stripe.InvalidRequestError:
            return None

    def get_invoices(self):
        return Invoice.list(customer=self.customer_id)

    def get_uninvoiced_items(self):
        return [item for item in InvoiceItem.list(customer=self.customer_id) if not item.invoiced]

    def add_source(self, source):
        self.get_customer().sources.create(source=source)

    def __repr__(self):
        return '<StripeCustomer %s>' % self.customer_id


class Item(models.Model):
    name = models.CharField(max_length=56)
    cost = models.DecimalField(default=0.0, max_digits=6, decimal_places=2)
    description = models.CharField(max_length=256)
    once_a_day = models.BooleanField(default=False)

    def __unicode__(self):
        return u'%s: %s' % (self.name, self.description,)

    class Meta:
        verbose_name = "Item"
        verbose_name_plural = "Items"


class OrderQuerySet(models.QuerySet):
    def uninvoiced(self):
        # The wording is a little misleading. This returns orders that do not have an
        # associated `InvoiceItem`. This is not concerned with whether or not the item
        # has been invoiced.
        return self.filter(is_invoiceable=True, invoiceitem_id='')

    def generate_invoice_items(self):
        """
        Ensure that the orders have corresponding invoice items.
        """
        Pool().map(
            create_invoiceitem,
            list(self.uninvoiced()),
        )

        return self.all()


class OrderManager(models.Manager.from_queryset(OrderQuerySet)):
    pass


class Order(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='orders')
    date = models.DateField(default=date.today)
    item = models.ForeignKey(Item)
    invoiceitem_id = models.CharField(max_length=36, blank=True)
    is_invoiceable = models.BooleanField(default=True)
    QUANTITY_CHOICES = (
        (1, "one"),
        (2, "two"),
        (3, "three"),
        (4, "four"),
        (5, "five"),
    )
    quantity = models.PositiveSmallIntegerField(choices=QUANTITY_CHOICES, default=1)

    objects = OrderManager()

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["date"]

    def __unicode__(self):
        return u"%d %s(s) on %s." % (self.quantity, self.item.name, self.date)


class RiceCooker(models.Model):
    is_on = models.BooleanField(default=False)

    def __unicode__(self):
        return u"%s." % (self.is_on)


class MonthlyCost(models.Model):
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=date.today)

    def __unicode__(self):
        return u"$%3.2f on %s." % (self.cost, self.date)


class AmountPaid(models.Model):
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField(default=date.today)
    user = models.ForeignKey(settings.AUTH_USER_MODEL)

    def __unicode__(self):
        return u"$%3.2f by %s on %s" % (self.amount, self.user, self.date)
