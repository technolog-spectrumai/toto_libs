from django.test import SimpleTestCase

from toto.formica.colony import pheromone


class PheromoneMathTests(SimpleTestCase):
    def test_clamp(self):
        self.assertEqual(pheromone.clamp(5, 0, 10), 5)
        self.assertEqual(pheromone.clamp(-1, 0, 10), 0)
        self.assertEqual(pheromone.clamp(11, 0, 10), 10)

    def test_evaporate_decays_and_floors(self):
        self.assertAlmostEqual(pheromone.evaporate(1.0, 0.1, 0.01, 100), 0.9)
        # repeated decay never crosses the floor
        value = 0.02
        for _ in range(50):
            value = pheromone.evaporate(value, 0.5, 0.01, 100)
        self.assertEqual(value, 0.01)

    def test_deposit_reinforces_and_caps(self):
        self.assertAlmostEqual(pheromone.deposit(1.0, 2.0, 1.0, 0.01, 100), 3.0)
        self.assertEqual(pheromone.deposit(99.0, 5.0, 1.0, 0.01, 100), 100)

    def test_deposits_from_visits_aggregates(self):
        out = pheromone.deposits_from_visits(
            {("a", "b"): 3, ("b", "c"): 1},
            strength=2.0, floor=0.01, ceiling=100,
            current={("a", "b"): 1.0},
        )
        self.assertAlmostEqual(out[("a", "b")], 7.0)   # 1 + 2*3
        self.assertAlmostEqual(out[("b", "c")], 2.01)  # floor + 2*1

    def test_material_from_covisits(self):
        out = pheromone.material_from_covisits(
            {("a", "b"): 4}, similarity_bonus={("a", "b"): 1.5},
            current={("a", "b"): 2.0},
        )
        self.assertAlmostEqual(out[("a", "b")], 7.5)
