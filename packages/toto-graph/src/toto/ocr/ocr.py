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
