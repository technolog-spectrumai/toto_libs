from django.shortcuts import get_object_or_404, render
from .page import PageProcessor
from django.http import JsonResponse
from federal.models import FederatedIdentity, Federation



def federation_detail_json(request, pk):
    federation = get_object_or_404(Federation, pk=pk)
    data = {
        "id": federation.id,
        "name": federation.name,
        "created_at": federation.created_at.isoformat(),
        "active": federation.active,
        "url": federation.url,
    }
    return JsonResponse(data)

def federation_list_json(request):
    federations = Federation.objects.all()
    data = [
        {
            "id": f.id,
            "name": f.name,
            "created_at": f.created_at.isoformat(),
            "active": f.active,
            "url": f.url,
        }
        for f in federations
    ]
    return JsonResponse(data, safe=False)


def current_federation_view(request):
    """
    Render the current active federation that has an active platform.
    """
    federation = get_object_or_404(
        Federation,
        active=True,
        platform__active=True
    )
    context = {"federation": federation}
    context = PageProcessor().decorate(context, request)
    return render(request, "federal/current_federation.html", context)





