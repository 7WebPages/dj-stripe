# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import decimal
import json
import logging

from django.contrib import messages
from django.core.urlresolvers import reverse_lazy, reverse
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.generic import DetailView
from django.views.generic import FormView
from django.views.generic import TemplateView
from django.views.generic import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from braces.views import CsrfExemptMixin
from braces.views import FormValidMessageMixin
from braces.views import LoginRequiredMixin
from braces.views import SelectRelatedMixin
import stripe

from .forms import PlanForm, CancelSubscriptionForm
from .mixins import PaymentsContextMixin, SubscriptionMixin, ListFilteredView
from .models import CurrentSubscription
from .models import Customer
from .models import Event
from .models import Plan
from .models import EventProcessingException
from .models import Charge
from .models import Invoice
from .settings import CANCELLATION_AT_PERIOD_END
from .settings import PRORATION_POLICY_FOR_UPGRADES
from .settings import PY3
from .settings import User
from .settings import STRIPE_PUBLIC_KEY
from .filters import HistoryFilter
from .sync import sync_customer
from stripe.error import InvalidRequestError
import django_tables2 as tables
from django_tables2 import RequestConfig
from django_tables2 import BooleanColumn


logger = logging.getLogger('dj-stripe')


@csrf_exempt
@require_http_methods(["POST"])
def card(request):
    customer, creates = Customer.get_or_create(request.user)
    token = request.POST.get('id')
    status = request.POST.get('status')

    if status in 'delete':
        customer.delete_card(token=token)
    if status in 'default':
        customer.set_default_card(token=token)
    return HttpResponse(json.dumps({'message': 'Card list changed'}),
                        content_type="application/json")


class OneTime(TemplateView):
    # TODO - needs tests

    template_name = 'djstripe/one_time.html'

    def get_context_data(self, *args, **kwargs):
        context = super(OneTime, self).get_context_data(*args, **kwargs)
        customer, created = Customer.get_or_create(User.objects.get(pk=self.request.user.pk))
        context['STRIPE_PUBLIC_KEY'] = STRIPE_PUBLIC_KEY

        return context

    def post(self, request, *args, **kwargs):
        card = request.POST.get('stripe_token')
        amount = request.POST.get('amount')
        customer, created = Customer.get_or_create(
            User.objects.get(pk=self.request.user.pk)
        )
        res = customer.one_time_pay(
            card=card,
            amount=amount,
            customer=customer
        )
        if not res.failure_message:
            messages.add_message(request, messages.SUCCESS, 'Payment successfully completed')
            return redirect(reverse('djstripe:history'))
        else:
            messages.add_message(request, messages.ERROR, res.failure_message)
            return redirect(reverse('djstripe:one_time_pay'))


class OneTimePayment(LoginRequiredMixin,
                     PaymentsContextMixin,
                     TemplateView):
    template_name = 'djstripe/one_time.html'

    def get_object(self):
        if hasattr(self, "customer"):
            return self.customer
        self.customer, created = Customer.get_or_create(self.request.user)
        return self.customer

    def get_context_data(self, **kwargs):
        context = super(OneTimePayment, self).get_context_data(**kwargs)
        context['customer'] = self.get_object()
        return context

    def post(self, request):
        customer = self.get_object()
        if customer.valid_payment_method == False:
            tmp = {'msg': 'You must enter your credit card information in order to subscribe to a plan',
                   'error': True,
                   'customer': self.get_object()}
            return render(request, self.template_name, tmp)
        try:
            customer.charge(decimal.Decimal(request.POST.get('amount')))
            customer.set_account_balance(request.POST.get('amount'))
            tmp = {'msg': 'Your payment has been successfully',
                   'error': False,
                   'customer': self.get_object()}
        except InvalidRequestError as e:
            tmp = {'msg': e,
                   'error': True,
                   'customer': self.get_object()}
        return render(request, self.template_name, tmp)


class DeleteCardView(LoginRequiredMixin, PaymentsContextMixin, View):

    def get_object(self):
        if hasattr(self, "customer"):
            return self.customer
        self.customer, created = Customer.get_or_create(self.request.user)
        return self.customer

    def post(self, request):
        customer = self.get_object()
        customer.cards.retrieve("card_id").delete()


