import json
import mimetypes
import os
from io import BytesIO
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import (FileResponse, Http404, HttpResponse,
                         HttpResponseForbidden, JsonResponse)
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _, ngettext
from django.views import View
from django.views.generic import TemplateView, DetailView, ListView
from django.urls import reverse, NoReverseMatch
from django.contrib.auth.mixins import LoginRequiredMixin

from django.contrib import messages
from django.utils.decorators import method_decorator
from toto.ui import PageProcessor
from . import access, scanning
from . import storage_backends as _storage_backends
from .models import (VaultFile, Bucket, FileGateway, VaultDirectory,
                     BucketCopyLog, StorageBackend)
from .storage_backends import get_bucket_storage


# Empty-file-creatable types and their extension labels (keys mirror
# CreateEmptyFileView._INITIAL). A type is only offered/allowed in "New file" when an
# editor plugin is registered for it — so deployments missing an editor app (e.g. faros
# has no latex/notebook/neojson editor) neither show nor accept those types.
CREATABLE_TYPES = [
    ("text", ".txt"), ("markdown", ".md"),
    ("json", ".json"), ("yaml", ".yaml"), ("xml", ".xml"),
    ("csv", ".csv"), ("html", ".html"), ("latex", ".tex"), ("bib", ".bib"),
    ("svg", ".svg"), ("neojson", ".neojson"),
    # Presentations (toto.memo), contracts (toto.notarius) and notebooks
    # (toto.mandragora) are ordinary .xml files now — created/edited via their own
    # apps (which content-sniff the XML root), not the vault "New file" menu.
]


def available_create_types():
    """[(type, ext), …] for creatable types that have a registered editor plugin.

    Two sources, and the second is why rich formats can be created at all. The
    list above is what the VAULT knows how to seed; a plugin that declares
    `new_file_extension` answers for itself, because "what does an empty
    spreadsheet look like" is a question this package must not import primula to
    answer. See VaultEditorPlugin.blank_content.
    """
    from toto.vault.models import file_edits_allowed, refused_file_types
    if not file_edits_allowed():
        return []
    from toto.vault.plugins import VaultEditorPlugin  # local: registry filled in ready()

    types = [(t, ext) for t, ext in CREATABLE_TYPES
             if VaultEditorPlugin.for_file_type(t)]
    seen = {t for t, _ in types}
    for plugin in VaultEditorPlugin.all():
        extension = getattr(plugin, "new_file_extension", "")
        # `is_available` as well as the extension: a plugin whose editor route
        # is not mounted on this host would put a type in the New-file menu
        # that lands the user on a 500 the moment the file is created, since
        # creation redirects straight into the editor. `for_file_type` above
        # already applies this; this loop reaches the registry directly.
        if not plugin.is_available():
            continue
        if extension and plugin.file_type and plugin.file_type not in seen:
            types.append((plugin.file_type, extension))
            seen.add(plugin.file_type)
    refused = refused_file_types()
    return [(t, ext) for t, ext in types if t not in refused]


# ============================================================
# Public File Views
# ============================================================

class PublicFileListView(TemplateView):
    """
    Renders the full vault tree for all public files.
    Supports ?bucket=<slug> filter. Returns a flat item list
    consumed by the Alpine.js vaultTree() component.
    """
    template_name = "vault/public_file_list.html"

    def _build_flat_items(self, dirs, files, dir_gateway_map, user_bucket_pks=None,
                          clean_pks=None):
        from toto.vault.plugins import VaultPlayPlugin

        def _play_url_for(f):
            if f.is_encrypted:
                return ""
            plugin = VaultPlayPlugin.for_file_type(f.file_type)
            # A plugin whose target URL isn't mounted (its feature flag is off) must
            # not take down the whole listing — degrade to "no play link" instead.
            try:
                return plugin.get_play_url(f) if plugin else ""
            except NoReverseMatch:
                return ""

        from toto.vault.plugins import VaultEditorPlugin

        def _editor_url_for(f):
            # Encrypted files hold ciphertext — never editable. Blanking the URL hides
            # the "Open in editor" button in list, grid and the actions chooser at once
            # (mirrors _play_url_for above). Non-local content gets the same
            # treatment: the editors open local handles, and a remote file's
            # bytes are on another host — download works, editing does not.
            if f.is_encrypted or not access.is_local_content(f):
                return ""
            plugin = VaultEditorPlugin.for_file_type(f.file_type)
            try:
                return plugin.get_editor_url(f) if plugin else ""
            except NoReverseMatch:
                return ""

        # The registry lives here now (toto.vault.plugins), so this no longer
        # reaches into a wheel most hosts do not pin — and the wand finally
        # appears on the host that owns the editors.
        from toto.vault.plugins import FileServicePlugin

        _fs_plugins = FileServicePlugin.all()

        def _has_services(f):
            return any(p.accepts(f) for p in _fs_plugins)

        # NO git decorations here any more. The per-directory Git dropdown was
        # this browser's, and it made git a property of every folder — which
        # read as clutter next to files that would never see a commit. Git
        # belongs to the SURFACES now (workspaces, documents, presentations):
        # each carries its own init and its own menu, and the dashboard's Git
        # page is the overview. The vault shows files; the editors version them.

        by_parent = {}
        for d in dirs:
            pid = d.parent_id
            if pid not in by_parent:
                by_parent[pid] = []
            by_parent[pid].append(d)

        files_by_dir = {}
        for f in files:
            did = f.directory_id
            if did not in files_by_dir:
                files_by_dir[did] = []
            files_by_dir[did].append(f)

        accessible_pks = {d.pk for d in dirs}
        flat = []

        def visit(parent_pk, depth):
            for d in sorted(by_parent.get(parent_pk, []), key=lambda x: x.name):
                n_files = len(files_by_dir.get(d.pk, []))
                n_dirs = sum(1 for c in by_parent.get(d.pk, []) if c.pk in accessible_pks)
                flat.append({
                    "t": "dir",
                    "id": d.pk,
                    "pid": parent_pk,
                    "depth": depth,
                    "name": d.name,
                    "bucket": d.bucket.name,
                    "bpk": d.bucket_id,
                    "n_files": n_files,
                    "n_dirs": n_dirs,
                    "locked": d.allowed_users.exists(),
                    "upload_url": dir_gateway_map.get(d.pk, ""),
                    "can_create": d.bucket_id in user_bucket_pks if user_bucket_pks else False,
                })
                visit(d.pk, depth + 1)
                for f in sorted(files_by_dir.get(d.pk, []), key=lambda x: x.title):
                    _url = f.get_public_url() or ""
                    flat.append({
                        "t": "file",
                        "id": f.pk,
                        "pid": d.pk,
                        "depth": depth + 1,
                        "title": f.title,
                        "file_type": f.file_type,
                        "owner": f.owner.username,
                        "uploaded": f.uploaded_at.strftime("%Y-%m-%d"),
                        "encrypted": f.is_encrypted,
                        "url": _url if not f.is_encrypted else "",
                        "raw_url": _url,
                        "bpk": f.bucket_id,
                        "play_url": _play_url_for(f),
                        "editor_url": _editor_url_for(f),
                        "has_services": _has_services(f),
                        "scan_ok": f.pk in clean_pks if clean_pks else False,
                        # False in a mounted remote bucket: Delete is
                        # immediate there, and the dialog says so.
                        "trashable": f.can_be_trashed,
                    })

        visit(None, 0)

        for f in sorted(files_by_dir.get(None, []), key=lambda x: x.title):
            _url = f.get_public_url() or ""
            flat.append({
                "t": "file",
                "id": f.pk,
                "pid": None,
                "depth": 0,
                "title": f.title,
                "file_type": f.file_type,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at.strftime("%Y-%m-%d"),
                "encrypted": f.is_encrypted,
                "url": _url if not f.is_encrypted else "",
                "raw_url": _url,
                "bpk": f.bucket_id,
                "play_url": _play_url_for(f),
                "editor_url": _editor_url_for(f),
                "has_services": _has_services(f),
                "scan_ok": f.pk in clean_pks if clean_pks else False,
                "trashable": f.can_be_trashed,
            })

        return flat

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bucket_slug = self.request.GET.get("bucket", "")
        user = self.request.user

        # The rename dialog's type dropdown, from the one list that defines
        # them. It used to be hand-written in the template and had drifted:
        # it still offered the retired "notebook" and was missing half the
        # real classes. That is not cosmetic — the dialog posts the selected
        # type back on every rename, and a browser shown no matching option
        # selects the FIRST one, so renaming a file of an unlisted type
        # silently retyped it to "pdf".
        context["file_types"] = VaultFile.FILE_TYPES

        dir_qs = VaultDirectory.objects.select_related(
            "bucket", "parent"
        ).prefetch_related("allowed_users")
        if bucket_slug:
            dir_qs = dir_qs.filter(bucket__slug=bucket_slug)
        accessible_dirs = [d for d in dir_qs if d.user_can_access(user)]

        # Public files + the authenticated owner's own files (private files they
        # created/exported — e.g. .neojson graphs — must be visible to their owner,
        # not only public ones or encrypted-privates).
        if user.is_authenticated:
            visibility_q = Q(is_public=True) | Q(owner=user)
        else:
            visibility_q = Q(is_public=True)
        # A file in a bucket kept to clearances (2026-09-30) is listed to the
        # holders of one of them alone — not its owner, and the public flag
        # does not put it on this page.
        file_qs = access.gate_by_bucket(user, VaultFile.objects.all(), open=visibility_q
                                        ).select_related("owner", "bucket", "directory").order_by("title")
        if bucket_slug:
            file_qs = file_qs.filter(bucket__slug=bucket_slug)

        _gw_direct = {gw.directory_id for gw in FileGateway.objects.only("directory_id")}
        _dirs_by_pk = {d.pk: d for d in accessible_dirs}

        def _find_gateway_dir(dir_pk):
            node_pk, seen = dir_pk, set()
            while node_pk is not None and node_pk not in seen:
                seen.add(node_pk)
                if node_pk in _gw_direct:
                    return node_pk
                node = _dirs_by_pk.get(node_pk)
                if node is None:
                    break
                node_pk = node.parent_id
            return None

        dir_gateway_map = {}
        for _d in accessible_dirs:
            _gw_pk = _find_gateway_dir(_d.pk)
            if _gw_pk is None:
                continue
            _gw_url = reverse("vault:gateway_page", kwargs={"dir_pk": _gw_pk})
            dir_gateway_map[_d.pk] = _gw_url if _gw_pk == _d.pk else f"{_gw_url}?target_dir={_d.pk}"

        user_bucket_pks = (
            set(Bucket.objects.filter(owner=self.request.user).values_list("pk", flat=True))
            if self.request.user.is_authenticated else set()
        )
        # One query for the whole listing, not one per row — and an empty set on
        # a host without antivirus, which is what keeps this page byte-identical
        # to what it was there.
        _files = list(file_qs)
        clean_pks = scanning.clean_file_ids(_files)
        flat_items = self._build_flat_items(accessible_dirs, _files, dir_gateway_map,
                                            user_bucket_pks, clean_pks)

        context["flat_items"] = flat_items
        context["selected_bucket"] = bucket_slug
        from toto.vault.models import trash_days

        context["trash_days"] = trash_days()
        # The "Move to…" picker for the selected files (bulk.py).
        from toto.vault.bulk import move_targets

        context["move_targets"] = move_targets(user)
        context["total_files"] = sum(1 for i in flat_items if i["t"] == "file")
        context["total_dirs"] = sum(1 for i in flat_items if i["t"] == "dir")
        # The metrics page is owner-or-superuser 404 now; render its link
        # only where it will open.
        context["may_see_metrics"] = bool(
            bucket_slug and self.request.user.is_authenticated and (
                self.request.user.is_superuser
                or Bucket.objects.filter(
                    slug=bucket_slug, owner=self.request.user).exists()))

        # The wand. LISTING is the vault's own endpoint, so it works on every
        # host; RUNNING is fileservices' — its run substrate is ffmpeg-shaped and
        # stays in toto-media-ops. A builder-backed service needs neither: it
        # redirects to its own page, which is how the assistant offers a
        # whole-file action on a host with no media wheel at all.
        context["services_url_tpl"] = reverse(
            "vault:file_services", kwargs={"file_pk": 0})
        try:
            context["run_service_url_tpl"] = reverse("fileservices:run_service", kwargs={"file_pk": 0})
            context["open_service_url_tpl"] = reverse("fileservices:open_primary", kwargs={"file_pk": 0})
        except Exception:
            context["run_service_url_tpl"] = ""
            context["open_service_url_tpl"] = ""

        # Per-bucket quota usage so the template can show "X MB / Y MB" next to each bucket name.
        bucket_quota_info = {}
        buckets = list(Bucket.objects.all())
        if buckets:
            from django.db.models import Sum as _Sum
            # all_objects (2026-10-01): trashed bytes still count against
            # the bucket's quota.
            usage_qs = (
                VaultFile.all_objects
                .filter(bucket__in=buckets)
                .values("bucket_id")
                .annotate(used_bytes=_Sum("file_size_bytes"))
            )
            usage_map = {row["bucket_id"]: row["used_bytes"] or 0 for row in usage_qs}
            for b in buckets:
                used_mb = round((usage_map.get(b.pk, 0)) / 1_048_576, 2)
                quota_mb = b.storage_quota_mb
                bucket_quota_info[b.pk] = {
                    "used_mb": used_mb,
                    "quota_mb": quota_mb,
                    "pct": min(round(used_mb / quota_mb * 100) if quota_mb else 0, 100),
                    "over": quota_mb is not None and used_mb > quota_mb,
                }
        context["buckets"] = buckets
        context["bucket_quota_info"] = bucket_quota_info
        # "New file" type pills — only types whose editor is installed on this deployment.
        context["create_file_types"] = available_create_types()

        # Archiving moved to the Archive tab, which is the same tree carrying
        # the zip actions this one deliberately no longer offers. The Alpine
        # state below (zipModal, zipFiles, openZip) is left in place rather than
        # torn out: `zipEnabled` false hides the only control that reaches it,
        # so the modal is unreachable, and removing the state would mean editing
        # a 2000-line component for no behavioural gain.
        context["zip_enabled"] = False
        context["active_tab"] = "files"

        return PageProcessor().decorate(context, self.request)


