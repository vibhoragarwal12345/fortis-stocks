"""
Quantitative Models Desk -- configuration.
Conservative defaults: tail risk is deliberately over- rather than under-stated.
"""

CONFIDENCE_LEVELS      = [0.95, 0.99]
VAR_LOOKBACK_DAYS      = 504     # 2 years -- deliberately long for conservatism
MIN_DATA_REQUIRED_DAYS = 252     # below this, models do not run for a ticker
MONTE_CARLO_SIMS       = 10000
VAR_FLOOR_BPS          = 50      # never report a daily single-name VaR below 0.50%

# Derived
VAR_FLOOR = VAR_FLOOR_BPS / 10000.0          # 0.0050
TRADING_DAYS_PER_YEAR = 252

# Calibration / quality thresholds
MIN_CALIBRATION_QUALITY = 0.6    # below this -> warning flag, excluded from A-grade
COVERAGE_BAND = (0.85, 0.95)     # acceptable empirical coverage of a 90% CI

# Monte Carlo horizons persisted per ticker
MC_HORIZONS = [1, 5, 30]

# Stress-test scenarios: (label, start_date, end_date) -- exact historical windows.
STRESS_SCENARIOS = {
    "gfc_2008":        ("2008-09-02", "2009-03-09"),   # Lehman -> March 2009 trough
    "covid_2020":      ("2020-02-19", "2020-03-23"),   # COVID crash
    "rate_shock_2022": ("2022-01-03", "2022-10-12"),   # 2022 rate-driven drawdown
}