class ChangeCardView(LoginRequiredMixin, PaymentsContextMixin, DetailView):
    # TODO - needs tests
    # Needs a form
    # Not done yet
    template_name = "djstripe/change_card.html"

    def get_object(self):
        if hasattr(self, "customer"):
            return self.customer
        self.customer, created = Customer.get_or_create(self.request.user)
        return self.customer

    def post(self, request, *args, **kwargs):
        customer = self.get_object()
        token = request.POST.get("stripe_token")
        city = request.POST.get("address-city")
        country = request.POST.get("address-country")
        line1 = request.POST.get("address-line1")
        line2 = request.POST.get("address-line2")
        state = request.POST.get("address-state")
        address_zip = request.POST.get("address-zip")
        name = request.POST.get("card-name")

        is_card_added = False
        is_subscribed = False

        try:
            send_invoice = customer.card_fingerprint == ""
            customer.update_card(token, city, country, line1,
                                 line2, state, address_zip, name)
            is_card_added = True

            if send_invoice:
                customer.send_invoice()
            customer.retry_unpaid_invoices()
        except stripe.StripeError as e:
            logger.exception(e)
            return render(
                request,
                self.template_name,
                {
                    "customer": self.get_object(),
                    "stripe_error": e.message
                }
            )

        plan_id = request.session.get('plan')
        if plan_id:
            try:
                plan_obj = Plan.objects.get(stripe_id=plan_id)
                customer.subscribe(plan_obj.stripe_id)
                is_subscribed = True
            except stripe.StripeError as e:
                msg = "Subscription failed. %s" % e.message
                messages.add_message(request, messages.ERROR, msg)
                logger.exception(e)

            request.session['plan'] = None

        if is_card_added and not is_subscribed:
            messages.info(request, "Your card is succesfully updated.")

        if is_subscribed:
            msg = "You successfully subscribed to %s plan" % plan_obj.name
            messages.info(request, msg)

        return redirect(self.get_post_success_url())

    def get_post_success_url(self):
        """ Makes it easier to do custom dj-stripe integrations. """
        return reverse("djstripe:subscribe")


class CancelSubscriptionView(LoginRequiredMixin,
                             SubscriptionMixin,
                             FormView):
    # TODO - needs tests
    template_name = "djstripe/cancel_subscription.html"
    form_class = CancelSubscriptionForm
    success_url = reverse_lazy("djstripe:history")

    def form_valid(self, form):
        customer, created = Customer.get_or_create(self.request.user)
        current_subscription = customer.cancel_subscription(at_period_end=True)
        plan = Plan.objects.get(stripe_id=current_subscription.plan)
        free_plan = Plan.objects.filter(amount=0).first()

        msg = u"Your subscription %s is cancelled. " % plan.name

        if current_subscription.status == current_subscription.STATUS_CANCELLED:
            # If no pro-rate, they get kicked right out.
            customer.subscribe(free_plan.stripe_id)

        else:
            # If pro-rate, they get some time to stay.
            msg += "The changes will become effective on %s." % current_subscription.current_period_end.date()

        messages.success(self.request, msg)

        return super(CancelSubscriptionView, self).form_valid(form)


class WebHook(CsrfExemptMixin, View):

    def post(self, request, *args, **kwargs):
        if PY3:
            # Handles Python 3 conversion of bytes to str
            body = request.body.decode(encoding="UTF-8")
        else:
            # Handles Python 2
            body = request.body
        data = json.loads(body)
        if Event.objects.filter(stripe_id=data["id"]).exists():
            EventProcessingException.objects.create(
                data=data,
                message="Duplicate event record",
                traceback=""
            )
        else:
            event = Event.objects.create(
                stripe_id=data["id"],
                kind=data["type"],
                livemode=data["livemode"],
                webhook_message=data
            )
            event.validate()
            event.process()
        return HttpResponse()


class HistoryTable(tables.Table):
    paid = BooleanColumn(yesno='Paid,Failed')

    class Meta:
        model = Charge
        fields = ("created",
                  "card_kind",
                  "card_last_4",
                  "amount",
                  "paid",
                  "amount_refunded"
                  )
        order_by = '-created'
        per_page = 10
        attrs = {'id': 'history', 'class': 'paleblue'}


class HistoryView(LoginRequiredMixin,
                  SelectRelatedMixin,
                  ListFilteredView,
                  DetailView):
    # TODO - needs tests
    template_name = "djstripe/history.html"
    model = Charge
    select_related = ["charge"]
    filter_set = HistoryFilter
    object = Invoice

    def get_queryset(self):
        queryset = super(SelectRelatedMixin, self).get_queryset()
        config = RequestConfig(self.request)
        user = User.objects.get(pk=self.request.user.id)

        customer = HistoryTable(
            queryset.select_related(*self.select_related).filter(customer=user.customer)
        )
        config.configure(customer)
        return customer


