import React from "react";
import { getSettings, putSettings } from "./api";

export type ScopeUnit = "minutes" | "hours" | "days";

export function useAutoSaveSettings() {
    const [settings, setSettings] = React.useState<any>(null);
    const [saveState, setSaveState] = React.useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
    const lastSavedRef = React.useRef<any>(null);
    const saveTimerRef = React.useRef<number | null>(null);
    const savingRef = React.useRef(false);
    const pendingRef = React.useRef(false);
    const mounted = React.useRef(false);

    React.useEffect(() => {
        mounted.current = true;
        (async () => {
            try {
                const s = await getSettings();
                if (mounted.current) {
                    setSettings(s);
                    lastSavedRef.current = s;
                }
            } catch {
                // ignore
            }
        })();
        return () => { mounted.current = false; };
    }, []);

    const update = React.useCallback((patch: Partial<any>) => {
        setSettings((prev: any) => {
            if (!prev) return prev;
            return { ...prev, ...patch };
        });
    }, []);

    const isDirty = React.useMemo(() => {
        if (!settings || !lastSavedRef.current) return false;
        // Compare only keys present in settings (simple shallow equality of primitives)
        const keys = Object.keys(settings);
        for (const k of keys) {
            if (String(settings[k]) !== String(lastSavedRef.current[k])) return true;
        }
        return false;
    }, [settings]);

    const doSave = React.useCallback(async () => {
        if (!lastSavedRef.current || !settings) return;
        if (!isDirty) {
            if (saveState === "dirty") setSaveState("idle");
            return;
        }
        if (savingRef.current) {
            pendingRef.current = true;
            return;
        }
        savingRef.current = true;
        setSaveState("saving");
        try {
            const saved = await putSettings(settings);
            if (mounted.current) {
                setSettings(saved);
                lastSavedRef.current = saved;
                setSaveState("saved");
                window.setTimeout(() => {
                    if (mounted.current) setSaveState((s) => (s === "saved" ? "idle" : s));
                }, 1200);
            }
        } catch (e) {
            if (mounted.current) setSaveState("error");
        } finally {
            savingRef.current = false;
            if (pendingRef.current) {
                pendingRef.current = false;
                doSave();
            }
        }
    }, [settings, isDirty, saveState]);

    React.useEffect(() => {
        if (!lastSavedRef.current) return;
        if (isDirty) {
            setSaveState((s) => (s === "saving" ? s : "dirty"));
            if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
            saveTimerRef.current = window.setTimeout(doSave, 800);
            return () => {
                if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
            };
        } else {
            if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
            saveTimerRef.current = null;
            if (saveState === "dirty") setSaveState("idle");
        }
    }, [settings, isDirty, doSave, saveState]);

    return { settings, update, saveState };
}
