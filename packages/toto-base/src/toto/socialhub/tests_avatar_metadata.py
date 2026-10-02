"""Avatars without their metadata (2026-10-01, 37c.4).

A photo says where it was taken and with what: EXIF (the GPS position, the
camera), XMP, an ICC profile, comments. Stored as uploaded, /media/ handed
all of it to anyone shown the avatar. Every avatar is now drawn again from
its pixels (``toto.socialhub.forms.reencode_avatar``): nothing of that kind
is stored, the picture shows turned as its EXIF orientation said, and the
format, the size limits and the random name stay as they were. The admin's
Person form follows the same rules.
"""

from __future__ import annotations

import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import ExifTags, Image, PngImagePlugin

from toto.core.models import Platform
from toto.people.models import Person

User = get_user_model()

#: What the pictures below say about where and with what; none of it may be
#: found in a stored avatar.
TELLTALES = (b"PhoneCo", b"Phone 12", b"secret comment", b"GPS here", b"fake-icc")
XMP = b'<x:xmpmeta xmlns:x="adobe:ns:meta/">GPS here</x:xmpmeta>'
RED, BLUE = (220, 20, 20), (20, 20, 220)


def phone_exif(orientation=None):
    tags = Image.Exif()
    tags[ExifTags.Base.Make] = "PhoneCo"
    tags[ExifTags.Base.Model] = "Phone 12"
    if orientation:
        tags[ExifTags.Base.Orientation] = orientation
    tags[ExifTags.IFD.GPSInfo] = {
        ExifTags.GPS.GPSLatitudeRef: "N", ExifTags.GPS.GPSLatitude: (52.0, 13.0, 37.0),
        ExifTags.GPS.GPSLongitudeRef: "E", ExifTags.GPS.GPSLongitude: (21.0, 0.0, 41.0),
    }
    return tags


