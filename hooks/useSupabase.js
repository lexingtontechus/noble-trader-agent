"use client";
import { useCallback } from "react";
import { useAuth } from "@clerk/nextjs";
import { getAuthClient } from "../lib/supabase";

export const PAGE_SIZE = 10;

// Days-back values for each filter preset
export const DATE_FILTERS = [
  { label: "1 Day", value: "1d", days: 1 },
  { label: "3 Days", value: "3d", days: 3 },
  { label: "1 Week", value: "1w", days: 7 },
  { label: "1 Month", value: "1m", days: 30 },
];

export function useSupabase() {
  const { getToken, userId } = useAuth();

  const client = useCallback(async () => {
    const token = await getToken({ template: "supabase" }).catch(() =>
      getToken()
    );
    return getAuthClient(token);
  }, [getToken]);

  // ── Save a calculation ──────────────────────────────────────────────────
  const saveCalculation = useCallback(
    async ({ params, result, markovProbs }) => {
      if (!userId) return { error: new Error("Not authenticated") };
      try {
        const db = await client();

        const { data: factor, error: factorErr } = await db
          .from("trade_sizing_factors")
          .insert({
            user_id: userId,
            equity: params.equity,
            stop_distance: params.stop_distance,
            point_value: params.point_value,
            p_win: params.p_win,
            reward_risk: params.reward_risk,
            regime_quality: params.regime_quality,
            state_confidence: params.state_confidence,
            current_drawdown: params.current_drawdown,
            atr_baseline: params.atr_baseline,
            atr_current: params.atr_current,
            direction: params.direction,
            regime_label: params.regime_label,
            batch_size: params.batch_size,
            target_wins: params.target_wins,
            trade_index: params.trade_index,
            wins_so_far: params.wins_so_far,
            losses_so_far: params.losses_so_far,
            masaniello_factor: result.masaniello_factor,
            quality_factor: result.quality_factor,
            drawdown_factor: result.drawdown_factor,
            volatility_factor: result.volatility_factor,
            expected_edge: result.expected_edge,
            risk_fraction: result.risk_fraction,
            risk_amount: result.risk_amount,
            units: result.units,
            contracts: result.contracts,
            allowed: result.allowed,
            reason: result.reason,
            markov_bull: markovProbs.bull,
            markov_bear: markovProbs.bear,
            markov_range: markovProbs.range,
            markov_volatile: markovProbs.volatile,
            base_risk: params.base_risk,
            min_risk: params.min_risk,
            max_risk: params.max_risk,
            max_drawdown: params.max_drawdown,
            use_kelly: params.use_kelly,
            kelly_fraction: params.kelly_fraction,
          })
          .select("id")
          .single();

        if (factorErr) throw factorErr;

        const { error: logErr } = await db.from("session_log").insert({
          user_id: userId,
          sizing_factor_id: factor.id,
          direction: params.direction,
          p_win: params.p_win,
          regime_label: params.regime_label,
          contracts: result.contracts,
          risk_amount: result.risk_amount,
          expected_edge: result.expected_edge,
          allowed: result.allowed,
        });

        if (logErr) throw logErr;
        return { error: null, factorId: factor.id };
      } catch (err) {
        console.error("[useSupabase] saveCalculation:", err);
        return { error: err };
      }
    },
    [userId, client]
  );

  // ── Fetch session log — last 3 days, paginated (dashboard widget) ───────
  const fetchSessionLog = useCallback(
    async ({ page = 1 } = {}) => {
      if (!userId) return { data: [], count: 0, error: null };
      try {
        const db = await client();
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - 3);
        const from = (page - 1) * PAGE_SIZE;
        const to = from + PAGE_SIZE - 1;

        const { data, count, error } = await db
          .from("session_log")
          .select("*", { count: "exact" })
          .eq("user_id", userId)
          .gte("created_at", cutoff.toISOString())
          .order("created_at", { ascending: false })
          .range(from, to);

        if (error) throw error;
        return { data: data ?? [], count: count ?? 0, error: null };
      } catch (err) {
        console.error("[useSupabase] fetchSessionLog:", err);
        return { data: [], count: 0, error: err };
      }
    },
    [userId, client]
  );

  // ── Fetch session log with flexible date filter (history page) ──────────
  // dateFilter: '1d' | '3d' | '1w' | '1m'
  const fetchSessionLogFiltered = useCallback(
    async ({ page = 1, dateFilter = "3d" } = {}) => {
      if (!userId) return { data: [], count: 0, error: null };
      try {
        const db = await client();
        const preset =
          DATE_FILTERS.find((f) => f.value === dateFilter) ?? DATE_FILTERS[1];
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - preset.days);

        const from = (page - 1) * PAGE_SIZE;
        const to = from + PAGE_SIZE - 1;

        const { data, count, error } = await db
          .from("session_log")
          .select("*", { count: "exact" })
          .eq("user_id", userId)
          .gte("created_at", cutoff.toISOString())
          .order("created_at", { ascending: false })
          .range(from, to);

        if (error) throw error;
        return { data: data ?? [], count: count ?? 0, error: null };
      } catch (err) {
        console.error("[useSupabase] fetchSessionLogFiltered:", err);
        return { data: [], count: 0, error: err };
      }
    },
    [userId, client]
  );

  // ── Fetch full trade_sizing_factors row for a session_log entry ─────────
  const fetchFactorById = useCallback(
    async (id) => {
      if (!userId || !id) return { data: null, error: null };
      try {
        const db = await client();
        const { data, error } = await db
          .from("trade_sizing_factors")
          .select("*")
          .eq("id", id)
          .eq("user_id", userId)
          .single();
        if (error) throw error;
        return { data, error: null };
      } catch (err) {
        return { data: null, error: err };
      }
    },
    [userId, client]
  );

  return {
    saveCalculation,
    fetchSessionLog,
    fetchSessionLogFiltered,
    fetchFactorById,
    PAGE_SIZE,
  };
}
