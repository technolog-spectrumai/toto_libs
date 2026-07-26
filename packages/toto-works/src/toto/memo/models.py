"""
``toto.memo`` no longer stores anything in the database.

Presentations now live entirely as self-contained ``.pml`` vault files
(``file_type="presentation"``) parsed by :mod:`toto.memo.presentation_format`.
The memo app is purely the viewer + browser editor for those files — see
:mod:`toto.memo.views`.  The former DB models (Tag / MemoDiagram / MemoDeck /
MemoCard) were dropped in migration ``0002_drop_memo_models``.
"""
