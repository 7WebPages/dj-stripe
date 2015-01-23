from django import forms
import django_filters
from bootstrap_toolkit.widgets import BootstrapDateInput
from .models import Invoice


class DateRangeWidget(forms.MultiWidget):
    def __init__(self, widgets=None, *args, **kwargs):
        widgets = (
            BootstrapDateInput(attrs={
                'class': 'form-control glyphicon glyphicon-list',
                'placeholder': 'Starting after'
            }),
            BootstrapDateInput(attrs={
                'class': 'form-control glyphicon glyphicon-list',
                'placeholder': 'Ending before'
            })
        )
        super(DateRangeWidget, self).__init__(widgets, *args, **kwargs)

    def render(self, name, value, attrs=None):
        return super(DateRangeWidget, self).render(name, value, attrs)


class DateRangeField(forms.MultiValueField):
    widget = DateRangeWidget

    def __init__(self, *args, **kwargs):
        fields = (
            forms.DateTimeField(),
            forms.DateTimeField(),
        )
        super(DateRangeField, self).__init__(fields, *args, **kwargs)

    def compress(self, data_list):
        if data_list:
            return slice(*data_list)
        return None


class DateRangeFilter(django_filters.RangeFilter):
    field_class = DateRangeField


class HistoryFilter(django_filters.FilterSet):

    invoice__period_start = DateRangeFilter()
    invoice__customer__card_last_4 = django_filters.Filter()

    def __init__(self, *args, **kwargs):
        super(HistoryFilter, self).__init__(*args, **kwargs)
        self.filters['invoice__period_start'].label = ''

    class Meta:
        model = Invoice
        fields = ['invoice__period_start', 'invoice__customer__card_last_4']
