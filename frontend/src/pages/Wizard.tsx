import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { createRouter, listRouters, routerInterfaces, routerPeers, testRouter, listSavedPeers, seedDemo, importPeers, deleteRouter, type Router, type RouterProto, type PeerView } from "../api";

export default function Wizard() {
  const [step, setStep] = React.useState<1|2|3>(1);
  const [router, setRouter] = React.useState<Router | null>(null);
  const [demoMode, setDemoMode] = React.useState(false);
  const [iface, setIface] = React.useState<string>("");
  const [pendingImport, setPendingImport] = React.useState<{ interface: string; public_key: string; selected: boolean }[]>([]);
  const [submitting, setSubmitting] = React.useState(false);
  const [submitErr, setSubmitErr] = React.useState("");
  const navigate = useNavigate();

  // Directional slide transitions across steps
  const [direction, setDirection] = React.useState(1);
  const prevStepRef = React.useRef(step);
  React.useEffect(() => {
    if (step > prevStepRef.current) setDirection(1);
    else if (step < prevStepRef.current) setDirection(-1);
    prevStepRef.current = step;
  }, [step]);

  const connected = demoMode || !!router;
  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <div className="mt-10 md:mt-16 mb-6">
        <StepHeader current={step} total={3} />
      </div>
      <div className="mx-auto my-12 md:my-16 w-full max-w-[760px] h-[520px] md:h-[560px] rounded-3xl ring-1 ring-gray-200 bg-white shadow-sm p-5 md:p-6 overflow-y-auto overflow-x-hidden relative">
        <AnimatePresence initial={false} custom={direction} mode="wait">
          <motion.div
            key={step}
            custom={direction}
            initial="enter"
            animate="center"
            exit="exit"
            className="w-full h-full"
            variants={{
              enter: (dir: number) => ({ x: dir > 0 ? 40 : -40, opacity: 0 }),
              center: { x: 0, opacity: 1 },
              exit: (dir: number) => ({ x: dir > 0 ? -40 : 40, opacity: 0 }),
            }}
            transition={{ type: "tween", ease: "easeOut", duration: 0.28 }}
          >
            {step === 1 && (
              <StepConnect
                onConnected={(r) => { setDirection(1); setRouter(r); setDemoMode(false); setStep(2); }}
                onDemo={async () => {
                  await seedDemo();
                  setDirection(1);
                  setRouter(null);
                  setDemoMode(true);
                  setStep(2);
                }}
              />
            )}
            {step === 2 && (
              <StepInterface
                router={router}
                demoMode={demoMode}
                onSelected={(i) => { setDirection(1); setIface(i); setStep(3); }}
              />
            )}
            {step === 3 && (
              <StepPeers router={router} demoMode={demoMode} iface={iface} onSelectionChange={setPendingImport} />
            )}
          </motion.div>
        </AnimatePresence>
        
      </div>
      {connected && (
        <div className="mx-auto w-full max-w-[760px] mt-3 flex items-center gap-3">
          <button
            type="button"
            onClick={() => { setDirection(-1); setStep((s) => (s === 1 ? 1 : (s - 1) as 1|2|3)); }}
            disabled={step === 1}
            className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Previous"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M15 18l-6-6 6-6" />
            </svg>
            Previous
          </button>
          {step < 3 ? (
            <button
              type="button"
              onClick={() => { setDirection(1); setStep((s) => (s === 3 ? 3 : (s + 1) as 1|2|3)); }}
              className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Next"
            >
              Next
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 6l6 6-6 6" />
              </svg>
            </button>
          ) : (
            <button
              type="button"
              onClick={async () => {
                setSubmitErr("");
                // Only submit to backend when connected to a real router
                if (!router) { navigate("/"); return; }
                try {
                  setSubmitting(true);
                  const items = pendingImport && pendingImport.length ? pendingImport : [];
                  await importPeers(router.id, items);
                  navigate("/");
                } catch (e: any) {
                  setSubmitErr(e?.message || "Failed to import peers");
                } finally {
                  setSubmitting(false);
                }
              }}
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="Submit"
            >
              {submitting ? 'Submitting…' : 'Submit'}
              {!submitting && (
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 6l6 6-6 6" />
                </svg>
              )}
            </button>
          )}
        </div>
      )}
      {submitErr && <div className="mx-auto w-full max-w-[760px] mt-2 text-sm text-red-600">{submitErr}</div>}
    </div>
  );
}

