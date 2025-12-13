from django.shortcuts import get_object_or_404, render
from oya.page import PageProcessor
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




