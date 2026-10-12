"""APEX V5.2 aggressive PAPER trader. Never sends live orders."""
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
DOC = ROOT / 'docs'
DOC.mkdir(exist_ok=True)
CFG = json.loads((ROOT / 'config.json').read_text())
STATE = ROOT / 'paper_state.json'
NOW = datetime.now(timezone.utc)
STAMP = NOW.isoformat(timespec='seconds')
TODAY = NOW.date().isoformat()

EXCLUDED_IDS = {
    'dai','usds','usd-coin','tether','first-digital-usd',
    'paypal-usd','true-usd','frax','ethena-usde','usdd',
    'pax-dollar','ripple-usd','tether-gold','pax-gold',
    'gemini-dollar','usual-usd','usde','global-dollar',
    'usdb','staked-usde','wrapped-steth','wrapped-bitcoin',
    'coinbase-wrapped-btc'
}

EXCLUDED_SYMBOLS = {
    'USDT','USDC','DAI','USDS','FDUSD','PYUSD','TUSD',
    'FRAX','USDE','USDD','PAXG','XAUT','RLUSD','GUSD',
    'USD1','USDG','USDB','WBTC','WETH','WSTETH','SUSDE'
}


def eligible(c):
    name = str(c.get('name', '')).lower()
    symbol = str(c.get('symbol', '')).upper()
    return (
        c.get('id') not in EXCLUDED_IDS
        and symbol not in EXCLUDED_SYMBOLS
        and not any(
            x in name for x in (
                'stablecoin',
                'tether gold',
                'pax gold',
                'wrapped bitcoin',
                'wrapped ether',
                'synthetic usd'
            )
        )
    )


def initial():
    return {
        'cash': float(CFG['starting_balance_aud']),
        'positions': {},
        'trades': [],
        'equity_history': [],
        'last_scan': None
    }


def load():
    if not STATE.exists():
        return initial()

    s = json.loads(STATE.read_text())

    if not isinstance(s, dict) or not all(
        k in s for k in (
            'cash',
            'positions',
            'trades',
            'equity_history'
        )
    ):
        raise ValueError(
            'Invalid paper_state.json; refusing to reset balance'
        )

    return s


def ema(vals, span):
    if not vals:
        return 0.0

    v = float(vals[0])
    alpha = 2 / (span + 1)

    for x in vals[1:]:
        v = alpha * float(x) + (1 - alpha) * v

    return v


def rsi(vals, period=14):
    if len(vals) < period + 1:
        return 50.0

    changes = [
        b - a for a, b in zip(
            vals[-period-1:-1],
            vals[-period:]
        )
    ]

    gain = sum(max(x, 0) for x in changes) / period
    loss = sum(max(-x, 0) for x in changes) / period

    return (
        100.0 if loss == 0
        else 100 - 100 / (1 + gain / loss)
    )