function Card(props: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5";
  return <div className={base + (props.className ? " " + props.className : "")} {...props} />;
}

function StepConnect({ onConnected, onDemo }: { onConnected: (r: Router) => void; onDemo: () => void }) {
  const protoDefaults: Record<RouterProto, number> = { rest: 443, "rest-http": 80, api: 8729, "api-plain": 8728 };
  type RouterForm = {
    name: string;
    host: string;
    proto: RouterProto;
    port: number;
    username: string;
    password: string;
    tls_verify: boolean;
  };
  const makeBlankForm = (): RouterForm => ({
    name: "",
    host: "",
    proto: "rest" as RouterProto,
    port: protoDefaults.rest,
    username: "",
    password: "",
    tls_verify: true,
  });
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [loadingRouters, setLoadingRouters] = React.useState(true);
  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [testBusyId, setTestBusyId] = React.useState<number | null>(null);
  const [testStatus, setTestStatus] = React.useState<Record<number, string>>({});
  const [connectBusy, setConnectBusy] = React.useState(false);
  const [connectErr, setConnectErr] = React.useState("");

  const [form, setForm] = React.useState<RouterForm>(makeBlankForm());
  const [addErr, setAddErr] = React.useState("");
  const [addBusy, setAddBusy] = React.useState(false);
  const [deleteBusyId, setDeleteBusyId] = React.useState<number | null>(null);
  const [confirmDeleteRouter, setConfirmDeleteRouter] = React.useState<Router | null>(null);

  const loadRouters = React.useCallback(async () => {
    setLoadingRouters(true);
    try {
      const rows = await listRouters();
      setRouters(rows);
      setSelectedId((prev) => {
        if (prev && rows.some((r) => r.id === prev)) return prev;
        return rows.length ? rows[0].id : null;
      });
    } catch {
      setRouters([]);
      setSelectedId(null);
    } finally {
      setLoadingRouters(false);
    }
  }, []);

  React.useEffect(() => { loadRouters(); }, [loadRouters]);

  const selectedRouter = routers.find((r) => r.id === selectedId) || null;

  async function handleTest(routerId: number) {
    setTestBusyId(routerId);
    try {
      await testRouter(routerId);
      setTestStatus((prev) => ({ ...prev, [routerId]: "OK" }));
    } catch (e: any) {
      setTestStatus((prev) => ({ ...prev, [routerId]: e?.message || "Failed" }));
    } finally {
      setTestBusyId(null);
    }
  }

  async function handleConnect() {
    if (!selectedRouter) {
      setConnectErr("Select a profile first.");
      return;
    }
    setConnectErr("");
    setConnectBusy(true);
    try {
      await testRouter(selectedRouter.id);
      setTestStatus((prev) => ({ ...prev, [selectedRouter.id]: "OK" }));
      onConnected(selectedRouter);
    } catch (e: any) {
      const msg = e?.message || "Connection failed. Verify RouterOS service/port/firewall.";
      setConnectErr(msg);
      setTestStatus((prev) => ({ ...prev, [selectedRouter.id]: msg }));
    } finally {
      setConnectBusy(false);
    }
  }

  async function handleAddProfile() {
    if (!form.name.trim() || !form.host.trim() || !form.username.trim()) {
      setAddErr("Name, host, and username are required.");
      return;
    }
    if (!form.password.trim()) {
      setAddErr("Password is required.");
      return;
    }
    setAddErr("");
    setAddBusy(true);
    try {
      const payload = {
        name: form.name.trim(),
        host: form.host.trim(),
        proto: form.proto,
        port: Number(form.port) || protoDefaults[form.proto],
        username: form.username.trim(),
        password: form.password,
        tls_verify: form.tls_verify,
      };
      const created = await createRouter(payload);
      setForm(makeBlankForm());
      await loadRouters();
      setSelectedId(created.id);
      setTestStatus((prev) => ({ ...prev, [created.id]: "" }));
    } catch (e: any) {
      setAddErr(e?.message || "Failed to add profile.");
    } finally {
      setAddBusy(false);
    }
  }

  return (
    <Card className="p-4 h-full flex flex-col">
      <div className="mb-3 text-gray-700">Choose a RouterOS profile</div>
      <div className="grid gap-6 md:grid-cols-2 flex-1">
        <div className="flex flex-col">
          <div className="text-xs text-gray-500 mb-2">Saved profiles (select one to continue)</div>
          <div className="flex-1 overflow-y-auto px-1 py-1 space-y-3">
            {loadingRouters ? (
              <div className="text-sm text-gray-500">Loading profiles…</div>
            ) : routers.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-gray-200 p-4 text-sm text-gray-500">
                No profiles yet. Add one on the right or in Settings.
              </div>
            ) : (
              routers.map((r) => {
                const isSelected = selectedId === r.id;
                const status = testStatus[r.id];
                return (
                  <div
                    key={r.id}
                    onClick={() => setSelectedId(r.id)}
                    className={`rounded-2xl ring-1 p-4 cursor-pointer transition hover:shadow-md ${isSelected ? "ring-gray-900 bg-gray-50" : "ring-gray-200 bg-white"}`}
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-sm font-medium text-gray-900">{r.name}</div>
                        <div className="text-xs text-gray-500">{r.proto.toUpperCase()} · {r.host}:{r.port}</div>
                        <div className="text-xs text-gray-500">User: {r.username}</div>
                      </div>
                      {isSelected && (
                        <span className="inline-flex items-center rounded-full bg-gray-900 text-white px-2 py-0.5 text-[10px]">Selected</span>
                      )}
                    </div>
                    {status && (
                      <div className={`mt-2 text-xs ${status === "OK" ? "text-green-700" : "text-rose-600"}`}>
                        Status: {status}
                      </div>
                    )}
                    <div className="mt-3 flex items-center gap-2">
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); handleTest(r.id); }}
                        className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200"
                        disabled={testBusyId === r.id}
                      >
                        {testBusyId === r.id ? "Testing…" : "Test"}
                      </button>
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); setConfirmDeleteRouter(r); }}
                        className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100"
                        disabled={deleteBusyId === r.id}
                      >
                        {deleteBusyId === r.id ? "Removing…" : "Remove"}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
        <div className="flex flex-col">
          <div className="text-xs text-gray-500 mb-2">Add new profile</div>
          <div className="grid gap-3">
            <input className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300" placeholder="Profile name" value={form.name} onChange={e=>setForm({ ...form, name: e.target.value })} />
            <input className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300" placeholder="Host / IP" value={form.host} onChange={e=>setForm({ ...form, host: e.target.value })} />
            <div className="grid grid-cols-[1fr_auto] gap-3">
              <select
                className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                value={form.proto}
                onChange={e=>{
                  const next = e.target.value as RouterProto;
                  setForm((prev) => ({ ...prev, proto: next, port: prev.port || protoDefaults[next] }));
                }}
              >
                <option value="rest">REST (HTTPS)</option>
                <option value="rest-http">REST (HTTP)</option>
                <option value="api">API (TLS)</option>
                <option value="api-plain">API (Plain)</option>
              </select>
              <input className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm w-24 md:w-28 focus:ring-2 focus:ring-gray-300" placeholder="Port" type="number" value={form.port} onChange={e=>setForm({ ...form, port: Number(e.target.value) })} />
            </div>
            <input className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300" placeholder="Username" value={form.username} onChange={e=>setForm({ ...form, username: e.target.value })} />
            <input className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300" placeholder="Password" type="password" value={form.password} onChange={e=>setForm({ ...form, password: e.target.value })} />
            <label className="inline-flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" className="rounded border-gray-300 text-gray-900 focus:ring-gray-300" checked={form.tls_verify} onChange={e=>setForm({ ...form, tls_verify: e.target.checked })} />
              Verify TLS certificates
            </label>
            {addErr && <div className="text-sm text-red-600">{addErr}</div>}
            <button
              type="button"
              onClick={handleAddProfile}
              disabled={addBusy}
              className="rounded-xl px-4 py-2.5 text-sm bg-gray-900 text-white hover:bg-black disabled:opacity-50"
            >
              {addBusy ? "Saving…" : "Save profile"}
            </button>
          </div>
        </div>
      </div>
      <div className="mt-6 flex flex-col gap-3 pb-8">
        {connectErr && <div className="text-sm text-red-600 text-center">{connectErr}</div>}
        <div className="flex flex-wrap gap-3 justify-center">
          <button
            type="button"
            disabled={!selectedRouter || connectBusy}
            onClick={handleConnect}
            className="rounded-full px-5 py-2 text-sm bg-gray-900 text-white hover:bg-black disabled:opacity-50"
          >
            {connectBusy ? "Connecting…" : "Use selected profile"}
          </button>
          <button type="button" disabled={connectBusy} onClick={onDemo} className="rounded-full px-5 py-2 text-sm bg-gray-100 text-gray-800 hover:bg-gray-200 disabled:opacity-50">
            Use demo data
          </button>
        </div>
      </div>
      {confirmDeleteRouter && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
            <div className="text-lg font-semibold text-gray-900">Remove profile</div>
            <div className="text-sm text-gray-600">
              This removes{" "}
              <span className="font-medium text-gray-900">{confirmDeleteRouter.name}</span>{" "}
              from this app. It does not touch the RouterOS device.
            </div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setConfirmDeleteRouter(null)}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={deleteBusyId === confirmDeleteRouter.id}
                onClick={async () => {
                  try {
                    setDeleteBusyId(confirmDeleteRouter.id);
                    await deleteRouter(confirmDeleteRouter.id);
                    await loadRouters();
                    // Clear selection if we just deleted the selected router
                    setSelectedId((prev) => (prev === confirmDeleteRouter.id ? null : prev));
                    setConfirmDeleteRouter(null);
                  } catch (e: any) {
                    setConnectErr(e?.message || "Failed to delete profile");
                    setConfirmDeleteRouter(null);
                  } finally {
                    setDeleteBusyId(null);
                  }
                }}
                className="rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700 disabled:opacity-50"
              >
                {deleteBusyId === confirmDeleteRouter.id ? "Removing…" : "Delete profile"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Card>
  );
}

