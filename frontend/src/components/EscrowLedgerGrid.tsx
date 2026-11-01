"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, GridApi, GridReadyEvent, ICellRendererParams, RowClickedEvent } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-quartz.css";
import { FSM_STATES, type SlaContractResponse } from "@/lib/types";
import { formatBudget, stateColor } from "@/lib/format";

function StateBadge(p: ICellRendererParams<SlaContractResponse, string>) {
  if (!p.value) return null;
  return <span className={`rounded border px-2 py-0.5 text-xs ${stateColor(p.value)}`}>{p.value}</span>;
}

function IdBadge(p: ICellRendererParams<SlaContractResponse, string | null>) {
  if (!p.value) return <span className="text-slate-600">-</span>;
  const tone = String(p.colDef?.field).includes("payout")
    ? "border-fuchsia-500/40 bg-fuchsia-500/10 text-fuchsia-300"
    : String(p.colDef?.field).includes("capture")
      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
      : "border-sky-500/40 bg-sky-500/10 text-sky-300";
  return <span className={`rounded border px-2 py-0.5 font-mono text-[11px] ${tone}`}>{p.value}</span>;
}

interface Props {
  contracts: SlaContractResponse[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function EscrowLedgerGrid({ contracts, selectedId, onSelect }: Props) {
  const apiRef = useRef<GridApi<SlaContractResponse> | null>(null);
  const known = useRef<Map<string, string>>(new Map());
  const [ready, setReady] = useState(false);
  const [stateFilter, setStateFilter] = useState("ALL");
  const stateFilterRef = useRef(stateFilter);

  const columnDefs = useMemo<ColDef<SlaContractResponse>[]>(
    () => [
      { field: "agreement_id", headerName: "ID", minWidth: 170, filter: "agTextColumnFilter" },
      { field: "fsm_state", headerName: "State", minWidth: 230, cellRenderer: StateBadge },
      {
        field: "verification_score",
        headerName: "Score",
        width: 110,
        filter: "agNumberColumnFilter",
        valueFormatter: (p) => (p.value == null ? "-" : Number(p.value).toFixed(3)),
      },
      {
        field: "budget_micros",
        headerName: "Budget",
        minWidth: 270,
        filter: "agNumberColumnFilter",
        valueFormatter: (p) => formatBudget(p.value),
      },
      { field: "paypal_authorization_id", headerName: "Auth ID", minWidth: 170, cellRenderer: IdBadge },
      { field: "paypal_capture_id", headerName: "Capture ID", minWidth: 170, cellRenderer: IdBadge },
      { field: "paypal_payout_batch_id", headerName: "Payout Batch ID", minWidth: 180, cellRenderer: IdBadge },
    ],
    [],
  );

  const defaultColDef = useMemo<ColDef>(
    () => ({ sortable: true, resizable: true, filter: true, enableCellChangeFlash: true, flex: 1 }),
    [],
  );

  const onGridReady = useCallback((e: GridReadyEvent<SlaContractResponse>) => {
    apiRef.current = e.api;
    setReady(true);
  }, []);

  // Push row diffs through applyTransactionAsync so changed cells flash.
  useEffect(() => {
    const api = apiRef.current;
    if (!ready || !api) return;
    const add: SlaContractResponse[] = [];
    const update: SlaContractResponse[] = [];
    for (const c of contracts) {
      const sig = c.updated_at + c.fsm_state + (c.verification_score ?? "") + (c.paypal_capture_id ?? "") + (c.paypal_payout_batch_id ?? "");
      const prev = known.current.get(c.agreement_id);
      if (prev === undefined) add.push(c);
      else if (prev !== sig) update.push(c);
      known.current.set(c.agreement_id, sig);
    }
    if (add.length || update.length) api.applyTransactionAsync({ add, update });
  }, [contracts, ready]);

  useEffect(() => {
    const api = apiRef.current;
    if (!ready || !api || !selectedId) return;
    api.forEachNode((n) => n.setSelected(n.data?.agreement_id === selectedId));
  }, [selectedId, ready, contracts]);

  const onFilterChange = (v: string) => {
    setStateFilter(v);
    stateFilterRef.current = v;
    apiRef.current?.onFilterChanged();
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <label className="text-xs text-slate-400" htmlFor="state-filter">
          State
        </label>
        <select
          id="state-filter"
          value={stateFilter}
          onChange={(e) => onFilterChange(e.target.value)}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
        >
          <option value="ALL">All states</option>
          {FSM_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button
          onClick={() => apiRef.current?.exportDataAsCsv({ fileName: "aegispay-escrow-ledger.csv" })}
          className="ml-auto rounded border border-slate-600 bg-slate-800 px-3 py-1 text-xs hover:bg-slate-700"
        >
          Export CSV
        </button>
      </div>
      <div className="ag-theme-quartz-dark" style={{ height: 340, width: "100%" }}>
        <AgGridReact<SlaContractResponse>
          columnDefs={columnDefs}
          defaultColDef={defaultColDef}
          getRowId={(p) => p.data.agreement_id}
          rowSelection="single"
          onGridReady={onGridReady}
          onRowClicked={(e: RowClickedEvent<SlaContractResponse>) => e.data && onSelect(e.data.agreement_id)}
          isExternalFilterPresent={() => stateFilterRef.current !== "ALL"}
          doesExternalFilterPass={(node) => node.data?.fsm_state === stateFilterRef.current}
          asyncTransactionWaitMillis={100}
          overlayNoRowsTemplate="No contracts yet. Run a scenario above."
        />
      </div>
    </div>
  );
}