def red_and_blue(size=(40, 20), mode="RGB"):
    """The left half red, the right half blue."""
    image = Image.new(mode, size, BLUE)
    image.paste(RED, (0, 0, size[0] // 2, size[1]))
    return image


def upload(data, name, content_type):
    return SimpleUploadedFile(name, data, content_type=content_type)


def phone_jpeg(orientation=None, quality=90, image=None):
    buffer = io.BytesIO()
    (image or red_and_blue()).save(
        buffer, format="JPEG", quality=quality, exif=phone_exif(orientation),
        comment=b"secret comment", xmp=XMP, icc_profile=b"fake-icc" * 8)
    return buffer.getvalue()


class AvatarCase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        media = override_settings(MEDIA_ROOT=self.media)
        media.enable()
        self.addCleanup(media.disable)
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("ada", password="pw")
        self.person = Person.objects.create(user=self.user, display_name="Ada")
        self.client.force_login(self.user)

    def post(self, avatar):
        return self.client.post(reverse("account:profile"), {
            "display_name": "Ada", "bio": "", "phone": "", "avatar": avatar})

    def stored(self):
        """The stored avatar's bytes, and Pillow's reading of them."""
        self.person.refresh_from_db()
        self.assertTrue(self.person.avatar, "no avatar was stored")
        with self.person.avatar.open("rb") as handle:
            data = handle.read()
        return data, Image.open(io.BytesIO(data))

    def assertNothingTells(self, data, image):
        for telltale in TELLTALES:
            self.assertFalse(telltale in data, f"{telltale!r} is still in the stored avatar")
        self.assertEqual(dict(image.getexif()), {})
        self.assertEqual(image.getexif().get_ifd(ExifTags.IFD.GPSInfo), {})
        for key in ("exif", "xmp", "XML:com.adobe.xmp", "icc_profile", "comment", "Comment"):
            self.assertNotIn(key, image.info)


class JpegTests(AvatarCase):
    def test_a_jpeg_loses_its_position_and_its_camera(self):
        original = phone_jpeg()
        self.assertIn(b"PhoneCo", original)  # what the phone sent says it all
        response = self.post(upload(original, "IMG_0001.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (40, 20))
        self.assertTrue(self.person.avatar.name.endswith(".jpg"))
        self.assertNotIn("IMG_0001", self.person.avatar.name)

    def test_the_orientation_is_applied_and_its_tag_goes(self):
        # 6: the camera was held turned; shown upright, the left half (red)
        # is on top and the picture is taller than wide.
        response = self.post(upload(phone_jpeg(orientation=6), "IMG_0002.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual(image.size, (20, 40))
        top, bottom = image.convert("RGB").getpixel((10, 5)), image.convert("RGB").getpixel((10, 35))
        self.assertGreater(top[0], 150)
        self.assertLess(top[2], 100)
        self.assertGreater(bottom[2], 150)
        self.assertLess(bottom[0], 100)

    def test_a_cmyk_jpeg_is_stored_as_rgb(self):
        original = phone_jpeg(image=red_and_blue(mode="CMYK"))
        self.assertEqual(self.post(upload(original, "print.jpg", "image/jpeg")).status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual((image.format, image.mode), ("JPEG", "RGB"))

    def test_a_cameras_mpo_is_stored_as_the_jpeg_in_front(self):
        """Some cameras write MPO, a JPEG with a second picture behind the
        first, which Pillow names apart: refused as a format off the list
        until 37c.24. Now an avatar, stored as a plain JPEG of the picture in
        front, without the camera's metadata."""
        buffer = io.BytesIO()
        red_and_blue().save(buffer, format="MPO", save_all=True,
                            append_images=[Image.new("RGB", (40, 20), BLUE)],
                            exif=phone_exif(), comment=b"secret comment")
        original = buffer.getvalue()
        self.assertEqual(Image.open(io.BytesIO(original)).format, "MPO")
        response = self.post(upload(original, "DSCF0001.JPG", "image/jpeg"))
        self.assertEqual(response.status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual((image.format, image.size), ("JPEG", (40, 20)))
        self.assertNotIn("mp", image.info)
        self.assertTrue(self.person.avatar.name.endswith(".jpg"))
        left = image.convert("RGB").getpixel((5, 10))
        self.assertGreater(left[0], 150)  # the red half in front, not the blue picture behind
        self.assertLess(left[2], 100)

    def test_pixels_that_cannot_be_read_are_refused(self):
        # The header is whole, so Pillow's first look passes; the pixels are
        # cut short. Drawing it again is what finds out.
        original = phone_jpeg(image=Image.effect_noise((64, 64), 60).convert("RGB"))
        response = self.post(upload(original[:len(original) // 2], "cut.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    def test_pixels_cut_short_are_refused_when_pillow_was_told_to_forgive(self):
        # WeasyPrint turns Pillow's LOAD_TRUNCATED_IMAGES on for the whole
        # process the moment it is imported (Aralia's renderer imports it).
        # With it on, the half JPEG above was stored as a picture over grey
        # (2026-10-02, 41.4); the redraw reads the pixels whole regardless,
        # and leaves the switch as it found it.
        from PIL import ImageFile

        self.addCleanup(setattr, ImageFile, "LOAD_TRUNCATED_IMAGES",
                        ImageFile.LOAD_TRUNCATED_IMAGES)
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        original = phone_jpeg(image=Image.effect_noise((64, 64), 60).convert("RGB"))
        response = self.post(upload(original[:len(original) // 2], "cut.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)
        self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, True)


class RedrawTests(SimpleTestCase):
    """``redraw_picture`` itself: the avatar's door and the host's Trix door
    both draw through it."""

    def cut_jpeg(self):
        original = phone_jpeg(image=Image.effect_noise((64, 64), 60).convert("RGB"))
        return original[:len(original) // 2]

    def test_a_cut_picture_raises_whatever_pillow_was_told(self):
        from PIL import ImageFile

        from toto.socialhub.forms import redraw_picture

        self.addCleanup(setattr, ImageFile, "LOAD_TRUNCATED_IMAGES",
                        ImageFile.LOAD_TRUNCATED_IMAGES)
        for forgiving in (False, True):
            with self.subTest(load_truncated_images=forgiving):
                ImageFile.LOAD_TRUNCATED_IMAGES = forgiving
                with self.assertRaises(OSError):
                    redraw_picture(self.cut_jpeg(), "JPEG", 10 * 1024 * 1024)
                self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, forgiving)

    def test_a_whole_picture_is_drawn_with_the_switch_on_too(self):
        from PIL import ImageFile

        from toto.socialhub.forms import redraw_picture

        self.addCleanup(setattr, ImageFile, "LOAD_TRUNCATED_IMAGES",
                        ImageFile.LOAD_TRUNCATED_IMAGES)
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        data = redraw_picture(phone_jpeg(), "JPEG", 10 * 1024 * 1024)
        self.assertEqual(Image.open(io.BytesIO(data)).size, (40, 20))
        self.assertIs(ImageFile.LOAD_TRUNCATED_IMAGES, True)


class OtherFormatTests(AvatarCase):
    def test_a_png_loses_its_text_exif_and_profile(self):
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "secret comment")
        info.add_itxt("XML:com.adobe.xmp", XMP.decode())
        buffer = io.BytesIO()
        red_and_blue(mode="RGBA").save(buffer, format="PNG", pnginfo=info,
                                       exif=phone_exif(), icc_profile=b"fake-icc" * 8)
        self.assertEqual(self.post(upload(buffer.getvalue(), "me.png", "image/png")).status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual((image.format, image.mode), ("PNG", "RGBA"))
        self.assertTrue(self.person.avatar.name.endswith(".png"))

    def test_a_webp_loses_its_exif_xmp_and_profile(self):
        buffer = io.BytesIO()
        red_and_blue().save(buffer, format="WEBP", exif=phone_exif(), xmp=XMP,
                            icc_profile=b"fake-icc" * 8)
        self.assertEqual(self.post(upload(buffer.getvalue(), "me.webp", "image/webp")).status_code,
                         302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual(image.format, "WEBP")
        self.assertTrue(self.person.avatar.name.endswith(".webp"))

    def test_a_gif_loses_its_comment_and_keeps_its_transparency(self):
        picture = Image.new("P", (8, 8), 1)
        picture.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        picture.putpixel((0, 0), 0)
        buffer = io.BytesIO()
        picture.save(buffer, format="GIF", comment=b"secret comment", transparency=0)
        self.assertEqual(self.post(upload(buffer.getvalue(), "me.gif", "image/gif")).status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual(image.format, "GIF")
        self.assertEqual(image.info.get("transparency"), 0)

    def test_an_animated_gif_keeps_its_first_frame(self):
        frames = []
        for index in range(3):
            frame = Image.new("P", (8, 8), index)
            frame.putpalette([200, 0, 0, 0, 200, 0, 0, 0, 200] + [0] * 759)
            frames.append(frame)
        buffer = io.BytesIO()
        frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:],
                       duration=100, loop=0, comment=b"secret comment")
        self.assertEqual(self.post(upload(buffer.getvalue(), "me.gif", "image/gif")).status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertFalse(getattr(image, "is_animated", False))
        self.assertEqual(image.convert("RGB").getpixel((4, 4)), (200, 0, 0))


class SizeTests(AvatarCase):
    """The size limits stay those of the upload: a lossy picture drawn again
    is tried at lower qualities until it fits, and refused if none does."""

    def setUp(self):
        super().setUp()
        # A picture of noise saved at a low quality grows when drawn again
        # at a high one.
        noise = Image.effect_noise((128, 128), 80).convert("RGB")
        self.original = phone_jpeg(image=noise, quality=20)
        sizes = {}
        for quality in (90, 70):
            buffer = io.BytesIO()
            Image.open(io.BytesIO(self.original)).save(buffer, format="JPEG", quality=quality)
            sizes[quality] = buffer.tell()
        self.sizes = sizes
        self.assertLess(len(self.original), sizes[70])
        self.assertLess(sizes[70], sizes[90])

    def test_a_lower_quality_is_tried_until_it_fits(self):
        cap = (self.sizes[70] + self.sizes[90]) // 2
        with override_settings(SOCIALHUB_AVATAR_MAX_BYTES=cap):
            response = self.post(upload(self.original, "noise.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 302)
        data, image = self.stored()
        self.assertLessEqual(len(data), cap)
        self.assertNothingTells(data, image)

    def test_a_picture_that_fits_at_no_quality_is_refused(self):
        cap = len(self.original) + 1
        with override_settings(SOCIALHUB_AVATAR_MAX_BYTES=cap):
            response = self.post(upload(self.original, "noise.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 400)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)


class AdminTests(AvatarCase):
    """The admin's Person form: a superuser's upload follows the same rules."""

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_superuser("root", password="pw"))

    def post(self, avatar):
        return self.client.post(reverse("admin:people_person_change", args=[self.person.pk]), {
            "user": self.user.pk, "display_name": "Ada", "slug": self.person.slug,
            "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
            "location_sharing": "off", "preferred_language": "en", "avatar": avatar})

    def test_an_avatar_set_in_the_admin_loses_its_metadata(self):
        response = self.post(upload(phone_jpeg(orientation=6), "IMG_0003.jpg", "image/jpeg"))
        self.assertEqual(response.status_code, 302)
        data, image = self.stored()
        self.assertNothingTells(data, image)
        self.assertEqual(image.size, (20, 40))
        self.assertTrue(self.person.avatar.name.endswith(".jpg"))
        self.assertNotIn("IMG_0003", self.person.avatar.name)

    def test_the_admin_refuses_a_format_outside_the_list(self):
        buffer = io.BytesIO()
        red_and_blue().save(buffer, format="BMP")
        response = self.post(upload(buffer.getvalue(), "me.bmp", "image/bmp"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("avatar", response.context["adminform"].form.errors)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)

    # No earlier picture is left in /media/ (the review of stage 37c,
    # 2026-10-01): My account deleted it, the admin kept every one.

    def first_avatar(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(upload(phone_jpeg(), "a.jpg", "image/jpeg")).status_code, 302)
        self.person.refresh_from_db()
        name = self.person.avatar.name
        self.assertTrue(self.storage.exists(name))
        return name

    @property
    def storage(self):
        return Person._meta.get_field("avatar").storage

    def test_an_avatar_replaced_in_the_admin_leaves_no_earlier_file(self):
        first = self.first_avatar()
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(upload(phone_jpeg(), "b.jpg", "image/jpeg")).status_code, 302)
        self.person.refresh_from_db()
        self.assertNotEqual(self.person.avatar.name, first)
        self.assertTrue(self.storage.exists(self.person.avatar.name))
        self.assertFalse(self.storage.exists(first))

    def test_an_avatar_taken_off_in_the_admin_leaves_no_file(self):
        first = self.first_avatar()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:people_person_change", args=[self.person.pk]), {
                    "user": self.user.pk, "display_name": "Ada", "slug": self.person.slug,
                    "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
                    "location_sharing": "off", "preferred_language": "en",
                    "avatar-clear": "on"})
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertFalse(self.person.avatar)
        self.assertFalse(self.storage.exists(first))

    def test_a_change_that_keeps_the_avatar_keeps_its_file(self):
        first = self.first_avatar()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:people_person_change", args=[self.person.pk]), {
                    "user": self.user.pk, "display_name": "Ada L.", "slug": self.person.slug,
                    "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
                    "location_sharing": "off", "preferred_language": "en"})
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.avatar.name, first)
        self.assertTrue(self.storage.exists(first))

    def test_a_person_deleted_in_the_admin_leaves_no_avatar_file(self):
        first = self.first_avatar()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("admin:people_person_delete", args=[self.person.pk]), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Person.objects.filter(pk=self.person.pk).exists())
        self.assertFalse(self.storage.exists(first))
