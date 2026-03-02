from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('progress/<str:task_id>/', views.check_progress, name='check_progress'),
]
