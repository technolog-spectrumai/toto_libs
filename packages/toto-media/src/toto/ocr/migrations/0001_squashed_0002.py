from django.db import migrations


class Migration(migrations.Migration):
    """OCR owns no tables. Squashed to say so, and to cut the workflows edge.

    History: 0001_initial created five models (OcrProject, OcrImage, OcrLine,
    ImageTransform, ImageTransformParam) and 0002_drop_ocr_models deleted all
    five, leaving the app stateless — see ``toto.ocr.models``. The net effect of
    the pair is nothing, so the squash has no operations.

    The point of squashing is the dependency list, not the operations. 0001
    declared ``('workflows', '0001_initial')`` because the deleted
    ImageTransform had an FK to ``workflows.LambdaFunction``. That edge outlived
    the model: Django's migration loader raises NodeNotFoundError for a
    dependency on an app that is not installed, so BUILD_OCR=1 without
    BUILD_WORKFLOWS=1 could not migrate — for a table that has not existed since
    0002. It also survives a squash that keeps the old files, because
    remove_replaced_nodes re-parents a replaced node's dependencies onto the
    replacement. Hence the two originals are deleted rather than kept.

    ``replaces`` is insurance rather than necessity: no host in the monorepo pins
    toto-graph (where ocr lived until 1.21) or sets BUILD_OCR, so no database has
    ever recorded an ocr migration. If one somewhere has both entries, this is
    marked applied without running.
    """

    initial = True

    replaces = [
        ("ocr", "0001_initial"),
        ("ocr", "0002_drop_ocr_models"),
    ]

    # Deliberately empty: no operations means nothing to depend on.
    dependencies = []

    operations = []
