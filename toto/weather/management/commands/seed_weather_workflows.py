"""
Seeds the weather workflows and their lambda functions in the workflow engine.
Run once after initial deployment or when resetting the database.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed weather workflows (current + forecast) in the workflow engine."

    def handle(self, *args, **options):
        from toto.workflows.models import LambdaFunction, Workflow, WorkflowNode
        from toto.weather.workflows import (
            WEATHER_CURRENT_SLUG,
            WEATHER_FORECAST_SLUG,
            WEATHER_CURRENT_LAMBDA,
            WEATHER_FORECAST_LAMBDA,
            LAYER_EXPORT_WEATHER_TEMPERATURE_SLUG,
            LAYER_EXPORT_WEATHER_PRECIPITATION_SLUG,
            LAYER_EXPORT_WEATHER_WIND_SLUG,
            LAYER_EXPORT_WEATHER_CLOUD_SLUG,
            LAYER_EXPORT_WEATHER_TEMPERATURE_LAMBDA,
            LAYER_EXPORT_WEATHER_PRECIPITATION_LAMBDA,
            LAYER_EXPORT_WEATHER_WIND_LAMBDA,
            LAYER_EXPORT_WEATHER_CLOUD_LAMBDA,
        )

        self._seed_workflow(
            slug=WEATHER_CURRENT_SLUG,
            name="Weather — Current",
            description="Fetches current weather conditions for a list of locations and stores them as WeatherObservation records.",
            lambda_name="weather_fetch_current",
            lambda_content=WEATHER_CURRENT_LAMBDA,
        )
        self._seed_workflow(
            slug=WEATHER_FORECAST_SLUG,
            name="Weather — Forecast",
            description="Fetches hourly weather forecast for a list of locations and stores a ForecastSession with ForecastPoints.",
            lambda_name="weather_fetch_forecast",
            lambda_content=WEATHER_FORECAST_LAMBDA,
        )
        self._seed_workflow(
            slug=LAYER_EXPORT_WEATHER_TEMPERATURE_SLUG,
            name="Layer Export — Weather Temperature",
            description="Rebuilds the weather temperature MapLayer from the latest stored observations.",
            lambda_name="layer_export_weather_temperature",
            lambda_content=LAYER_EXPORT_WEATHER_TEMPERATURE_LAMBDA,
        )
        self._seed_workflow(
            slug=LAYER_EXPORT_WEATHER_PRECIPITATION_SLUG,
            name="Layer Export — Weather Precipitation",
            description="Rebuilds the weather precipitation MapLayer from the latest stored observations.",
            lambda_name="layer_export_weather_precipitation",
            lambda_content=LAYER_EXPORT_WEATHER_PRECIPITATION_LAMBDA,
        )
        self._seed_workflow(
            slug=LAYER_EXPORT_WEATHER_WIND_SLUG,
            name="Layer Export — Weather Wind Speed",
            description="Rebuilds the weather wind speed MapLayer from the latest stored observations.",
            lambda_name="layer_export_weather_wind",
            lambda_content=LAYER_EXPORT_WEATHER_WIND_LAMBDA,
        )
        self._seed_workflow(
            slug=LAYER_EXPORT_WEATHER_CLOUD_SLUG,
            name="Layer Export — Weather Cloud Cover",
            description="Rebuilds the weather cloud cover MapLayer from the latest stored observations.",
            lambda_name="layer_export_weather_cloud",
            lambda_content=LAYER_EXPORT_WEATHER_CLOUD_LAMBDA,
        )
        self.stdout.write(self.style.SUCCESS("Weather workflows seeded."))

    def _seed_workflow(self, *, slug, name, description, lambda_name, lambda_content):
        from toto.workflows.models import LambdaFunction, Workflow, WorkflowNode

        fn, fn_created = LambdaFunction.objects.update_or_create(
            function_name=lambda_name,
            defaults={"content": lambda_content},
        )
        if fn_created:
            self.stdout.write(f"  Created lambda: {lambda_name}")
        else:
            self.stdout.write(f"  Updated lambda: {lambda_name}")

        wf, wf_created = Workflow.objects.update_or_create(
            slug=slug,
            defaults={"name": name, "description": description},
        )
        if wf_created:
            self.stdout.write(f"  Created workflow: {slug}")
        else:
            self.stdout.write(f"  Workflow exists: {slug}")

        if not wf.nodes.filter(node_type=WorkflowNode.LAMBDA).exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.LAMBDA,
                label=name,
                lambda_function=fn,
            )
            self.stdout.write(f"  Created lambda node for: {slug}")
        else:
            node = wf.nodes.filter(node_type=WorkflowNode.LAMBDA).first()
            node.lambda_function = fn
            node.save(update_fields=["lambda_function"])
            self.stdout.write(f"  Lambda node already exists for: {slug}")
