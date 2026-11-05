"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { DemoStore } from "./fixtures";
import type { LedgerResponse, ScenarioName, SlaContractResponse } from "./types";

export type Connection = "connecting" | "online" | "offline";

const POLL_MS = 2000;
const EMPTY_LEDGER: LedgerResponse = { entries: [], balances: {} };

export function useAegis() {
  const [connection, setConnection] = useState<Connection>("connecting");
  const [demoMode, setDemoMode] = useState(false);
  const [contracts, setContracts] = useState<SlaContractResponse[]>([]);
  const [ledger, setLedger] = useState<LedgerResponse>(EMPTY_LEDGER);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<SlaContractResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<ScenarioName | null>(null);
  const demo = useRef<DemoStore | null>(null);
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;

  const refresh = useCallback(async () => {
    if (demoMode) {
      const store = (demo.current ??= new DemoStore());
      store.tick();
      const list = store.list();
      setContracts(list);
      setLedger(store.ledger());
      const id = selectedRef.current;
      setSelected(id ? (list.find((c) => c.agreement_id === id) ?? null) : null);
      return;
    }
    try {
      await api.getHealth();
      const [list, led] = await Promise.all([api.listContracts(), api.getLedger()]);
      setConnection("online");
      setError(null);
      setContracts(list);
      setLedger(led);
      const id = selectedRef.current;
      if (id) {
        const inList = list.find((c) => c.agreement_id === id);
        if (inList?.history) setSelected(inList);
        else setSelected(await api.getContract(id));
      }
    } catch (e) {
      setConnection("offline");
      setError((e as Error).message);
    }
  }, [demoMode]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  // Selecting a row should show its detail immediately, not after the next poll.
  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      return;
    }
    const fromList = contracts.find((c) => c.agreement_id === selectedId);
    if (fromList) setSelected(fromList);
    if (!demoMode && connection === "online" && !fromList?.history) {
      api.getContract(selectedId).then(setSelected).catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const run = useCallback(
    async (name: ScenarioName) => {
      setBusy(name);
      try {
        const created = demoMode
          ? (demo.current ??= new DemoStore()).run(name)
          : await api.runScenario(name);
        setSelectedId(created.agreement_id);
        await refresh();
      } catch (e) {
        setError((e as Error).message);
        if (!demoMode) setConnection("offline");
      } finally {
        setBusy(null);
      }
    },
    [demoMode, refresh],
  );

  const enableDemo = useCallback(() => {
    demo.current = new DemoStore();
    setContracts([]);
    setLedger(EMPTY_LEDGER);
    setSelectedId(null);
    setDemoMode(true);
  }, []);

  const disableDemo = useCallback(() => {
    setContracts([]);
    setLedger(EMPTY_LEDGER);
    setSelectedId(null);
    setConnection("connecting");
    setDemoMode(false);
  }, []);

  return {
    connection,
    demoMode,
    contracts,
    ledger,
    selectedId,
    selected,
    error,
    busy,
    setSelectedId,
    refresh,
    run,
    enableDemo,
    disableDemo,
  };
}
