"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { fetchWebSocketTicket, getWebSocketUrl } from "./api";
import type { SlaContractResponse, WebSocketEnvelope } from "./types";

export type WebSocketStatus = "connecting" | "connected" | "reconnecting" | "disconnected";

export interface WebSocketCallbacks {
  onSnapshot?: (data: { contracts: SlaContractResponse[]; balances: Record<string, number> }) => void;
  onFsmTransition?: (envelope: WebSocketEnvelope) => void;
  onArbiterEvaluated?: (envelope: WebSocketEnvelope) => void;
  onLedgerTransaction?: (envelope: WebSocketEnvelope) => void;
  onContractUpdate?: (updatedContract: Partial<SlaContractResponse> & { agreement_id: string }) => void;
}

// Both live under /api/v1, which the dashboard's rewrite proxies to the backend.
const WS_PATHS = ["/api/v1/ws/ledger", "/api/v1/ws/dashboard"];

export function useLedgerWebSocket(callbacks: WebSocketCallbacks = {}, enabled = true) {
  const [status, setStatus] = useState<WebSocketStatus>("disconnected");
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);
  const heartbeatTimerRef = useRef<NodeJS.Timeout | null>(null);
  const pathIndexRef = useRef(0);
  const callbacksRef = useRef(callbacks);
  callbacksRef.current = callbacks;
  // Bumped on every connect and on unmount, so a ticket fetch that resolves late is discarded.
  const generationRef = useRef(0);

  const connect = useCallback(async () => {
    if (!enabled || typeof window === "undefined") return;
    const generation = ++generationRef.current;

    // Clean up any existing connection
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
      wsRef.current = null;
    }

    const currentPath = WS_PATHS[pathIndexRef.current % WS_PATHS.length];
    const baseUrl = getWebSocketUrl(currentPath);

    setStatus(reconnectAttemptRef.current > 0 ? "reconnecting" : "connecting");

    const ticket = await fetchWebSocketTicket();
    if (generation !== generationRef.current) return;
    const wsUrl = ticket
      ? `${baseUrl}${baseUrl.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`
      : baseUrl;

    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus("connected");
        reconnectAttemptRef.current = 0;

        // Start heartbeat ping
        if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
        heartbeatTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "ping" }));
          }
        }, 25000);
      };

      ws.onmessage = (event) => {
        try {
          const envelope: WebSocketEnvelope = JSON.parse(event.data);
          const { event_type, agreement_id, data } = envelope;

          if (event_type === "INITIAL_SNAPSHOT") {
            const contracts = data?.contracts || data?.agreements || [];
            const balances = data?.balances || {};
            callbacksRef.current.onSnapshot?.({ contracts, balances });
          } else if (event_type === "FSM_TRANSITION") {
            callbacksRef.current.onFsmTransition?.(envelope);
            if (agreement_id && data?.to_state) {
              callbacksRef.current.onContractUpdate?.({
                agreement_id,
                fsm_state: data.to_state,
                updated_at: envelope.timestamp,
                ...(data.contract || {}),
              });
            }
          } else if (event_type === "ARBITER_EVALUATED") {
            callbacksRef.current.onArbiterEvaluated?.(envelope);
            if (agreement_id) {
              const report = data?.report || data;
              callbacksRef.current.onContractUpdate?.({
                agreement_id,
                verification_score: report?.composite_score ?? data?.composite_score,
                arbitration_report: report,
                updated_at: envelope.timestamp,
              });
            }
          } else if (event_type === "LEDGER_TRANSACTION") {
            callbacksRef.current.onLedgerTransaction?.(envelope);
            if (agreement_id && data?.reference_id) {
              callbacksRef.current.onContractUpdate?.({
                agreement_id: data.reference_id,
                updated_at: envelope.timestamp,
              });
            }
          } else if (event_type === "AUTH_LOCKED") {
            callbacksRef.current.onContractUpdate?.({
              agreement_id,
              fsm_state: "PAYPAL_AUTH_LOCKED",
              paypal_authorization_id: data?.authorization_id,
              updated_at: envelope.timestamp,
            });
          } else if (event_type === "TEST_PASSED" || event_type === "AST_CLEAN") {
            callbacksRef.current.onContractUpdate?.({
              agreement_id,
              fsm_state: "ARBITRATION_AUDITING",
              verification_score: data?.score ?? data?.composite_score,
              updated_at: envelope.timestamp,
            });
          } else if (event_type === "SETTLEMENT_SPLIT") {
            callbacksRef.current.onContractUpdate?.({
              agreement_id,
              paypal_capture_id: data?.capture_id,
              updated_at: envelope.timestamp,
            });
          } else if (event_type === "LEDGER_POSTED") {
            callbacksRef.current.onLedgerTransaction?.(envelope);
            if (agreement_id) {
              callbacksRef.current.onContractUpdate?.({
                agreement_id,
                updated_at: envelope.timestamp,
              });
            }
          }
        } catch (err) {
          // ignore non-json messages (e.g. heartbeat pongs)
        }
      };

      ws.onerror = () => {
        // Switch to alternate path on next attempt
        pathIndexRef.current = (pathIndexRef.current + 1) % WS_PATHS.length;
      };

      ws.onclose = () => {
        if (generation !== generationRef.current) return; // superseded or unmounted
        setStatus("disconnected");
        if (heartbeatTimerRef.current) {
          clearInterval(heartbeatTimerRef.current);
          heartbeatTimerRef.current = null;
        }

        // Exponential backoff reconnect: 1s, 2s, 4s, up to 8s
        if (enabled) {
          const delay = Math.min(1000 * Math.pow(2, reconnectAttemptRef.current), 8000) + Math.random() * 500;
          reconnectAttemptRef.current += 1;
          if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
          reconnectTimerRef.current = setTimeout(() => void connect(), delay);
        }
      };
    } catch {
      setStatus("disconnected");
    }
  }, [enabled]);

  useEffect(() => {
    void connect();
    return () => {
      generationRef.current += 1;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return {
    status,
    reconnect: connect,
  };
}
