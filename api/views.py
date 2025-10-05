# api/views.py
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView, LogoutView
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, ListView, DetailView, UpdateView, DeleteView
from .forms import (UserRegistrationForm, ReservationForm, ApprovalForm,
                    SpaceForm, ResourceForm, ProfileForm)
from .models import Reservation, Approval, Space, Resource, Notification, Profile
import csv

# ---------- Autenticación ----------
class UserLoginView(LoginView):
    template_name = "auth/login.html"

def register(request):
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.get_or_create(user=user)
            messages.success(request, "Cuenta creada. ¡Bienvenido!")
            login(request, user)
            return redirect("dashboard_user")
    else:
        form = UserRegistrationForm()
    return render(request, "auth/register.html", {"form": form})

class UserLogoutView(LogoutView):
    pass

# ---------- Dashboards ----------
@login_required
def dashboard_user(request):
    my_pending = Reservation.objects.filter(user=request.user, status=Reservation.PENDING)[:5]
    upcoming = Reservation.objects.filter(user=request.user, status=Reservation.APPROVED, start__gte=timezone.now())[:5]
    unread = request.user.notifications.filter(is_read=False).count()
    return render(request, "dashboard/user.html", {
        "my_pending": my_pending, "upcoming": upcoming, "unread": unread
    })

def is_staff(user):  # Admin dashboard
    return user.is_staff

@user_passes_test(is_staff)
def dashboard_admin(request):
    pending = Reservation.objects.filter(status=Reservation.PENDING)
    return render(request, "dashboard/admin.html", {"pending": pending})

# ---------- Calendario / Disponibilidad ----------
@login_required
def availability_json(request):
    """Devuelve reservas (aprobadas/pendientes) para un space opcional en formato FullCalendar."""
    qs = Reservation.objects.filter(status__in=[Reservation.PENDING, Reservation.APPROVED])
    space_id = request.GET.get("space")
    if space_id:
        qs = qs.filter(space_id=space_id)
    events = [{
        "id": r.id,
        "title": f"{r.space.name} ({r.get_status_display()})",
        "start": r.start.isoformat(),
        "end": r.end.isoformat()
    } for r in qs]
    return JsonResponse(events, safe=False)

# ---------- Reservas ----------
class ReservationCreateView(CreateView):
    model = Reservation
    form_class = ReservationForm
    template_name = "reservations/form.html"
    success_url = reverse_lazy("dashboard_user")

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.info(self.request, "Reserva creada y enviada a aprobación.")
        # notificar a admins
        for admin in Profile.objects.filter(user__is_staff=True):
            Notification.objects.create(user=admin.user, message="Nueva reserva pendiente de aprobación.")
        return super().form_valid(form)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return super().dispatch(request, *args, **kwargs)

class ReservationDetailView(DetailView):
    model = Reservation
    template_name = "reservations/detail.html"

@login_required
def my_history(request):
    qs = Reservation.objects.filter(user=request.user).order_by("-start")
    return render(request, "reservations/history.html", {"reservations": qs})

# ---------- Aprobaciones ----------
@user_passes_test(is_staff)
def approvals_pending(request):
    qs = Reservation.objects.filter(status=Reservation.PENDING)
    return render(request, "approvals/pending.html", {"reservations": qs})

@user_passes_test(is_staff)
def approve_or_reject(request, pk):
    reservation = get_object_or_404(Reservation, pk=pk)
    if request.method == "POST":
        form = ApprovalForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data["decision"]
            notes = form.cleaned_data.get("notes", "")
            Approval.objects.update_or_create(
                reservation=reservation,
                defaults={"approver": request.user, "decision": decision, "notes": notes}
            )
            reservation.status = Reservation.APPROVED if decision == "APPR" else Reservation.REJECTED
            reservation.save()
            Notification.objects.create(user=reservation.user,
                message=f"Tu reserva '{reservation}' fue {'aprobada' if decision=='APPR' else 'rechazada'}.")
            messages.success(request, "Decisión registrada.")
            return redirect("approvals_pending")
    else:
        form = ApprovalForm()
    return render(request, "approvals/decision_form.html", {"reservation": reservation, "form": form})

# ---------- CRUD Espacios ----------
class SpaceListView(ListView):
    model = Space
    template_name = "spaces/list.html"

class SpaceCreateView(CreateView):
    model = Space
    form_class = SpaceForm
    template_name = "spaces/form.html"
    success_url = reverse_lazy("spaces_list")

class SpaceUpdateView(UpdateView):
    model = Space
    form_class = SpaceForm
    template_name = "spaces/form.html"
    success_url = reverse_lazy("spaces_list")

class SpaceDeleteView(DeleteView):
    model = Space
    template_name = "spaces/confirm_delete.html"
    success_url = reverse_lazy("spaces_list")

# ---------- CRUD Recursos ----------
class ResourceListView(ListView):
    model = Resource
    template_name = "resources/list.html"

class ResourceCreateView(CreateView):
    model = Resource
    form_class = ResourceForm
    template_name = "resources/form.html"
    success_url = reverse_lazy("resources_list")

class ResourceUpdateView(UpdateView):
    model = Resource
    form_class = ResourceForm
    template_name = "resources/form.html"
    success_url = reverse_lazy("resources_list")

class ResourceDeleteView(DeleteView):
    model = Resource
    template_name = "resources/confirm_delete.html"
    success_url = reverse_lazy("resources_list")

# ---------- Notificaciones ----------
@login_required
def notifications_view(request):
    qs = request.user.notifications.order_by("-created_at")
    if request.method == "POST":
        qs.update(is_read=True)
        return redirect("notifications")
    return render(request, "notifications/list.html", {"notifications": qs})

# ---------- Reportes (CSV simple) ----------
@user_passes_test(is_staff)
def export_reservations_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="reservas.csv"'
    writer = csv.writer(response)
    writer.writerow(["ID","Usuario","Espacio","Inicio","Fin","Estado"])
    for r in Reservation.objects.all().select_related("user","space"):
        writer.writerow([r.id, r.user.username, r.space.name, r.start, r.end, r.get_status_display()])
    return response

# ---------- Configuración (perfil) ----------
@login_required
def profile_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Preferencias guardadas.")
            return redirect("profile")
    else:
        form = ProfileForm(instance=profile)
    return render(request, "account/profile.html", {"form": form})
