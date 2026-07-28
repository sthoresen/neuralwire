"use client";

import { useEffect, useRef, useState } from "react";
import { createChart, IChartApi, ISeriesApi, UTCTimestamp } from "lightweight-charts";

export type PricePoint    = { date: string;      close: number };
export type IntradayPoint = { timestamp: string; close: number };

const RANGE_LABELS = ["1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "All"] as const;
type RangeLabel = (typeof RANGE_LABELS)[number];
const INTRADAY_RANGES: RangeLabel[] = ["1D", "5D"];

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  return `rgba(${parseInt(full.slice(0,2),16)},${parseInt(full.slice(2,4),16)},${parseInt(full.slice(4,6),16)},${alpha})`;
}

// Convert a date/datetime string to UTC seconds (UTCTimestamp for lightweight-charts)
function toUTC(str: string): UTCTimestamp {
  let s: string;
  if (str.length === 10) {
    s = str + "T12:00:00Z";                          // daily 'YYYY-MM-DD'
  } else if (str.includes("+") || str.endsWith("Z")) {
    s = str.replace(" ", "T");                        // already has tz info — don't add Z
  } else {
    s = str.replace(" ", "T") + "Z";                 // naive datetime — assume UTC
  }
  return Math.floor(new Date(s).getTime() / 1000) as UTCTimestamp;
}

// Slice daily prices to only include rows >= cutoff date string
function sliceDaily(prices: PricePoint[], fromDate: string): PricePoint[] {
  return prices.filter((r) => r.date >= fromDate);
}

// Slice intraday rows to only include rows >= cutoff UTC seconds
function sliceIntraday(rows: IntradayPoint[], fromSec: number): IntradayPoint[] {
  return rows.filter((r) => toUTC(r.timestamp) >= fromSec);
}

// Returns an ISO date string N months/years before the last daily price
function offsetDate(lastDate: string, months: number): string {
  const d = new Date(lastDate + "T12:00:00Z");
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
}
function offsetYears(lastDate: string, years: number): string {
  const d = new Date(lastDate + "T12:00:00Z");
  d.setUTCFullYear(d.getUTCFullYear() - years);
  return d.toISOString().slice(0, 10);
}

interface Props {
  prices:    PricePoint[];
  intraday:  IntradayPoint[];
  accent:    string;
}