def _egress_refusal(file_obj):
    """The cap check for bytes OUT, or None to proceed.

    ``storage.egress_mb`` is measured on every Django-served download and
    billed (as a number, never as money) to the file's OWNER — the same
    subject as bytes in and the gb_day levy, and the only subject that
    exists when the downloader is anonymous. Cap-only by design: an
    explicit staff policy refuses with a 429 sentence; without one nothing
    is refused and the events are the record.

    The arrears freeze is deliberately waved through. ``check_quota`` puts
    the freeze before everything, which is right for metered WORK — but a
    download is a read, and the arrears promise is explicit: nothing is
    deleted and nothing refuses to be read.
    """
    from decimal import Decimal as _D

    from toto.quota import InArrears, QuotaExceeded, check_quota

    from .models import VaultQuotaPolicy

    mb = _D(str(file_obj.file_size_bytes or 0)) / _D("1048576")
    try:
        check_quota(VaultQuotaPolicy, "storage.egress_mb", mb, file_obj.owner)
    except InArrears:
        return None
    except QuotaExceeded as exc:
        return HttpResponse(str(exc), status=429,
                            content_type="text/plain; charset=utf-8")
    return None


def _record_egress(file_obj, quantity_mb=None):
    """One served download, on the owner's meter. After the bytes are
    committed to, never before — a 502 serves nothing and records nothing."""
    from decimal import Decimal as _D

    from toto.quota import record_usage

    from .models import VaultUsageEvent

    mb = (_D(str(file_obj.file_size_bytes or 0)) / _D("1048576")
          if quantity_mb is None else quantity_mb)
    if mb > 0:
        record_usage(VaultUsageEvent, "storage.egress_mb", mb,
                     file_obj.owner, unit="MB",
                     source_type="vault.VaultFile",
                     source_id=str(file_obj.pk))


def _file_response_or_bad_gateway(file_obj):
    """Stream a file's bytes, or say plainly why they could not be fetched.

    The one shape every download door uses (this view and the peer API): a
    dead backend or unreachable peer becomes a 502 in plain text naming the
    bucket and the verbatim error — the jess honesty-sentence cascade, never
    a traceback page. Reachability badges elsewhere come only from job
    stamps; this sentence is the per-request truth.

    Egress rides the same choke point: capped before the stream is opened,
    recorded once it is. What nginx serves straight from disk (/media/)
    never reaches this function and is honestly uncounted.
    """
    import os as _os

    refusal = _egress_refusal(file_obj)
    if refusal is not None:
        return refusal
    try:
        stream = _storage_backends.open_file_stream(file_obj)
    except Exception as exc:  # noqa: BLE001 — a dead backend must not traceback
        label = file_obj.bucket.name if file_obj.bucket_id else _("its storage")
        return HttpResponse(
            _("'%(title)s' could not be fetched from %(label)s: "
              "%(error_type)s: %(error)s") % {
                "title": file_obj.title, "label": label,
                "error_type": type(exc).__name__, "error": exc},
            status=502, content_type="text/plain; charset=utf-8")
    _record_egress(file_obj)
    return FileResponse(
        stream,
        as_attachment=True,
        filename=_os.path.basename(file_obj.file.name) or file_obj.key
    )


def stream_file(file_obj, *, inline=False, content_type=None):
    """The vault's one download shape, for another app's door.

    Egress metering, the backend-error sentence and the streaming are
    `_file_response_or_bad_gateway`'s; a caller that has already made its OWN
    access decision (the wiki's image route, say) gets the same bytes the same
    way. ``inline`` serves for display rather than download. Nothing here
    checks access — the caller must have.
    """
    response = _file_response_or_bad_gateway(file_obj)
    if isinstance(response, FileResponse):
        if content_type:
            response["Content-Type"] = content_type
        if inline:
            import os as _os

            name = _os.path.basename(file_obj.file.name) or file_obj.key
            response["Content-Disposition"] = f'inline; filename="{name}"'
        response["X-Content-Type-Options"] = "nosniff"
    return response


class VaultFileDownloadView(View):
    """Download a file, if it is yours to download.

    This is the URL ``VaultFile.get_public_url()`` hands out everywhere, so it is
    the door most of the platform links to.

    **It used to check only whether you were logged in.** The docstring said it
    "respects public/private visibility" and the two branches returned an
    identical ``FileResponse`` — so any authenticated account could fetch any
    private file by guessing a bucket slug and a key, and neither is secret:
    bucket slugs are listed on the metrics pages and a key is the slug of a
    title. It now applies the same five-clause rule ``accessible_files`` applies
    to a listing, from :func:`toto.vault.access.may_read`.

    **404, not 403, on a refusal.** A 403 confirms the file exists, which turns
    this into an oracle for enumerating other people's filenames. The only
    exception is an anonymous request for a private file, which is told to log
    in — there the existence is already implied by the caller having a link.
    """
    def get(self, request, bucket_slug, key):
        file_obj = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory"),
            bucket__slug=bucket_slug,
            key=key
        )

        if not access.may_read(request.user, file_obj):
            if not request.user.is_authenticated:
                return HttpResponseForbidden(
                    _("You must be logged in to access this file."))
            raise Http404("No such file.")

        # Through the bucket driver, not the FieldFile: an s3 or remote
        # bucket's bytes are not on this disk, and before this seam existed a
        # non-local bucket could be copied INTO but never downloaded from.
        return _file_response_or_bad_gateway(file_obj)


@login_required
def file_services(request, file_pk):
    """Which services apply to this file — the vault's own listing.

    fileservices had one of these, but it lives in a wheel most hosts do not
    pin, so on zenobia the wand modal fetched a URL that did not reverse and the
    whole affordance was invisible. The registry moved into
    ``toto.vault.plugins``; this is the read half moving with it.

    Access-checked, because it returns the file's TITLE: unchecked, it reads
    other people's filenames by walking primary keys. 404 rather than 403 for
    the same reason the download view does.

    ``builder`` on each row is what the modal needs to know: a builder service
    redirects to its own page and works anywhere, while a non-builder one needs
    fileservices' run endpoint and is therefore only offerable where that app is
    installed.
    """
    from toto.vault.plugins import FileServicePlugin

    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory"), pk=file_pk)
    if not access.may_read(request.user, vault_file):
        raise Http404("No such file.")

    runnable = apps.is_installed("toto.fileservices")
    services = [
        plugin.to_dict() for plugin in FileServicePlugin.for_file(vault_file)
        if plugin.builder or runnable
    ]
    return JsonResponse({"services": services, "file_title": vault_file.title})


