import datetime
import stripe
from decimal import Decimal, ROUND_UP

from django.conf import settings

from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.views.generic import CreateView, ListView, TemplateView
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse_lazy
from django.shortcuts import redirect
from django.db.models import Sum
from django.core.exceptions import ObjectDoesNotExist

from foodapp import forms, models
from models import StripeCustomer

stripe.api_key = settings.STRIPE_API_SECRET_KEY


class HomeView(LoginRequiredMixin, CreateView):
    model = models.Order
    form_class = forms.OrderForm
    success_url = reverse_lazy('foodapp:home')
    template_name = 'foodapp/home.html'

    def post(self, request, *args, **kwargs):
        if 'riceOn' in request.POST:
            models.RiceCooker.objects.all().update(is_on=True)
            return redirect(self.success_url)

        if 'riceOff' in request.POST:
            models.RiceCooker.objects.all().update(is_on=False)
            return redirect(self.success_url)

        if models.RiceCooker.objects.filter(is_on=True).exists():
            return self.get(request, error='Cannot place order, rice is currently cooking.', *args, **kwargs)

        return super(HomeView, self).post(request, *args, **kwargs)

    def get_form_kwargs(self):
        form_kwargs = super(HomeView, self).get_form_kwargs()
        form_kwargs['user'] = self.request.user

        return form_kwargs

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.user = self.request.user

        customer_id = obj.user.stripecustomer.customer_id

        obj.invoiceitem = stripe.InvoiceItem.create(
            customer=customer_id,
            # amount must be in cents
            amount=(int(100 * obj.item.cost * obj.quantity)),
            currency="usd",
            description=obj.item.name,
            metadata={"quantity": obj.quantity, "date": obj.date},
        )
        obj.save()

        return HttpResponseRedirect(self.success_url)

    def get_context_data(self, **kwargs):
        context = super(HomeView, self).get_context_data(**kwargs)

        now = datetime.datetime.now()
        orders = models.Order.objects.filter(date=datetime.date.today())

        context['last_month'] = "%02d" % (now.month - 1)
        context['year'] = now.year
        context['orders'] = orders
        context['rice_quantity'] = (orders.filter(item__description__icontains='rice').aggregate(Sum('quantity'))['quantity__sum'] or 0) * .5
        context['rice_is_on'] = models.RiceCooker.objects.filter(is_on=True).exists()
        return context

# Refactor into StripeCustomerCreateView and StripeCardCreateView


class StripeCreateView(LoginRequiredMixin, TemplateView):
    success_url = reverse_lazy('foodapp:stripe_card_list')
    template_name = 'foodapp/stripe_create_form.html'

    def get_context_data(self, **kwargs):
        context = super(StripeCreateView, self).get_context_data()
        context['customer_exists'] = True if get_stripe_customer(self.request.user) else False
        context['stripe_api_publishable_key'] = settings.STRIPE_API_PUBLISHABLE_KEY
        return context

    def post(self, request, *args, **kwargs):
        token = request.POST.get('stripeToken', False)
        # New Card
        stripe_customer = get_stripe_customer(self.request.user)
        if stripe_customer:
            stripe_customer.sources.create(source=token)
            return redirect(self.success_url, request)
        # New Customer
        else:
            customer = stripe.Customer.create(source=token)
            StripeCustomer.objects.update_or_create(user=self.request.user,
                                                    defaults={'customer_id': customer.id})
            return redirect(self.success_url, request)


class StripeCardDeleteView(LoginRequiredMixin, TemplateView):
    model = models.StripeCustomer
    success_url = reverse_lazy('foodapp:stripe_card_list')

    #args[0] stripe card id to delete
    def post(self, request, *args, **kwargs):
        customer = get_stripe_customer(self.request.user)
        customer.sources.retrieve(args[0]).delete()
        return redirect(self.success_url, request)


class StripeCardUpdateView(LoginRequiredMixin, TemplateView):
    success_url = reverse_lazy('foodapp:stripe_card_list')

    #args[0] stripe card id to update
    def post(self, request, *args, **kwargs):
        customer = get_stripe_customer(self.request.user)
        new_default_card = customer.sources.retrieve(args[0])
        customer.default_source = new_default_card.id
        customer.save()
        return redirect(self.success_url, request)


