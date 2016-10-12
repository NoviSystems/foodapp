
from django.conf.urls import url
from foodapp import views


app_name = 'foodapp'

urlpatterns = [
    url(r'^$', views.HomeView.as_view(), name='home'),

    url(r'^billing/cards/$', views.StripeCardListView.as_view(), name='stripe_card_list'),
    url(r'^billing/cards/create$', views.StripeCreateView.as_view(), name='stripe_card_create'),
    url(r'^billing/cards/create$', views.StripeCreateView.as_view(), name='stripe_customer_create'),
    url(r'^billing/cards/delete/(\w+)/$', views.StripeCardDeleteView.as_view(), name='stripe_card_delete'),
    url(r'^billing/cards/update/(\w+)/$', views.StripeCardUpdateView.as_view(), name='stripe_card_update'),

    url(r'^billing/invoices/$', views.StripeInvoiceView.as_view(), name='stripe_invoices'),
    url(r'^billing/invoices/super/', views.SuperStripeInvoiceView.as_view(), name='super_invoices'),

    url(r'^orders/$', views.OrderListView.as_view(), name='orders'),
    url(r'^orders/user/(?P<username>\w+)/$', views.UserOrderView.as_view(), name='user_orders'),
    url(r'^orders/last_month/$', views.last_month_view, name='last_month_view'),
    url(r'^orders/leaderboard/$', views.LeaderboardView.as_view(), name='leaderboard'),
    url(r'^orders/(?P<year>\d{4})/(?P<month>\d{2})/$', views.MonthOrdersView.as_view(), name='month_orders'),
    url(r'^orders/super/(?P<year>\d{4})/(?P<month>\d{2})/$', views.SuperMonthOrdersView.as_view(), name='super_month_orders'),
]
