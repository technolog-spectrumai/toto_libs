from django.shortcuts import render
from django.views.generic import ListView
from .models import TLSCertificate, ExternalCertificate


def example_view(request):
    return HttpResponse("Hello from Gervazy!")


class TLSCertificateListView(ListView):
    model = TLSCertificate
    template_name = 'gervazy/tls_certificate_list.html'
    context_object_name = 'certificates'
    paginate_by = 10


class ExternalCertificateListView(ListView):
    model = ExternalCertificate
    template_name = 'gervazy/external_certificate_list.html'
    context_object_name = 'certificates'
    paginate_by = 10


def certificate_dashboard(request):
    """A dashboard view that shows both types of certificates"""
    tls_certificates = TLSCertificate.objects.all().order_by('-issued_at')[:5]
    external_certificates = ExternalCertificate.objects.all().order_by('-issued_at')[:5]
    
    return render(request, 'gervazy/certificate_dashboard.html', {
        'tls_certificates': tls_certificates,
        'external_certificates': external_certificates,
    })
