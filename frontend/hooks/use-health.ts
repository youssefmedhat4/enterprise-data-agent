"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api/client";
import { getReadiness } from "@/lib/api/health";
import type { HealthResponse } from "@/lib/types/analytics";

export type SystemStatus = "checking" | "ready" | "degraded" | "offline";

export interface SystemHealth {
  status: SystemStatus;
  checks: Record<string, "ok" | "skipped">;
  message: string | null;
  refresh: () => void;
}

/**
 * Backend readiness for the status indicator.
 *
 * Polled slowly and only while the tab is visible — this is an ambient signal,
 * not a monitoring system, and it must not add load to the analytics service.
 */
const POLL_INTERVAL_MS = 60_000;

export function useHealth(): SystemHealth {
  const [status, setStatus] = useState<SystemStatus>("checking");
  const [checks, setChecks] = useState<Record<string, "ok" | "skipped">>({});
  const [message, setMessage] = useState<string | null>(null);
  const inFlight = useRef<AbortController | null>(null);

  const check = useCallback(async () => {
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      const health: HealthResponse = await getReadiness(controller.signal);
      setChecks(health.checks);
      setStatus("ready");
      setMessage(null);
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof ApiError) {
        setStatus(cause.code === "network_unreachable" ? "offline" : "degraded");
        setMessage(cause.message);
      } else {
        setStatus("offline");
        setMessage("The analytics service is unreachable.");
      }
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, []);

  useEffect(() => {
    void check();

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void check();
    };

    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void check();
    }, POLL_INTERVAL_MS);

    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      inFlight.current?.abort();
    };
  }, [check]);

  return { status, checks, message, refresh: check };
}
