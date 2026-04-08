#property strict

input string ServerUrl = "http://127.0.0.1/api/trades";
input int TimerSeconds = 1;
input int CandleCount = 10;


string WebRequestErrorText(int errorCode)
{
   switch(errorCode)
   {
      case 4014: return "Function is not allowed. Check EA settings and permissions.";
      case 4060: return "Function is not confirmed. Check terminal permissions.";
      case 5200: return "Invalid or blocked HTTP address. Check WebRequest allowed URLs and endpoint.";
      case 5201: return "Connection failed. Server unreachable or wrong host/port.";
      case 5202: return "Timeout. Server too slow or unreachable.";
      case 5203: return "HTTP request failed while sending or receiving data.";
      default:   return "Unknown error.";
   }
}


int CountTrackedTrades()
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;

      if(OrderType() != OP_BUY && OrderType() != OP_SELL)
         continue;

      count++;
   }
   return count;
}


string JsonEscape(string value)
{
   string out = value;
   StringReplace(out, "\\", "\\\\");
   StringReplace(out, "\"", "\\\"");
   StringReplace(out, "\r", "\\r");
   StringReplace(out, "\n", "\\n");
   StringReplace(out, "\t", "\\t");
   return out;
}


string TradeToJson(int index)
{
   if(!OrderSelect(index, SELECT_BY_POS, MODE_TRADES))
      return "";

   if(OrderType() != OP_BUY && OrderType() != OP_SELL)
      return "";

   string symbol = OrderSymbol();
   int symbolDigits = (int)MarketInfo(symbol, MODE_DIGITS);
   double totalPnl = OrderProfit() + OrderSwap() + OrderCommission();

   string json = "{";
   json += "\"ticket\":" + IntegerToString(OrderTicket()) + ",";
   json += "\"symbol\":\"" + JsonEscape(symbol) + "\",";
   json += "\"open_price\":" + DoubleToString(OrderOpenPrice(), symbolDigits) + ",";
   json += "\"stop_loss\":" + DoubleToString(OrderStopLoss(), symbolDigits) + ",";
   json += "\"take_profit\":" + DoubleToString(OrderTakeProfit(), symbolDigits) + ",";
   json += "\"profit\":" + DoubleToString(totalPnl, 2) + ",";
   json += "\"bid\":" + DoubleToString(MarketInfo(symbol, MODE_BID), symbolDigits) + ",";
   json += "\"ask\":" + DoubleToString(MarketInfo(symbol, MODE_ASK), symbolDigits);
   json += "}";
   return json;
}


string CandleTimeframeToString(int timeframe)
{
   switch(timeframe)
   {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      default:         return "UNKNOWN";
   }
}


string CandleToJson(string symbol, int timeframe, int shift)
{
   datetime openTime = iTime(symbol, timeframe, shift);
   if(openTime <= 0)
      return "";

   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   string json = "{";
   json += "\"symbol\":\"" + JsonEscape(symbol) + "\",";
   json += "\"timeframe\":\"" + CandleTimeframeToString(timeframe) + "\",";
   json += "\"open_time\":" + IntegerToString((int)openTime) + ",";
   json += "\"open\":" + DoubleToString(iOpen(symbol, timeframe, shift), digits) + ",";
   json += "\"high\":" + DoubleToString(iHigh(symbol, timeframe, shift), digits) + ",";
   json += "\"low\":" + DoubleToString(iLow(symbol, timeframe, shift), digits) + ",";
   json += "\"close\":" + DoubleToString(iClose(symbol, timeframe, shift), digits) + ",";
   json += "\"volume\":" + IntegerToString((int)iVolume(symbol, timeframe, shift)) + ",";
   json += "\"is_closed\":" + (shift == 0 ? "false" : "true");
   json += "}";
   return json;
}


string BuildCandlesJson()
{
   string symbol = Symbol();
   int timeframe = PERIOD_M1;
   int availableBars = iBars(symbol, timeframe);
   int maxCandles = CandleCount;

   if(maxCandles < 2)
      maxCandles = 2;

   if(availableBars < maxCandles)
      maxCandles = availableBars;

   string json = "[";
   bool first = true;

   for(int shift = maxCandles - 1; shift >= 0; shift--)
   {
      string candleJson = CandleToJson(symbol, timeframe, shift);
      if(candleJson == "")
         continue;

      if(!first)
         json += ",";

      json += candleJson;
      first = false;
   }

   json += "]";
   return json;
}


string BuildPayload()
{
   string body = "{\"trades\":[";
   bool first = true;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      string tradeJson = TradeToJson(i);
      if(tradeJson == "")
         continue;

      if(!first)
         body += ",";

      body += tradeJson;
      first = false;
   }

   body += "],\"candles\":";
   body += BuildCandlesJson();
   body += "}";
   return body;
}


void SendTrades()
{
   string body = BuildPayload();
   int tradeCount = CountTrackedTrades();

   char payload[];
   ArrayResize(payload, StringLen(body));
   StringToCharArray(body, payload, 0, StringLen(body), CP_UTF8);

   char result[];
   string headers = "Content-Type: application/json\r\n";
   string responseHeaders = "";
   int timeout = 3000;

   Print("Trade sync request url=", ServerUrl,
         " trades=", tradeCount,
         " payload_bytes=", ArraySize(payload),
         " payload=", body);

   ResetLastError();
   int status = WebRequest("POST", ServerUrl, headers, timeout, payload, result, responseHeaders);

   if(status == -1)
   {
      int errorCode = GetLastError();
      Print("WebRequest failed. url=", ServerUrl,
            " trades=", tradeCount,
            " error=", errorCode,
            " message=", WebRequestErrorText(errorCode));
      return;
   }

   string response = CharArrayToString(result, 0, -1, CP_UTF8);
   Print("Trade sync response. url=", ServerUrl,
         " status=", status,
         " trades=", tradeCount,
         " headers=", responseHeaders,
         " body=", response);
}


int OnInit()
{
   Print("EA init. timer_seconds=", TimerSeconds, " server_url=", ServerUrl);
   EventSetTimer(TimerSeconds);
   return(INIT_SUCCEEDED);
}


void OnDeinit(const int reason)
{
   Print("EA deinit. reason=", reason);
   EventKillTimer();
}


void OnTimer()
{
   SendTrades();
}
