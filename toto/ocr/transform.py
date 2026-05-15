import cv2
import traceback


class TransformHelper:
    """
    Executes a single ImageTransform using its LambdaFunction backend.
    Handles:
    - parameter building + validation
    - lambda execution
    - image loading/saving
    """

    def __init__(self, transform):
        self.transform = transform
        self.lambda_fn = transform.lambda_function

    # ---------------------------------------------------------
    # PARAMETER HANDLING
    # ---------------------------------------------------------

    def build_params(self, user_params=None):
        """
        Merge defaults + user overrides and validate ranges.
        """
        params = {p.key: p.default_value for p in self.transform.params.all()}

        if user_params:
            for key, value in user_params.items():
                try:
                    param_def = self.transform.params.get(key=key)
                except self.transform.params.model.DoesNotExist:
                    raise ValueError(f"Unknown parameter '{key}' for transform '{self.transform.name}'")

                value = int(value)

                if value < param_def.min_value or value > param_def.max_value:
                    raise ValueError(
                        f"Parameter '{key}' must be between {param_def.min_value} and {param_def.max_value}"
                    )

                params[key] = value

        return params

    # ---------------------------------------------------------
    # LAMBDA EXECUTION
    # ---------------------------------------------------------

    def execute_lambda(self, img, params):
        code = self.lambda_fn.content
        fn_name = self.lambda_fn.function_name

        namespace = {}

        try:
            exec(code, namespace)

            if fn_name not in namespace:
                raise ValueError(f"LambdaFunction does not define function '{fn_name}'")

            fn = namespace[fn_name]

            result = fn(img, **params)

            if result is None:
                raise ValueError("Lambda transform returned None")

            self.lambda_fn.stdout = "Executed successfully"
            self.lambda_fn.stderr = ""
            self.lambda_fn.save(update_fields=["stdout", "stderr"])

            return result

        except Exception:
            self.lambda_fn.stdout = ""
            self.lambda_fn.stderr = traceback.format_exc()
            self.lambda_fn.save(update_fields=["stdout", "stderr"])
            raise

    # ---------------------------------------------------------
    # MAIN EXECUTION ENTRYPOINT
    # ---------------------------------------------------------

    def execute(self, image, params):
        """
        Execute the transform on the given OcrImage instance.
        """
        src_path = image.get_image_path()
        img = cv2.imread(src_path)

        if img is None:
            raise ValueError(f"Could not read image at {src_path}")

        result = self.execute_lambda(img, params)

        cv2.imwrite(src_path, result)

        return True
