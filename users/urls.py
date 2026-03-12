from django.urls import path

from .views import (
    DashboardSummaryView,
    DashboardTimeseriesView,
    DashboardTrackEventView,
    GoogleAuthView,
    MeView,
    PublicContactLeadSubmitView,
    UserAppearanceView,
    UserAppearanceImageUploadView,
    UserContactLeadsView,
    UserLinksView,
    PublicProfileView,
    PublicTrackEventView,
    SetUsernameView,
    UsernameAvailabilityView,
)


urlpatterns = [
    path("auth/google/", GoogleAuthView.as_view(), name="auth-google"),
    path("users/me/", MeView.as_view(), name="users-me"),
    path("users/check-username/", UsernameAvailabilityView.as_view(), name="users-check-username"),
    path("users/set-username/", SetUsernameView.as_view(), name="users-set-username"),
    path("users/appearance/", UserAppearanceView.as_view(), name="users-appearance"),
    path("users/appearance/upload-image/", UserAppearanceImageUploadView.as_view(), name="users-appearance-upload-image"),
    path("users/links/", UserLinksView.as_view(), name="users-links"),
    path("users/contacts/", UserContactLeadsView.as_view(), name="users-contacts"),
    path("public/profile/", PublicProfileView.as_view(), name="public-profile"),
    path("public/track/", PublicTrackEventView.as_view(), name="public-track"),
    path("public/contacts/", PublicContactLeadSubmitView.as_view(), name="public-contacts"),
    path("dashboard/summary/", DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("dashboard/timeseries/", DashboardTimeseriesView.as_view(), name="dashboard-timeseries"),
    path("dashboard/track/", DashboardTrackEventView.as_view(), name="dashboard-track"),
]
