
#property strict
#property version "1.00"
#property description "ICT SMC Demo Trading Robot"

#include <Trade/Trade.mqh>
CTrade trade;

// ===== SETTINGS =====
input double RiskPercent = 0.5;
input double RR = 2.0;
input int Lookback = 10;
input int ATRPeriod = 14;
input double DisplacementATR = 0.8;
input double SLBufferATR = 0.15;
input ulong Magic = 6112026;

datetime lastBar = 0;

// ===== ATR =====
double GetATR(MqlRates &r[])
{
   double sum = 0;
   for(int i=1; i<=ATRPeriod; i++)
   {
      double tr = MathMax(r[i].high-r[i].low,
         MathMax(MathAbs(r[i].high-r[i+1].close),
                 MathAbs(r[i].low-r[i+1].close)));
      sum += tr;
   }
   return sum/ATRPeriod;
}

// ===== CHECK EXISTING TRADES =====
bool HasExposure()
{
   for(int i=PositionsTotal()-1; i>=0; i--)
   {
      ulong t=PositionGetTicket(i);
      if(t>0 &&
         PositionGetString(POSITION_SYMBOL)==_Symbol &&
         PositionGetInteger(POSITION_MAGIC)==(long)Magic)
         return true;
   }

   for(int i=OrdersTotal()-1; i>=0; i--)
   {
      ulong t=OrderGetTicket(i);
      if(t>0 &&
         OrderGetString(ORDER_SYMBOL)==_Symbol &&
         OrderGetInteger(ORDER_MAGIC)==(long)Magic)
         return true;
   }
   return false;
}

// ===== LOT CALCULATION =====
double GetLot(ENUM_ORDER_TYPE type,double entry,double sl)
{
   double risk=AccountInfoDouble(ACCOUNT_EQUITY)
               *RiskPercent/100.0;

   double profit=0;
   if(!OrderCalcProfit(type,_Symbol,1.0,entry,sl,profit))
      return 0;

   double loss=MathAbs(profit);
   if(loss<=0) return 0;

   double step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
   double minLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
   double maxLot=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);

   if(step<=0) return 0;

   double lot=MathFloor((risk/loss)/step)*step;
   if(lot<minLot) return 0;

   lot=MathMin(lot,maxLot);
   return NormalizeDouble(lot,4);
}

// ===== ICT / SMC SIGNALS =====
void CheckSignal()
{
   if(HasExposure()) return;

   MqlRates r[];
   ArraySetAsSeries(r,true);

   int count=Lookback+ATRPeriod+5;

   if(CopyRates(_Symbol,PERIOD_M5,0,count,r)<count)
      return;

   double atr=GetATR(r);
   if(atr<=0) return;

   double oldLow=r[3].low;
   double oldHigh=r[3].high;

   for(int i=4;i<Lookback+3;i++)
   {
      oldLow=MathMin(oldLow,r[i].low);
      oldHigh=MathMax(oldHigh,r[i].high);
   }

   // Liquidity sweep
   bool sweepLow =
      r[2].low<oldLow && r[2].close>oldLow;

   bool sweepHigh =
      r[2].high>oldHigh && r[2].close<oldHigh;

   // Displacement and structure break
   bool bullish =
      r[1].close>r[1].open &&
      r[1].close>r[2].high &&
      r[1].close-r[1].open>=atr*DisplacementATR;

   bool bearish =
      r[1].close<r[1].open &&
      r[1].close<r[2].low &&
      r[1].open-r[1].close>=atr*DisplacementATR;

   // Fair Value Gap
   bool bullFVG=r[1].low>r[3].high;
   bool bearFVG=r[1].high<r[3].low;

   // Order Block candidate
   bool bearOB=r[2].close<r[2].open;
   bool bullOB=r[2].close>r[2].open;

   MqlTick tick;
   if(!SymbolInfoTick(_Symbol,tick)) return;

   int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS);

   // ===== BUY SETUP =====
   if(sweepLow && bullish && bullFVG && bearOB)
   {
      double zoneLow=MathMax(r[3].high,r[2].low);
      double zoneHigh=MathMin(r[1].low,r[2].open);

      if(zoneLow<zoneHigh)
      {
         double entry=(zoneLow+zoneHigh)/2;
         double sl=r[2].low-atr*SLBufferATR;
         double tp=entry+(entry-sl)*RR;

         if(entry<tick.ask && sl<entry)
         {
            double lot=GetLot(ORDER_TYPE_BUY,entry,sl);

            if(lot>0)
            {
               bool sent=trade.BuyLimit(
                  lot,NormalizeDouble(entry,digits),
                  _Symbol,NormalizeDouble(sl,digits),
                  NormalizeDouble(tp,digits),
                  ORDER_TIME_GTC,0,"ICT SMC BUY");

               if(!sent)
                  Print("BUY failed: ",trade.ResultRetcodeDescription());
            }
         }
      }
   }

   // ===== SELL SETUP =====
   if(sweepHigh && bearish && bearFVG && bullOB)
   {
      double zoneLow=MathMax(r[1].high,r[2].open);
      double zoneHigh=MathMin(r[3].low,r[2].high);

      if(zoneLow<zoneHigh)
      {
         double entry=(zoneLow+zoneHigh)/2;
         double sl=r[2].high+atr*SLBufferATR;
         double tp=entry-(sl-entry)*RR;

         if(entry>tick.bid && sl>entry)
         {
            double lot=GetLot(ORDER_TYPE_SELL,entry,sl);

            if(lot>0)
            {
               bool sent=trade.SellLimit(
                  lot,NormalizeDouble(entry,digits),
                  _Symbol,NormalizeDouble(sl,digits),
                  NormalizeDouble(tp,digits),
                  ORDER_TIME_GTC,0,"ICT SMC SELL");

               if(!sent)
                  Print("SELL failed: ",trade.ResultRetcodeDescription());
            }
         }
      }
   }
}

// ===== INITIALIZATION =====
int OnInit()
{
   trade.SetExpertMagicNumber(Magic);
   trade.SetTypeFillingBySymbol(_Symbol);

   Print("ICT SMC Bot Started");
   return INIT_SUCCEEDED;
}

// ===== EXECUTION =====
void OnTick()
{
   datetime bar=iTime(_Symbol,PERIOD_M5,0);

   if(bar==0 || bar==lastBar)
      return;

   lastBar=bar;
   CheckSignal();
}
