import json, math, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests
ROOT=Path(__file__).parent; DOC=ROOT/'docs'; DOC.mkdir(exist_ok=True)
CFG=json.loads((ROOT/'config.json').read_text()); STATE=ROOT/'paper_state.json'
NOW=datetime.now(timezone.utc).isoformat(timespec='seconds')
EXCLUDED_IDS={'dai','usds','usd-coin','tether','first-digital-usd','paypal-usd','true-usd','frax','ethena-usde','usdd','pax-dollar','ripple-usd','tether-gold','pax-gold','gemini-dollar','usual-usd','usde','global-dollar','usdb','staked-usde','wrapped-steth','wrapped-bitcoin','coinbase-wrapped-btc'}
EXCLUDED_SYMBOLS={'USDT','USDC','DAI','USDS','FDUSD','PYUSD','TUSD','FRAX','USDE','USDD','PAXG','XAUT','RLUSD','GUSD','USD1','USDG','USDB','WBTC','WETH','WSTETH','SUSDE'}
def eligible(coin):
    name=str(coin.get('name','')).lower(); symbol=str(coin.get('symbol','')).upper()
    return coin.get('id') not in EXCLUDED_IDS and symbol not in EXCLUDED_SYMBOLS and not any(x in name for x in ('stablecoin','tether gold','pax gold','wrapped bitcoin','wrapped ether','synthetic usd'))

def initial(): return {'cash':float(CFG['starting_balance_aud']),'positions':{},'trades':[],'equity_history':[],'last_scan':None}
def load():
    try: return json.loads(STATE.read_text())
    except (OSError,ValueError): return initial()
def ema(values,span):
    if not values:return 0
    v=float(values[0]); alpha=2/(span+1)
    for x in values[1:]:v=alpha*float(x)+(1-alpha)*v
    return v
def rsi(values,period=14):
    if len(values)<period+1:return 50.
    dif=[b-a for a,b in zip(values[-period-1:-1],values[-period:])]
    up=sum(max(x,0) for x in dif)/period; down=sum(max(-x,0) for x in dif)/period
    return 100 if down==0 else 100-100/(1+up/down)
