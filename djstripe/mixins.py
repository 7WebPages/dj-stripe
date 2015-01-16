# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from django.contrib import messages
from django.shortcuts import redirect

from .models import Customer, CurrentSubscription
from . import settings as app_settings
from .utils import user_has_active_subscription
from django.core.urlresolvers import reverse
from django.core.exceptions import ImproperlyConfigured
from django.views.generic import ListView

ERROR_MSG = (
                "SubscriptionPaymentRequiredMixin requires the user be"
                "authenticated before use. Please use django-braces'"
                "LoginRequiredMixin."
            )


class ValidPaymentRedirectMixin(object):
    """
    Redirect user to card_change page if valid_payment_method is False
    """
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated():
            customer = Customer.objects.get_or_create(user=request.user.pk)
            customer_is_invalid_payment_method = not customer[0].valid_payment_method
        else:
            customer = False
            customer_is_invalid_payment_method = False
        if customer and customer_is_invalid_payment_method:
            return redirect(reverse("djstripe:change_card"))
        return super(ValidPaymentRedirectMixin, self).dispatch(request, *args, **kwargs)


class SubscriptionPaymentRequiredMixin(object):
    """ Used to check to see if someone paid """
    # TODO - needs tests
    def dispatch(self, request, *args, **kwargs):
        if not user_has_active_subscription(request.user):
            msg = "Your account is inactive. Please renew your subscription"
            messages.info(request, msg, fail_silently=True)
            return redirect("djstripe:subscribe")

        return super(SubscriptionPaymentRequiredMixin, self).dispatch(
            request, *args, **kwargs)


class PaymentsContextMixin(object):
    """ Used to check to see if someone paid """
    def get_context_data(self, **kwargs):
        context = super(PaymentsContextMixin, self).get_context_data(**kwargs)
        context.update({
            "STRIPE_PUBLIC_KEY": app_settings.STRIPE_PUBLIC_KEY,
            "PLAN_CHOICES": app_settings.PLAN_CHOICES,
            "PAYMENT_PLANS": app_settings.PAYMENTS_PLANS
        })
        return context


class SubscriptionMixin(PaymentsContextMixin):
    def get_context_data(self, *args, **kwargs):
        context = super(SubscriptionMixin, self).get_context_data(**kwargs)
        context['is_plans_plural'] = bool(len(app_settings.PLAN_CHOICES) > 1)
        context['customer'], created = Customer.get_or_create(self.request.user)
        context['CurrentSubscription'] = CurrentSubscription
        return context


class ListFilteredMixin(object):
    """
    Mixin that adds support for django-filter
    """

    filter_set = None

    def get_filter_set(self):
        if self.filter_set:
            return self.filter_set
        else:
            raise ImproperlyConfigured(
                "ListFilterMixin requires either a definition of "
                "'filter_set' or an implementation of 'get_filter()'")

    def get_filter_set_kwargs(self):
        """
        Returns the keyword arguments for instanciating the filterset.
        """
        return {
            'data': self.request.GET,
            'queryset': self.get_base_queryset(),
        }

    def get_base_queryset(self):
        """
        We can decided to either alter the queryset before or after applying the
        FilterSet
        """
        return super(ListFilteredMixin, self).get_queryset()

    def get_constructed_filter(self):
        # We need to store the instantiated FilterSet cause we use it in
        # get_queryset and in get_context_data
        if getattr(self, 'constructed_filter', None):
            return self.constructed_filter
        else:
            f = self.get_filter_set()(**self.get_filter_set_kwargs())
            self.constructed_filter = f
            return f

    def get_queryset(self):
        return self.get_constructed_filter().qs

    def get_context_data(self, **kwargs):
        kwargs.update({'filter': self.get_constructed_filter()})
        return super(ListFilteredMixin, self).get_context_data(**kwargs)


class ListFilteredView(ListFilteredMixin, ListView):
    """
    A list view that can be filtered by django-filter
    """