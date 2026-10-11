import json, math, os, time
from datetime import datetime, timezone
from pathlib import Path
import requests
ROOT=Path(__file__).parent; DOC=ROOT/'docs'; DOC.mkdir(exist_ok=True)
CFG=json.loads((ROOT/'config.json').read_text()); STATE=ROOT/'paper_state.json'
NOW=datetime.now(timezone.utc).isoformat(timespec='seconds')
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
    if price<=0 or len(prices)<50:return None
    prices[-1]=price
    fast=ema(prices[-72:],12); slow=ema(prices[-100:],26)
    macd=fast-slow; sig=ema([ema(prices[max(0,i-35):i+1],12)-ema(prices[max(0,i-60):i+1],26) for i in range(max(0,len(prices)-45),len(prices))],9)
    relative=rsi(prices); day=float(coin.get('price_change_percentage_24h') or 0); week=float(coin.get('price_change_percentage_7d_in_currency') or 0)
    volume=float(coin.get('total_volume') or 0)
    # Score is a rule alignment index, NOT a predicted probability of profit.
    score=40+ (16 if fast>slow else -12)+(12 if macd>sig else -8)+(10 if 42<=relative<=68 else -8)+(8 if day>0 else -6)+(8 if week>0 else -5)+(6 if volume>=CFG['min_24h_volume_aud'] else -20)
    score=max(0,min(100,int(score)))
    reasons=[f'EMA trend {"bullish" if fast>slow else "bearish"}',f'MACD {"positive" if macd>sig else "negative"}',f'RSI {relative:.1f}',f'24h {day:+.2f}%',f'7d {week:+.2f}%']
    return {'id':coin['id'],'symbol':coin['symbol'].upper(),'name':coin['name'],'price':price,'score':score,'signal':'BUY' if score>=CFG['min_signal_score'] and fast>slow and macd>sig else 'WAIT','rsi':round(relative,1),'day':round(day,2),'week':round(week,2),'volume':volume,'reasons':reasons,'sparkline':prices[::max(1,len(prices)//48)][-48:]}
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
        raw=markets(); items=[x for x in (signal(c) for c in raw) if x]; items.sort(key=lambda x:x['score'],reverse=True)
        lookup={x['id']:x['price'] for x in items}
        for k,pos in list(s['positions'].items()):
            price=lookup.get(k)
            if not price:continue
            pnl=(price/pos['entry']-1)
            current=next((x for x in items if x['id']==k),None)
            reason='stop loss' if pnl<=-CFG['stop_loss_pct'] else 'take profit' if pnl>=CFG['take_profit_pct'] else 'signal reversal' if current and current['score']<40 else None
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
            trade={'at':NOW,'side':'BUY','symbol':x['symbol'],'price':x['price'],'quantity':qty,'pnl_aud':None,'reason':'Rules passed; score '+str(x['score'])}
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
    payload={'app':'APEX V5','mode':'PAPER ONLY','currency':'AUD','updated':NOW,'last_successful_scan':s['last_scan'],'error':error,'scanned':len(items),'equity':total,'cash':round(s['cash'],2),'pnl':round(total-CFG['starting_balance_aud'],2),'positions':positions,'trades':s['trades'][-100:][::-1],'history':s['equity_history'],'opportunities':items[:10],'markets':items,'workers':[{'name':'Researcher','detail':f'{len(items)} markets analysed; public CoinGecko market data','status':'OK' if items else 'ERROR'},{'name':'Analyst','detail':'EMA 12/26, MACD, RSI, 24h/7d momentum; rule-based scoring','status':'OK' if items else 'WAIT'},{'name':'Risk Manager','detail':f'Max {CFG["max_positions"]} positions, {CFG["max_position_pct"]:.0%} each, stop {CFG["stop_loss_pct"]:.0%}, take profit {CFG["take_profit_pct"]:.0%}','status':'ACTIVE'},{'name':'Execution Manager','detail':f'{len(actions)} simulated orders this scan; no real exchange orders','status':'PAPER'}]}
    (DOC/'data.json').write_text(json.dumps(payload,separators=(',',':')))
    print('Scanned',len(items),'equity AUD',total,'orders',len(actions),'error',error)
    if error:raise SystemExit(1)
if __name__=='__main__':run()
