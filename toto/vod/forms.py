from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from toto.subscriptions.models import SubscriptionPlan

from .models import VodAccessMode, VodCollection, VodVideo, VodVideoAccessMode
from .services import create_video_from_upload


class VodCollectionForm(forms.ModelForm):
    class Meta:
        model = VodCollection
        fields = [
            "title",
            "slug",
            "description",
            "owner",
            "bucket",
            "cover_file",
            "access_mode",
            "required_plan",
            "usage_feature_code",
            "invoice_amount",
            "invoice_currency_label",
            "allow_downloads",
            "position",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["required_plan"].queryset = SubscriptionPlan.objects.filter(
            status=SubscriptionPlan.Status.ACTIVE
        ).select_related("community")


class VodVideoForm(forms.ModelForm):
    class Meta:
        model = VodVideo
        fields = [
            "collection",
            "source_file",
            "poster_file",
            "title",
            "slug",
            "description",
            "status",
            "access_mode",
            "required_plan",
            "usage_feature_code",
            "duration_seconds",
            "position",
            "tags",
            "hls_bucket",
            "hls_segment_seconds",
            "invoice_amount",
            "invoice_currency_label",
            "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
            "tags": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["required_plan"].queryset = SubscriptionPlan.objects.filter(
            status=SubscriptionPlan.Status.ACTIVE
        ).select_related("community")


class VodUploadForm(forms.Form):
    collection = forms.ModelChoiceField(queryset=VodCollection.objects.all())
    title = forms.CharField(max_length=240)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    video_file = forms.FileField(label=_("Video file"))
    poster_file = forms.FileField(label=_("Poster image"), required=False)
    status = forms.ChoiceField(choices=VodVideo.Status.choices, initial=VodVideo.Status.DRAFT)
    access_mode = forms.ChoiceField(choices=VodVideoAccessMode.choices, initial=VodVideoAccessMode.INHERIT)
    required_plan = forms.ModelChoiceField(
        queryset=SubscriptionPlan.objects.none(),
        required=False,
        help_text=_("Use an existing toto.subscriptions plan. Leave blank to inherit collection plan."),
    )
    build_hls = forms.BooleanField(required=False, initial=False, help_text=_("Run ffmpeg now."))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["collection"].queryset = VodCollection.objects.select_related("owner", "bucket").all()
        self.fields["required_plan"].queryset = SubscriptionPlan.objects.filter(
            status=SubscriptionPlan.Status.ACTIVE
        ).select_related("community")

    def clean_video_file(self):
        f = self.cleaned_data["video_file"]
        name = (getattr(f, "name", "") or "").lower()
        content_type = (getattr(f, "content_type", "") or "").lower()
        if not (content_type.startswith("video/") or name.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".ts", ".m2ts"))):
            raise forms.ValidationError(_("Upload a video file."))
        return f

    def save(self):
        return create_video_from_upload(
            collection=self.cleaned_data["collection"],
            uploaded_file=self.cleaned_data["video_file"],
            poster_file=self.cleaned_data.get("poster_file"),
            owner=self.user,
            title=self.cleaned_data["title"],
            description=self.cleaned_data.get("description") or "",
            status=self.cleaned_data.get("status") or VodVideo.Status.DRAFT,
            access_mode=self.cleaned_data.get("access_mode") or VodVideoAccessMode.INHERIT,
            required_plan=self.cleaned_data.get("required_plan"),
            build_hls=self.cleaned_data.get("build_hls", False),
        )