class FileGatewayPageView(LoginRequiredMixin, DetailView):
    model = FileGateway
    template_name = "vault/gateway.html"
    context_object_name = "gateway"

    def get_object(self):
        return get_object_or_404(
            FileGateway.objects.select_related("directory__bucket", "bucket"),
            directory_id=self.kwargs["dir_pk"],
        )

    def get(self, request, *args, **kwargs):
        gateway = self.get_object()
        if (
            not request.user.is_superuser
            and gateway.allowed_users.exists()
            and request.user not in gateway.allowed_users.all()
        ):
            return HttpResponseForbidden(_("You are not allowed to access this gateway"))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gateway = self.get_object()
        user = self.request.user

        all_dirs = list(VaultDirectory.objects.filter(bucket=gateway.bucket))
        dirs_by_pk = {d.pk: d for d in all_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        target_dir = gateway.directory
        target_dir_id_str = self.request.GET.get("target_dir", "").strip()
        if target_dir_id_str:
            try:
                td = VaultDirectory.objects.get(pk=int(target_dir_id_str), bucket=gateway.bucket)
                target_dir = td
            except (VaultDirectory.DoesNotExist, ValueError):
                pass

        context["target_dir_path"] = get_full_path(target_dir)
        context["target_dir_id"] = target_dir.pk

        recent = access.gate_by_bucket(user, VaultFile.objects.filter(
            directory=target_dir, owner=user
        )).select_related("directory").order_by("-uploaded_at")[:10]

        context["recent_uploads_list"] = [
            {
                "title": f.title,
                "file_type": f.file_type,
                "location": get_full_path(f.directory) if f.directory else "—",
                "uploaded": f.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "public": f.is_public,
            }
            for f in recent
        ]

        return PageProcessor().decorate(context, self.request)


class FileGatewayUploadView(LoginRequiredMixin, View):
    """
    Handle uploads to a bucket through a gateway.
    """
    def post(self, request, dir_pk):
        gateway = get_object_or_404(
            FileGateway.objects.select_related("directory", "bucket"),
            directory_id=dir_pk,
        )

        if (
            not request.user.is_superuser
            and gateway.allowed_users.exists()
            and request.user not in gateway.allowed_users.all()
        ):
            return JsonResponse({"error": _("You are not allowed to use this gateway")}, status=403)

        uploaded_files = request.FILES.getlist("file")
        if not uploaded_files:
            return JsonResponse({"error": _("No file uploaded")}, status=400)

        target_directory_id = request.POST.get("target_directory_id", "").strip()
        if target_directory_id:
            try:
                directory = VaultDirectory.objects.get(pk=int(target_directory_id), bucket=gateway.bucket)
            except (VaultDirectory.DoesNotExist, ValueError):
                return JsonResponse({"error": _("Invalid target directory.")}, status=400)
        else:
            directory = gateway.directory

        valid_types = {code for code, _ in VaultFile.FILE_TYPES}
        manual_type = request.POST.get("file_type", "").strip()
        max_bytes = gateway.max_file_size * 1024

        if directory:
            all_dirs = list(VaultDirectory.objects.filter(bucket=gateway.bucket))
            dirs_by_pk = {d.pk: d for d in all_dirs}

            def get_full_path(d):
                parts = []
                node = d
                while node is not None:
                    parts.append(node.name)
                    node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
                return "/".join(reversed(parts))

            location = get_full_path(directory)
        else:
            location = _("Root")

        from toto.quota import InArrears, QuotaExceeded, check_quota, record_usage as _ru
        from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
        from toto.vault import scanning as _scanning
        from toto.vault.models import VaultQuotaPolicy, VaultUsageEvent

        # One rate-card lookup for the whole batch; None when nothing is priced
        # or the host carries no billing at all.
        tariff = price_for(request.user, "vault") if request.user.is_authenticated else None

        results, errors = [], []
        for uploaded_file in uploaded_files:
            # ── Per-file size limit ──────────────────────────────────────────
            if uploaded_file.size > max_bytes:
                errors.append(
                    _("%(name)s: too large (%(size).1f MB; max %(max).1f MB).") % {
                        "name": uploaded_file.name,
                        "size": uploaded_file.size / (1024*1024),
                        "max": gateway.max_file_size / 1024}
                )
                continue

            # ── Per-file quota and funding checks ────────────────────────────
            # Both degrade into this file's error entry rather than failing the
            # batch, so one over-limit file does not lose the others.
            if request.user.is_authenticated:
                _size_mb = Decimal(str(uploaded_file.size)) / Decimal("1048576")
                try:
                    check_quota(VaultQuotaPolicy, "storage.request", 1, request.user)
                    check_quota(VaultQuotaPolicy, "storage.transfer_mb", _size_mb, request.user)
                    check_funds(request.user, tariff, "storage.request", 1)
                    check_funds(request.user, tariff, "storage.transfer_mb", _size_mb)
                except (QuotaExceeded, InArrears, InsufficientFunds) as _exc:
                    errors.append(f"{uploaded_file.name}: {_exc}")
                    continue

            try:
                mime = mimetypes.guess_type(uploaded_file.name)[0]
                auto_file_type = VaultFile.detect_type(mime or "", uploaded_file.name)
                file_type = manual_type if manual_type in valid_types else auto_file_type

                from toto.vault.models import upload_refusal
                refusal = upload_refusal(uploaded_file.name, file_type=file_type,
                                         content=uploaded_file, mime=mime or "")
                if refusal:
                    errors.append(f"{uploaded_file.name}: {refusal}")
                    continue

                # Screen before the row is made. `is_scannable` first so a 200 MB
                # video is never read into memory just to be told nobody screens
                # it — the façade would answer that anyway, but not before the
                # read.
                verdict = _scanning.Verdict.clean(scanned=False)
                if _scanning.should_scan(request.user, file_type,
                                         door="gateway"):
                    _body = uploaded_file.read()
                    uploaded_file.seek(0)
                    verdict = _scanning.scan(_body, file_type=file_type,
                                             filename=uploaded_file.name)
                    if not verdict.ok:
                        # This file's error entry, not the batch's — same rule
                        # the size and quota checks above already follow.
                        errors.append(
                            _("%(name)s: refused (%(reason)s).") % {
                                "name": uploaded_file.name,
                                "reason": verdict.reason + (
                                    ": " + verdict.detail
                                    if verdict.detail else "")})
                        continue

                vault_file = VaultFile(
                    owner=request.user,
                    title=uploaded_file.name,
                    # Assign a bucket-unique key up front so a colliding filename
                    # (re-upload, or two batch files slugging to the same key) gets
                    # suffixed -1/-2 instead of raising ValueError in save() — which
                    # would otherwise 500 the whole request with an HTML page and
                    # break the client's JSON parsing.
                    key=_unique_file_key(slugify(os.path.splitext(uploaded_file.name)[0]), gateway.bucket),
                    file_type=file_type,
                    bucket=gateway.bucket,
                    directory=directory,
                    is_public=gateway.make_public,
                )
                _storage_backends.persist_upload(vault_file, uploaded_file)
                _scanning.record(vault_file, verdict, user=request.user,
                                 door="gateway")
            except Exception as _exc:  # noqa: BLE001 — one bad file mustn't 500 the batch
                errors.append(f"{uploaded_file.name}: {_exc}")
                continue

            # ── Record usage and bill for it ─────────────────────────────────
            # The file exists by now, so its pk makes both the idempotency key
            # and the charge reference stable across a retried request.
            if request.user.is_authenticated:
                _src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk)}
                _ru(VaultUsageEvent, "storage.request", 1, request.user,
                    idempotency_key=f"vault.upload.request:{vault_file.pk}", **_src)
                _size_mb = Decimal(str(vault_file.file_size_bytes or uploaded_file.size)) / Decimal("1048576")
                if _size_mb > 0:
                    _ru(VaultUsageEvent, "storage.transfer_mb", _size_mb, request.user,
                        unit="MB",
                        idempotency_key=f"vault.upload.transfer:{vault_file.pk}", **_src)

                charge(request.user, tariff, "storage.request", 1, **_src)
                if _size_mb > 0:
                    charge(request.user, tariff, "storage.transfer_mb", _size_mb,
                           unit="MB", **_src)

            results.append({
                "title": vault_file.title,
                "key": vault_file.key,
                "bucket": gateway.bucket.slug,
                "file_type": vault_file.file_type,
                "location": location,
                "public": vault_file.is_public,
                "public_url": vault_file.get_public_url(),
                "size": f"{uploaded_file.size / (1024*1024):.2f} MB",
            })

        # All files failed (e.g. every one over the limit) → surface as an error.
        if not results:
            return JsonResponse({"results": [], "errors": errors}, status=400)
        return JsonResponse({"results": results, "errors": errors})


# ============================================================
# Metrics / Statistics
# ============================================================

