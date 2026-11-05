"use client";

import React, { useEffect, useState } from "react";
import * as api from "@/lib/api";
import { microsToUsd } from "@/lib/format";
import type { VaultVelocityStatus } from "@/lib/types";

export interface VaultManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  vaultId?: string;
  initialVault?: VaultVelocityStatus | null;
}

const DEFAULT_VAULT_STATE: VaultVelocityStatus = {
  vault_id: "vault_buyer_primary",
  buyer_did: "did:aegis:buyer:enterprise-agent-01",
  max_amount_per_task_micros: 100_000_000, // $100.00 ceiling
  max_daily_budget_micros: 250_000_000,    // $250.00 24h cap
  current_daily_spent_micros: 45_000_000,  // $45.00 spent
  window_reset_timestamp: Math.floor(Date.now() / 1000) + 72000,
  consecutive_void_count: 0,
  is_frozen: 0,
  status: "ACTIVE",
};

export default function VaultManagementModal({
  isOpen,
  onClose,
  vaultId = "vault_buyer_primary",
  initialVault,
}: VaultManagementModalProps) {
  const [vault, setVault] = useState<VaultVelocityStatus>(initialVault || DEFAULT_VAULT_STATE);
  const [loading, setLoading] = useState<boolean>(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    if (initialVault) {
      setVault(initialVault);
    }
  }, [initialVault]);

  useEffect(() => {
    if (!isOpen) return;
    let isSubscribed = true;
    const loadVaultData = async () => {
      try {
        const data = await api.getVaultStatus(vaultId);
        if (data && isSubscribed) {
          setVault(data);
        }
      } catch {
        if (isSubscribed) {
          setVault((prev) => prev || DEFAULT_VAULT_STATE);
        }
      }
    };
    loadVaultData();
    return () => {
      isSubscribed = false;
    };
  }, [isOpen, vaultId]);

  const handleRefreshAllowance = async () => {
    setLoading(true);
    setActionMessage(null);
    try {
      const res = await api.refreshVaultAllowance(vault.vault_id);
      if (res && res.vault) {
        setVault({ ...res.vault, status: "ACTIVE", is_frozen: 0, consecutive_void_count: 0 });
      } else {
        setVault((prev) => ({
          ...prev,
          current_daily_spent_micros: 0,
          consecutive_void_count: 0,
          is_frozen: 0,
          status: "ACTIVE",
        }));
      }
      setActionMessage("✓ Vault allowance and circuit breaker successfully refreshed.");
    } catch {
      // Simulated local refresh in offline/demo mode
      setVault((prev) => ({
        ...prev,
        current_daily_spent_micros: 0,
        consecutive_void_count: 0,
        is_frozen: 0,
        status: "ACTIVE",
      }));
      setActionMessage("✓ Vault allowance refreshed (local simulated mode).");
    } finally {
      setLoading(false);
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  const handleToggleFreeze = async () => {
    setLoading(true);
    setActionMessage(null);
    const targetFreeze = vault.is_frozen === 1 ? false : true;
    try {
      const res = await api.toggleVaultFreeze(vault.vault_id, targetFreeze);
      if (res && res.vault) {
        setVault({
          ...res.vault,
          status: res.is_frozen === 1 ? "VAULT_LOCKED_CIRCUIT_BREAKER" : "ACTIVE",
        });
      } else {
        setVault((prev) => ({
          ...prev,
          is_frozen: targetFreeze ? 1 : 0,
          status: targetFreeze ? "VAULT_LOCKED_CIRCUIT_BREAKER" : "ACTIVE",
        }));
      }
      setActionMessage(
        targetFreeze
          ? "⚠ Emergency circuit breaker engaged. Vault locked against draws."
          : "✓ Emergency lock released. Vault status restored to ACTIVE."
      );
    } catch {
      setVault((prev) => ({
        ...prev,
        is_frozen: targetFreeze ? 1 : 0,
        status: targetFreeze ? "VAULT_LOCKED_CIRCUIT_BREAKER" : "ACTIVE",
      }));
      setActionMessage(
        targetFreeze
          ? "⚠ Emergency circuit breaker engaged (local mode)."
          : "✓ Emergency lock released (local mode)."
      );
    } finally {
      setLoading(false);
      setTimeout(() => setActionMessage(null), 4000);
    }
  };

  if (!isOpen) return null;

  const isLocked = vault.is_frozen === 1 || vault.status === "VAULT_LOCKED_CIRCUIT_BREAKER";
  const spentUsd = parseFloat(microsToUsd(vault.current_daily_spent_micros || 0));
  const maxDailyUsd = parseFloat(microsToUsd(vault.max_daily_budget_micros || 250_000_000));
  const taskCeilingUsd = parseFloat(microsToUsd(vault.max_amount_per_task_micros || 100_000_000));
  const spendPercentage = Math.min(100, Math.round((spentUsd / (maxDailyUsd || 1)) * 100));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md animate-fadeIn">
      {/* Modal Container */}
      <div
        className="relative w-full max-w-2xl rounded-2xl border border-cyan-500/30 bg-[#0B1120]/95 p-6 shadow-[0_0_50px_rgba(0,240,255,0.25)] text-slate-100 backdrop-blur-xl transition-all"
        style={{
          boxShadow: isLocked
            ? "0 0 40px rgba(244,63,94,0.3), 0 0 0 1px rgba(244,63,94,0.4)"
            : "0 0 40px rgba(0,240,255,0.25), 0 0 0 1px rgba(0,240,255,0.3)",
        }}
      >
        {/* Header Bar */}
        <div className="flex items-center justify-between border-b border-cyan-500/20 pb-4 mb-5">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-cyan-950/80 border border-cyan-400/40 text-cyan-400 shadow-[0_0_15px_rgba(0,240,255,0.3)]">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
                />
              </svg>
            </div>
            <div>
              <h2 className="text-lg font-bold tracking-tight text-white flex items-center gap-2">
                Buyer Vault Management
                <span className="text-[11px] font-mono font-normal text-cyan-400 px-2 py-0.5 rounded-full bg-cyan-950/60 border border-cyan-500/30">
                  PayPal Vault v3
                </span>
              </h2>
              <p className="text-xs text-slate-400 font-mono truncate max-w-xs sm:max-w-md">
                DID: {vault.buyer_did || "did:aegis:buyer:primary"}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="rounded-lg p-2 text-slate-400 hover:text-white hover:bg-slate-800/60 transition-colors"
            title="Close modal"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Action Message Banner */}
        {actionMessage && (
          <div
            className={`mb-4 px-4 py-2.5 rounded-lg text-xs font-mono border transition-all ${
              actionMessage.startsWith("⚠")
                ? "bg-rose-950/70 border-rose-500/50 text-rose-300"
                : "bg-cyan-950/70 border-cyan-500/50 text-cyan-300 shadow-[0_0_15px_rgba(0,240,255,0.2)]"
            }`}
          >
            {actionMessage}
          </div>
        )}

        {/* Status & Velocity Indicators Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-5">
          {/* Vault State Indicator */}
          <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-900/60 backdrop-blur-sm">
            <div className="text-xs uppercase tracking-wider text-slate-400 font-medium mb-2">
              Vault Authorization State
            </div>
            <div className="flex items-center gap-3">
              <span
                className={`relative flex h-3.5 w-3.5 rounded-full ${
                  isLocked ? "bg-rose-500 shadow-[0_0_12px_#f43f5e]" : "bg-emerald-400 shadow-[0_0_12px_#00F0FF]"
                }`}
              >
                <span
                  className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${
                    isLocked ? "bg-rose-400" : "bg-cyan-400"
                  }`}
                />
              </span>
              <span
                className={`font-mono text-sm font-bold tracking-wide ${
                  isLocked ? "text-rose-400" : "text-[#00F0FF]"
                }`}
              >
                {isLocked ? "VAULT_LOCKED_CIRCUIT_BREAKER" : "ACTIVE"}
              </span>
            </div>
            <p className="mt-2 text-[11px] text-slate-400 leading-relaxed">
              {isLocked
                ? "Automatic AST circuit breaker tripped. Zero pre-authorized draws permitted until unfrozen."
                : "Pre-authorized micro-draws unlocked within velocity ceilings."}
            </p>
          </div>

          {/* Per-Task Authorization Ceiling */}
          <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-900/60 backdrop-blur-sm">
            <div className="text-xs uppercase tracking-wider text-slate-400 font-medium mb-1">
              Per-Task Authorization Ceiling
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold font-mono text-white">
                ${taskCeilingUsd.toFixed(2)}
              </span>
              <span className="text-xs text-slate-400 font-mono">USD / task</span>
            </div>
            <div className="mt-2 flex items-center gap-1.5 text-[11px] text-cyan-300">
              <svg className="w-3.5 h-3.5 text-cyan-400" fill="currentColor" viewBox="0 0 20 20">
                <path
                  fillRule="evenodd"
                  d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                  clipRule="evenodd"
                />
              </svg>
              <span>Hard single-draw financial limit enforced</span>
            </div>
          </div>
        </div>

        {/* Rolling 24-Hour Velocity Cap Section */}
        <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-900/60 mb-5">
          <div className="flex items-center justify-between text-xs mb-2">
            <span className="uppercase tracking-wider text-slate-400 font-medium">
              Rolling 24-Hour Spending Cap
            </span>
            <span className="font-mono text-xs font-semibold text-cyan-300">
              ${spentUsd.toFixed(2)} / ${maxDailyUsd.toFixed(2)} USD ({spendPercentage}%)
            </span>
          </div>

          {/* Progress Bar with Cyan Glow */}
          <div className="w-full h-3 rounded-full bg-slate-950 overflow-hidden border border-slate-800 p-0.5">
            <div
              className={`h-full rounded-full transition-all duration-700 ease-out ${
                spendPercentage >= 90
                  ? "bg-rose-500 shadow-[0_0_12px_rgba(244,63,94,0.8)]"
                  : spendPercentage >= 70
                    ? "bg-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.8)]"
                    : "bg-[#00F0FF] shadow-[0_0_12px_rgba(0,240,255,0.8)]"
              }`}
              style={{ width: `${Math.max(5, spendPercentage)}%` }}
            />
          </div>

          <div className="flex items-center justify-between mt-2 text-[11px] text-slate-400 font-mono">
            <span>Remaining Allowance: ${(maxDailyUsd - spentUsd).toFixed(2)}</span>
            <span>Resets every rolling 24-hour cycle</span>
          </div>
        </div>

        {/* Consecutive Void Counter Indicator */}
        <div className="p-4 rounded-xl border border-cyan-500/20 bg-slate-900/60 mb-6">
          <div className="flex items-center justify-between text-xs mb-2">
            <span className="uppercase tracking-wider text-slate-400 font-medium">
              Consecutive AST Security Void Counter
            </span>
            <span
              className={`font-mono font-bold text-xs ${
                vault.consecutive_void_count >= 3 ? "text-rose-400" : "text-amber-300"
              }`}
            >
              {vault.consecutive_void_count} / 3 Voids
            </span>
          </div>

          {/* 3-Pip Visual Counter */}
          <div className="flex items-center gap-3 mt-3">
            {[1, 2, 3].map((step) => {
              const isTripped = (vault.consecutive_void_count || 0) >= step;
              return (
                <div key={step} className="flex-1 flex flex-col items-center gap-1.5">
                  <div
                    className={`w-full h-2.5 rounded-full border transition-all ${
                      isTripped
                        ? "bg-rose-500 border-rose-400 shadow-[0_0_10px_#f43f5e]"
                        : "bg-slate-950 border-slate-700"
                    }`}
                  />
                  <span className="text-[10px] font-mono text-slate-400">
                    {step === 3 ? "Trip Lock (3)" : `Void ${step}`}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-[11px] text-slate-400">
            3 consecutive malicious AST injection voids trip the autonomous circuit breaker to protect funds.
          </p>
        </div>

        {/* Action Controls Footer */}
        <div className="flex flex-col sm:flex-row items-center justify-between gap-3 pt-3 border-t border-cyan-500/20">
          {/* Emergency Freeze Toggle */}
          <button
            onClick={handleToggleFreeze}
            disabled={loading}
            className={`w-full sm:w-auto px-4 py-2.5 rounded-xl font-mono text-xs font-semibold flex items-center justify-center gap-2 border transition-all ${
              isLocked
                ? "border-emerald-500/50 bg-emerald-950/60 text-emerald-300 hover:bg-emerald-900/80 shadow-[0_0_15px_rgba(52,211,153,0.3)]"
                : "border-rose-500/50 bg-rose-950/60 text-rose-300 hover:bg-rose-900/80 shadow-[0_0_15px_rgba(244,63,94,0.3)]"
            }`}
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              {isLocked ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 11V7a4 4 0 118 0m-4 8v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2z" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              )}
            </svg>
            <span>{isLocked ? "Unfreeze Vault" : "Emergency Freeze Vault"}</span>
          </button>

          {/* Refresh Allowance Button */}
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <button
              onClick={handleRefreshAllowance}
              disabled={loading}
              className="w-full sm:w-auto px-5 py-2.5 rounded-xl font-mono text-xs font-bold text-slate-950 bg-[#00F0FF] hover:bg-cyan-300 transition-all shadow-[0_0_20px_rgba(0,240,255,0.4)] hover:shadow-[0_0_30px_rgba(0,240,255,0.6)] flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <svg className="animate-spin w-4 h-4 text-slate-950" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  <span>Processing...</span>
                </>
              ) : (
                <>
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  <span>Refresh Vault Allowance</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
