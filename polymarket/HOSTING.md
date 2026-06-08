# Live trader: setup + hosting

## Strategy recap

After 30-day replay validation (8639 markets, 9.4M trades, 1209 conservative
trades after fees & realistic ask-side fills):

- Trigger: Binance BTC 60s return ≥ 0.10%
- Entry: lift the ask on the matching Up/Down token, but only if ask ∈ [0.30, 0.70]
- Size: $5 USDC marketable FOK
- Win rate: 84% overall, **93% in the sweet-spot bucket**
- Avg payoff after 200bp fees: +0.17 per unit (sweet spot: +0.39)
- 31/31 positive days, max drawdown −$0.37
- Beats random direction with p<0.001 (2000 permutations)

## Wallet setup

You need a Polygon (chain id 137) wallet that holds USDC and a tiny bit of MATIC.

1. **Create a fresh wallet** (Metamask, Rabby, or `cast wallet new` — do *not*
   reuse a wallet that holds other funds). Save the seed phrase offline.
2. **Fund it** with ~$20-50 USDC.e (bridged USDC) on Polygon and ~$1 MATIC for gas.
3. **Approve Polymarket on the wallet**: visit https://polymarket.com, connect
   the wallet, and complete the one-time onboarding (USDC + Conditional-Token
   allowances). This is by far the easiest way; doing it from CLI requires
   sending three ERC-20 approval transactions.
4. **Export the private key** and put it in `polymarket/.env`:
   ```
   POLY_PRIVATE_KEY=0xabc...
   POLY_DRY_RUN=true
   POLY_MAX_ORDER_USDC=5
   POLY_MAX_DAILY_USDC=50
   ```
5. **Run the setup script** to derive API creds:
   ```
   ../backtest/.venv/bin/python setup_wallet.py
   ```
   This prints the address it derived (verify it matches the wallet you funded)
   and caches `polymarket_creds.json` locally.

## Where to host

The strategy needs to react within ~30 seconds of a BTC move. Latency to
Polymarket's CLOB (AWS us-east-1) matters; latency to Binance (Tokyo) less so
since we stream their websocket.

### Recommended: DigitalOcean droplet in NYC1 or NYC3

- **Size**: `s-1vcpu-1gb` ($6/mo) is enough — the bot is CPU-idle.
- **Region**: `NYC1` or `NYC3` — closest to Polymarket's infra.
- **Image**: Ubuntu 24.04.
- **Setup**:
  ```
  apt update && apt install -y python3.12-venv git
  git clone <your repo>
  cd trading-bot/polymarket
  python3.12 -m venv ../backtest/.venv
  ../backtest/.venv/bin/pip install -r requirements.txt
  ../backtest/.venv/bin/pip install py-clob-client python-dotenv
  cp .env.example .env  # fill in real values
  ../backtest/.venv/bin/python setup_wallet.py
  # Always-on with systemd or screen:
  screen -dmS bot ./run_live.sh
  ```

### Alternatives

- **Fly.io** — easy to deploy a small VM, ~$5/mo, US East regions available.
- **Hetzner Cloud** — cheaper ($4/mo) but their US Ashburn region is the only
  one close enough; EU regions add 80ms.
- **AWS EC2 `t4g.small` in us-east-1** — best latency, ~$12/mo, more setup.
- **Your laptop** — fine for the first 24h of dry-run validation. Don't sleep it.

### What NOT to use

- AWS Lambda or other "serverless" — cold starts blow our 30s budget.
- Anywhere in Asia or EU — adds 150ms+ to every CLOB call.
- Free tiers that pause idle instances.

## Recommended rollout

1. **Day 0**: Run on your laptop with `POLY_DRY_RUN=true`. Verify orders are
   being *intended* at sensible ask prices and counts match the backtest (~40/day).
2. **Day 1**: Deploy to DigitalOcean, still in dry-run. Verify uptime + log
   rotation works.
3. **Day 2**: Set `POLY_DRY_RUN=false`. Use `POLY_MAX_ORDER_USDC=5` and
   `POLY_MAX_DAILY_USDC=20`. Real fills, tiny size. Watch closely.
4. **Day 5+**: If realized PnL matches backtest within 50%, scale to
   `POLY_MAX_ORDER_USDC=25` and `POLY_MAX_DAILY_USDC=200`.

If at any point the bot diverges meaningfully from the backtest (e.g.
fills happen at noticeably worse prices, win rate drops below 70%, or
daily PnL goes negative twice in a row), stop trading and re-examine.
