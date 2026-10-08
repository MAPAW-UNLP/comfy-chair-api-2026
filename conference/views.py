from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Conference
from .serializers import ConferenceSerializer
from django.utils import timezone
from django.db.models import Q
from .permissions import IsAdmin

class ConferenceViewSet(viewsets.ModelViewSet):
    queryset = Conference.objects.all().order_by('-id')
    serializer_class = ConferenceSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search')
        if search and search.strip():
            search = search.strip()
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    # /api/conference/finished/  -->  devuelve las conferencias con end_date < fecha_actual
    @action(detail=False, methods=['get'])
    def finished(self, request):
        fecha_actual = timezone.now().date()
        conferencias = self.get_queryset().filter(
            end_date__lt=fecha_actual
        ).order_by('-id')  
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)
    
    # /api/conference/active/ --> devuelve las conferencias con end_date >= fecha_actual
    @action(detail=False, methods=['get'])
    def active(self, request):
        fecha_actual = timezone.now().date()
        conferencias = self.get_queryset().filter(
            end_date__gte=fecha_actual
        ).order_by('-id') 
        serializer = self.get_serializer(conferencias, many=True)
        return Response(serializer.data)
