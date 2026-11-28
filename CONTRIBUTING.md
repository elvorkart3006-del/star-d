# Contributing to STAR-D

Thank you for your interest in **Systematic Trading Research & Decision Analytics (STAR-D)**. This is an early-stage, evidence-led research project—not a trading service or an investment recommendation.

## Useful contributions

We welcome thoughtful **issues, methodological critiques and proposed improvements** concerning:

- Reproducible experiments and synthetic test coverage
- Look-ahead bias, historical validation and execution assumptions
- Transaction costs, slippage, drawdown and robustness reporting
- Python / Pine Script signal parity
- Clear documentation and accurate interpretation of results

## Before proposing a code change

1. Open a narrowly scoped issue explaining the problem, evidence and expected result before starting a large change.
2. Keep the **DC 5% baseline unchanged** unless a separate, explicitly reviewed research experiment is proposed.
3. Use **synthetic test inputs**. Never commit broker credentials, raw provider data, personal information, trading journals or third-party licensed materials.
4. Include the method, reproducible test instructions, assumptions and limitations. Do not claim historical returns are live or forward returns.
5. Do not add real-money execution, Kite live-quote paper trading, or unlicensed market-data storage/distribution.

The [local workflows](docs/setup/local-workflows.md) explain the available offline tests. See [research notes](docs/research/research-log.md) and [repository data-retention rules](docs/security/repository-audit.md) before submitting a change.

## Licensing and review

This repository currently does **not** contain an open-source license. Public visibility alone does not grant permission to reuse, distribute or modify the code. Any future license and acceptance of third-party code should be explicitly reviewed by the repository owner. Issue reports and suggestions may be used to guide research; proposed pull requests are subject to review and may not be accepted.
