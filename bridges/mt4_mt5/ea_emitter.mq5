//+------------------------------------------------------------------+
//| MT4/MT5 EA emitter for the Hermes / Noble Trader heartbeat bridge |
//| Drop-in logic: call EmitHeartbeat() whenever your EA generates a |
//| buy/sell/neutral decision with entry/SL/TP.                      |
//|                                                                    |
//| Transport A (default): file-drop -> bridges/mt4_mt5/bridge_relay  |
//|   writes <MT5 DataFolder>/Files/hermes_heartbeats.jsonl          |
//| Transport B: WebRequest POST -> http://127.0.0.1:9100/heartbeat   |
//|   (add URL to Tools->Options->EA->Allow WebRequest)              |
//+------------------------------------------------------------------+
#property strict

input bool   UseWebRequest   = false;        // false=file-drop, true=HTTP
input string BridgeHttpUrl   = "http://127.0.0.1:9100/heartbeat";
input double BrickMult       = 10.0;         // brick_size = tick_size * BrickMult
input double KellyFraction   = 0.02;         // fixed EA sizing fraction (Hermes reads this)

//--- shared field builder
string BuildHeartbeat(string symbol, int signal, double entry, double sl, double tp)
{
   long   ts   = (long)TimeCurrent() * 1000;
   double tick = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double brick= (tick > 0 ? tick : _Point) * BrickMult;
   string dir  = (signal > 0) ? "buy" : (signal < 0 ? "sell" : "neutral");
   string mk   = (signal > 0) ? "UP"  : (signal < 0 ? "DOWN" : "FLAT");

   // NobleTraderHeartbeat schema (required + safe defaults) — see schemas/heartbeat.py
   return StringFormat(
      "{\"symbol\":\"%s\",\"ts\":%lld,\"signal\":\"%s\",\"entry_price\":%f,"
      "\"stop_loss\":%f,\"take_profit\":%f,\"aggression\":\"mid\","
      "\"brick_size\":%f,\"sl_bricks\":%f,\"tp_bricks\":%f,"
      "\"regime\":\"ea_native\",\"regime_conf\":0.5,\"regime_shift\":\"false\","
      "\"shift_at\":0,\"shifts_24h\":0,"
      "\"kelly_f\":%f,\"effective_kelly\":%f,"
      "\"ev\":0.0,\"ev_per_dollar\":0.0,\"p_win\":0.5,\"p_regime\":0.5,"
      "\"p_imbalance\":0.5,\"p_markov\":0.5,\"ev_scale\":1.0,"
      "\"markov_current_state\":\"%s\"}",
      symbol, ts, dir, entry, sl, tp, brick,
      (entry - sl) / brick, (tp - entry) / brick,
      KellyFraction, KellyFraction, mk);
}

//--- Transport A: file-drop (append jsonl)
void EmitHeartbeat(string symbol, int signal, double entry, double sl, double tp)
{
   string j = BuildHeartbeat(symbol, signal, entry, sl, tp);

   if(UseWebRequest)
   {
      char   post[], resp[];
      string hdr = "Content-Type: application/json\r\n";
      StringToCharArray(j, post, 0, StringLen(j));
      int code = WebRequest("POST", BridgeHttpUrl, hdr, 3000, post, resp);
      if(code != 200) Print("Bridge HTTP fail code=", code);
      return;
   }

   string fn = "hermes_heartbeats.jsonl";   // FILE_COMMON -> shared Data Folder
   int h = FileOpen(fn, FILE_READ | FILE_WRITE | FILE_TXT | FILE_COMMON | FILE_ANSI);
   if(h == INVALID_HANDLE) { Print("Bridge: cannot open ", fn); return; }
   FileSeek(h, 0, SEEK_END);
   FileWrite(h, j);
   FileClose(h);
}

//--- Example hook (wire into your EA's signal logic)
void OnTick()
{
   // ... your strategy computes signal/entry/sl/tp ...
   // int sig = ...; double ep=..., sl=..., tp=...;
   // EmitHeartbeat(_Symbol, sig, ep, sl, tp);
}
//+------------------------------------------------------------------+
