class OcrHelper:
    """
    Pure OCR helper:
    - constructor takes language
    - execute(image) loads image and runs OCR
    - returns structured OCR data (lines, words, bounding boxes)
    - does NOT touch Django models
    """

    def __init__(self, lang):
        self.lang = lang

    # ---------------------------------------------------------
    # RAW OCR EXECUTION
    # ---------------------------------------------------------

    def run_tesseract(self, image_path):
        """
        Runs pytesseract and returns the raw data dict.
        """
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        return pytesseract.image_to_data(
            img,
            lang=self.lang,
            output_type=pytesseract.Output.DICT
        )

    # ---------------------------------------------------------
    # LINE GROUPING (NO MODEL TOUCHING)
    # ---------------------------------------------------------

    def extract_lines(self, data):
        """
        Converts Tesseract word-level data into line-level structures.
        Returns a list of dicts:
        [
            {
                "text": "...",
                "left": int,
                "top": int,
                "width": int,
                "height": int,
                "confidence": int
            },
            ...
        ]
        """

        lines = {}
        n = len(data["text"])

        for i in range(n):
            if data["level"][i] == 5:  # word level
                text = data["text"][i].strip()
                if not text:
                    continue

                key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
                lines.setdefault(key, []).append(i)

        results = []

        for key, word_indices in lines.items():
            words = [data["text"][i] for i in word_indices]
            full_text = " ".join(words)

            lefts = [data["left"][i] for i in word_indices]
            tops = [data["top"][i] for i in word_indices]
            rights = [data["left"][i] + data["width"][i] for i in word_indices]
            bottoms = [data["top"][i] + data["height"][i] for i in word_indices]

            confidence = int(
                sum(int(data["conf"][i]) for i in word_indices) / len(word_indices)
            )

            results.append({
                "text": full_text,
                "left": min(lefts),
                "top": min(tops),
                "width": max(rights) - min(lefts),
                "height": max(bottoms) - min(tops),
                "confidence": confidence,
            })

        return results

    # ---------------------------------------------------------
    # MAIN ENTRYPOINT
    # ---------------------------------------------------------

    def execute(self, image):
        """
        Full OCR pipeline:
        - load image
        - run tesseract
        - extract lines
        Returns: list of line dicts
        """
        data = self.run_tesseract(image.get_image_path())
        return self.extract_lines(data)


# ---------------------------------------------------------------------------
# Where the scan actually happens
# ---------------------------------------------------------------------------

def gear_for(user, uuid=None):
    """The Compute Gear to scan in, or None to scan in this process.

    None on a host with no toto.anastasia. Where Gears exist but this user
    holds none, the fallback is only offered if THIS host has tesseract — and
    since 1.50 Zenobia does not, so the refusal is the honest answer there.

    ``uuid`` is the Gear the form asked for. A CHOSEN Gear never falls back:
    "scan it there" has no local equivalent, and scanning somewhere else
    quietly is the answer nobody asked for. Without a choice the old
    behaviour stands, which is what keeps a one-Gear user from having to
    answer a question with one possible reply.
    """
    import shutil

    from django.apps import apps

    if not apps.is_installed("toto.anastasia"):
        return None
    from toto.anastasia import jobs

    try:
        return jobs.require_gear(user, uuid)
    except jobs.NoGear:
        if uuid is None and shutil.which("tesseract"):
            return None
        raise


def gear_choices(user) -> list:
    """The Gears this person may scan in, for the form. Empty without Gears."""
    from django.apps import apps

    if not apps.is_installed("toto.anastasia"):
        return []
    from toto.anastasia import jobs
    return jobs.gear_options(user)


def scan_in_gear(data: bytes, *, filename: str, language: str, lease, user):
    """Read text from an image inside a bounded runner.

    Returns the same ``lines`` structure ``OcrHelper.extract_lines`` produces,
    because the page renders it and should not learn that tesseract moved.

    This is the path that closed the worst hole in the old media tier: OCR ran
    tesseract SYNCHRONOUSLY INSIDE A POST HANDLER, with no run row, no metric,
    no timeout and no memory bound — on bytes a user had just uploaded. It is
    now a job in a container with all four.
    """
    import json
    import os

    from toto.anastasia import jobs

    extension = os.path.splitext(filename or "")[1].lower() or ".png"
    staged = f"input{extension}"
    result = jobs.run(
        lease=lease, operation="run_ocr",
        params={"input": staged, "lang": language},
        inputs={staged: data},
        subject_label="ocr.scan", subject_id=user.pk if user else "",
        requested_by=user)

    payload = result["outputs"].get("output.json")
    if not payload:
        raise RuntimeError("The Compute Gear produced no OCR output.")
    parsed = json.loads(payload.decode("utf-8"))
    return parsed.get("lines") or []
