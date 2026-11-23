# Roadmap

## Repository approval

- Review the proposed source list and the research summaries in the [repository audit](docs/security/repository-audit.md).
- Confirm the intended GitHub owner and private visibility before creating a remote. Review the first staged file list against the approved paths.
- Confirm data use and sharing terms, including permission to share the proposed research summaries and to use any feed for paper simulation.

## Reproducibility

- Add a synthetic scanner CSV fixture for the legacy compatibility test so every offline test can run from a clean checkout.
- Record the tested dependency versions and add an offline test workflow for later repository use.
- Compare the Pine on-chart paper summary against Python for the same seven stocks on matching dates and candles. Exercise the alert conditions without connecting them to order execution.

## Forward paper evidence

- Keep the DC threshold at 5% and collect paper outcomes using permitted data. Maintain the manual and DC journals separately.
- Continue recording quote freshness, completeness, missed opportunities and fees alongside trading performance.
- Review the existing readiness criteria after enough scheduled sessions and closed trades are observed. [Research log](docs/research/research-log.md#forward-test-criteria)