export default function PriceChart({ prices, intraday, accent }: Props) {
  // Augment daily prices with today's live intraday close as a synthetic point (only when fresh)
  const latestIntraday  = intraday.length ? intraday[intraday.length - 1] : null;
  const intradayFresh   = latestIntraday != null && (Date.now() - new Date(latestIntraday.timestamp).getTime()) < 3 * 24 * 60 * 60 * 1000;
  const todayDate       = intradayFresh ? latestIntraday!.timestamp.slice(0, 10) : null;
  const effectivePrices: PricePoint[] = (todayDate && prices.length && prices[prices.length - 1].date < todayDate)
    ? [...prices, { date: todayDate, close: latestIntraday!.close }]
    : prices;
  const containerRef      = useRef<HTMLDivElement>(null);
  const tooltipRef        = useRef<HTMLDivElement>(null);
  const chartRef          = useRef<IChartApi | null>(null);
  const seriesRef         = useRef<ISeriesApi<"Area"> | null>(null);
  const [activeRange, setActiveRange] = useState<RangeLabel>("1Y");
  const activeRangeRef    = useRef<RangeLabel>("1Y");
  // Tracks whether the currently rendered data is intraday (minute bars) or daily bars.
  // Needed so the tooltip can show time for real intraday data but not for daily-bar fallbacks.
  const usingIntradayRef  = useRef(false);

  // Build chart once on mount / when accent changes
  useEffect(() => {
    if (!containerRef.current || !effectivePrices.length) return;

    const isDark = (document.documentElement.getAttribute("data-theme") ?? "dark") !== "light";

    // Shorter chart on narrow (phone) screens
    const chartHeight = containerRef.current.clientWidth < 480 ? 240 : 320;

    const chart = createChart(containerRef.current, {
      height: chartHeight,
      width: containerRef.current.clientWidth,
      layout: {
        background:  { color: "transparent" },
        textColor:   isDark ? "#71717a" : "#8c8c8c",
        fontFamily:  "JetBrains Mono",
        fontSize:    11,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: isDark ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.05)" },
      },
      rightPriceScale: { borderVisible: false },
      timeScale:       { borderVisible: false, timeVisible: false },
      crosshair:       { vertLine: { labelVisible: false } },
      handleScroll:    false,
      handleScale:     false,
    });
    chartRef.current = chart;

    const series = chart.addAreaSeries({
      lineColor:   accent,
      topColor:    hexToRgba(accent, 0.12),
      bottomColor: hexToRgba(accent, 0.01),
      lineWidth:   2,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
    });
    seriesRef.current = series;

    // Render the current active range (preserves user selection on intraday refresh)
    loadRange(activeRangeRef.current, series, chart);

    // Floating tooltip on crosshair move
    chart.subscribeCrosshairMove((param) => {
      const tooltip = tooltipRef.current;
      if (!tooltip) return;

      if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        tooltip.style.display = "none";
        return;
      }

      const price = param.seriesData.get(series);
      if (!price || !("value" in price)) {
        tooltip.style.display = "none";
        return;
      }

      // Format label depending on whether we're showing intraday (minute) or daily data
      const ts  = (param.time as number) * 1000;
      const d   = new Date(ts);
      let label: string;

      if (usingIntradayRef.current) {
        const timeStr = d.toLocaleTimeString("en-US", {
          hour: "numeric", minute: "2-digit", hour12: true, timeZone: "America/New_York",
        });
        if (activeRangeRef.current === "1D") {
          // Single day — time is enough
          label = timeStr;
        } else {
          // 5D — show abbreviated weekday + date so each day is identifiable
          const dayStr = d.toLocaleDateString("en-US", {
            weekday: "short", month: "short", day: "numeric", timeZone: "America/New_York",
          });
          label = `${dayStr} · ${timeStr}`;
        }
      } else {
        label = d.toLocaleDateString("en-US", {
          month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
        });
      }

      tooltip.innerHTML = `
        <span style="color:var(--sn-text-tertiary);font-size:10px">${label}</span>
        <span style="color:var(--sn-text);font-size:13px;font-weight:500">$${price.value.toFixed(2)}</span>
      `;

      // Position near cursor, clamped inside container
      const container = containerRef.current!;
      const cw = container.clientWidth;
      const left = param.point.x + 12;
      const clampedLeft = left + 110 > cw ? param.point.x - 122 : left;
      tooltip.style.left    = `${clampedLeft}px`;
      tooltip.style.top     = `${Math.max(0, param.point.y - 36)}px`;
      tooltip.style.display = "flex";
    });

    // Keep the chart width in sync with its container (orientation change, drawer, resize)
    const ro = new ResizeObserver(() => {
      const el = containerRef.current;
      if (!el) return;
      chart.applyOptions({
        width:  el.clientWidth,
        height: el.clientWidth < 480 ? 240 : 320,
      });
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current  = null;
      seriesRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prices, intraday, accent]);

  // Populate the series with the right data slice and fit
  function loadRange(
    label: RangeLabel,
    series: ISeriesApi<"Area">,
    chart: IChartApi
  ) {
    const useIntra = INTRADAY_RANGES.includes(label) && intraday.length > 0;
    const lastDaily = effectivePrices[effectivePrices.length - 1]?.date ?? "";

    let chartData: { time: UTCTimestamp; value: number }[];

    if (useIntra) {
      // 1D / 5D: use intraday data filtered to last N days
      const cutoffDays   = label === "1D" ? 1 : 5;
      const lastIntraSec = toUTC(intraday[intraday.length - 1].timestamp);
      const cutoffSec    = lastIntraSec - cutoffDays * 86400;
      const slice        = sliceIntraday(intraday, cutoffSec);

      if (slice.length > 0) {
        usingIntradayRef.current = true;
        chartData = slice.map((r) => ({ time: toUTC(r.timestamp), value: r.close }));
      } else {
        // Intraday data exists but is too sparse — fall back to last N daily bars
        usingIntradayRef.current = false;
        chartData = effectivePrices
          .slice(-cutoffDays)
          .map((r) => ({ time: toUTC(r.date), value: r.close }));
      }
    } else {
      usingIntradayRef.current = false;
      let fromDate: string;
      switch (label) {
        case "1M":  fromDate = offsetDate(lastDaily, 1);   break;
        case "3M":  fromDate = offsetDate(lastDaily, 3);   break;
        case "6M":  fromDate = offsetDate(lastDaily, 6);   break;
        case "YTD": fromDate = lastDaily.slice(0, 4) + "-01-01"; break;
        case "1Y":  fromDate = offsetYears(lastDaily, 1);  break;
        case "3Y":  fromDate = offsetYears(lastDaily, 3);  break;
        default:    fromDate = effectivePrices[0]?.date ?? lastDaily; // All
      }
      chartData = sliceDaily(effectivePrices, fromDate).map((r) => ({
        time:  toUTC(r.date),
        value: r.close,
      }));
    }

    series.setData(chartData);
    chart.timeScale().fitContent();
  }

  function applyRange(label: RangeLabel) {
    setActiveRange(label);
    activeRangeRef.current = label;
    if (chartRef.current && seriesRef.current) {
      loadRange(label, seriesRef.current, chartRef.current);
    }
  }

  if (!effectivePrices.length) {
    return (
      <p className="text-[var(--sn-text-tertiary)] text-sm italic mt-3">
        No price data available.
      </p>
    );
  }

  return (
    <div>
      {/* Range buttons */}
      <div className="flex gap-1 mb-3 flex-wrap">
        {RANGE_LABELS.map((label) => {
          const disabled = INTRADAY_RANGES.includes(label) && !intraday.length;
          const active   = activeRange === label;
          return (
            <button
              key={label}
              disabled={disabled}
              onClick={() => applyRange(label)}
              className="font-mono text-[11px] px-2.5 py-1.5 rounded-[var(--sn-radius-xs)] border
                         transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
              style={{
                background:   active ? accent : "var(--sn-surface)",
                color:        active ? "#fff" : "var(--sn-text-secondary)",
                borderColor:  active ? accent : "var(--sn-border)",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      <div className="relative">
        <div ref={containerRef} />
        {/* Floating tooltip */}
        <div
          ref={tooltipRef}
          className="absolute pointer-events-none hidden flex-col gap-0.5
                     px-2.5 py-1.5 rounded-[var(--sn-radius-xs)] border"
          style={{
            background:   "var(--sn-surface-2)",
            borderColor:  "var(--sn-border)",
            fontFamily:   "JetBrains Mono, monospace",
            lineHeight:   1.4,
            whiteSpace:   "nowrap",
          }}
        />
      </div>
    </div>
  );
}
