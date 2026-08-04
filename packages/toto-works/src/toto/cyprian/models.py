"""
``toto.cyprian`` stores nothing in the database.

A document is one self-contained XML vault file (``file_type="document"``)
parsed by :mod:`toto.cyprian.document_format` — the same arrangement memo uses
for a slide deck. That is what makes a document movable, downloadable,
backup-able and shareable with the vault tools that already exist, and what
means it can never half-exist.

This module exists so Django's app machinery has something to import, and so
this paragraph has somewhere to live.
"""
