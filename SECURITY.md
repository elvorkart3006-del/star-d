# Security policy

STAR-D is an experimental research repository, **not a trading service or production order-execution system**.

## Reporting security concerns

- **Never post** API credentials, access tokens, private market datasets, brokerage account details, personal records or exploitable secret values in a public GitHub issue, screenshot or pull request.
- If this repository offers GitHub's **Report a vulnerability** option under its Security tab, use that private reporting route.
- If the private reporting option is unavailable, open a **non-sensitive** issue asking for a secure reporting channel. Describe only the general area of concern; do not include an exploit, credential or private data.
- If a credential may have been exposed, **revoke or rotate it with the relevant provider immediately**. Deleting a GitHub file or adding it to `.gitignore` does not erase its commit history.

## Scope and limitations

The codebase contains experimental broker-interface research and offline simulations. Live order placement is intentionally disabled. Do not enable broker order execution, live-data paper trading, or persistent/redistributed market data without confirming the relevant permissions and safeguards.

The public repository intentionally excludes `.env`, private journals, raw market data, local reports and backups. See the [data-retention audit](docs/security/repository-audit.md) for additional exclusions and restrictions.

No response-time SLA, security certification or warranty is offered. See the [research limitations](docs/research/research-log.md) before relying on the results.
