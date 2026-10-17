import unittest

from trading_os import (
    Analysis,
    Discovery,
    calculate_rsi,
    percent_change,
    risk_agent,
    run_agents,
    technical_agent,
)


def sample_discovery(prices=None):
    return Discovery(
        asset_id="bitcoin",
        asset_name="Bitcoin",
        asset_symbol="BTC",
        price_usd=prices[-1] if prices else 110.0,
        market_cap_usd=1000.0,
        volume_24h_usd=100.0,
        change_24h_pct=1.0,
        price_history=prices or [float(value) for value in range(100, 190)],
        company_ticker="TEST",
        company_name="Test Inc.",
        company_description="A test company.",
        company_price=50.0,
        revenue_latest_usd=120.0,
        revenue_prior_usd=100.0,
        news=[],
    )


class TechnicalAnalysisTests(unittest.TestCase):
    def test_rsi_is_bounded_for_rising_and_falling_prices(self):
        self.assertEqual(calculate_rsi(list(range(20))), 100.0)
        self.assertEqual(calculate_rsi(list(range(20, 0, -1))), 0.0)

    def test_rsi_requires_sufficient_history(self):
        self.assertIsNone(calculate_rsi([1.0] * 14))

    def test_percent_change_handles_zero_start(self):
        self.assertIsNone(percent_change(0, 5))
        change = percent_change(10, 12)
        assert change is not None
        self.assertAlmostEqual(float(change), 20.0)

    def test_technical_analysis_reports_reference_levels(self):
        analysis = technical_agent(sample_discovery())
        self.assertEqual(analysis.metrics["support_90d_usd"], 100.0)
        self.assertEqual(analysis.metrics["resistance_90d_usd"], 189.0)
        self.assertEqual(analysis.metrics["return_7d_pct"], (189 / 182 - 1) * 100)

    def test_risk_flags_slowing_positive_momentum(self):
        prices = [100.0 + index for index in range(50)]
        prices.extend([149.0 + index * 0.05 for index in range(40)])
        data = sample_discovery(prices)
        technical = technical_agent(data)
        risk = risk_agent(data, technical)
        self.assertEqual(risk.metrics["momentum_fading"], "yes")

    def test_five_departments_run_and_synthesize(self):
        analyses = run_agents(sample_discovery())
        self.assertEqual(len(analyses), 5)
        self.assertEqual(analyses[-1].department, "Thesis Synthesis")
        self.assertIn("evidence_alignment_score_90", analyses[-1].metrics)


if __name__ == "__main__":
    unittest.main()
