from django.urls import path
from django.contrib.auth import views as auth_views
from .views import lead_submissions,insights_dashboard, send_merge_to_n8n, save_remark, update_email_source, update_lead_status, delete_lead, update_intent_level, update_location, update_call_status, student_profile,student_dashboard_view, send_zoho_mail, update_student_data


urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path("leads/", lead_submissions, name="lead-submissions"),
    path("leads/merge/", send_merge_to_n8n, name="merge-leads"),
    path("leads/remark/", save_remark, name="save-remark"),
    path("leads/update-email-source/", update_email_source, name="update-email-source"),
    path("leads/update-status/", update_lead_status, name="update-lead-status"),
    path("leads/delete/", delete_lead, name="delete-lead"),
    path('leads/update-intent/', update_intent_level, name='update_intent_level'),
    path("leads/update-call/", update_call_status, name="update-call-status"),
    path("leads/update-location/", update_location, name="update-location"),
    path("leads/profile/<uuid:lead_id>/", student_profile, name="student-profile"),
    path("leads/send-zoho-mail/", send_zoho_mail, name="send-zoho-mail"),
    path("leads/update-student-data/", update_student_data, name="update-student-data"),
    path("insights/", insights_dashboard, name="insights-dashboard"),
    path("leads/profile/<uuid:lead_id>/", student_profile, name="student-profile"),
    path("leads/profile/<str:email>/", student_dashboard_view, name="student-dashboard"),
]

