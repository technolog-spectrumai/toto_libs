from django.db import models

class Resume(models.Model):
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=50)
    email = models.EmailField()
    citizenship = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    disclaimer = models.CharField(max_length=1024, blank=True)

    def __str__(self):
        return self.full_name


class WorkExperience(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='work_experiences')
    start_date = models.CharField(max_length=20)
    end_date = models.CharField(max_length=20, blank=True, null=True)
    title = models.CharField(max_length=255)
    company = models.CharField(max_length=255)
    location = models.CharField(max_length=255)
    description = models.TextField()

    def __str__(self):
        return f"{self.title} at {self.company}"


class Education(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='education_entries')
    start_date = models.CharField(max_length=20)
    end_date = models.CharField(max_length=20, blank=True, null=True)
    degree = models.CharField(max_length=255)
    institution = models.CharField(max_length=255)
    specialization = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return f"{self.degree} at {self.institution}"


class Distinction(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='distinctions')
    date_awarded = models.CharField(max_length=20)
    title = models.CharField(max_length=255)

    def __str__(self):
        return self.title


class Language(models.Model):
    PROFICIENCY_CHOICES = [
        ('native', 'Native'),
        ('fluent', 'Fluent'),
        ('basic', 'Basic'),
        ('retracted', 'Retracted'),
    ]

    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='languages')
    name = models.CharField(max_length=100)
    proficiency = models.CharField(max_length=20, choices=PROFICIENCY_CHOICES)

    def __str__(self):
        return f"{self.name} ({self.proficiency})"


class Skill(models.Model):
    resume = models.ForeignKey(Resume, on_delete=models.CASCADE, related_name='skills')
    category = models.CharField(max_length=100, blank=True)
    description = models.TextField()

    def __str__(self):
        return self.description