class StripeCardListView(LoginRequiredMixin, TemplateView):
    template_name = 'foodapp/cards.html'

    def get_context_data(self, **kwargs):
        context = super(StripeCardListView, self).get_context_data(**kwargs)
        stripe_customer = get_stripe_customer(self.request.user)
        if stripe_customer:
            cards = stripe_customer.sources.all(object='card')
            defaultCard = stripe_customer.default_source
            cardVals = []
            for data in cards.get('data'):
                if data['id'] == defaultCard:
                    cardVals += [(data['id'], data['last4'], True)]
                else:
                    cardVals += [(data['id'], data['last4'], False)]
            context['cards'] = cardVals
        context['customer_exists'] = True if stripe_customer else False
        return context


class StripeInvoiceView(LoginRequiredMixin, TemplateView):
    success_url = reverse_lazy('foodapp:stripe_invoices')
    template_name = 'foodapp/stripe_invoices.html'

    def get_context_data(self, **kwargs):
        context = super(StripeInvoiceView, self).get_context_data(**kwargs)
        stripe_customer = get_stripe_customer(self.request.user)

        context['customer_exists'] = True if stripe_customer else False

        if stripe_customer:
            ### Create ###
            context.update(_get_uninvoiced_items_dict(stripe_customer))

            ### List ###
            context['invoices'] = _get_invoices_list(stripe_customer)
            context['payment_error'] = _has_payment_error(stripe_customer)

        return context

    def post(self, request, *args, **kwargs):
        # customer = get_stripe_customer(self.request.user)
        stripe.Invoice.create(customer=self.request.user.stripecustomer.customer_id)
        return redirect(self.success_url, request)


def _get_uninvoiced_items_dict(stripe_customer):
    all_invoice_items = stripe.InvoiceItem.all(customer=stripe_customer)
    invoice_items = []
    total_count = 0
    total_cost = 0

    for data in all_invoice_items.get('data'):
        if data['invoice'] is None:
            invoice_items += [
                ('$%.2f' % (data['amount'] / 100.00),
                    data['description'],
                    int(data['metadata']['quantity']),
                    data['metadata']['date']
                 )]
            total_count += int(data['metadata']['quantity'])
            total_cost += int(data['amount'])

    return {
        'invoice_items': invoice_items,
        'total_cost': '$%.2f' % (total_cost / 100.00),
        'total_count': total_count,
    }


def _get_invoices_list(stripe_customer):
    invoices = []
    all_invoices = stripe.Invoice.all(customer=stripe_customer)
    for data in all_invoices.get('data'):
        items = []
        for item in data['lines']:
            items += [
                (item['description'],
                    item['metadata']['date'],
                    int(item['metadata']['quantity']),
                    '$%.2f' % (item['amount'] / 100.00),
                 )]

        invoice_total = '$%.2f' % (sum([item['amount'] for item in data['lines']]) / 100.00)

        invoices.append([
            data['id'],
            datetime.datetime.fromtimestamp(int(data['date'])),
            data['paid'],
            items,
            invoice_total
        ])
    return invoices


def get_stripe_customer(user):
    try:
        customer_id = user.stripecustomer.customer_id
        customer = stripe.Customer.retrieve(customer_id)
        return customer
    except stripe.InvalidRequestError:
        return None
    except ObjectDoesNotExist:
        return None


def _has_payment_error(stripe_customer):
    """ Determines if the stripe customer has an unresolved stripe invoice payment error
    """
    payment_error = False
    invoices = stripe.Invoice.list(customer=stripe_customer).get('data')

    if len(invoices) > 0:
        last_invoice = invoices.pop(0)
        payment_error = not last_invoice.paid and \
            last_invoice.attempted and not last_invoice.forgiven

    return payment_error


class OrderListView(LoginRequiredMixin, ListView):
    model = models.Order
    context_object_name = 'orders'
    template_name = 'foodapp/orders.html'


class SuperStripeInvoiceView(LoginRequiredMixin, TemplateView):
    template_name = 'foodapp/super_invoice_view.html'

    def get_context_data(self, **kwargs):
        context = super(SuperStripeInvoiceView, self).get_context_data(**kwargs)
        users = models.User.objects.filter(is_active=True)
        customer_dict = {user: get_stripe_customer(user) for user in users}
        invoices_dict = {}

        for user, stripe_customer in customer_dict.items():
            if stripe_customer is None:
                invoices_dict[user] = None
            else:
                invoices_dict[user] = (
                    stripe_customer is not None,  # user_exists
                    _get_uninvoiced_items_dict(stripe_customer)['total_cost'],  # amount_owed
                    bool(list(stripe.Invoice.all(customer=stripe_customer, paid=False))),  # unpaid_invoices
                    _has_payment_error(stripe_customer),
                )

        context['invoices_dict'] = invoices_dict
        return context


