#region Using declarations
using System;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

// Import into NinjaTrader 8: New > NinjaScript Editor > Strategies > right-click > Import, or paste as a new strategy file.
namespace NinjaTrader.NinjaScript.Strategies
{
    public class ORBStrategyTester : Strategy
    {
        private double orHigh;
        private double orLow;
        private double orRange;
        private bool orReady;

        private int dailyTradeCount;
        private bool firstTradeWon;
        private bool firstTradeClosed;
        private bool resetTouchedAfterFirst;
        private int? firstTradeDirection;

        private int pendingDirection; // +1 long, -1 short
        private DateTime pendingEntryTime;
        private bool pendingEntry;

        private DateTime sessionDate;
        private bool hadOpenPosition;
        private double resetLevel;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name = "ORBStrategyTester";
                Description = "Opening Range Breakout/Fade strategy tester with adjustable OR, threshold, SL/TP, and 2-trade daily rules.";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsInstantiatedOnEachOptimizationIteration = false;

                // Defaults from your current Python baseline
                Quantity = 1;
                ThresholdOr = 0.10;
                StopBufferOr = 0.25;
                TargetOr = 0.75;
                UseFadeMode = false;

                OpeningRangeStart = 63000;  // 06:30:00
                OpeningRangeEnd = 64459;    // 06:44:59
                SignalStart = 64500;        // 06:45:00
                SignalEnd = 83000;          // 08:30:00