class VaultMetricsView(LoginRequiredMixin, TemplateView):
    """
    Aggregate statistics and charts for the vault: file counts by type,
    bucket breakdowns, upload activity over the last 30 days.
    """
    template_name = "vault/metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        total_buckets = Bucket.objects.count()
        total_dirs = VaultDirectory.objects.count()
        total_files = VaultFile.objects.count()
        public_files = VaultFile.objects.filter(is_public=True).count()
        encrypted_files = VaultFile.objects.filter(is_encrypted=True).count()
        week_ago = timezone.now() - timedelta(days=7)
        recent_count = VaultFile.objects.filter(uploaded_at__gte=week_ago).count()

        context.update({
            "total_buckets": total_buckets,
            "total_dirs": total_dirs,
            "total_files": total_files,
            "public_files": public_files,
            "encrypted_files": encrypted_files,
            "recent_count": recent_count,
        })

        context["files_by_type"] = list(
            VaultFile.objects.values("file_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        context["files_by_bucket"] = list(
            VaultFile.objects.filter(bucket__isnull=False)
            .values("bucket__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:12]
        )

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_qs = {
            entry["day"]: entry["count"]
            for entry in VaultFile.objects.filter(uploaded_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("uploaded_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        today = date.today()
        context["daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": daily_qs.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        # Counts are live files (2026-10-01): a reverse join does not go
        # through the manager that hides the trash, so it says so here.
        live = Q(files__trashed_at__isnull=True)
        context["bucket_stats"] = list(
            Bucket.objects.annotate(
                file_count=Count("files", filter=live, distinct=True),
                dir_count=Count("directories", distinct=True),
                public_count=Count("files", filter=live & Q(files__is_public=True),
                                   distinct=True),
                encrypted_count=Count("files", filter=live & Q(files__is_encrypted=True),
                                      distinct=True),
            ).select_related("owner").order_by("name")
        )
        context["gateway_bucket_pks"] = set(
            FileGateway.objects.values_list("bucket_id", flat=True)
        )

        context["recent_files"] = VaultFile.objects.select_related(
            "owner", "bucket", "directory"
        ).order_by("-uploaded_at")[:8]

        # Copy flow data
        from django.db.models import Max
        context["copy_flows"] = list(
            BucketCopyLog.objects
            .filter(from_bucket__isnull=False, to_bucket__isnull=False)
            .values("from_bucket__name", "from_bucket__slug", "to_bucket__name", "to_bucket__slug")
            .annotate(total_files=Sum("file_count"), last_copy=Max("performed_at"))
            .order_by("-total_files")[:20]
        )

        thirty_days_ago_dt = timezone.now() - timedelta(days=29)
        copy_daily_qs = {
            entry["day"]: entry["count"]
            for entry in BucketCopyLog.objects
            .filter(performed_at__gte=thirty_days_ago_dt)
            .annotate(day=TruncDate("performed_at"))
            .values("day")
            .annotate(count=Sum("file_count"))
        }
        context["copy_daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": copy_daily_qs.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        from toto.quota import usage_summary
        from toto.vault.models import VaultQuotaPolicy
        context["quota_data"] = usage_summary(VaultQuotaPolicy, self.request.user)

        context["active_tab"] = "metrics"
        return PageProcessor().decorate(context, self.request)


class BucketMetricsView(LoginRequiredMixin, TemplateView):
    """
    Per-bucket statistics: file type breakdown, directory breakdown,
    upload activity, and recent files.
    """
    template_name = "vault/bucket_metrics.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        bucket = get_object_or_404(
            Bucket.objects.select_related("peer", "provider"),
            slug=self.kwargs["bucket_slug"])
        # Owner-or-superuser, 404 otherwise. This page lists every member's
        # usage, per-directory locks and gateway doors — any authenticated
        # account could read any bucket's whole shape by guessing a slug,
        # and slugs are not secrets. 404, not 403: a refusal that confirms
        # the bucket exists is an enumeration oracle.
        if not (bucket.owner_id == self.request.user.pk
                or self.request.user.is_superuser):
            raise Http404("No such bucket.")
        # A bucket kept to clearances (2026-09-30) keeps its files from whoever
        # holds none of them — its owner included: to them its files are
        # missing, so every list and count below comes out empty.
        from toto.socialhub.clearance_access import group_hidden

        files_hidden = group_hidden(self.request.user, Bucket.objects.filter(pk=bucket.pk))
        bucket_files = (VaultFile.objects.none() if files_hidden
                        else VaultFile.objects.filter(bucket=bucket))
        copy_files_qs = bucket_files.filter(owner=self.request.user).order_by("title")
        context["copy_files_data"] = [
            {"id": str(f.pk), "title": f.title, "file_type": f.file_type, "key": f.key or ""}
            for f in copy_files_qs
        ]
        context["dest_buckets"] = list(
            Bucket.objects.filter(owner=self.request.user, deletion_requested_at__isnull=True)
            .exclude(pk=bucket.pk).order_by("name")
        )

        total_files = bucket_files.count()
        total_dirs = VaultDirectory.objects.filter(bucket=bucket).count()
        public_files = bucket_files.filter(is_public=True).count()
        encrypted_files = bucket_files.filter(is_encrypted=True).count()
        root_files = bucket_files.filter(directory__isnull=True).count()
        week_ago = timezone.now() - timedelta(days=7)
        recent_count = bucket_files.filter(uploaded_at__gte=week_ago).count()

        gateways = list(bucket.gateways.select_related("directory").all())

        # Through the façade, never an import: None on a host with no
        # antivirus, and the card simply does not render — the honest state,
        # not a card full of zeroes about a scanner that does not exist.
        from toto.vault import scanning

        context.update({
            "bucket": bucket,
            "gateways": gateways,
            "total_files": total_files,
            "total_dirs": total_dirs,
            "public_files": public_files,
            "encrypted_files": encrypted_files,
            "root_files": root_files,
            "recent_count": recent_count,
            # None for a mounted bucket, same as a host with no antivirus:
            # this host never scanned the peer's bytes, and a card of zeroes
            # would claim it had.
            "antivirus_report": (
                None if bucket.storage_backend == StorageBackend.REMOTE_TOTO
                else scanning.health_report(
                    bucket_files)),
            "remote_info": self._remote_info(bucket),
        })

        context["files_by_type"] = list(
            bucket_files
            .values("file_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

        raw_by_dir = list(
            bucket_files
            .values("directory__name")
            .annotate(count=Count("id"))
            .order_by("-count")[:12]
        )
        context["files_by_dir"] = [
            {"name": (d["directory__name"] or "Root"), "count": d["count"]}
            for d in raw_by_dir
        ]

        thirty_days_ago = timezone.now() - timedelta(days=29)
        daily_qs = {
            entry["day"]: entry["count"]
            for entry in bucket_files.filter(uploaded_at__gte=thirty_days_ago)
            .annotate(day=TruncDate("uploaded_at"))
            .values("day")
            .annotate(count=Count("id"))
        }
        today = date.today()
        context["daily_series"] = [
            {
                "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
                "count": daily_qs.get(today - timedelta(days=29 - i), 0),
            }
            for i in range(30)
        ]

        all_bucket_dirs = list(
            VaultDirectory.objects.filter(bucket=bucket)
            .prefetch_related("allowed_users")
            .annotate(
                file_count=Count("files", distinct=True),
                public_count=Count("files", filter=Q(files__is_public=True), distinct=True),
                encrypted_count=Count("files", filter=Q(files__is_encrypted=True), distinct=True),
            )
        )
        dirs_by_pk = {d.pk: d for d in all_bucket_dirs}

        def get_full_path(d):
            parts = []
            node = d
            while node is not None:
                parts.append(node.name)
                node = dirs_by_pk.get(node.parent_id) if node.parent_id else None
            return "/".join(reversed(parts))

        context["dir_stats"] = sorted(
            [
                {
                    "pk": d.pk,
                    "full_path": get_full_path(d),
                    "file_count": 0 if files_hidden else d.file_count,
                    "public_count": 0 if files_hidden else d.public_count,
                    "encrypted_count": 0 if files_hidden else d.encrypted_count,
                    "locked": d.allowed_users.exists(),
                }
                for d in all_bucket_dirs
            ],
            key=lambda x: x["full_path"],
        )

        quota_mb = bucket.storage_quota_mb
        # Usage against the quota counts the trash (2026-10-01): its bytes
        # are still held.
        quota_files = (VaultFile.all_objects.none() if files_hidden
                       else VaultFile.all_objects.filter(bucket=bucket))
        raw_user_stats = list(
            quota_files
            .values("owner__id", "owner__username")
            .annotate(file_count=Count("id"), total_bytes=Sum("file_size_bytes"))
            .order_by("-total_bytes")
        )
        bucket_total_bytes = sum((row["total_bytes"] or 0) for row in raw_user_stats)
        bucket_total_mb = round(bucket_total_bytes / 1_048_576, 2)

        user_quota_rows = []
        for row in raw_user_stats:
            used_mb = round((row["total_bytes"] or 0) / 1_048_576, 2)
            pct = min(round(used_mb / quota_mb * 100) if quota_mb else 0, 100)
            user_quota_rows.append({
                "username": row["owner__username"],
                "file_count": row["file_count"],
                "used_mb": used_mb,
                "quota_mb": quota_mb,
                "pct": pct,
                "over": quota_mb is not None and used_mb > quota_mb,
            })
        context["user_quota_rows"] = user_quota_rows
        context["bucket_quota_mb"] = quota_mb
        context["bucket_total_mb"] = bucket_total_mb

        context["recent_files"] = bucket_files.select_related(
            "owner", "directory"
        ).order_by("-uploaded_at")[:8]

        context["service_stats"] = self._service_stats(bucket)

        from . import clearances

        context.update(clearances.page_context(self.request.user, bucket))

        return PageProcessor().decorate(context, self.request)

    @staticmethod
    def _remote_info(bucket):
        """Delegates to :func:`toto.vault.remote_status.peer_info`.

        Kept as a staticmethod so this page's callers and its tests are
        unchanged; the body moved so the Remote listing shares it.
        """
        from . import remote_status

        return remote_status.peer_info(bucket)

    def _service_stats(self, bucket):
        """Per-service run counts + success/failure for this bucket's files."""
        try:
            from toto.fileservices.models import FileServiceRun
            from toto.fileservices.plugin import FileServicePlugin
        except Exception:
            return []

        rows = (
            FileServiceRun.objects.filter(bucket=bucket)
            .values("service_key", "status")
            .annotate(n=Count("id"))
        )
        agg = {}
        for r in rows:
            entry = agg.setdefault(r["service_key"], {"total": 0, "success": 0, "failed": 0, "outputs": 0})
            entry["total"] += r["n"]
            if r["status"] == FileServiceRun.SUCCESS:
                entry["success"] += r["n"]
            elif r["status"] == FileServiceRun.FAILED:
                entry["failed"] += r["n"]

        # Count produced output files per service.
        for run in FileServiceRun.objects.filter(bucket=bucket).only("service_key", "output_file_pks"):
            if run.service_key in agg:
                agg[run.service_key]["outputs"] += len(run.output_file_pks or [])

        result = []
        for key, data in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            plugin = FileServicePlugin.get(key)
            result.append({
                "key": key,
                "title": plugin.get_title() if plugin else key,
                "icon": plugin.icon if plugin else "fa-solid fa-wand-magic-sparkles",
                **data,
            })
        return result


# ============================================================
# Copy Files
# ============================================================

def _unique_file_key(base_key, target_bucket):
    """A bucket-unique key for a new file: ``base_key`` (or ``file`` when empty),
    suffixed -1, -2, … until free. Avoids the ValueError VaultFile.save() raises when
    a slugified key already exists (different titles can slugify to the same key)."""
    base_key = base_key or "file"
    key = base_key
    counter = 1
    while VaultFile.objects.filter(bucket=target_bucket, key=key).exists():
        key = f"{base_key}-{counter}"
        counter += 1
    return key


def _unique_copy_key(source_file, target_bucket):
    return _unique_file_key(source_file.key or slugify(source_file.title), target_bucket)


def _delegate_to_transfer(request, source_bucket, destination_bucket,
                          dest_directory, selected_files, copy_policy):
    """Queue a TransferRun for a copy with a non-local endpoint.

    The synchronous loop stays for local→local; the moment either end is S3
    or a peer, the copy becomes billed, network-bound work with a run row
    and a poll. Affordability is checked BEFORE the row exists — nobody
    occupies a worker they cannot pay for — against the frozen size
    estimate; real bytes are billed per landed file by the runner.
    """
    from decimal import Decimal as _D

    from toto.quota import InArrears, QuotaExceeded, check_quota
    from toto.quota.charge import InsufficientFunds, check_funds, price_for

    from . import transfer_dispatch
    from .models import VaultQuotaPolicy

    est_bytes = sum(f.file_size_bytes or 0 for f in selected_files)
    est_mb = _D(str(est_bytes)) / _D("1048576")
    tariff = price_for(request.user, "vault")
    try:
        check_quota(VaultQuotaPolicy, "storage.transfer_mb", est_mb,
                    request.user)
        check_funds(request.user, tariff, "storage.transfer_mb", est_mb)
    except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
        return JsonResponse({"ok": False, "error": str(exc)},
                            status=getattr(exc, "status_code", 402))

    run = transfer_dispatch.create_transfer_run(
        user=request.user, source_bucket=source_bucket,
        dest_bucket=destination_bucket, dest_directory=dest_directory,
        files=selected_files, copy_policy=copy_policy)
    try:
        transfer_dispatch.dispatch_transfer_run(run)
    except transfer_dispatch.CannotQueue as exc:
        transfer_dispatch.fail_transfer_run(run, str(exc))
        return JsonResponse(
            {"ok": False, "error": str(exc), "run_id": run.pk}, status=503)
    return JsonResponse({
        "ok": True,
        "async": True,
        "run_id": run.pk,
        "status_url": reverse("vault:transfer_status", args=[run.pk]),
    })


def create_empty_vault_file(owner, bucket, directory, title, file_type, content=None):
    """Create + persist a vault file. Caller validates ownership and the type.

    Pre-assigns a bucket-unique key (see _unique_file_key) so colliding slugs
    don't raise.

    ``content`` is the file's actual bytes when the caller already has them —
    the desktop client pushing a document it wrote offline. Without it the file
    is seeded with the type's starter content, which is the New-file case and
    the reason `_INITIAL` gates that path: a type is "creatable" when we know
    what an empty one looks like. A caller supplying content needs no such
    answer, so it may create any editable type.
    """
    from django.core.files.base import ContentFile
    if content is None:
        # The OWNING APP first, then the vault's own stub. A rich format — a
        # workbook, a deck, a document, a drawing — is never an empty file, and
        # only the app that reads it knows what an empty one looks like; asking
        # it here rather than in the view means every caller of this function
        # gets the same answer. See VaultEditorPlugin.blank_content.
        #
        # The order used to be the other way round, which made `blank_content`
        # unreachable for any type the vault also had a stub for. `svg` is that
        # type: the vault's stub is a 100x100 board, on which sketch's default
        # stroke widths (2–12) and text sizes (16–56) are absurd, so every new
        # drawing opened broken. The `or None` guard is load-bearing — the base
        # `blank_content` returns "", and "" is a legitimate blank for text,
        # yaml, bib and csv, so without it every plugin-having type would be
        # seeded empty.
        from toto.vault.plugins import VaultEditorPlugin

        plugin = VaultEditorPlugin.for_file_type(file_type)
        content = (plugin.blank_content(title) or None) if plugin else None
    if content is None:
        content = CreateEmptyFileView._INITIAL.get(file_type)
    if content is None:
        raise ValueError(f"nothing knows what an empty {file_type} looks like")
    vault_file = VaultFile(
        owner=owner,
        title=title,
        key=_unique_file_key(slugify(title), bucket),
        file_type=file_type,
        bucket=bucket,
        directory=directory,
        is_public=False,
    )
    vault_file.save()
    vault_file.file.save(title, ContentFile(content.encode("utf-8")), save=True)
    vault_file.content_hash = vault_file.create_hash()
    vault_file.save()
    return vault_file


def resolve_new_file_target(user, bucket_id=None, directory_id=None):
    """Resolve the (bucket, directory) a user-chosen new file should land in.

    - Blank/None ``bucket_id`` -> the user's personal bucket (auto-created), root.
    - Otherwise the bucket must be owned by ``user`` (Http404 on miss).
    - Blank/None ``directory_id`` -> bucket root; otherwise the directory must
      belong to the resolved bucket (Http404 on miss).

    Enforces the same ownership rules as vault.api_views.FileCreateApiView so no
    app can create a file inside another user's bucket/folder.
    """
    if bucket_id in (None, "", 0, "0"):
        # models.personal_bucket: never hands over a personal-<username>
        # bucket that lost its owner (an old account's) or changed hands.
        from .models import personal_bucket

        bucket = personal_bucket(user)
    else:
        # A bucket being deleted takes nothing new: it is not a target.
        bucket = get_object_or_404(Bucket, pk=bucket_id, owner=user,
                                   deletion_requested_at__isnull=True)

    directory = None
    if directory_id not in (None, "", 0, "0"):
        directory = get_object_or_404(VaultDirectory, pk=directory_id, bucket=bucket)
    return bucket, directory


def new_file_picker_json(user):
    """(buckets_json, directories_json) for a bucket+directory "save-as" picker,
    scoped to the buckets/folders ``user`` may write to (their own). JSON shapes
    match the weather export modal: ``{id,name}`` and ``{id,bucket_id,path}``.
    Returns two empty-list JSON strings for anonymous users."""
    if not getattr(user, "is_authenticated", False):
        return "[]", "[]"
    buckets = Bucket.objects.filter(owner=user, deletion_requested_at__isnull=True).order_by("name")
    directories = (
        VaultDirectory.objects.filter(owner=user, bucket__deletion_requested_at__isnull=True)
        .select_related("bucket")
        .order_by("bucket__name", "name")
    )
    buckets_json = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
    directories_json = json.dumps(
        [{"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()} for d in directories]
    )
    return buckets_json, directories_json


def _office_titles(files) -> str:
    """'' or the refusal naming the Office files among ``files``.

    A copy between two LOCAL buckets does not go through the transfer runner,
    which skips an Office row from before the rule (2026-09-30); without this
    the same row walked into another bucket here (2026-10-01).
    """
    from .models import is_office_file, office_refusal_sentence

    names = [f.title or f.key for f in files if is_office_file(f.title or "")
             or is_office_file(f.key or "")]
    if not names:
        return ""
    return "%s: %s" % (", ".join(names), office_refusal_sentence())


class CopyFilesToBucketView(LoginRequiredMixin, View):
    template_name = "vault/copy_files.html"

    def _source_bucket(self, request, source_slug):
        return get_object_or_404(Bucket, slug=source_slug, owner=request.user)

    @staticmethod
    def _build_source_tree(request, source_bucket):
        dirs = list(
            VaultDirectory.objects.filter(bucket=source_bucket)
            .prefetch_related("allowed_users")
            .order_by("name")
        )
        files = list(
            access.gate_by_bucket(request.user, VaultFile.objects.filter(
                bucket=source_bucket, owner=request.user)).order_by("title")
        )
        by_parent = {}
        for d in dirs:
            by_parent.setdefault(d.parent_id, []).append(d)
        files_by_dir = {}
        for f in files:
            files_by_dir.setdefault(f.directory_id, []).append(f)
        accessible_pks = {d.pk for d in dirs}
        flat = []

        def _file_item(f, depth):
            return {
                "t": "file", "id": str(f.pk),
                "pid": str(f.directory_id) if f.directory_id else None,
                "depth": depth, "title": f.title, "file_type": f.file_type, "key": f.key,
            }

        def visit(parent_pk, depth):
            for d in sorted(by_parent.get(parent_pk, []), key=lambda x: x.name):
                n_files = len(files_by_dir.get(d.pk, []))
                n_dirs = sum(1 for c in by_parent.get(d.pk, []) if c.pk in accessible_pks)
                flat.append({
                    "t": "dir", "id": str(d.pk),
                    "pid": str(d.parent_id) if d.parent_id else None,
                    "depth": depth, "name": d.name,
                    "n_files": n_files, "n_dirs": n_dirs,
                })
                visit(d.pk, depth + 1)
                for f in sorted(files_by_dir.get(d.pk, []), key=lambda x: x.title):
                    flat.append(_file_item(f, depth + 1))

        visit(None, 0)
        for f in sorted(files_by_dir.get(None, []), key=lambda x: x.title):
            flat.append(_file_item(f, 0))
        return flat

    @staticmethod
    def _build_dest_tree(request, source_bucket):
        buckets = list(
            Bucket.objects.filter(owner=request.user, deletion_requested_at__isnull=True)
            .exclude(pk=source_bucket.pk).order_by("name")
        )
        bucket_pks = [b.pk for b in buckets]
        all_dirs = list(VaultDirectory.objects.filter(bucket__in=bucket_pks).order_by("name"))
        dirs_by_bucket = {}
        for d in all_dirs:
            dirs_by_bucket.setdefault(d.bucket_id, []).append(d)
        by_parent_bucket = {}
        for d in all_dirs:
            by_parent_bucket.setdefault((d.parent_id, d.bucket_id), []).append(d)
        flat = []

        def visit_dest(parent_id, depth, bucket_pk):
            pid_val = str(parent_id) if parent_id else f"b{bucket_pk}"
            for d in sorted(by_parent_bucket.get((parent_id, bucket_pk), []), key=lambda x: x.name):
                flat.append({
                    "t": "dir", "id": str(d.pk), "bpk": d.bucket_id,
                    "pid": pid_val, "depth": depth, "name": d.name,
                })
                visit_dest(d.pk, depth + 1, bucket_pk)

        for bucket in buckets:
            flat.append({
                "t": "bucket_root", "id": f"b{bucket.pk}", "bpk": bucket.pk,
                "pid": None, "depth": 0, "name": bucket.name,
                "n_dirs": len(dirs_by_bucket.get(bucket.pk, [])),
                # The badge text, computed here because this tree is flat
                # dicts — templates never parse storage_config.
                "remote_label": bucket.remote_label,
            })
            visit_dest(None, 1, bucket.pk)
        return flat

    def _build_context(self, request, source_bucket, form=None):
        from .forms import CopyFilesForm
        context = {
            "source_bucket": source_bucket,
            "form": form or CopyFilesForm(request.user, source_bucket),
            "source_items": self._build_source_tree(request, source_bucket),
            "dest_items": self._build_dest_tree(request, source_bucket),
        }
        return PageProcessor().decorate(context, request)

    def get(self, request, source_slug):
        source_bucket = self._source_bucket(request, source_slug)
        return render(request, self.template_name, self._build_context(request, source_bucket))

    def post(self, request, source_slug):
        from .forms import CopyFilesForm
        source_bucket = self._source_bucket(request, source_slug)
        form = CopyFilesForm(request.user, source_bucket, request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self._build_context(request, source_bucket, form))

        destination_bucket = form.cleaned_data["destination_bucket"]
        selected_files = list(form.cleaned_data["files"])

        dest_dir_id = request.POST.get("destination_directory", "").strip()
        destination_directory = None
        if dest_dir_id:
            try:
                destination_directory = VaultDirectory.objects.get(
                    pk=dest_dir_id, bucket=destination_bucket
                )
            except VaultDirectory.DoesNotExist:
                form.add_error(None, _("Invalid destination directory."))
                return render(request, self.template_name, self._build_context(request, source_bucket, form))

        copy_policy = request.POST.get("copy_policy", "add_suffix")
        if copy_policy not in ("replace", "fail", "add_suffix"):
            copy_policy = "add_suffix"

        if not (source_bucket.is_local and destination_bucket.is_local):
            resp = _delegate_to_transfer(
                request, source_bucket, destination_bucket,
                destination_directory, selected_files, copy_policy)
            data = json.loads(resp.content)
            if data.get("ok"):
                messages.success(
                    request,
                    _("Transfer queued — it continues in the background."))
                return redirect("vault:transfer_detail", pk=data["run_id"])
            form.add_error(None, data.get("error",
                                          _("Could not queue the transfer.")))
            return render(request, self.template_name,
                          self._build_context(request, source_bucket, form))

        if copy_policy == "fail":
            conflicts = [
                f.key for f in selected_files
                if VaultFile.objects.filter(bucket=destination_bucket, key=f.key).exists()
            ]
            if conflicts:
                preview = ", ".join(f'"{k}"' for k in conflicts[:5])
                if len(conflicts) > 5:
                    preview += _(" … (+%(count)s more)") % {
                        "count": len(conflicts) - 5}
                form.add_error(None, _("Key conflict(s): %(keys)s") % {
                    "keys": preview})
                return render(request, self.template_name, self._build_context(request, source_bucket, form))

        office = _office_titles(selected_files)
        if office:
            form.add_error(None, office)
            return render(request, self.template_name, self._build_context(request, source_bucket, form))

        src_driver = get_bucket_storage(source_bucket)
        dst_driver = get_bucket_storage(destination_bucket)

        with transaction.atomic():
            for source_file in selected_files:
                if copy_policy == "replace":
                    VaultFile.objects.filter(bucket=destination_bucket, key=source_file.key).delete()
                    key = source_file.key
                elif copy_policy == "fail":
                    key = source_file.key
                else:
                    key = _unique_copy_key(source_file, destination_bucket)

                content = src_driver.read(source_file.file.name)
                stored_name = dst_driver.save(source_file.file.name, content)

                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=len(content),
                    bucket=destination_bucket,
                    directory=destination_directory,
                )
                new_file.file = stored_name
                new_file.save()

        count = len(selected_files)
        BucketCopyLog.objects.create(
            from_bucket=source_bucket,
            to_bucket=destination_bucket,
            performed_by=request.user,
            file_count=count,
        )
        messages.success(
            request,
            ngettext("Copied %(count)s file to \"%(bucket)s\".",
                     "Copied %(count)s files to \"%(bucket)s\".",
                     count) % {"count": count,
                               "bucket": destination_bucket.name},
        )
        return redirect("vault:bucket_metrics", bucket_slug=destination_bucket.slug)


class EncryptFileView(LoginRequiredMixin, View):
    @staticmethod
    def _ensure_workflow():
        """Get-or-create the single-node 'vault-encrypt' workflow so encryption works
        even if ingress hasn't (re)seeded it on this deployment (mirrors CreateZipView)."""
        from toto.workflows.models import Workflow, WorkflowNode
        wf, created = Workflow.objects.get_or_create(
            slug="vault-encrypt",
            defaults={
                "name": "Encrypt file",
                "description": "Encrypt a vault file at rest. The password is supplied "
                               "out-of-band and never stored on the run.",
            },
        )
        if created or not wf.nodes.filter(task_name="vault_encrypt_file").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Encrypt file",
                task_name="vault_encrypt_file",
                position_x=0,
                position_y=0,
            )
        return wf

    def post(self, request):
        
        file_pk = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        owner_password = request.POST.get("owner_password", "").strip() or None
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": _("Missing required fields.")}, status=400)
        vault_file = get_object_or_404(access.gate_by_bucket(
            request.user, VaultFile.objects.filter(owner=request.user)), pk=file_pk)
        if not access.is_local_content(vault_file):
            return access.remote_lock_response(request, vault_file)
        if vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": _("File is already encrypted.")}, status=400)

        # Offload to a 'vault-encrypt' workflow run when the engine is installed
        # (faros + portal): encryption does an S3 download → crypto → re-upload that
        # can run for minutes, and a long synchronous request is dropped by Tor. The
        # password rides only as a transient Celery arg — it is NEVER written to the
        # run's input_data. The browser polls EncryptStatusView with the run id.
        # Ownership was just verified, so the task may trust file_pk.
        if getattr(settings, "VAULT_ENCRYPT_ASYNC", False) and apps.is_installed("toto.workflows"):
            run = None
            use_celery = False
            try:
                from toto.celery_utils import celery_available
                from toto.workflows.models import WorkflowRun

                wf = self._ensure_workflow()
                run = WorkflowRun.objects.create(
                    workflow=wf,
                    input_data={"data": {"file_pk": vault_file.pk, "owner_id": request.user.id}},
                    started_by=request.user,
                )
                use_celery = celery_available()
            except Exception:  # noqa: BLE001 — engine/db issue → fall back to synchronous
                run = None
            if run is not None:
                from .tasks import encrypt_workflow_run
                try:
                    run_url = reverse("workflows:workflow_run_detail", args=[run.id])
                except Exception:
                    run_url = ""
                if use_celery:
                    try:
                        encrypt_workflow_run.delay(run.id, password, owner_password)
                    except Exception:  # noqa: BLE001 — broker down after all → run inline
                        encrypt_workflow_run(run.id, password, owner_password)
                else:
                    # No worker (dev/runserver): run inline; it self-handles errors.
                    encrypt_workflow_run(run.id, password, owner_password)
                return JsonResponse({
                    "ok": True, "async": True,
                    "workflow_run_id": run.id, "workflow_run_url": run_url,
                })

        try:
            vault_file.encrypt(password=password, owner_password=owner_password)
            vault_file.is_public = False
            vault_file.save(update_fields=["is_public"])
        except Exception as e:
            msg = str(e)
            if "EOF marker not found" in msg or "PdfRead" in type(e).__name__:
                msg = _("File does not appear to be a valid PDF.")
            return JsonResponse({"ok": False, "error": msg}, status=500)
        return JsonResponse({"ok": True, "raw_url": vault_file.get_public_url() or ""})


class EncryptStatusView(LoginRequiredMixin, View):
    """Poll a 'vault-encrypt' workflow run dispatched by EncryptFileView.

    Returns ``{status, is_terminal, done}`` plus ``{ok, raw_url, vault_file_id}`` /
    ``{ok, error}`` once terminal. The run is owner-bound via its input_data, and the
    result holds only the file's (already public) raw URL.
    """

    def get(self, request):
        run_id = request.GET.get("run_id", "").strip()
        if not run_id:
            return JsonResponse({"ok": False, "error": _("Missing run_id.")}, status=400)
        from toto.workflows.models import WorkflowRun

        run = get_object_or_404(WorkflowRun, pk=run_id)
        owner_id = ((run.input_data or {}).get("data") or {}).get("owner_id")
        if owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"ok": False, "error": _("Not found.")}, status=404)

        out = run.output_data or {}
        is_terminal = run.status in (WorkflowRun.COMPLETED, WorkflowRun.FAILED)
        payload = {"status": run.status, "is_terminal": is_terminal, "done": is_terminal}
        if run.status == WorkflowRun.COMPLETED:
            payload.update({"ok": True, "raw_url": out.get("raw_url", ""),
                            "vault_file_id": out.get("vault_file_id")})
        elif run.status == WorkflowRun.FAILED:
            payload.update({"ok": False, "error": out.get("error", _("Encryption failed."))})
        return JsonResponse(payload)


class DecryptFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": _("Missing required fields.")}, status=400)
        vault_file = get_object_or_404(access.gate_by_bucket(
            request.user, VaultFile.objects.filter(owner=request.user)), pk=file_pk)
        if not access.is_local_content(vault_file):
            return access.remote_lock_response(request, vault_file)
        if not vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": _("File is not encrypted.")}, status=400)
        try:
            vault_file.decrypt(password=password)
            vault_file.is_public = True
            vault_file.save(update_fields=["is_public"])
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=500)
        return JsonResponse({"ok": True, "url": vault_file.get_public_url() or ""})


class EncryptedDownloadView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk  = request.POST.get("file_pk", "").strip()
        password = request.POST.get("password", "").strip()
        if not file_pk or not password:
            return JsonResponse({"ok": False, "error": _("Missing required fields.")}, status=400)
        try:
            vault_file = access.gate_by_bucket(
                request.user, VaultFile.objects.select_related("owner", "bucket")
            ).get(pk=file_pk, owner=request.user)
        except VaultFile.DoesNotExist:
            return JsonResponse({"ok": False, "error": _("File not found.")}, status=404)
        if not vault_file.is_encrypted:
            return JsonResponse({"ok": False, "error": _("File is not encrypted.")}, status=400)
        refusal = _egress_refusal(vault_file)
        if refusal is not None:
            return JsonResponse({"ok": False, "error": refusal.content.decode()},
                                status=429)
        try:
            data, content_type = vault_file.get_strategy().decrypt_to_bytes(vault_file, password=password)
        except Exception as e:
            return JsonResponse({"ok": False, "error": str(e)}, status=400)
        filename = vault_file.title or os.path.basename(vault_file.file.name)
        # The decrypted length, not file_size_bytes: what leaves the wire is
        # the plaintext, and the stored figure drifts on encrypt/decrypt.
        from decimal import Decimal as _D

        _record_egress(vault_file, _D(str(len(data))) / _D("1048576"))
        return FileResponse(BytesIO(data), content_type=content_type, as_attachment=True, filename=filename)


class MoveFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        dest_dir_pk = request.POST.get("destination_directory", "").strip()
        if not file_pk:
            return JsonResponse({"ok": False, "error": _("Missing file_pk.")}, status=400)
        vault_file = get_object_or_404(access.gate_by_bucket(
            request.user, VaultFile.objects.filter(owner=request.user)), pk=file_pk)
        if access.is_mirror_row(vault_file):
            return access.mirror_lock_response(vault_file)
        if dest_dir_pk:
            dest_dir = get_object_or_404(VaultDirectory, pk=dest_dir_pk, bucket=vault_file.bucket)
            vault_file.directory = dest_dir
        else:
            vault_file.directory = None
        vault_file.save(update_fields=["directory"])
        return JsonResponse({"ok": True, "new_pid": vault_file.directory_id})


class RenameFileView(LoginRequiredMixin, View):
    _VALID_TYPES = {k for k, _ in VaultFile.FILE_TYPES}

    def post(self, request):
        file_pk   = request.POST.get("file_pk", "").strip()
        new_title = request.POST.get("title", "").strip()
        file_type = request.POST.get("file_type", "").strip()
        if not file_pk or not new_title:
            return JsonResponse({"ok": False, "error": _("Missing required fields.")}, status=400)
        if file_type and file_type not in self._VALID_TYPES:
            return JsonResponse({"ok": False, "error": _("Invalid file type.")}, status=400)
        from toto.vault.models import upload_refusal
        # A name is a door too: renaming a note to .docx would list an Office
        # file the upload doors refused (2026-09-30).
        refusal = upload_refusal(new_title, file_type=file_type)
        if refusal:
            return JsonResponse({"ok": False, "error": refusal}, status=400)
        vault_file = get_object_or_404(access.gate_by_bucket(
            request.user, VaultFile.objects.filter(owner=request.user)), pk=file_pk)
        if access.is_mirror_row(vault_file):
            return access.mirror_lock_response(vault_file)
        vault_file.title = new_title
        update_fields = ["title"]
        if file_type and file_type != vault_file.file_type:
            vault_file.file_type = file_type
            update_fields.append("file_type")
        vault_file.save(update_fields=update_fields)
        return JsonResponse({"ok": True, "title": vault_file.title, "file_type": vault_file.file_type})


class DeleteFileView(LoginRequiredMixin, View):
    def post(self, request):
        file_pk = request.POST.get("file_pk", "").strip()
        if not file_pk:
            return JsonResponse({"ok": False, "error": _("Missing file_pk.")}, status=400)
        vault_file = get_object_or_404(access.gate_by_bucket(
            request.user, VaultFile.objects.filter(owner=request.user)), pk=file_pk)
        if access.is_mirror_row(vault_file):
            # Deleting the stub would neither delete the remote file nor
            # stick — the next refresh resurrects it.
            return access.mirror_lock_response(vault_file)
        # To the trash (2026-10-01): bytes and versions kept for the
        # restore, and still counted against quota and levy. A mounted
        # remote bucket names the peer's file, so there it goes at once.
        from toto.vault.trash import remove_file

        trashed = remove_file(vault_file, by=request.user, request=request,
                              door="delete_file")
        return JsonResponse({"ok": True, "trashed": trashed})


class BucketCopyAjaxView(LoginRequiredMixin, View):
    """
    JSON endpoint used by the inline copy modal on the bucket metrics page.
    Accepts POST: files[] (IDs) + destination_bucket (ID).
    Returns {"ok": true, "count": N, "dest_slug": "...", "dest_name": "...", "dest_url": "..."}
    or {"ok": false, "error": "..."}.
    """
    def post(self, request, source_slug):
        source_bucket = get_object_or_404(Bucket, slug=source_slug, owner=request.user)

        file_ids = request.POST.getlist("files")
        dest_bucket_id = request.POST.get("destination_bucket", "").strip()

        if not file_ids:
            return JsonResponse({"ok": False, "error": _("Select at least one file.")}, status=400)
        if not dest_bucket_id:
            return JsonResponse({"ok": False, "error": _("Choose a destination bucket.")}, status=400)

        try:
            destination_bucket = Bucket.objects.get(pk=dest_bucket_id, owner=request.user)
        except Bucket.DoesNotExist:
            return JsonResponse({"ok": False, "error": _("Invalid destination bucket.")}, status=400)
        if destination_bucket.is_being_deleted:
            from .models import closed_bucket_sentence

            return JsonResponse({"ok": False, "error": closed_bucket_sentence(destination_bucket)},
                                status=409)

        if destination_bucket.pk == source_bucket.pk:
            return JsonResponse({"ok": False, "error": _("Source and destination must differ.")}, status=400)

        selected_files = list(
            access.gate_by_bucket(request.user, VaultFile.objects.filter(
                pk__in=file_ids, bucket=source_bucket, owner=request.user))
        )
        if len(selected_files) != len(file_ids):
            return JsonResponse({"ok": False, "error": _("Some selected files are invalid.")}, status=400)

        if not (source_bucket.is_local and destination_bucket.is_local):
            return _delegate_to_transfer(
                request, source_bucket, destination_bucket, None,
                selected_files, "add_suffix")

        office = _office_titles(selected_files)
        if office:
            return JsonResponse({"ok": False, "error": office}, status=400)

        src_driver = get_bucket_storage(source_bucket)
        dst_driver = get_bucket_storage(destination_bucket)

        with transaction.atomic():
            for source_file in selected_files:
                unique_key = _unique_copy_key(source_file, destination_bucket)
                content = src_driver.read(source_file.file.name)
                stored_name = dst_driver.save(source_file.file.name, content)
                new_file = VaultFile(
                    owner=source_file.owner,
                    title=source_file.title,
                    key=unique_key,
                    content_hash=source_file.content_hash,
                    file_type=source_file.file_type,
                    is_encrypted=source_file.is_encrypted,
                    is_public=source_file.is_public,
                    notes=source_file.notes,
                    file_size_bytes=len(content),
                    bucket=destination_bucket,
                )
                new_file.file = stored_name
                new_file.save()

        count = len(selected_files)
        BucketCopyLog.objects.create(
            from_bucket=source_bucket,
            to_bucket=destination_bucket,
            performed_by=request.user,
            file_count=count,
        )
        return JsonResponse({
            "ok": True,
            "count": count,
            "dest_slug": destination_bucket.slug,
            "dest_name": destination_bucket.name,
            "dest_url": reverse("vault:bucket_metrics", kwargs={"bucket_slug": destination_bucket.slug}),
        })


# ============================================================
# Invoices
# ============================================================

# ============================================================
# Bucket connection URL + remote import
# ============================================================

class BucketConnectionUrlView(LoginRequiredMixin, View):
    """
    GET  /vault/buckets/<slug>/connection-url/
    Returns the credential-free connection URL for a bucket owned by the
    current user. Suitable for sharing with another toto instance.
    """

    def get(self, request, bucket_slug):
        bucket = get_object_or_404(Bucket, slug=bucket_slug, owner=request.user)
        from .models import external_buckets_allowed
        if bucket.storage_backend != "local" and not external_buckets_allowed():
            # Don't advertise endpoint hosts of backends this host refuses to use.
            return JsonResponse({"error": _("Not found.")}, status=404)
        from .connection import BucketConnectionSpec
        spec = BucketConnectionSpec.from_bucket(bucket)
        return JsonResponse({
            "url": spec.to_url(),
            "backend": spec.backend,
            "provider": spec.provider,
            "bucket_name": spec.bucket_name,
        })


class RefreshRemoteBucketView(LoginRequiredMixin, View):
    """POST /vault/buckets/<slug>/refresh/ — queue one mirror refresh.

    Owner-or-superuser, 404 otherwise (a 403 would confirm the bucket
    exists). The run row is created BEFORE dispatch so the browser has
    something to poll even when queueing fails; a build with no worker gets a
    503 naming the flag, never an inline walk of another host's listing.

    (This door replaced ``RemoteBucketImportView`` — remote mounts are
    created by pairing a BucketPeer in the admin, never from a pasted URL.)
    """

    def post(self, request, bucket_slug):
        from .models import external_buckets_allowed
        if not external_buckets_allowed():
            return JsonResponse(
                {"error": _("External buckets are disabled on this host.")},
                status=403)
        bucket = get_object_or_404(
            Bucket.objects.select_related("peer"), slug=bucket_slug)
        if not (bucket.owner_id == request.user.pk or request.user.is_superuser):
            raise Http404("No such bucket.")
        if bucket.storage_backend != "remote_toto":
            return JsonResponse(
                {"error": _("Only a mounted remote bucket can be refreshed.")},
                status=400)

        from . import transfer_dispatch

        run = transfer_dispatch.create_refresh_run(
            user=request.user, bucket=bucket)
        try:
            transfer_dispatch.dispatch_refresh_run(run)
        except transfer_dispatch.CannotQueue as exc:
            transfer_dispatch.fail_refresh_run(run, str(exc))
            return JsonResponse({"ok": False, "error": str(exc)}, status=503)
        return JsonResponse({
            "ok": True,
            "run_id": run.pk,
            "status": run.status,
            "status_url": reverse("vault:bucket_refresh_status",
                                  args=[run.pk]),
        })


class BucketRefreshStatusView(LoginRequiredMixin, View):
    """Poll one refresh run. Owner-or-superuser of the BUCKET, 404 otherwise
    — the same guard as the dispatch door, so polling leaks nothing the
    button did not."""

    def get(self, request, pk):
        from .mirror import BucketRefreshRun, run_payload

        run = get_object_or_404(
            BucketRefreshRun.objects.select_related("bucket"), pk=pk)
        bucket = run.bucket
        if not (bucket and (bucket.owner_id == request.user.pk
                            or request.user.is_superuser)):
            raise Http404("No such run.")
        return JsonResponse(run_payload(run))


class TransferStatusView(LoginRequiredMixin, View):
    """Poll one transfer run. Run owner or superuser, 404 otherwise."""

    def get(self, request, pk):
        from .transfer import TransferRun
        from .transfer import run_payload as transfer_payload

        run = get_object_or_404(TransferRun, pk=pk)
        if not (run.owner_id == request.user.pk or request.user.is_superuser):
            raise Http404("No such run.")
        return JsonResponse(transfer_payload(run))


def _transfer_or_404(request, pk):
    from .transfer import TransferRun

    run = get_object_or_404(
        TransferRun.objects.select_related("source_bucket", "dest_bucket"),
        pk=pk)
    if not (run.owner_id == request.user.pk or request.user.is_superuser):
        raise Http404("No such run.")
    return run


class TransferPanelView(LoginRequiredMixin, TemplateView):
    """Your transfer runs, newest first. Owner's runs only — a transfer names
    two buckets and the file count between them, which is the owner's
    information exactly like the metrics page it links from."""

    template_name = "vault/transfers.html"

    def get_context_data(self, **kwargs):
        from .transfer import TransferRun

        context = super().get_context_data(**kwargs)
        context["runs"] = list(
            TransferRun.objects.filter(owner=self.request.user)
            .select_related("source_bucket", "dest_bucket")[:50])
        return PageProcessor().decorate(context, self.request)


class TransferDetailView(LoginRequiredMixin, TemplateView):
    """One run: the bar, the skips, and the retry door."""

    template_name = "vault/transfer_detail.html"

    def get_context_data(self, **kwargs):
        from .transfer import run_payload as transfer_payload

        context = super().get_context_data(**kwargs)
        run = _transfer_or_404(self.request, self.kwargs["pk"])
        sibling_running = run.__class__.objects.filter(
            owner=run.owner_id, source_bucket=run.source_bucket,
            dest_bucket=run.dest_bucket,
            status__in=("pending", "running")).exclude(pk=run.pk).exists()
        context.update({
            "run": run,
            "payload": transfer_payload(run),
            "can_retry": (run.status == "failed" or (
                run.status == "success" and run.files_skipped > 0))
                and bool(run.retry_file_ids())
                and not sibling_running,
            "sibling_running": sibling_running,
        })
        return PageProcessor().decorate(context, self.request)


class TransferRetryView(LoginRequiredMixin, View):
    """POST — a NEW run seeded with the remainder plus the skipped files.

    A new row, never a reopened one: the old run's counters are a record of
    money and bytes that actually moved, and mutating them to run again
    would rewrite that record. Refused while a sibling run between the same
    buckets is still going — two concurrent walks would race the same keys.
    """

    def post(self, request, pk):
        run = _transfer_or_404(request, pk)
        from . import transfer_dispatch

        if not run.is_finished:
            return JsonResponse(
                {"ok": False, "error": _("This run is still going.")}, status=400)
        if run.source_bucket is None or run.dest_bucket is None:
            return JsonResponse(
                {"ok": False,
                 "error": _("A bucket this run used no longer exists.")},
                status=400)
        retry_ids = run.retry_file_ids()
        if not retry_ids:
            return JsonResponse(
                {"ok": False, "error": _("Nothing left to retry.")}, status=400)
        sibling = run.__class__.objects.filter(
            owner=run.owner_id, source_bucket=run.source_bucket,
            dest_bucket=run.dest_bucket,
            status__in=("pending", "running")).exists()
        if sibling:
            return JsonResponse(
                {"ok": False,
                 "error": _("A transfer between these buckets is already "
                            "running — wait for it to finish.")}, status=409)

        files = list(access.gate_by_bucket(request.user, VaultFile.objects.filter(
            pk__in=retry_ids, bucket=run.source_bucket)))
        if not files:
            return JsonResponse(
                {"ok": False,
                 "error": _("The files to retry no longer exist at the "
                            "source.")}, status=400)
        new_run = transfer_dispatch.create_transfer_run(
            user=request.user, source_bucket=run.source_bucket,
            dest_bucket=run.dest_bucket, dest_directory=run.dest_directory,
            files=files, copy_policy=run.copy_policy)
        try:
            transfer_dispatch.dispatch_transfer_run(new_run)
        except transfer_dispatch.CannotQueue as exc:
            transfer_dispatch.fail_transfer_run(new_run, str(exc))
            return JsonResponse(
                {"ok": False, "error": str(exc), "run_id": new_run.pk},
                status=503)
        return JsonResponse({
            "ok": True,
            "run_id": new_run.pk,
            "detail_url": reverse("vault:transfer_detail",
                                  args=[new_run.pk]),
        })


@method_decorator(csrf_exempt, "dispatch")
class CreateEmptyFileView(LoginRequiredMixin, View):
    """Create an empty text-based vault file directly in a directory."""

    #: Superseded by `available_create_types()`, which derives the same set and
    #: also carries the types an editor plugin declares for itself. Kept because
    #: `api_views` imports it, and because it is the record of what the vault
    #: can seed WITHOUT asking anybody.
    _ALLOWED = {"text", "markdown", "json", "yaml", "xml", "csv", "html", "latex",
                "bib", "svg", "neojson"}
    _INITIAL = {
        "text":  "",
        # Empty, like "text". A starter heading would be a guess about what the
        # file is for, and every editor here opens an empty file happily.
        "markdown": "",
        "json":  "{}\n",
        "yaml":  "",
        "csv":   "",
        "xml":   '<?xml version="1.0" encoding="utf-8"?>\n<root>\n</root>\n',
        "html": (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            "  <title></title>\n"
            "</head>\n"
            "<body>\n"
            "</body>\n"
            "</html>\n"
        ),
        # Starter .tex document. Mirrors toto.texlab.views.BLANK_TEX_DOCUMENT —
        # has a line of body so a freshly-created doc compiles to a PDF (an empty
        # body yields "No pages of output").
        "latex": (
            "\\documentclass{article}\n"
            "\n"
            "\\begin{document}\n"
            "\n"
            "Hello, \\LaTeX!\n"
            "\n"
            "\\end{document}\n"
        ),
        "bib":   "",
        "svg":   '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">\n</svg>\n',
        # Empty NeoJSON graph. Mirrors toto.ravioli.neojson.dumps(neojson.new_graph()).
        "neojson": (
            "{\n"
            '  "neojson": "1.0",\n'
            '  "type": "Graph",\n'
            '  "directed": true,\n'
            '  "nodes": [],\n'
            '  "relationships": [],\n'
            '  "metadata": {\n'
            '    "node_count": 0,\n'
            '    "relationship_count": 0,\n'
            '    "labels": [],\n'
            '    "relationship_types": []\n'
            "  }\n"
            "}\n"
        ),
    }

    def post(self, request):
        from toto.vault.models import file_edits_allowed
        if not file_edits_allowed():
            return JsonResponse({"error": _("File editing is disabled on this host.")}, status=403)
        from toto.vault.plugins import VaultEditorPlugin

        title      = request.POST.get("title", "").strip()
        file_type  = request.POST.get("file_type", "").strip()
        dir_id     = request.POST.get("directory_id", "").strip()

        if not title:
            return JsonResponse({"error": _("Filename is required.")}, status=400)
        from toto.vault.models import upload_refusal
        refusal = upload_refusal(title)
        if refusal:
            return JsonResponse({"error": refusal}, status=400)
        # Derived, not a second hardcoded list: `_ALLOWED` and CREATABLE_TYPES
        # used to be two of them that had to agree, and a plugin-declared type
        # would have been offered by the menu and refused by this check.
        if file_type not in {t for t, _ in available_create_types()}:
            return JsonResponse({"error": _("Unsupported type: %(type)s") % {"type": file_type}}, status=400)
        # Only allow creating a type whose editor app is installed on this deployment
        # (the UI already hides the others; this guards direct POSTs).
        if VaultEditorPlugin.for_file_type(file_type) is None:
            return JsonResponse({"error": _("No editor available for this file type.")}, status=400)
        if not dir_id:
            return JsonResponse({"error": _("directory_id is required.")}, status=400)

        directory = get_object_or_404(VaultDirectory, pk=int(dir_id))
        if directory.bucket.owner_id is None or directory.bucket.owner_id != request.user.pk:
            return JsonResponse({"error": _("Permission denied.")}, status=403)
        if directory.bucket.is_being_deleted:
            from .models import closed_bucket_sentence

            return JsonResponse({"error": closed_bucket_sentence(directory.bucket)}, status=409)
        if not directory.bucket.is_local:
            # An empty file exists to be edited, and editors need local bytes.
            return JsonResponse(
                {"error": _("This bucket's storage is remote — files are "
                            "uploaded or transferred into it, not created "
                            "empty here.")}, status=403)

        vault_file = create_empty_vault_file(
            request.user, directory.bucket, directory, title, file_type,
        )

        plugin = VaultEditorPlugin.for_file_type(file_type)
        editor_url = plugin.get_editor_url(vault_file) if plugin else None

        return JsonResponse({
            "status": "ok",
            "file_pk": vault_file.pk,
            "title": vault_file.title,
            "editor_url": editor_url,
        }, status=201)


class CreateZipView(LoginRequiredMixin, View):
    """Trigger a Celery-backed 'vault-zip' workflow that archives the selected
    files into a new .zip VaultFile. Only available when the workflow engine is
    installed (the Zip button is hidden otherwise)."""

    def post(self, request):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            return JsonResponse({"error": _("Archiving is not available on this deployment.")}, status=400)

        source_id = request.POST.get("source_directory_id", "").strip()
        target_id = request.POST.get("target_directory_id", "").strip()
        output_name = request.POST.get("output_name", "").strip()

        if not source_id:
            return JsonResponse({"error": _("source_directory_id is required.")}, status=400)
        source = get_object_or_404(VaultDirectory, pk=source_id)
        if source.bucket.owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"error": _("Permission denied.")}, status=403)

        target = None
        if target_id:
            target = get_object_or_404(VaultDirectory, pk=target_id, bucket=source.bucket)

        try:
            ids = [int(x) for x in request.POST.getlist("file_ids")]
        except (TypeError, ValueError):
            return JsonResponse({"error": _("Invalid file selection.")}, status=400)
        valid_ids = list(
            access.gate_by_bucket(request.user, VaultFile.objects.filter(
                pk__in=ids, bucket=source.bucket, is_encrypted=False))
            # Non-local content is filtered the same way encrypted is: the
            # zip task opens local handles, and a remote file has none.
            .filter(access.local_content_q())
            .values_list("pk", flat=True)
        )
        if not valid_ids:
            return JsonResponse({"error": _("Select at least one file to archive.")}, status=400)

        payload = {"data": {
            "owner_id": request.user.id,
            "source_directory_id": source.pk,
            "target_directory_id": target.pk if target else None,
            "file_ids": valid_ids,
            "output_name": output_name,
        }}
        run, queued = self._start_zip_run(payload, request.user)

        try:
            run_url = reverse("workflows:workflow_run_detail", args=[run.id])
        except Exception:
            run_url = ""
        return JsonResponse({
            "status": "queued" if queued else "ok",
            "workflow_run_id": run.id,
            "workflow_run_url": run_url,
            "count": len(valid_ids),
        })

    @staticmethod
    def _ensure_workflow():
        """Get-or-create the single-node 'vault-zip' workflow so archiving works
        even if ingress hasn't (re)seeded it on this deployment."""
        from toto.workflows.models import Workflow, WorkflowNode
        wf, created = Workflow.objects.get_or_create(
            slug="vault-zip",
            defaults={
                "name": "Zip files",
                "description": "Bundle selected vault files into a single .zip archive saved back to the vault.",
            },
        )
        if created or not wf.nodes.filter(task_name="vault_zip_files").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Zip selected files",
                task_name="vault_zip_files",
                position_x=0,
                position_y=0,
            )
        return wf

    def _start_zip_run(self, payload, user):
        """Create the WorkflowRun and execute it. Uses Celery when a worker is
        reachable, otherwise runs inline (so zipping still works on a dev/runserver
        setup with no worker — mirrors fileservices.dispatch.dispatch_run)."""
        from toto.celery_utils import celery_available
        from toto.workflows.models import WorkflowRun

        wf = self._ensure_workflow()
        run = WorkflowRun.objects.create(workflow=wf, input_data=payload,
                                         started_by=user)
        if celery_available():
            from toto.workflows.tasks import start_workflow_run_task
            start_workflow_run_task.delay(run.id)
            return run, True

        from toto.workflows.services.executor import WorkflowExecutor
        WorkflowExecutor().start(run)
        return run, False


class ZipStatusView(LoginRequiredMixin, View):
    """Poll endpoint for a vault-zip workflow run."""

    def get(self, request):
        from django.apps import apps
        if not apps.is_installed("toto.workflows"):
            return JsonResponse({"error": "unavailable"}, status=400)
        from toto.workflows.models import WorkflowNodeRun, WorkflowRun

        run = get_object_or_404(WorkflowRun, pk=request.GET.get("run_id"))
        owner_id = ((run.input_data or {}).get("data") or {}).get("owner_id")
        if owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({"error": _("Not found.")}, status=404)

        vfid = None
        if run.status == WorkflowRun.COMPLETED:
            for n in WorkflowNodeRun.objects.filter(workflow_run=run).order_by("-id"):
                d = (n.output_data or {}).get("data") or {}
                if d.get("vault_file_id"):
                    vfid = d["vault_file_id"]
                    break
        return JsonResponse({
            "status": run.status,
            "is_terminal": run.status in (WorkflowRun.COMPLETED, WorkflowRun.FAILED),
            "vault_file_id": vfid,
        })
