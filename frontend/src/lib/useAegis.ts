"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { DemoStore } from "./fixtures";
import type { HealthResponse, LedgerResponse, ScenarioName, SlaContractResponse } from "./types";

export type Connection = "connecting" | "online" | "offline";
export const POLL_MS = 3000;
const EMPTY_LEDGER: LedgerResponse = { entries: [], balances: {} };

export function useAegis() {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [demoMode, setDemoMode] = useState(false);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [contracts, setContracts] = useState<SlaContractResponse[]>([]);
  const [ledger, setLedger] = useState<LedgerResponse>(EMPTY_LEDGER);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<SlaContractResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ScenarioName | "SEED" | null>(null);
  const demo = useRef<DemoStore | null>(null);
  const selectedRef = useRef<SlaContractResponse | null>(null);
  selectedRef.current = selected;
  const generation = useRef(0);
  const inFlight = useRef<{ controller: AbortController; task: Promise<void> } | null>(null);

  const refresh = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current.task;
    const current = generation.current;
    const controller = new AbortController();
    const task = (async () => {
      if (demoMode) {
        const store = (demo.current ??= new DemoStore());
        store.tick();
        const list = store.list();
        setContracts(list);
        setLedger(store.ledger());
        setSelectedId((id) => id ?? list[0]?.agreement_id ?? null);
        return;
      }
      const timer = setTimeout(() => controller.abort(), 6000);
      try {
        const [status, list, led] = await Promise.all([
          api.getHealth(controller.signal),
          api.listContracts(controller.signal),
          api.getLedger(controller.signal),
        ]);
        if (current !== generation.current) return;
        setHealth(status);
        setConnection("online");
        setError(null);
        // Empty lists and zero balances are valid live responses.
        setContracts(list);
        setLedger(led);
        setSelectedId((id) => list.some((c) => c.agreement_id === id) ? id : list[0]?.agreement_id ?? null);
      } catch (e) {
        if (current !== generation.current) return;
        controller.abort();
        setConnection("offline");
        setError((e as Error).message);
        // Preserve the last successful snapshot; demo data requires an explicit choice.
      } finally {
        clearTimeout(timer);
      }
    })();
    inFlight.current = { controller, task };
    void task.finally(() => {
      if (inFlight.current?.task === task) inFlight.current = null;
    });
    return task;
  }, [demoMode]);

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => {
      clearInterval(timer);
      generation.current += 1;
      inFlight.current?.controller.abort();
      inFlight.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    const fromList = contracts.find((c) => c.agreement_id === selectedId) ?? null;
    // List rows omit the report and transition history. Keep the full record already on screen
    // and lay the fresher list fields over it, so the detail panel does not blank on every poll.
    const prev = selectedRef.current;
    const keepDetail = Boolean(fromList && prev?.history && prev.agreement_id === fromList.agreement_id);
    setSelected(fromList && keepDetail ? { ...prev, ...fromList } : fromList);
    // The full record is still current, so there is nothing new to fetch.
    if (keepDetail && prev?.updated_at === fromList?.updated_at && prev?.fsm_state === fromList?.fsm_state) return;
    if (!selectedId || !fromList || demoMode || connection !== "online" || fromList.history) return;
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6000);
    api.getContract(selectedId, controller.signal)
      .then((detail) => { if (active) setSelected(detail); })
      .catch(() => undefined)
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [selectedId, contracts, demoMode, connection]);

  // A mutation must fetch a fresh snapshot even if an older poll was in flight.
  const refreshAfterMutation = useCallback(async () => {
    generation.current += 1;
    inFlight.current?.controller.abort();
    inFlight.current = null;
    await refresh();
  }, [refresh]);

  const run = useCallback(async (name: ScenarioName) => {
    const current = generation.current;
    setBusy(name);
    setError(null);
    try {
      const created = demoMode
        ? (demo.current ??= new DemoStore()).run(name)
        : await api.runScenario(name);
      if (current !== generation.current) return;
      setSelectedId(created.agreement_id);
      await refreshAfterMutation();
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [demoMode, refreshAfterMutation]);

  const seed = useCallback(async () => {
    const current = generation.current;
    setBusy("SEED");
    setError(null);
    try {
      await api.seedDemoScenarios();
      if (current === generation.current) await refreshAfterMutation();
    } catch (e) {
      if (current === generation.current) setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [refreshAfterMutation]);

  const switchMode = useCallback((enabled: boolean) => {
    generation.current += 1;
    inFlight.current?.controller.abort();
    inFlight.current = null;
    demo.current = enabled ? new DemoStore() : null;
    setContracts([]);
    setLedger(EMPTY_LEDGER);
    setSelectedId(null);
    setSelected(null);
    setError(null);
    setHealth(null);
    setConnection("connecting");
    setDemoMode(enabled);
  }, []);

  const getDemoDetail = useCallback((id: string) => demo.current?.getDetail(id) ?? null, []);

  return {
    connection, demoMode, health, contracts, ledger, selectedId, selected, error, busy,
    setSelectedId, refresh, run, seed, getDemoDetail,
    enableDemo: useCallback(() => switchMode(true), [switchMode]),
    disableDemo: useCallback(() => switchMode(false), [switchMode]),
  };
}