def signal(coin):
    prices=(coin.get('sparkline_in_7d') or {}).get('price') or []
    prices=[float(x) for x in prices if isinstance(x,(float,int)) and x>0]
    price=float(coin.get('current_price') or 0)
    if price<=0 or len(prices)<80:return None
    prices[-1]=price
    fast=ema(prices,12); slow=ema(prices,26)
    macd_series=[]
    for i in range(26,len(prices)):
        macd_series.append(ema(prices[:i+1],12)-ema(prices[:i+1],26))
    macd=macd_series[-1]; macd_sig=ema(macd_series,9)
    relative=rsi(prices)
    day=float(coin.get('price_change_percentage_24h') or 0)
    week=float(coin.get('price_change_percentage_7d_in_currency') or 0)
    volume=float(coin.get('total_volume') or 0)
    liquid=volume>=CFG['min_24h_volume_aud']
    # Continuous score components vary by asset; score is NOT a probability of profit.
    trend=max(-15,min(15,(fast/slow-1)*1600)) if slow else 0
    macd_strength=max(-12,min(12,(macd-macd_sig)/price*9000))
    rsi_quality=max(-12,12-abs(relative-56)*0.8)
    momentum=max(-10,min(10,day*1.8))+max(-7,min(7,week*.65))
    score=round(max(0,min(100,50+trend+macd_strength+rsi_quality+momentum+(4 if liquid else -25))),1)
    confirmations=sum((fast>slow,macd>macd_sig,42<=relative<=68,day>0,week>0))
    buy=eligible(coin) and liquid and confirmations>=4 and score>=CFG['min_signal_score']
    reasons=[f'EMA 12/26: {"bullish" if fast>slow else "bearish"}',f'MACD: {"bullish" if macd>macd_sig else "bearish"}',f'RSI: {relative:.1f}',f'24h: {day:+.2f}%',f'7d: {week:+.2f}%',f'Confirmations: {confirmations}/5']
    return {'id':coin['id'],'symbol':coin['symbol'].upper(),'name':coin['name'],'price':price,'score':score,'signal':'BUY' if buy else 'WAIT','rsi':round(relative,1),'day':round(day,2),'week':round(week,2),'volume':volume,'reasons':reasons,'confirmations':confirmations,'eligible':eligible(coin),'sparkline':prices[::max(1,len(prices)//48)][-48:]}
def markets():
    url='https://api.coingecko.com/api/v3/coins/markets'
    response=requests.get(url,params={'vs_currency':'aud','order':'market_cap_desc','per_page':CFG['coin_count'],'page':1,'sparkline':'true','price_change_percentage':'7d'},timeout=35,headers={'accept':'application/json','user-agent':'APEX-V5-paper-trader/1.0'})
    response.raise_for_status(); data=response.json()
    if not isinstance(data,list) or not data:raise ValueError('Market API returned no coins')
    return data
def equity(s,lookup):return round(s['cash']+sum(v['quantity']*lookup.get(k,v['entry']) for k,v in s['positions'].items()),2)
def run():
    s=load(); error=None; items=[]; actions=[]
    try:
        raw=markets(); items=[x for x in (signal(c) for c in raw) if x]; items.sort(key=lambda x:(x['signal']=='BUY',x['score']),reverse=True)
        lookup={x['id']:x['price'] for x in items}
        for k,pos in list(s['positions'].items()):
            price=lookup.get(k)
            if not price:continue
            pnl=(price/pos['entry']-1)
            current=next((x for x in items if x['id']==k),None)
            reason='excluded asset (V5.1 cleanup)' if (current and not current['eligible']) or pos['symbol'].upper() in EXCLUDED_SYMBOLS else 'stop loss' if pnl<=-CFG['stop_loss_pct'] else 'take profit' if pnl>=CFG['take_profit_pct'] else 'signal reversal' if current and current['score']<40 else None
            if reason:
                proceeds=pos['quantity']*price*(1-CFG['fee_rate']);s['cash']+=proceeds
                trade={'at':NOW,'side':'SELL','symbol':pos['symbol'],'price':price,'quantity':pos['quantity'],'pnl_aud':round(proceeds-pos['cost'],2),'reason':reason}
                s['trades'].append(trade);actions.append(trade);del s['positions'][k]
        for x in items:
            if len(s['positions'])>=CFG['max_positions']:break
            if x['signal']!='BUY' or x['id'] in s['positions'] or x['volume']<CFG['min_24h_volume_aud']:continue
            total=equity(s,lookup); budget=min(total*CFG['max_position_pct'],s['cash']*0.98)
            if budget<1:break
            qty=budget/(x['price']*(1+CFG['fee_rate']))
            s['cash']-=budget;s['positions'][x['id']]={'symbol':x['symbol'],'quantity':qty,'entry':x['price'],'cost':budget,'opened':NOW}
            trade={'at':NOW,'side':'BUY','symbol':x['symbol'],'price':x['price'],'quantity':qty,'pnl_aud':None,'reason':'Score '+str(x['score'])+'; '+', '.join(x['reasons'])}
            s['trades'].append(trade);actions.append(trade)
        s['last_scan']=NOW;s['equity_history'].append({'at':NOW,'equity':equity(s,lookup)})
        s['equity_history']=s['equity_history'][-2000:];s['trades']=s['trades'][-500:]
        STATE.write_text(json.dumps(s,indent=2))
    except Exception as exc:
        error=f'{type(exc).__name__}: {exc}'
        print('Market scan failed:',error)
    prices={x['id']:x['price'] for x in items}
    positions=[{'id':k,**v,'market_price':prices.get(k,v['entry']),'value':round(v['quantity']*prices.get(k,v['entry']),2)} for k,v in s['positions'].items()]
    total=equity(s,prices)
    payload={'app':'APEX V5.1','mode':'PAPER ONLY','currency':'AUD','updated':NOW,'last_successful_scan':s['last_scan'],'error':error,'scanned':len(items),'equity':total,'cash':round(s['cash'],2),'pnl':round(total-CFG['starting_balance_aud'],2),'positions':positions,'trades':s['trades'][-100:][::-1],'history':s['equity_history'],'opportunities':[x for x in items if x['eligible']][:10],'markets':[x for x in items if x['eligible']],'workers':[{'name':'Researcher','detail':f'{len(items)} markets analysed; public CoinGecko market data','status':'OK' if items else 'ERROR'},{'name':'Analyst','detail':'Per-coin EMA 12/26, MACD, RSI and momentum; 4/5 confirmations required','status':'OK' if items else 'WAIT'},{'name':'Risk Manager','detail':f'Max {CFG["max_positions"]} positions, {CFG["max_position_pct"]:.0%} each, stop {CFG["stop_loss_pct"]:.0%}, take profit {CFG["take_profit_pct"]:.0%}','status':'ACTIVE'},{'name':'Execution Manager','detail':f'{len(actions)} simulated orders this scan; no real exchange orders','status':'PAPER'}]}
    (DOC/'data.json').write_text(json.dumps(payload,separators=(',',':')))
    print('Scanned',len(items),'equity AUD',total,'orders',len(actions),'error',error)
    if error:raise SystemExit(1)
if __name__=='__main__':run()
