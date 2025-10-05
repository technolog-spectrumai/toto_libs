from django import forms

class MessageForm(forms.Form):
    content = forms.CharField(
        label="Your message",
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Type your message here...",
            "class": "w-full px-4 py-2 rounded border focus:outline-none focus:ring-2 transition duration-300",
            "x-bind:class": "darkMode ? 'bg-primary-bg-dark text-text-main-dark' : 'bg-primary-bg-light text-text-main-light'"
        })
    )
