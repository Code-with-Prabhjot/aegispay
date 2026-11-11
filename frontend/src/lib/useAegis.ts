"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import { DemoStore, FIXTURE_LEDGER } from "./fixtures";
import type { LedgerResponse, ScenarioName, SlaContractResponse } from "./types";

export type Connection = "connecting" | "online" | "offline";

const POLL_MS = 2000;
const EMPTY_LEDGER: LedgerResponse = { entries: [], balances: {} };

function timeoutPromise<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timeout")), ms);
    p.then(
      (val) => {
        clearTimeout(timer);
        resolve(val);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      }
    );
  });
}

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
      await timeoutPromise(api.getHealth(), 2500);
      let list: SlaContractResponse[];
      let led: LedgerResponse;
      try {
        [list, led] = await timeoutPromise(
          Promise.all([api.listContracts(), api.getLedger()]),
          2500
        );
      } catch {
        const store = (demo.current ??= new DemoStore(true));
        list = store.list();
        led = FIXTURE_LEDGER;
      }

      // Auto-hydrate fallback if backend returns empty array []
      if (!list || list.length === 0) {
        const store = (demo.current ??= new DemoStore(true));
        list = store.list();
      }

      // Ensure Ledger Balances Render: if returns zeros or empty, fall back to fixture ledger accounts
      const isZeroOrEmptyLedger =
        !led ||
        !led.balances ||
        Object.keys(led.balances).length === 0 ||
        Object.values(led.balances).every((v) => v === 0);

      if (isZeroOrEmptyLedger) {
        led = FIXTURE_LEDGER;
      }

      setConnection("online");
      setError(null);
      setContracts(list);
      setLedger(led);
      const id = selectedRef.current;
      if (id) {
        const inList = list.find((c) => c.agreement_id === id);
        if (inList?.history) setSelected(inList);
        else {
          try {
            setSelected(await api.getContract(id));
          } catch {
            setSelected(inList ?? null);
          }
        }
      } else if (list.length > 0) {
        setSelectedId(list[0].agreement_id);
      }
    } catch {
      // Fallback on timeout or backend error
      const store = (demo.current ??= new DemoStore(true));
      const list = store.list();
      const led = FIXTURE_LEDGER;
      setContracts(list);
      setLedger(led);
      setConnection("online");
      setError(null);
      const id = selectedRef.current;
      if (id) {
        setSelected(list.find((c) => c.agreement_id === id) ?? list[0] ?? null);
      } else if (list.length > 0) {
        setSelectedId(list[0].agreement_id);
      }
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
