#property strict

input string ServerUrl = "http://127.0.0.1/api/trades";
input int TimerSeconds = 1;


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

   body += "]}";
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
