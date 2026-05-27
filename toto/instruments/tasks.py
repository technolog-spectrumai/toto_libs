import logging

logger = logging.getLogger(__name__)

# Subscription processing has moved to toto.subscriptions.tasks.
# This file is kept to avoid import errors from any cached beat schedules.
