from django.test import TestCase


class InstrumentsSmokeTests(TestCase):
    def test_import_models(self):
        from .models import FinancialInstrument, EscrowContract, ForwardContract
        self.assertIsNotNone(FinancialInstrument)
        self.assertIsNotNone(EscrowContract)
        self.assertIsNotNone(ForwardContract)
