from django.urls import path
from django.views.generic import RedirectView
from .api_views import (
    FileListApiView, FileUploadApiView, FileDetailApiView, FileDownloadApiView,
    FileEncryptApiView, FileDecryptApiView, VaultMetricsApiView,
    BucketTreeApiView, FileContentApiView, FileCreateApiView,
    DirectoryCreateApiView, DirectoryDeleteApiView,
)
from .views import (
    PublicFileListView, VaultFileDownloadView,
    FileGatewayPageView, FileGatewayUploadView,
    VaultMetricsView, BucketMetricsView,
    CopyFilesToBucketView, BucketCopyAjaxView,
    EncryptFileView, EncryptStatusView, DecryptFileView, EncryptedDownloadView,
    MoveFileView, RenameFileView, DeleteFileView,
    BucketConnectionUrlView, RemoteBucketImportView,
    CreateEmptyFileView,
    CreateZipView, ZipStatusView,
)

app_name = "vault"

urlpatterns = [
    # Enigma JSON API
    path("api/files/", FileListApiView.as_view(), name="api_file_list"),
    path("api/files/create/", FileCreateApiView.as_view(), name="api_file_create"),
    path("api/files/upload/", FileUploadApiView.as_view(), name="api_file_upload"),
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
    path("gateways/dir/<int:dir_pk>/", FileGatewayPageView.as_view(), name="gateway_page"),
    path("gateways/dir/<int:dir_pk>/upload/", FileGatewayUploadView.as_view(), name="gateway_upload"),
    path("metrics/", VaultMetricsView.as_view(), name="metrics"),
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
    path("buckets/import-remote/", RemoteBucketImportView.as_view(), name="bucket_import_remote"),
    path("file/create/", CreateEmptyFileView.as_view(), name="create_file"),
    path("directory/zip/", CreateZipView.as_view(), name="create_zip"),
    path("directory/zip/status/", ZipStatusView.as_view(), name="zip_status"),
]