class UserOrderView(OrderListView):

    def get_queryset(self):
        username = self.kwargs.get('username', None)

        if username is not None:
            return models.Order.objects.filter(user__username=username)

    def get_context_data(self, **kwargs):
        context = super(UserOrderView, self).get_context_data(**kwargs)
        context['title'] = 'Order History for ' + self.request.user.username + ":"
        return context


def last_month_view(request):
    now = datetime.date.today()
    last_month = (now.month - 1)
    year = now.year
    if now.month == 1:
        year = (now.year - 1)
        last_month = 12

    year = str(year)
    last_month = "%02d" % last_month
    # if request.user.is_superuser:
    #     return redirect('foodapp:super_month_orders', year, last_month)
    # else:
    return redirect('foodapp:month_orders', year, last_month)


class LeaderboardView(LoginRequiredMixin, TemplateView):
    template_name = 'foodapp/leader.html'

    def helper(self, leaderboard, items, leaderboardMax):
        helper = 1
        mvp = ""
        item_values = items.values_list('username', 'b_count')

        if len(item_values) > 0:
            mvp = item_values[0][0]

        for i in range(0, len(item_values)):
            item = item_values[i]

            if item[0] not in mvp and item[1] == item_values[0][1]:
                mvp += ", " + item[0]

            if i > 0 and item_values[i][1] == item_values[i - 1][1]:
                helper = helper - 1

            if len(leaderboard) < leaderboardMax:
                leaderboard.append((i + helper, item[0], item[1]))
            elif item[0] == str(self.request.user):
                leaderboard.pop()
                leaderboard.append((i + helper, item[0], item[1]))

        return mvp

    def annotate_user_orders(self, orders):
        return orders.annotate(b_count=Sum('orders__quantity')).order_by('-b_count')

    def get_burrito_eater_diet(self, year=None, month=None):
        if month is None and year is not None:
            return User.objects.filter(orders__item__name__iexact="Burrito", orders__date__year=year)
        elif month is not None and year is not None:
            return User.objects.filter(orders__item__name__iexact="Burrito", orders__date__year=year, orders__date__month=month)
        return User.objects.filter(orders__item__name__iexact="Burrito")

    def get_context_data(self, **kwargs):
        context = super(LeaderboardView, self).get_context_data(**kwargs)
        now = datetime.date.today()
        user = self.request.user

        last_month_date = now - datetime.timedelta(days=now.day + 1)
        last_year_date = datetime.date(int(now.year - 1), 1, 1)

        all_time_burrito_eaters_score = self.annotate_user_orders(self.get_burrito_eater_diet())
        last_year_burrito_eaters_score = self.annotate_user_orders(self.get_burrito_eater_diet(year=last_year_date.year))
        last_month_burrito_eaters_score = self.annotate_user_orders(self.get_burrito_eater_diet(year=last_month_date.year, month=last_month_date.month))
        current_month_burrito_eaters_score = self.annotate_user_orders(self.get_burrito_eater_diet(year=now.year, month=now.month))

        sorted_alltime = []
        sorted_year = []
        sorted_month = []
        sorted_current = []

        month_mvp = self.helper(sorted_month, last_month_burrito_eaters_score, 5)
        year_mvp = self.helper(sorted_year, last_year_burrito_eaters_score, 5)
        alltime_mvp = self.helper(sorted_alltime, all_time_burrito_eaters_score, 5)
        current_mvp = self.helper(sorted_current, current_month_burrito_eaters_score, 5)

        context['last_month_month'] = last_month_date.strftime('%B')
        context['current_month_month'] = now.strftime('%B')
        context['last_year'] = last_year_date.year
        context['alltime_dict'] = sorted_alltime
        context['year_dict'] = sorted_year
        context['month_dict'] = sorted_month
        context['current_dict'] = sorted_current
        context['current_user'] = str(user)
        context['month_mvp'] = month_mvp
        context['year_mvp'] = year_mvp
        context['alltime_mvp'] = alltime_mvp
        context['current_mvp'] = current_mvp

        return context


