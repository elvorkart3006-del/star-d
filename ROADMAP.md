# Roadmap
## Repository approval
- Keep the source list aligned with the repository data-retention audit
- Confirm the intended GitHub owner and private visibility before creating a remote. Review the first staged file list against the approved paths.
- Use synthetic inputs or explicitly authorized market data for simulation; do not permanently store or redistribute raw Kite market data.
## Reproducibility
- Synthetic scanner compatibility fixture is implemented and cleaned up after each test.
- Record the tested dependency versions and add an offline test workflow for later repository use.
- Compare the Pine on-chart paper summary against Python for the same seven stocks on matching dates and candles. Exercise the alert conditions without connecting them to order execution.
## Forward paper evidence
- Keep the DC threshold at 5% and collect paper outcomes using permitted data. Maintain the manual and DC journals separately.
- Continue recording quote freshness, completeness, missed opportunities and fees alongside trading performance.
- Review the existing readiness criteria after enough scheduled sessions and closed trades are observed. [Research log](docs/research/research-log.md#forward-test-criteria)