def signal(c):
    prices = (
        (c.get('sparkline_in_7d') or {}).get('price') or []
    )

    prices = [
        float(p) for p in prices
        if isinstance(p, (int, float)) and p > 0
    ]

    price = float(c.get('current_price') or 0)

    if price <= 0 or len(prices) < 80:
        return None

    prices[-1] = price

    fast = ema(prices, 12)
    slow = ema(prices, 26)

    macds = [
        ema(prices[:i+1], 12) - ema(prices[:i+1], 26)
        for i in range(26, len(prices))
    ]

    macd = macds[-1]
    macd_signal = ema(macds, 9)

    relative = rsi(prices)

    day = float(
        c.get('price_change_percentage_24h') or 0
    )

    week = float(
        c.get('price_change_percentage_7d_in_currency') or 0
    )

    volume = float(c.get('total_volume') or 0)

    liquid = (
        volume >= float(CFG['min_24h_volume_aud'])
    )

    trend = (
        max(-15, min(15, (fast / slow - 1) * 1600))
        if slow else 0
    )

    strength = max(
        -12,
        min(12, (macd - macd_signal) / price * 9000)
    )

    quality = max(
        -12,
        12 - abs(relative - 56) * 0.8
    )

    momentum = (
        max(-10, min(10, day * 1.8))
        + max(-7, min(7, week * 0.65))
    )

    score = round(
        max(
            0,
            min(
                100,
                50 + trend + strength + quality
                + momentum + (4 if liquid else -25)
            )
        ),
        1
    )

    confirmations = sum((
        fast > slow,
        macd > macd_signal,
        42 <= relative <= 68,
        day > 0,
        week > 0
    ))

    buy = (
        eligible(c)
        and liquid
        and confirmations >= int(
            CFG.get('min_confirmations', 4)
        )
        and score >= float(CFG['min_signal_score'])
    )

    reasons = [
        f'EMA 12/26: {"bullish" if fast > slow else "bearish"}',
        f'MACD: {"bullish" if macd > macd_signal else "bearish"}',
        f'RSI: {relative:.1f}',
        f'24h: {day:+.2f}%',
        f'7d: {week:+.2f}%',
        f'Confirmations: {confirmations}/5'
    ]

    return {
        'id': c['id'],
        'symbol': c['symbol'].upper(),
        'name': c['name'],
        'price': price,
        'score': score,
        'signal': 'BUY' if buy else 'WAIT',
        'rsi': round(relative, 1),
        'day': round(day, 2),
        'week': round(week, 2),
        'volume': volume,
        'reasons': reasons,
        'confirmations': confirmations,
        'eligible': eligible(c),
        'sparkline': prices[
            ::max(1, len(prices) // 48)
        ][-48:]
    }


def get_json(url, params):
    response = requests.get(
        url,
        params=params,
        timeout=35,
        headers={
            'accept': 'application/json',
            'user-agent': 'APEX-V5.2-paper-trader/1.0'
        }
    )

    response.raise_for_status()
    return response.json()


def markets():
    data = get_json(
        'https://api.coingecko.com/api/v3/coins/markets',
        {
            'vs_currency': 'aud',
            'order': 'market_cap_desc',
            'per_page': int(CFG['coin_count']),
            'page': 1,
            'sparkline': 'true',
            'price_change_percentage': '7d'
        }
    )

    if not isinstance(data, list) or not data:
        raise ValueError('Market API returned no coins')

    return data


def held_prices(ids):
    if not ids:
        return {}

    data = get_json(
        'https://api.coingecko.com/api/v3/simple/price',
        {
            'ids': ','.join(ids),
            'vs_currencies': 'aud'
        }
    )

    return {
        k: float(v['aud'])
        for k, v in data.items()
        if isinstance(v, dict)
        and float(v.get('aud') or 0) > 0
    }


def equity(s, prices):
    return round(
        float(s['cash']) + sum(
            float(p['quantity']) * prices.get(
                k, float(p['entry'])
            )
            for k, p in s['positions'].items()
        ),
        2
    )


def mark_equity(s, prices):
    return float(s['cash']) + sum(
        float(p['quantity']) * prices.get(
            k, float(p['entry'])
        )
        for k, p in s['positions'].items()
    )


def run():
    s = load()
    error = None
    items = []
    actions = []
    prices = {}

    try:
        raw = markets()

        items = [
            x for x in (signal(c) for c in raw)
            if x
        ]

        items.sort(
            key=lambda x: (
                x['signal'] == 'BUY',
                x['score']
            ),
            reverse=True
        )

        prices = {
            x['id']: x['price']
            for x in items
        }

        missing = [
            k for k in s['positions']
            if k not in prices
        ]

        if missing:
            prices.update(held_prices(missing))

        unpriced = [
            k for k in s['positions']
            if k not in prices
        ]

        if unpriced:
            raise ValueError(
                'No fresh prices for holdings: '
                + ', '.join(unpriced)
            )

        risk = s.setdefault('risk', {})

        if risk.get('date') != TODAY:
            risk.update({
                'date': TODAY,
                'start_equity': mark_equity(s, prices),
                'locked': False
            })

        start = float(risk['start_equity'])

        if (
            start > 0
            and mark_equity(s, prices)
            <= start * (
                1 - float(CFG['daily_loss_limit_pct'])
            )
        ):
            risk['locked'] = True

        for k, pos in list(s['positions'].items()):
            price = prices[k]
            entry = float(pos['entry'])

            pos['peak_price'] = max(
                float(pos.get('peak_price', entry)),
                price
            )

            pnl = price / entry - 1

            peak_gain = (
                pos['peak_price'] / entry - 1
            )

            current = next(
                (x for x in items if x['id'] == k),
                None
            )

            reason = None

            if (
                pos['symbol'].upper() in EXCLUDED_SYMBOLS
                or (current and not current['eligible'])
            ):
                reason = 'excluded asset'

            elif pnl <= -float(CFG['stop_loss_pct']):
                reason = 'stop loss'

            elif (
                peak_gain >= float(
                    CFG['trailing_activation_pct']
                )
                and price <= pos['peak_price'] * (
                    1 - float(CFG['trailing_stop_pct'])
                )
            ):
                reason = 'trailing stop'

            elif pnl >= float(CFG['take_profit_pct']):
                reason = 'take profit'

            elif current and current['score'] < 45:
                reason = 'momentum reversal'

            if reason:
                fill = price * (
                    1 - float(
                        CFG.get('slippage_rate', 0)
                    )
                )

                proceeds = (
                    float(pos['quantity'])
                    * fill
                    * (1 - float(CFG['fee_rate']))
                )

                s['cash'] += proceeds

                trade = {
                    'at': STAMP,
                    'side': 'SELL',
                    'symbol': pos['symbol'],
                    'price': fill,
                    'quantity': pos['quantity'],
                    'pnl_aud': round(
                        proceeds - float(pos['cost']),
                        2
                    ),
                    'reason': reason
                }

                s['trades'].append(trade)
                actions.append(trade)

                del s['positions'][k]

        if (
            start > 0
            and mark_equity(s, prices)
            <= start * (
                1 - float(CFG['daily_loss_limit_pct'])
            )
        ):
            risk['locked'] = True

        if not risk['locked']:
            for x in items:
                if (
                    len(s['positions'])
                    >= int(CFG['max_positions'])
                ):
                    break

                if (
                    x['signal'] != 'BUY'
                    or x['id'] in s['positions']
                ):
                    continue

                budget = min(
                    mark_equity(s, prices)
                    * float(CFG['max_position_pct']),
                    float(s['cash']) * 0.98
                )

                if budget < 1:
                    break

                fill = x['price'] * (
                    1 + float(
                        CFG.get('slippage_rate', 0)
                    )
                )

                qty = budget / (
                    fill * (
                        1 + float(CFG['fee_rate'])
                    )
                )

                s['cash'] -= budget

                s['positions'][x['id']] = {
                    'symbol': x['symbol'],
                    'quantity': qty,
                    'entry': fill,
                    'cost': budget,
                    'opened': STAMP,
                    'peak_price': fill
                }

                trade = {
                    'at': STAMP,
                    'side': 'BUY',
                    'symbol': x['symbol'],
                    'price': fill,
                    'quantity': qty,
                    'pnl_aud': None,
                    'reason': (
                        'Score ' + str(x['score'])
                        + '; '
                        + ', '.join(x['reasons'])
                    )
                }

                s['trades'].append(trade)
                actions.append(trade)

        s['last_scan'] = STAMP

        s['equity_history'].append({
            'at': STAMP,
            'equity': equity(s, prices)
        })

        s['equity_history'] = (
            s['equity_history'][-2000:]
        )

        s['trades'] = s['trades'][-500:]

        temp = STATE.with_suffix('.json.tmp')

        temp.write_text(
            json.dumps(s, indent=2)
        )

        temp.replace(STATE)

    except Exception as exc:
        error = (
            f'{type(exc).__name__}: {exc}'
        )

        print('Market scan failed:', error)

    positions = [
        {
            'id': k,
            **p,
            'market_price': prices.get(
                k, p['entry']
            ),
            'value': round(
                float(p['quantity'])
                * prices.get(
                    k, float(p['entry'])
                ),
                2
            )
        }
        for k, p in s['positions'].items()
    ]

    total = equity(s, prices)

    sells = [
        t for t in s['trades']
        if t.get('side') == 'SELL'
        and isinstance(
            t.get('pnl_aud'),
            (int, float)
        )
    ]

    wins = sum(
        t['pnl_aud'] > 0
        for t in sells
    )

    history = [
        float(h['equity'])
        for h in s['equity_history']
        if isinstance(
            h.get('equity'),
            (int, float)
        )
    ]

    peak = history[0] if history else total
    max_dd = 0.0

    for value in history:
        peak = max(peak, value)

        if peak > 0:
            max_dd = max(
                max_dd,
                (peak - value) / peak
            )

    payload = {
        'app': 'APEX V5.2 AGGRESSIVE',
        'mode': 'PAPER ONLY',
        'currency': 'AUD',
        'updated': STAMP,
        'last_successful_scan': s.get('last_scan'),
        'error': error,
        'scanned': len(items),
        'equity': total,
        'cash': round(float(s['cash']), 2),
        'pnl': round(
            total - float(
                CFG['starting_balance_aud']
            ),
            2
        ),
        'positions': positions,
        'trades': s['trades'][-100:][::-1],
        'history': s['equity_history'],
        'opportunities': [
            x for x in items
            if x['eligible']
        ][:10],
        'markets': [
            x for x in items
            if x['eligible']
        ],
        'analytics': {
            'closed_trades': len(sells),
            'win_rate_pct': (
                round(
                    100 * wins / len(sells),
                    1
                )
                if sells else None
            ),
            'max_drawdown_pct': round(
                max_dd * 100,
                2
            ),
            'daily_loss_locked': (
                s.get('risk', {}).get(
                    'locked', False
                )
            )
        },
        'workers': [
            {
                'name': 'Researcher',
                'detail': (
                    f'{len(items)} markets analysed; '
                    'CoinGecko 7-day sparklines '
                    '(NOT 15-minute OHLCV)'
                ),
                'status': (
                    'OK' if items else 'ERROR'
                )
            },
            {
                'name': 'Analyst',
                'detail': (
                    'EMA 12/26, MACD, RSI, '
                    'momentum; aggressive '
                    'score threshold'
                ),
                'status': (
                    'OK' if items else 'WAIT'
                )
            },
            {
                'name': 'Risk Manager',
                'detail': (
                    f'{CFG["max_positions"]} '
                    'max positions, '
                    f'{CFG["max_position_pct"]:.0%} '
                    'allocation, '
                    f'{CFG["stop_loss_pct"]:.0%} '
                    'stop, trailing exit, '
                    'daily lockout'
                ),
                'status': (
                    'LOCKED'
                    if s.get('risk', {}).get('locked')
                    else 'ACTIVE'
                )
            },
            {
                'name': 'Execution Manager',
                'detail': (
                    f'{len(actions)} simulated '
                    'orders; fees and estimated '
                    'slippage; no live orders'
                ),
                'status': 'PAPER'
            }
        ]
    }

    (DOC / 'data.json').write_text(
        json.dumps(
            payload,
            separators=(',', ':')
        )
    )

    print(
        'Scanned', len(items),
        'equity AUD', total,
        'orders', len(actions),
        'error', error
    )

    if error:
        raise SystemExit(1)


if __name__ == '__main__':
    run()