                MaxTradesPerDay = 2;
                OppositeOnlySecondTrade = true;
                RequireHalfResetForSecond = true;
            }
            else if (State == State.Configure)
            {
                // 1-minute series for "enter on next 1m after 5m close" behavior
                AddDataSeries(BarsPeriodType.Minute, 1);
            }
        }

        protected override void OnBarUpdate()
        {
            if (CurrentBars[0] < 20 || CurrentBars[1] < 20)
                return;

            // reset daily state on primary series
            if (BarsInProgress == 0)
            {
                DateTime curDate = Time[0].Date;
                if (sessionDate != curDate)
                {
                    sessionDate = curDate;
                    ResetDailyState();
                }

                BuildOpeningRange();
                TrackPostFirstTradeResetTouch(High[0], Low[0]);
                DetectTradeClosure();
                Evaluate5mSignal();
            }

            // entry happens on 1-minute series
            if (BarsInProgress == 1)
            {
                DateTime curDate = Times[1][0].Date;
                if (sessionDate != curDate)
                {
                    sessionDate = curDate;
                    ResetDailyState();
                }

                TrackPostFirstTradeResetTouch(Highs[1][0], Lows[1][0]);
                DetectTradeClosure();
                TryExecutePendingEntryOn1m();
            }
        }

        private void ResetDailyState()
        {
            orHigh = double.MinValue;
            orLow = double.MaxValue;
            orRange = 0;
            orReady = false;

            dailyTradeCount = 0;
            firstTradeWon = false;
            firstTradeClosed = false;
            resetTouchedAfterFirst = false;
            firstTradeDirection = null;

            pendingDirection = 0;
            pendingEntry = false;
            pendingEntryTime = Core.Globals.MinDate;
            hadOpenPosition = false;
            resetLevel = 0;
        }

        private void BuildOpeningRange()
        {
            int t = ToTime(Time[0]);
            if (t >= OpeningRangeStart && t <= OpeningRangeEnd)
            {
                orHigh = Math.Max(orHigh, High[0]);
                orLow = Math.Min(orLow, Low[0]);
            }

            if (!orReady && t > OpeningRangeEnd && orHigh > orLow)
            {
                orRange = orHigh - orLow;
                if (orRange > TickSize)
                {
                    orReady = true;
                    resetLevel = orLow + 0.5 * orRange;
                }
            }
        }

        private void Evaluate5mSignal()
        {
            if (!orReady)
                return;

            if (dailyTradeCount >= MaxTradesPerDay)
                return;

            if (Position.MarketPosition != MarketPosition.Flat)
                return;

            if (pendingEntry)
                return;

            int t = ToTime(Time[0]);
            if (t < SignalStart || t > SignalEnd)
                return;

            // stop trading after first win
            if (firstTradeWon)
                return;

            int signal = 0;
            double close = Close[0];
            bool upBreak = close >= orHigh + ThresholdOr * orRange;
            bool dnBreak = close <= orLow - ThresholdOr * orRange;

            if (upBreak) signal = UseFadeMode ? -1 : 1;
            else if (dnBreak) signal = UseFadeMode ? 1 : -1;

            if (signal == 0)
                return;

            // second-trade restrictions
            if (dailyTradeCount == 1)
            {
                if (OppositeOnlySecondTrade && firstTradeDirection.HasValue && signal == firstTradeDirection.Value)
                    return;

                if (RequireHalfResetForSecond && !resetTouchedAfterFirst)
                    return;
            }

            pendingDirection = signal;
            pendingEntryTime = Time[0].AddMinutes(1);
            pendingEntry = true;
        }

        private void TryExecutePendingEntryOn1m()
        {
            if (!pendingEntry)
                return;

            if (Position.MarketPosition != MarketPosition.Flat)
                return;

            int t = ToTime(Times[1][0]);
            if (t > SignalEnd)
            {
                pendingEntry = false;
                return;
            }

            if (Times[1][0] < pendingEntryTime)
                return;

            double entryApprox = Closes[1][0];
            double stopPrice;
            double targetPrice;

            if (pendingDirection == 1)
            {
                stopPrice = orLow - StopBufferOr * orRange;
                targetPrice = entryApprox + TargetOr * orRange;

                if (stopPrice >= entryApprox)
                {
                    pendingEntry = false;
                    return;
                }

                SetStopLoss("L", CalculationMode.Price, stopPrice, false);
                SetProfitTarget("L", CalculationMode.Price, targetPrice);
                EnterLong(1, Quantity, "L");
            }
            else if (pendingDirection == -1)
            {
                stopPrice = orHigh + StopBufferOr * orRange;
                targetPrice = entryApprox - TargetOr * orRange;

                if (stopPrice <= entryApprox)
                {
                    pendingEntry = false;
                    return;
                }

                SetStopLoss("S", CalculationMode.Price, stopPrice, false);
                SetProfitTarget("S", CalculationMode.Price, targetPrice);
                EnterShort(1, Quantity, "S");
            }

            pendingEntry = false;
            dailyTradeCount++;
            if (!firstTradeDirection.HasValue)
                firstTradeDirection = pendingDirection;
        }

        private void TrackPostFirstTradeResetTouch(double hi, double lo)
        {
            if (!firstTradeClosed || !RequireHalfResetForSecond || resetTouchedAfterFirst || !orReady)
                return;

            if (lo <= resetLevel && hi >= resetLevel)
                resetTouchedAfterFirst = true;
        }

        private void DetectTradeClosure()
        {
            bool openNow = Position.MarketPosition != MarketPosition.Flat;
            if (openNow)
                hadOpenPosition = true;

            if (!openNow && hadOpenPosition)
            {
                hadOpenPosition = false;
                if (SystemPerformance.AllTrades.Count > 0)
                {
                    Trade lastTrade = SystemPerformance.AllTrades[SystemPerformance.AllTrades.Count - 1];
                    bool win = lastTrade.ProfitCurrency > 0;
                    if (!firstTradeClosed)
                    {
                        firstTradeClosed = true;
                        firstTradeWon = win;
                    }
                }
            }
        }

        #region Parameters
        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Quantity", GroupName = "Risk", Order = 1)]
        public int Quantity { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 2.0)]
        [Display(Name = "Threshold OR", GroupName = "Signals", Order = 2)]
        public double ThresholdOr { get; set; }

        [NinjaScriptProperty]
        [Range(0.0, 2.0)]
        [Display(Name = "Stop Buffer OR", GroupName = "Risk", Order = 3)]
        public double StopBufferOr { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 5.0)]
        [Display(Name = "Target OR", GroupName = "Risk", Order = 4)]
        public double TargetOr { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Use Fade Mode", GroupName = "Signals", Order = 5)]
        public bool UseFadeMode { get; set; }

        [NinjaScriptProperty]
        [Range(0, 235959)]
        [Display(Name = "Opening Range Start (HHmmss)", GroupName = "Time", Order = 6)]
        public int OpeningRangeStart { get; set; }

        [NinjaScriptProperty]
        [Range(0, 235959)]
        [Display(Name = "Opening Range End (HHmmss)", GroupName = "Time", Order = 7)]
        public int OpeningRangeEnd { get; set; }

        [NinjaScriptProperty]
        [Range(0, 235959)]
        [Display(Name = "Signal Start (HHmmss)", GroupName = "Time", Order = 8)]
        public int SignalStart { get; set; }

        [NinjaScriptProperty]
        [Range(0, 235959)]
        [Display(Name = "Signal End (HHmmss)", GroupName = "Time", Order = 9)]
        public int SignalEnd { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10)]
        [Display(Name = "Max Trades Per Day", GroupName = "Rules", Order = 10)]
        public int MaxTradesPerDay { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Opposite Only Second Trade", GroupName = "Rules", Order = 11)]
        public bool OppositeOnlySecondTrade { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Require 0.5 Reset For Second", GroupName = "Rules", Order = 12)]
        public bool RequireHalfResetForSecond { get; set; }
        #endregion
    }
}
