import os
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.contrib.auth import get_user_model
from toto.vault.models import VaultFile, Bucket
from toto.ocr.transform import TransformHelper
from toto.workflows.models import LambdaFunction
import os
import shutil
from django.core.files import File

User = get_user_model()


# ---------------------------------------------------------
# OCR PROJECT
# ---------------------------------------------------------

class OcrProject(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    bucket = models.ForeignKey(Bucket, on_delete=models.CASCADE, related_name="ocr_projects")
    allowed_users = models.ManyToManyField(User, related_name="shared_ocr_projects", blank=True)

    class Meta:
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"

    def user_has_access(self, user):
        return (
            user == self.bucket.owner or
            self.allowed_users.filter(id=user.id).exists()
        )

    def __str__(self):
        return self.name


# ---------------------------------------------------------
# OCR LINE
# ---------------------------------------------------------

class OcrLine(models.Model):
    image = models.ForeignKey("OcrImage", on_delete=models.CASCADE, related_name="lines")

    text = models.TextField()
    left = models.IntegerField()
    top = models.IntegerField()
    width = models.IntegerField()
    height = models.IntegerField()
    confidence = models.IntegerField()

    def __str__(self):
        return f"{self.text[:16]}..."


# ---------------------------------------------------------
# OCR IMAGE
# ---------------------------------------------------------

class OcrImage(models.Model):
    image = models.ImageField(upload_to='uploads/', null=True, blank=True)
    project = models.ForeignKey(OcrProject, on_delete=models.CASCADE, related_name="images")

    vault_file = models.ForeignKey(
        VaultFile,
        on_delete=models.CASCADE,
        related_name="ocr_images",
        null=True,
        blank=True
    )

    filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    mime_type = models.CharField(max_length=50, blank=True)

    language = models.CharField(max_length=20, default="eng")
    processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    # ---------------------------------------------------------
    # IMAGE SOURCE
    # ---------------------------------------------------------

    def get_image_path(self):
        if self.image:
            return self.image.path
        if self.vault_file:
            return self.vault_file.file.path
        raise ValueError("No image source available for OCR.")

    # ---------------------------------------------------------
    # SAVE HOOKS
    # ---------------------------------------------------------

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            self._extract_metadata()
            self._ensure_vault_file()

    def _extract_metadata(self):
        path = self.get_image_path() if (self.image or self.vault_file) else None
        if not path:
            return

        self.filename = os.path.basename(path)
        self.file_size = os.path.getsize(path)
        self.mime_type = getattr(self.image.file, "content_type", "") if self.image else ""
        self.save(update_fields=["filename", "file_size", "mime_type"])

    def _ensure_vault_file(self):
        if self.vault_file or not self.image:
            return

        vf = VaultFile.objects.create(
            owner=self.project.bucket.owner,
            bucket=self.project.bucket,
            title=self.filename or "ocr-image",
            file=self.image,
            file_type="image",
        )

        self.vault_file = vf
        self.save(update_fields=["vault_file"])

    def apply_transform(self, transform, user_params=None):
        helper = TransformHelper(transform)
        params = helper.build_params(user_params)
        return helper.execute(self, params)

    def run_ocr(self, lang=None):
        """
        Runs OCR via KernelServer (isolated process), then updates DB models here.
        """
        from django.conf import settings
        from toto.mandragora.kernel import KernelClient

        # 1. Determine language
        lang = lang or self.language
        self.language = lang

        # 2. Delegate to kernel server
        addr = getattr(settings, "KERNEL_SERVER_ADDR", "tcp://127.0.0.1:5555")
        client = KernelClient(addr=addr)
        result = client.ocr(self.get_image_path(), lang=lang)
        if "error" in result:
            raise RuntimeError(f"KernelServer OCR failed: {result['error']}")
        lines = result["lines"]

        # 3. Update DB models
        self.lines.all().delete()

        for line in lines:
            OcrLine.objects.create(
                image=self,
                text=line["text"],
                left=line["left"],
                top=line["top"],
                width=line["width"],
                height=line["height"],
                confidence=line["confidence"],
            )

        # 4. Update processed flags
        self.processed = True
        self.processed_at = timezone.now()
        self.save(update_fields=["processed", "processed_at", "language"])
        return lines

    def __str__(self):
        return self.filename or f"OCR Image #{self.pk}"

    def reset(self):
        self.lines.all().delete()
        self.processed = False
        self.processed_at = None
        self.save(update_fields=["processed", "processed_at"])

    @property
    def extracted_text(self):
        return "\n".join(line.text for line in self.lines.all())

    def clone(self, *, copy_lines=True):
        """
        Fully duplicates this OcrImage, including a physical copy of the file
        and optionally all OCR lines.
        """

        # --- 1. Generate a safe new filename ---
        base = self.filename or "image"
        name, ext = os.path.splitext(base)

        existing = OcrImage.objects.filter(
            project=self.project,
            filename__startswith=name
        ).count()

        new_filename = f"{name} (copy {existing}){ext}" if existing else f"{name} (copy){ext}"

        # --- 2. Copy the physical file ---
        src_path = self.get_image_path()
        new_path = os.path.join(os.path.dirname(src_path), new_filename)

        shutil.copy2(src_path, new_path)

        # --- 3. Create a new VaultFile for the copied file ---
        with open(new_path, "rb") as f:
            new_vault = VaultFile.objects.create(
                owner=self.project.bucket.owner,
                bucket=self.project.bucket,
                title=new_filename,
                file=File(f, name=new_filename),
                file_type="image",
            )

        # --- 4. Create the new OcrImage object ---
        new_image = OcrImage.objects.create(
            image=new_vault.file,  # new file
            vault_file=new_vault,  # new vault file
            project=self.project,
            filename=new_filename,
            file_size=os.path.getsize(new_path),
            mime_type=self.mime_type,
            language=self.language,
            processed=False,
            processed_at=None,
        )

        # --- 5. Copy OCR lines if requested ---
        if copy_lines:
            for line in self.lines.all():
                OcrLine.objects.create(
                    image=new_image,
                    text=line.text,
                    left=line.left,
                    top=line.top,
                    width=line.width,
                    height=line.height,
                    confidence=line.confidence,
                )

        return new_image

class ImageTransform(models.Model):
    """
    Defines a reusable image transformation that calls a LambdaFunction.
    """
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)

    lambda_function = models.ForeignKey(
        LambdaFunction,
        on_delete=models.CASCADE,
        related_name="image_transforms"
    )

    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name



class ImageTransformParam(models.Model):
    """
    Defines a parameter for an ImageTransform.
    Each parameter has a name and integer constraints.
    """
    transform = models.ForeignKey(
        ImageTransform,
        on_delete=models.CASCADE,
        related_name="params"
    )

    key = models.CharField(max_length=100)

    min_value = models.IntegerField(default=0)
    max_value = models.IntegerField(default=100)
    default_value = models.IntegerField(default=0)
    description = models.CharField(max_length=64, default="")
    # Optional: helps UI sliders
    step = models.IntegerField(default=1)

    def __str__(self):
        return f"{self.key} ({self.min_value}-{self.max_value}, default={self.default_value})"

    def clean(self):
        # Ensure default is within range
        if not (self.min_value <= self.default_value <= self.max_value):
            raise ValidationError("default_value must be between min_value and max_value")
