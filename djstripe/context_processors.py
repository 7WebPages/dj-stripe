# -*- coding: utf-8 -*-
"""
Beging porting from django-stripe-payments
"""
from . import settings
from .models import Customer
from djstripe.settings import get_user_model

User = get_user_model()


def djstripe_settings(request):
    # TODO - needs tests
    return {
        "STRIPE_PUBLIC_KEY": settings.STRIPE_PUBLIC_KEY,
        "request": request
    }


def check_valid_payment_method(request):
    for user in User.objects.filter(customer__isnull=True):
        Customer.get_or_create(user=user)

    if request.user.is_authenticated():
        customer = Customer.objects.get(user=request.user)
        customer_is_invalid_payment_method = not customer.valid_payment_method
    else:
        customer = False
        customer_is_invalid_payment_method = False
    if customer and customer_is_invalid_payment_method:
        return {'message': True}
    return {'message': False}