function StepHeader({ current, total }: { current: number; total: number }) {
  const items = Array.from({ length: total }, (_, i) => i + 1);
  const labels: Record<number, string> = { 1: 'Connect', 2: 'Interface', 3: 'Peers' };
  return (
    <div className="flex items-center justify-center mb-6 select-none">
      {items.map((n, idx) => (
        <React.Fragment key={n}>
          <div className="flex flex-col items-center">
            <span className={`inline-block rounded-full ${current === n ? 'bg-gray-900' : 'bg-transparent ring-1 ring-gray-300'} w-3.5 h-3.5`} />
            <span className={`mt-2 text-xs ${current === n ? 'text-gray-900 font-medium' : 'text-gray-500'}`}>{labels[n] || `Step ${n}`}</span>
          </div>
          {idx < items.length - 1 && <span className="mx-4 h-px w-10 bg-gray-300 self-center" />}
        </React.Fragment>
      ))}
    </div>
  );
}

function StepInterface({ router, demoMode, onSelected }: { router: Router | null; demoMode: boolean; onSelected: (iface: string) => void }) {
  const [ifs, setIfs] = React.useState<string[]>([]);
  React.useEffect(()=>{
    (async () => {
      try {
        if (demoMode || !router) {
          const saved = await listSavedPeers();
          const unique = Array.from(new Set(saved.map(p => p.interface)));
          setIfs(unique);
        } else {
          const fetched = await routerInterfaces(router.id);
          setIfs(fetched);
        }
      } catch { setIfs([]); }
    })();
  }, [demoMode, router?.id]);
  return (
    <Card className="p-4">
      <div className="mb-3 text-gray-700">Select a WireGuard interface</div>
      <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {ifs.map(i => (
          <div
            key={i}
            onClick={()=>onSelected(i)}
            className="rounded-xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 p-4 cursor-pointer"
          >
            <div className="text-sm text-gray-900">{i}</div>
          </div>
        ))}
        {ifs.length === 0 && <div className="text-sm text-gray-500">No interfaces (check RouterOS REST/API settings).</div>}
      </div>
    </Card>
  );
}

