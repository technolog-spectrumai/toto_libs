from django.urls import path

from . import views
from django.views.generic import RedirectView
from .api_views import (
    StrongboxApiView,
    FileListApiView, FileUploadApiView, FileDetailApiView, FileDownloadApiView,
    FileEncryptApiView, FileDecryptApiView, VaultMetricsApiView,
    BucketTreeApiView, FileContentApiView, FileCreateApiView,
    DirectoryCreateApiView, DirectoryDeleteApiView,
)
from . import peer_views
from . import remote_views
from . import version_views
from .views import (
    PublicFileListView, VaultFileDownloadView,
    FileGatewayPageView, FileGatewayUploadView,
    VaultMetricsView, BucketMetricsView,
    CopyFilesToBucketView, BucketCopyAjaxView,
    EncryptFileView, EncryptStatusView, DecryptFileView, EncryptedDownloadView,
    MoveFileView, RenameFileView, DeleteFileView,
    BucketConnectionUrlView, RefreshRemoteBucketView, BucketRefreshStatusView,
    TransferStatusView, TransferPanelView, TransferDetailView,
    TransferRetryView,
    CreateEmptyFileView,
    CreateZipView, ZipStatusView,
)

app_name = "vault"

urlpatterns = [
    # Peer API — the server half of the bucket link. Path shape is
    # peering.PEER_PATH; tests assert the two cannot drift.
    path("peer/<uuid:grant_uid>/<str:magic_token>/manifest/",
         peer_views.peer_manifest, name="peer_manifest"),
    path("peer/<uuid:grant_uid>/<str:magic_token>/files/",
         peer_views.peer_files, name="peer_files"),
    path("peer/<uuid:grant_uid>/<str:magic_token>/files/<slug:key>/",
         peer_views.peer_file_detail, name="peer_file_detail"),
    path("peer/<uuid:grant_uid>/<str:magic_token>/files/<slug:key>/download/",
         peer_views.peer_file_download, name="peer_file_download"),
    # Enigma JSON API
    path("api/files/", FileListApiView.as_view(), name="api_file_list"),
    path("api/files/create/", FileCreateApiView.as_view(), name="api_file_create"),
    path("api/files/upload/", FileUploadApiView.as_view(), name="api_file_upload"),
    path("api/strongbox/", StrongboxApiView.as_view(), name="api_strongbox"),
    path("api/buckets/", BucketTreeApiView.as_view(), name="api_bucket_tree"),
    path("api/directories/", DirectoryCreateApiView.as_view(), name="api_directory_create"),
    path("api/directories/<int:pk>/", DirectoryDeleteApiView.as_view(), name="api_directory_delete"),
    path("api/metrics/", VaultMetricsApiView.as_view(), name="api_metrics"),
    path("api/files/<slug:key>/", FileDetailApiView.as_view(), name="api_file_detail"),
    path("api/files/<slug:key>/content/", FileContentApiView.as_view(), name="api_file_content"),
    path("api/files/<slug:key>/download/", FileDownloadApiView.as_view(), name="api_file_download"),
    path("api/files/<slug:key>/encrypt/", FileEncryptApiView.as_view(), name="api_file_encrypt"),
    path("api/files/<slug:key>/decrypt/", FileDecryptApiView.as_view(), name="api_file_decrypt"),

    path("", RedirectView.as_view(pattern_name="vault:public_list", permanent=False), name="root"),
    path("public/", PublicFileListView.as_view(), name="public_list"),
    path("public/<slug:bucket_slug>/<slug:key>/", VaultFileDownloadView.as_view(), name="public_file"),
    # The wand's listing half. In the vault because the registry is, and
    # because a host without toto-media-ops still has files to act on.
    path("files/<int:file_pk>/services/", views.file_services, name="file_services"),
    path("gateways/dir/<int:dir_pk>/", FileGatewayPageView.as_view(), name="gateway_page"),
    path("gateways/dir/<int:dir_pk>/upload/", FileGatewayUploadView.as_view(), name="gateway_upload"),
    path("metrics/", VaultMetricsView.as_view(), name="metrics"),
    # The Remote tab. Deliberately NOT under /vault/metrics/<slug>/ —
    # tests_remote_ui asserts that substring is absent for a stranger, so a
    # page nested there would break it by substring alone.
    path("remote/", remote_views.RemoteBucketsView.as_view(), name="remote_buckets"),
    # The Archive tab: the same tree as Files, carrying the zip actions.
    path("archive/", remote_views.ArchiveView.as_view(), name="archive"),
    path("metrics/<slug:bucket_slug>/", BucketMetricsView.as_view(), name="bucket_metrics"),
    path("copy/<slug:source_slug>/", CopyFilesToBucketView.as_view(), name="copy_files"),
    path("copy/<slug:source_slug>/ajax/", BucketCopyAjaxView.as_view(), name="copy_files_ajax"),
    path("file/encrypt/", EncryptFileView.as_view(), name="encrypt_file"),
    path("file/encrypt-status/", EncryptStatusView.as_view(), name="encrypt_status"),
    path("file/decrypt/", DecryptFileView.as_view(), name="decrypt_file"),
    path("file/download-encrypted/", EncryptedDownloadView.as_view(), name="download_encrypted"),
    path("file/move/", MoveFileView.as_view(), name="move_file"),
    path("file/rename/", RenameFileView.as_view(), name="rename_file"),
    path("file/delete/", DeleteFileView.as_view(), name="delete_file"),
    path("buckets/<slug:bucket_slug>/connection-url/", BucketConnectionUrlView.as_view(), name="bucket_connection_url"),
    path("buckets/<slug:bucket_slug>/refresh/", RefreshRemoteBucketView.as_view(), name="bucket_refresh"),
    path("refresh-runs/<int:pk>/", BucketRefreshStatusView.as_view(), name="bucket_refresh_status"),
    path("transfer-runs/<int:pk>/", TransferStatusView.as_view(), name="transfer_status"),
    path("transfers/", TransferPanelView.as_view(), name="transfer_panel"),
    path("transfers/<int:pk>/", TransferDetailView.as_view(), name="transfer_detail"),
    path("transfers/<int:pk>/retry/", TransferRetryView.as_view(), name="transfer_retry"),
    path("file/create/", CreateEmptyFileView.as_view(), name="create_file"),
    path("directory/zip/", CreateZipView.as_view(), name="create_zip"),
    path("directory/zip/status/", ZipStatusView.as_view(), name="zip_status"),

    # Versions and editing locks. One surface for every editor — cyprian, memo
    # and primula all drive these rather than each growing their own, which is
    # how primula ended up with a scheme the other two never got.
    path("file/<int:pk>/versions/", version_views.version_list, name="version_list"),
    path("file/<int:pk>/versions/save/", version_views.version_save, name="version_save"),
    path("file/<int:pk>/versions/<int:version_pk>/restore/",
         version_views.version_restore, name="version_restore"),
    path("file/<int:pk>/lock/", version_views.lock_acquire, name="lock_acquire"),
    path("file/<int:pk>/lock/beat/", version_views.lock_heartbeat, name="lock_heartbeat"),
    path("file/<int:pk>/lock/release/", version_views.lock_release, name="lock_release"),
]
