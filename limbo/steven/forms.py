from django import forms
from .models import AgentRun


class AgentRunForm(forms.ModelForm):
    class Meta:
        model = AgentRun
        fields = ["user_prompt"]
        widgets = {
            "user_prompt": forms.Textarea(attrs={
                "rows": 7,
                "placeholder": "Ask Steven to delegate, reason, calculate, or use an enabled tool...",
                "class": (
                    "min-h-40 w-full resize-y rounded-2xl border px-4 py-3 "
                    "text-sm leading-6 shadow-sm outline-none transition duration-300 "
                    "placeholder:opacity-50 focus:ring-4 focus:ring-current/10"
                ),
                "x-bind:class": (
                    "darkMode "
                    "? 'border-accent-1 bg-primary-bg-dark text-text-main-dark focus:border-accent-dark' "
                    ": 'border-accent-2 bg-primary-bg-light text-text-main-light focus:border-accent-light'"
                ),
            }),
        }
        labels = {
            "user_prompt": "What should Steven do?",
        }