class MonthOrdersView(LoginRequiredMixin, TemplateView):
    template_name = 'foodapp/month.html'

    def get_context_data(self, **kwargs):
        context = super(MonthOrdersView, self).get_context_data(**kwargs)

        # Grabs year and month keywords from url
        year = self.kwargs['year']
        month = self.kwargs['month']
        now = datetime.date(int(year), int(month), 1)
        context['year'] = year
        context['month'] = now.strftime('%B')
        # Calculates the total number of burritos in a given month.
        # ASSUMES ALL MONTHLY COSTS ARE APPLIED TO BURRITOS ONLY!!!
        month_orders = models.Order.objects.filter(date__month=month, date__year=year)
        num_burritos = 0
        num_rice = 0

        for order in month_orders:
            if order.item.name.lower() == "burrito":
                num_burritos += order.quantity
            elif "rice" in order.item.description.lower():
                num_rice += order.quantity * .5

        context["num_burritos"] = num_burritos
        context["num_rice"] = num_rice

        # Creates a dictionary of user to number of burritos consumed
        user_to_orders_dict = {}
        rice_products = month_orders.filter(item__description__icontains="rice")

        for order in rice_products.filter(item__name__iexact="Burrito"):
            user_to_orders_dict[order.user.username] = [user_to_orders_dict.get(order.user.username, [0, 0])[0] + order.quantity, 0]

        for order in rice_products.exclude(item__name__iexact="Burrito"):
            if (order.user.username not in user_to_orders_dict):
                user_to_orders_dict[order.user.username] = [0, 0]
            user_to_orders_dict[order.user.username][1] = user_to_orders_dict.get(order.user.username, [0, 0])[1] + order.quantity * .5

        for username in user_to_orders_dict:
            num_burritos = user_to_orders_dict[username][0]
            num_just_rice = user_to_orders_dict[username][1]
            user_to_orders_dict[username] = (num_burritos, num_just_rice)

        context["user_to_orders_dict"] = user_to_orders_dict

        return context


class SuperMonthOrdersView(LoginRequiredMixin, TemplateView):
    template_name = 'foodapp/super_month.html'
    form_class = forms.PaidForm
    object = models.AmountPaid

    def post(self, request, *args, **kwargs):
        success_url = reverse_lazy('foodapp:last_month_view')
        if request.user.is_superuser:
            year = self.kwargs['year']
            month = self.kwargs['month']
            filtered_orders = models.Order.objects.filter(item__name__iexact="Burrito").filter(date__month=month).filter(date__year=year)

            usernames = {}
            for order in filtered_orders:
                user_used = False
                for username in usernames:
                    if order.user.username == usernames[username]:
                        user_used = True
                if not user_used:
                    usernames[order] = order.user.username
                    if request.method == 'POST':
                        form_id = 'id_' + order.user.username
                        form = request.POST.get(form_id)
                        if form:
                            new_save = models.AmountPaid(amount=form, user=order.user, date=order.date)
                            new_save.save()
        return HttpResponseRedirect(success_url)

    def get_context_data(self, **kwargs):
        context = super(SuperMonthOrdersView, self).get_context_data(**kwargs)
        # Grabs year and month keywords from url
        year = self.kwargs['year']
        month = self.kwargs['month']
        now = datetime.date(int(year), int(month), 1)
        context['year'] = year
        context['month'] = now.strftime('%B')

        # Calculates the total number of burritos in a given month.
        # ASSUMES ALL MONTHLY COSTS ARE APPLIED TO BURRITOS ONLY!!!
        month_orders = models.Order.objects.filter(date__month=month, date__year=year)
        num_burritos = 0
        for order in month_orders:
            if order.item.name.lower() == "burrito":
                num_burritos += order.quantity
        context["num_burritos"] = num_burritos

        # Sum the total MonthlyCosts objects for a given month/year. Set to zero if no objects returned.
        cost = 0
        costs = models.MonthlyCost.objects.filter(date__month=month, date__year=year)
        for item in costs:
            cost += item.cost

        cost = float(cost) / 0.9725
        context["cost"] = ("%.2f" % cost)

        # Calculates cost per burrito
        if num_burritos:
            cost_per_burrito = Decimal(cost / num_burritos).quantize(Decimal('.01'), rounding=ROUND_UP)
        else:
            cost_per_burrito = 0

        context["cost_per_burrito"] = cost_per_burrito

        # Creates a dictionary of user to number of burritos consumed
        user_to_orders_dict = {}
        for order in models.Order.objects.filter(item__name__iexact="Burrito").filter(date__month=month).filter(date__year=year):
            user_to_orders_dict[order.user.username] = user_to_orders_dict.get(order.user.username, 0) + order.quantity
        for username in user_to_orders_dict:
            money_paid = Decimal(0)
            num_burritos = user_to_orders_dict[username]
            money_owed = num_burritos * cost_per_burrito
            for paid in models.AmountPaid.objects.filter(date__month=month).filter(date__year=year):
                if paid.user.username == username:
                    money_paid += paid.amount
                    money_owed = (num_burritos * cost_per_burrito) - money_paid
            user_to_orders_dict[username] = (num_burritos, num_burritos * cost_per_burrito, money_owed, money_paid)

        context["user_to_orders_dict"] = user_to_orders_dict
        return context
