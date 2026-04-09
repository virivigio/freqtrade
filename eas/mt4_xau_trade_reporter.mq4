#property strict

input string ServerUrl = "http://127.0.0.1/api/trades";
input int TimerSeconds = 1;
input int CandleCount = 10;
input double CommandLotSize = 0.01;
input int CommandSlippagePoints = 30;
input int CommandMagicNumber = 424242;
input string CommandComment = "mt4_trade_monitor";


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


int CountSymbolTrades(string symbol)
{
   int count = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;

      if(OrderSymbol() != symbol)
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


string TradeSideToString(int orderType)
{
   if(orderType == OP_BUY)
      return "BUY";
   if(orderType == OP_SELL)
      return "SELL";
   return "UNKNOWN";
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
   json += "\"side\":\"" + TradeSideToString(OrderType()) + "\",";
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


datetime BrokerTimeToUtc(datetime brokerTime)
{
   int brokerOffsetSeconds = (int)(TimeCurrent() - TimeGMT());
   return brokerTime - brokerOffsetSeconds;
}


datetime NormalizeOpenTime(datetime value, int timeframe)
{
   int timeframeSeconds = PeriodSeconds(timeframe);
   if(timeframeSeconds <= 0)
      return value;
   return value - (value % timeframeSeconds);
}


string CandleToJson(string symbol, int timeframe, int shift)
{
   datetime openTime = iTime(symbol, timeframe, shift);
   if(openTime <= 0)
      return "";

   datetime openTimeUtc = NormalizeOpenTime(BrokerTimeToUtc(openTime), timeframe);

   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   string json = "{";
   json += "\"symbol\":\"" + JsonEscape(symbol) + "\",";
   json += "\"timeframe\":\"" + CandleTimeframeToString(timeframe) + "\",";
   json += "\"open_time\":" + IntegerToString((int)openTimeUtc) + ",";
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


int FindJsonKey(string json, string key)
{
   return StringFind(json, "\"" + key + "\"");
}


string ExtractJsonString(string json, string key, string fallback)
{
   int keyPos = FindJsonKey(json, key);
   if(keyPos < 0)
      return fallback;

   int colonPos = StringFind(json, ":", keyPos);
   if(colonPos < 0)
      return fallback;

   int pos = colonPos + 1;
   while(pos < StringLen(json))
   {
      int ch = StringGetCharacter(json, pos);
      if(ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n')
         break;
      pos++;
   }

   if(pos >= StringLen(json) || StringGetCharacter(json, pos) != '"')
      return fallback;

   int start = pos + 1;
   int end = StringFind(json, "\"", start);
   if(end < 0)
      return fallback;

   return StringSubstr(json, start, end - start);
}


double ExtractJsonNumber(string json, string key, double fallback)
{
   int keyPos = FindJsonKey(json, key);
   if(keyPos < 0)
      return fallback;

   int colonPos = StringFind(json, ":", keyPos);
   if(colonPos < 0)
      return fallback;

   int start = colonPos + 1;
   while(start < StringLen(json))
   {
      int ch = StringGetCharacter(json, start);
      if(ch != ' ' && ch != '\t' && ch != '\r' && ch != '\n')
         break;
      start++;
   }

   int end = start;
   while(end < StringLen(json))
   {
      int ch = StringGetCharacter(json, end);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-')
      {
         end++;
         continue;
      }
      break;
   }

   if(end <= start)
      return fallback;

   return StrToDouble(StringSubstr(json, start, end - start));
}


double NormalizePriceForSymbol(string symbol, double price)
{
   int digits = (int)MarketInfo(symbol, MODE_DIGITS);
   return NormalizeDouble(price, digits);
}


bool OpenTradeFromCommand(string side, double lotSize, double stopLoss, double takeProfit)
{
   string symbol = Symbol();
   int type = OP_BUY;
   if(side == "SELL")
      type = OP_SELL;

   RefreshRates();
   double price = MarketInfo(symbol, type == OP_BUY ? MODE_ASK : MODE_BID);
   if(stopLoss > 0)
      stopLoss = NormalizePriceForSymbol(symbol, stopLoss);
   if(takeProfit > 0)
      takeProfit = NormalizePriceForSymbol(symbol, takeProfit);
   int ticket = OrderSend(
      symbol,
      type,
      lotSize,
      price,
      CommandSlippagePoints,
      stopLoss,
      takeProfit,
      CommandComment,
      CommandMagicNumber,
      0,
      clrNONE
   );

   if(ticket < 0)
   {
      int errorCode = GetLastError();
      Print("Command OPEN failed. symbol=", symbol,
            " side=", side,
            " lot=", DoubleToString(lotSize, 2),
            " error=", errorCode);
      return false;
   }

   Print("Command OPEN executed. symbol=", symbol,
         " side=", side,
         " lot=", DoubleToString(lotSize, 2),
         " ticket=", ticket);
   return true;
}


bool CloseTradesFromCommand()
{
   string symbol = Symbol();
   bool closedAny = false;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
         continue;

      if(OrderSymbol() != symbol)
         continue;

      int type = OrderType();
      if(type != OP_BUY && type != OP_SELL)
         continue;

      RefreshRates();
      double price = (type == OP_BUY) ? MarketInfo(symbol, MODE_BID) : MarketInfo(symbol, MODE_ASK);
      bool ok = OrderClose(OrderTicket(), OrderLots(), price, CommandSlippagePoints, clrNONE);
      if(!ok)
      {
         int errorCode = GetLastError();
         Print("Command CLOSE failed. symbol=", symbol,
               " ticket=", OrderTicket(),
               " error=", errorCode);
         continue;
      }

      closedAny = true;
      Print("Command CLOSE executed. symbol=", symbol,
            " ticket=", OrderTicket());
   }

   return closedAny;
}


void HandleServerCommand(string response)
{
   string action = ExtractJsonString(response, "action", "NONE");
   if(action == "NONE")
      return;

   string symbol = Symbol();
   int symbolTradeCount = CountSymbolTrades(symbol);

   if(action == "OPEN")
   {
      if(symbolTradeCount > 0)
      {
         Print("Command OPEN ignored because symbol already has trades. symbol=", symbol,
               " trade_count=", symbolTradeCount);
         return;
      }

      string side = ExtractJsonString(response, "side", "BUY");
      double lotSize = ExtractJsonNumber(response, "lot", CommandLotSize);
      double stopLoss = ExtractJsonNumber(response, "stop_loss", 0);
      double takeProfit = ExtractJsonNumber(response, "take_profit", 0);
      if(lotSize <= 0)
         lotSize = CommandLotSize;

      OpenTradeFromCommand(side, lotSize, stopLoss, takeProfit);
      return;
   }

   if(action == "CLOSE")
   {
      if(symbolTradeCount <= 0)
      {
         Print("Command CLOSE ignored because symbol has no open trades. symbol=", symbol);
         return;
      }
      CloseTradesFromCommand();
      return;
   }

   Print("Unknown command action received. action=", action, " body=", response);
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

   if(status >= 200 && status < 300)
      HandleServerCommand(response);
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