function StepPeers({ router, demoMode, iface, onSelectionChange }: { router: Router | null; demoMode: boolean; iface: string; onSelectionChange?: (items: { interface: string; public_key: string; selected: boolean }[]) => void }) {
  const [list, setList] = React.useState<PeerView[]>([]);
  const [selected, setSelected] = React.useState<Record<string, boolean>>({});
  React.useEffect(()=>{
    (async () => {
      try {
        if (demoMode || !router) {
          const saved = await listSavedPeers();
          const peers = saved.filter(p => p.interface === iface).map(p => ({
            id: p.id,
            interface: p.interface,
            name: p.name,
            public_key: p.public_key,
            allowed_address: p.allowed_address,
            disabled: p.disabled,
            endpoint: "",
            last_handshake: undefined,
            online: true,
          } as PeerView));
          setList(peers);
        } else {
          const fetched = await routerPeers(router.id, iface);
          setList(fetched);
        }
      } catch { setList([]); }
    })();
  }, [demoMode, router?.id, iface]);
  // derive default selection: inbound peers preselected, outbound (0.0.0.0/0 or ::/0) unselected
  React.useEffect(() => {
    if (list.length === 0) { setSelected({}); return; }
    const next: Record<string, boolean> = {};
    for (const p of list) {
      const addr = (p.allowed_address || "").trim();
      const outbound = addr === "0.0.0.0/0" || addr === "::/0";
      next[p.public_key] = !outbound;
    }
    setSelected(next);
    if (onSelectionChange) {
      const items = list.map(p => ({ interface: p.interface, public_key: p.public_key, selected: next[p.public_key] }));
      onSelectionChange(items);
    }
  }, [list]);

  const toggle = (pk: string) => setSelected(prev => {
    const updated = { ...prev, [pk]: !prev[pk] };
    if (onSelectionChange) {
      const items = list.map(p => ({ interface: p.interface, public_key: p.public_key, selected: updated[p.public_key] }));
      onSelectionChange(items);
    }
    return updated;
  });
  const allSelectedCount = Object.values(selected).filter(Boolean).length;
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-gray-700">Peers on {iface}</div>
        <div className="text-xs text-gray-500">{allSelectedCount} selected</div>
      </div>
      <div className="grid gap-5 md:gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {list.map(p => {
          const isSelected = !!selected[p.public_key];
          const addr = (p.allowed_address || "").trim();
          const outbound = addr === "0.0.0.0/0" || addr === "::/0";
          return (
            <div
              key={p.public_key}
              onClick={() => toggle(p.public_key)}
              className={`relative cursor-pointer rounded-xl overflow-hidden ring-2 ${isSelected ? 'ring-gray-900 bg-gray-50' : 'ring-gray-200'} ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 p-4`}
            >
              {isSelected && (
                <span className="absolute top-3 right-3 inline-flex items-center justify-center w-5 h-5 rounded-full bg-gray-900 text-white">
                  <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M20 6L9 17l-5-5" />
                  </svg>
                </span>
              )}
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-gray-900">{p.name || p.public_key.slice(0,8)}</div>
                  <div className="text-xs text-gray-500 pr-6 break-words">{p.allowed_address} · {p.endpoint || "no endpoint"}</div>
                </div>
                <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs mr-6 ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-green-100 text-green-800'}`}>
                  <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-green-500'}`} />
                  {outbound ? 'Outbound' : 'Inbound'}
                </span>
              </div>
            </div>
          );
        })}
        {list.length === 0 && <div className="text-sm text-gray-500">No peers found.</div>}
      </div>
    </Card>
  );
}


