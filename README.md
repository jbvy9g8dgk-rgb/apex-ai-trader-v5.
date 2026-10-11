# APEX V5 — 100-coin paper-trading terminal

**Simulation only.** This project never connects to an exchange account or places real orders. The four workers are rule-based modules, **not** LLM agents. Confidence scores indicate rule alignment, not probability of success. This is not financial advice.

## iPhone / GitHub setup
1. Create a **new** repository named `apex-ai-trader-v5`. Use **Public** only if you're comfortable with all repository contents and paper portfolio data being publicly visible. **Never upload exchange API keys or personal details.**
2. Download the ZIP, extract it using the iPhone Files app, and upload the **contents of the `apex-ai-trader-v5` folder** to the repository, preserving folders. GitHub's mobile web uploader may be awkward with folders; use a desktop browser if needed. **Uploading the ZIP as one file will not run the bot.**
3. GitHub → Settings → Actions → General → Workflow permissions → select **Read and write permissions** and save (if available). The workflow commits simulated state and dashboard snapshots back to the repository.
4. GitHub → Actions → **APEX V5 Market Scan** → **Run workflow**. Wait for a green check. If the public CoinGecko API rate-limits the run, retry later.
5. GitHub → Settings → Pages → **Deploy from a branch** → branch **main**, folder **/docs** → Save. Your app will be available at `https://YOUR-USERNAME.github.io/apex-ai-trader-v5/` after deployment.
6. Open the Pages URL in Safari → Share → **Add to Home Screen**.

## Notes
- CoinGecko public API fetches the top 100 coins by market cap in **AUD**, with 7-day sparkline prices. It may be rate-limited; no guaranteed 15-minute refresh.
- A scheduled GitHub Actions scan is best effort and may be delayed or disabled for inactivity.
- This app shows **snapshots** of the latest scan; it does not stream tick-by-tick market prices or send push notifications.
- State is persisted by committing `paper_state.json` to the repository; deleting or resetting that file resets the paper portfolio. Public repositories expose paper state.
- Simulated trades use observed snapshot prices and a configurable fee, but do not account for spreads, slippage, order-book depth, or market gaps. Backtesting and careful validation are required before drawing conclusions about performance.
- CoinGecko's 7-day sparkline is used as an approximate hourly time series. Indicators are experimental, not independently validated.
- No external paid API keys are required. Configurable risk settings live in `config.json`.
