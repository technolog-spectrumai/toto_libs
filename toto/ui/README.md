# toto.ui

Shared UI utilities and template helpers. No models, no URLs, no views — only template tags, context processors, and front-end building blocks shared across the system.

## What it contains

- `page.py` — template tag library and/or context processor providing shared variables (platform config, theme, navigation data) available in every template.

## Usage

Templates load shared context via `{% load ui_tags %}` or via the context processor configured in `TEMPLATES[0]['OPTIONS']['context_processors']`.