class SyncHistoryView(CsrfExemptMixin,
                      HistoryView):

    template_name = "djstripe/includes/_history_table.html"
    filter_set = HistoryFilter

    # TODO - needs tests
    def post(self, request, *args, **kwargs):
        return render(
            request,
            self.template_name,
            {
                "customer": sync_customer(request.user),
                "object_list": self.get_queryset(),
                "filter": HistoryFilter(request.GET, self.get_queryset())
            }
        )


class AccountView(LoginRequiredMixin,
                  SelectRelatedMixin,
                  TemplateView):
    # TODO - needs tests
    template_name = "djstripe/account.html"

    def get_context_data(self, *args, **kwargs):
        context = super(AccountView, self).get_context_data(**kwargs)
        customer, created = Customer.get_or_create(self.request.user)
        context['customer'] = customer
        context['cards'] = customer.get_cards.data
        try:
            context['subscription'] = customer.current_subscription
        except CurrentSubscription.DoesNotExist:
            context['subscription'] = None
        context['plans'] = Plan.objects.all()
        return context


################## Subscription views


class SubscribeFormView(
        LoginRequiredMixin,
        FormValidMessageMixin,
        SubscriptionMixin,
        FormView):
    # TODO - needs tests

    form_class = PlanForm
    template_name = "djstripe/subscribe_form.html"

    def get_form_valid_message(self):
        plan_name = self.request.user.customer.current_subscription.plan_display()
        return "You are now subscribed to {0} plan!".format(plan_name)

    @property
    def success_url(self):
        return reverse_lazy('djstripe:history')

    def get_context_data(self, *args, **kwargs):
        context = super(SubscribeFormView, self).get_context_data(*args, **kwargs)
        customer, created = Customer.get_or_create(User.objects.get(pk=self.request.user.pk))
        context['cards'] = customer.get_cards.data
        context['plans'] = Plan.objects.all()

        return context

    def post(self, request, *args, **kwargs):
        """
        Handles POST requests, instantiating a form instance with the passed
        POST variables and then checked for validity.
        """
        form_class = self.get_form_class()
        form = self.get_form(form_class)
        if form.is_valid():
            try:
                customer, created = Customer.get_or_create(
                    User.objects.get(pk=self.request.user.pk)
                )
                # If user has no card
                if not customer.card_last_4:
                    customer.subscribe_without_card(
                        plan=form.cleaned_data['plan'],
                        card=request.POST.get('stripe_token')
                    )
                else:
                    customer.subscribe(form.cleaned_data["plan"])
            except stripe.StripeError as e:
                logger.exception(e)
                # add form error here
                self.error = e.args[0]
                return self.form_invalid(form)
            # redirect to confirmation page
            return self.form_valid(form)
        else:
            return self.form_invalid(form)


class ChangePlanView(LoginRequiredMixin,
                        FormValidMessageMixin,
                        SubscriptionMixin,
                        FormView):

    form_class = PlanForm
    template_name = "djstripe/subscribe_form.html"

    def get_form_valid_message(self):
        plan_name = self.request.user.customer.current_subscription.plan_display()
        return "You successfully subscribed to {0} plan!".format(plan_name)

    @property
    def success_url(self):
        return reverse_lazy('djstripe:history')

    def post(self, request, *args, **kwargs):
        form = PlanForm(request.POST)
        customer = request.user.customer
        plan_obj = Plan.objects.get(stripe_id=customer.current_subscription.plan)

        if form.is_valid():
            selected_plan = Plan.objects.get(stripe_id=form.cleaned_data.get('plan'))
            # Save plan id in to session
            request.session['plan'] = form.cleaned_data.get('plan')

            if not customer.get_cards.count and selected_plan.amount:
                messages.add_message(request, messages.INFO, "Please add card to subscribe")
                return redirect(reverse('djstripe:change_card'))

            try:
                customer.subscribe(form.data.get("plan"))
                request.session['plan'] = None
            except stripe.CardError as e:
                logger.exception(e)
                self.error = e.message
                msg = "%s. %s" % (self.error, "Please use another card and then we will try to subscribe you again")
                messages.add_message(request, messages.ERROR, msg)
                return redirect(reverse('djstripe:change_card'))
            except stripe.StripeError as e:
                logger.exception(e)
                self.error = e.message
                messages.add_message(request, messages.ERROR, self.error)
                return redirect(reverse('djstripe:subscribe'))
            except Exception as e:
                logger.exception(e)
                raise e

            return self.form_valid(form)
        else:
            return self.form_invalid(form)


######### Web services
class CheckAvailableUserAttributeView(View):

    def get(self, request, *args, **kwargs):
        attr_name = self.kwargs['attr_name']
        not_available = User.objects.filter(
                **{attr_name: request.GET.get("v", "")}
        ).exists()
        return HttpResponse(json.dumps(not not_available))
