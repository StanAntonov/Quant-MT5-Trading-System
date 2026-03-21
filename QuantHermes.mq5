#include <Trade/Trade.mqh>
CTrade trade;

input string API_URL = "http://127.0.0.1:8000/signal";
input int TimerSeconds = 5; // Polling every 5 seconds for precision
input int MaxRetries = 3;

//Init
int OnInit() {
   EventSetTimer(TimerSeconds);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
   EventKillTimer();
}

//Main loop
void OnTimer() {
   //Friday Risk Management (Hard Cutoff)
   MqlDateTime t;
   TimeCurrent(t);
   if(t.day_of_week == 5 && t.hour >= 20) {
      CloseAllPositions();
      return;
   }

   //2. Trailing/Position Management
   if(PositionsTotal() > 0) {
      ManageTrailing();
      return; 
   }

   //3. Fetch Signal with Retry Logic
   string response = "";
   for(int i = 0; i < MaxRetries; i++) {
      response = HttpGet(API_URL);
      if(response != "") break;
      Sleep(200); // 200ms gap between retries
   }
   
   if(response == "") return;

   //4. Parse & Execute
   string action = GetJsonValue(response, "action");
   if(action != "BUY" && action != "SELL") return;

   double risk_perc = StringToDouble(GetJsonValue(response, "risk_perc"));
   double sl_points = StringToDouble(GetJsonValue(response, "sl_points"));
   double tp_rr     = StringToDouble(GetJsonValue(response, "tp_rr"));

   ExecuteTrade(action, risk_perc, sl_points, tp_rr);
}

//EXECUTION ENGINE
void ExecuteTrade(string action, double risk_perc, double sl_points, double tp_rr) {
   double price = (action == "BUY") ? SymbolInfoDouble(_Symbol, SYMBOL_ASK) 
                                    : SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl, tp;
   
   if(action == "BUY") {
      sl = price - sl_points;
      tp = price + (sl_points * tp_rr);
   } else {
      sl = price + sl_points;
      tp = price - (sl_points * tp_rr);
   }

   double lot = CalculateLot(risk_perc, price, sl);
   if(lot <= 0) return;

   //MARGIN CHECK
   double marginReq = 0;
   ENUM_ORDER_TYPE type = (action == "BUY") ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcMargin(type, _Symbol, lot, price, marginReq)) return;
   
   if(marginReq > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.8) {
      Print("Risk Manager: Insufficient Margin.");
      return;
   }

   trade.SetDeviationInPoints(10);
   if(action == "BUY") trade.Buy(lot, _Symbol, price, sl, tp, "QuantBrain V4");
   else trade.Sell(lot, _Symbol, price, sl, tp, "QuantBrain V4");
}

//DEFENSIVE TRAILING
void ManageTrailing() {
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      if(PositionGetSymbol(i) == _Symbol) {
         ulong ticket = PositionGetTicket(i);
         double open = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl   = PositionGetDouble(POSITION_SL);
         double tp   = PositionGetDouble(POSITION_TP);
         long type   = PositionGetInteger(POSITION_TYPE);
         
         double price = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(_Symbol, SYMBOL_BID) 
                                                    : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         
         double profit_dist = MathAbs(price - open);
         double sl_dist     = MathAbs(open - sl);

         // Trail at 1:1 Risk/Reward
         if(profit_dist >= sl_dist) {
            double new_sl = (type == POSITION_TYPE_BUY) ? (price - sl_dist * 0.25) 
                                                        : (price + sl_dist * 0.25);
            
            // Only move Forward (Defensive check)
            if((type == POSITION_TYPE_BUY && new_sl > sl) || (type == POSITION_TYPE_SELL && (new_sl < sl || sl == 0))) {
               trade.PositionModify(ticket, new_sl, tp);
            }
         }
      }
   }
}

//CALCULATE RISK-BASED LOTS
double CalculateLot(double risk_perc, double entry, double sl) {
   double risk_money = AccountInfoDouble(ACCOUNT_EQUITY) * risk_perc;
   double tick_val   = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_size  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   
   double sl_dist = MathAbs(entry - sl);
   if(sl_dist <= 0) return 0;
   
   double money_per_lot = (sl_dist / tick_size) * tick_val;
   double lot = (money_per_lot > 0) ? (risk_money / money_per_lot) : 0;
   
   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   lot = MathFloor(lot / step) * step;
   return MathMax(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN), MathMin(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX), lot));
}

//HTTP UTILITIES
string HttpGet(string url) {
   uchar result[];
   uchar result_headers[];
   string response_string;
   
   int res = WebRequest("GET", url, "", 5000, result, result_headers, response_string);
   
   if(res == -1) {
      Print("API Error: ", GetLastError());
      return "";
   }
   return response_string;
}

//HARDENED JSON PARSER
string GetJsonValue(string json, string key) {
   string search = "\"" + key + "\":";
   int start = StringFind(json, search);
   if(start == -1) return "";
   start += StringLen(search);
   int end = StringFind(json, ",", start);
   if(end == -1) end = StringFind(json, "}", start);
   string value = StringSubstr(json, start, end - start);
   
   //MQL5 In-Place Modification
   StringReplace(value, "\"", "");
   StringReplace(value, " ", "");
   StringTrimLeft(value);
   StringTrimRight(value);
   return value;
}

void CloseAllPositions() {
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      if(PositionGetSymbol(i) == _Symbol) trade.PositionClose(PositionGetTicket(i));
   }
}
