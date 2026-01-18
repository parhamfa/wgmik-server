import React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate, useLocation } from "react-router-dom";
import {
    createRouter,
    listRouters,
    setActiveRouter,
    routerInterfaces,
    routerPeers,
    testRouter,
    importPeers,
    deleteRouter,
    type Router,
    type RouterProto,
    type PeerView,
} from "../api";
import { useAuth } from "../auth";

export default function LoginSetup() {
    const { user, login } = useAuth();
    const [step, setStep] = React.useState<1 | 2 | 3 | 4>(1); // 1=Login, 2=Connect, 3=Interface, 4=Peers
    const [router, setRouter] = React.useState<Router | null>(null);
    const [iface, setIface] = React.useState<string>("");
    const [pendingImport, setPendingImport] = React.useState<
        { interface: string; public_key: string; selected: boolean }[]
    >([]);
    const [submitting, setSubmitting] = React.useState(false);
    const [submitErr, setSubmitErr] = React.useState("");
    const navigate = useNavigate();
    const location = useLocation();

    // If already logged in, skip to Step 2 (Connect) on mount
    React.useEffect(() => {
        if (user && step === 1) {
            setStep(2);
        }
    }, [user, step]);

    // Directional slide transitions across steps
    const [direction, setDirection] = React.useState(1);
    const prevStepRef = React.useRef(step);
    React.useEffect(() => {
        if (step > prevStepRef.current) setDirection(1);
        else if (step < prevStepRef.current) setDirection(-1);
        prevStepRef.current = step;
    }, [step]);

    const connected = !!router;
    const isSetupSteps = step >= 2;

    return (
        <div className="mx-auto px-4 md:px-6 py-6">
            <div className="mt-10 md:mt-16 mb-6">
                <StepHeader current={step} total={4} />
            </div>
            <div className="mx-auto my-12 md:my-16 w-full max-w-[760px] h-[700px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 md:p-6 overflow-y-auto overflow-x-hidden relative">
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
                            <StepLogin
                                onSuccess={() => {
                                    setDirection(1);
                                    setStep(2);
                                }}
                            />
                        )}
                        {step === 2 && (
                            <StepConnect
                                onConnected={(r) => {
                                    setDirection(1);
                                    setRouter(r);
                                    setStep(3);
                                }}
                                onGoToDashboard={() => {
                                    navigate("/");
                                }}
                            />
                        )}
                        {step === 3 && (
                            <StepInterface
                                router={router}
                                onSelected={(i) => {
                                    setDirection(1);
                                    setIface(i);
                                    setStep(4);
                                }}
                            />
                        )}
                        {step === 4 && (
                            <StepPeers
                                router={router}
                                iface={iface}
                                onSelectionChange={setPendingImport}
                            />
                        )}
                    </motion.div>
                </AnimatePresence>
            </div>

            {/* Navigation Buttons (Only for Setup Steps) */}
            {isSetupSteps && (
                <div className="mx-auto w-full max-w-[760px] mt-3 flex items-center gap-3">
                    <button
                        type="button"
                        onClick={() => {
                            setDirection(-1);
                            setStep((s) => (s === 2 ? 2 : ((s - 1) as 1 | 2 | 3 | 4))); // Don't go back to Login (1) from Connect (2) via button, logout instead
                        }}
                        disabled={step === 2}
                        className="inline-flex items-center gap-2 rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 px-4 py-2 text-sm shadow hover:bg-black dark:hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        <svg
                            viewBox="0 0 24 24"
                            width="16"
                            height="16"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                        >
                            <path d="M15 18l-6-6 6-6" />
                        </svg>
                        Previous
                    </button>
                    {step < 4 ? (
                        <button
                            type="button"
                            onClick={() => {
                                setDirection(1);
                                setStep((s) => (s === 4 ? 4 : ((s + 1) as 1 | 2 | 3 | 4)));
                            }}
                            // Only enable Next if we have router/iface selection state logic handled within components or simple nav
                            // For now, StepConnect/StepInterface handle 'Next' via their own onSelected callbacks effectively?
                            // Actually StepConnect sets Step 3 on connect.
                            // StepInterface sets Step 4 on select.
                            // This Next button is mostly redundant if the items themselves trigger next, BUT useful if we want manual control.
                            // Consistent with Wizard.tsx, let's keep it but handle the click appropriately?
                            // In Wizard.tsx, "Next" increases step.
                            // Here, we only show it if applicable.
                            // Let's hide Next for steps where selection is required to proceed?
                            className="inline-flex items-center gap-2 rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 px-4 py-2 text-sm shadow hover:bg-black dark:hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed hidden"
                        >
                            Next
                            <svg
                                viewBox="0 0 24 24"
                                width="16"
                                height="16"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                            >
                                <path d="M9 6l6 6-6 6" />
                            </svg>
                        </button>
                    ) : (
                        <button
                            type="button"
                            onClick={async () => {
                                setSubmitErr("");
                                if (!router) {
                                    navigate("/");
                                    return;
                                }
                                try {
                                    setSubmitting(true);
                                    const items =
                                        pendingImport && pendingImport.length ? pendingImport : [];
                                    await importPeers(router.id, items);
                                    navigate("/");
                                } catch (e: any) {
                                    setSubmitErr(e?.message || "Failed to import peers");
                                } finally {
                                    setSubmitting(false);
                                }
                            }}
                            disabled={submitting}
                            className="inline-flex items-center gap-2 rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 px-4 py-2 text-sm shadow hover:bg-black dark:hover:bg-white disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            {submitting ? "Submitting…" : "Finish Import"}
                            {!submitting && (
                                <svg
                                    viewBox="0 0 24 24"
                                    width="16"
                                    height="16"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                >
                                    <path d="M9 6l6 6-6 6" />
                                </svg>
                            )}
                        </button>
                    )}
                </div>
            )}
            {submitErr && (
                <div className="mx-auto w-full max-w-[760px] mt-2 text-sm text-red-600 dark:text-red-400">
                    {submitErr}
                </div>
            )}
        </div>
    );
}

function Card(props: React.HTMLAttributes<HTMLDivElement>) {
    const base =
        "rounded-xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
    return (
        <div className={base + (props.className ? " " + props.className : "")} {...props} />
    );
}

function StepHeader({ current, total }: { current: number; total: number }) {
    const items = Array.from({ length: total }, (_, i) => i + 1);
    const labels: Record<number, string> = {
        1: "Login",
        2: "Connect",
        3: "Interface",
        4: "Peers",
    };
    return (
        <div className="flex items-center justify-center mb-6 select-none">
            {items.map((n, idx) => (
                <React.Fragment key={n}>
                    <div className="flex flex-col items-center">
                        <span
                            className={`inline-block rounded-full ${current === n
                                    ? "bg-gray-900 dark:bg-gray-100"
                                    : "bg-transparent ring-1 ring-gray-300 dark:ring-gray-600"
                                } w-3.5 h-3.5`}
                        />
                        <span
                            className={`mt-2 text-xs ${current === n
                                    ? "text-gray-900 dark:text-gray-100 font-medium"
                                    : "text-gray-500 dark:text-gray-400"
                                }`}
                        >
                            {labels[n] || `Step ${n}`}
                        </span>
                    </div>
                    {idx < items.length - 1 && (
                        <span className="mx-4 h-px w-10 bg-gray-300 dark:bg-gray-700 self-center" />
                    )}
                </React.Fragment>
            ))}
        </div>
    );
}

// --- Step 1: Login ---
function StepLogin({ onSuccess }: { onSuccess: () => void }) {
    const { login } = useAuth();
    const [username, setUsername] = React.useState("");
    const [password, setPassword] = React.useState("");
    const [error, setError] = React.useState("");
    const [busy, setBusy] = React.useState(false);

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setError("");
        setBusy(true);
        try {
            await login({ username, password });
            onSuccess();
        } catch (err: any) {
            console.error(err);
            setError("Invalid username or password");
        } finally {
            setBusy(false);
        }
    };

    return (
        <Card className="p-8 h-full flex flex-col items-center justify-center max-w-sm mx-auto shadow-none ring-0 hover:translate-y-0">
            <h2 className="text-2xl font-bold text-gray-900 dark:text-white mb-2">Welcome Back</h2>
            <p className="text-gray-500 dark:text-gray-400 mb-8 text-center text-sm">
                Sign in to manage your WireGuard peers
            </p>

            <form className="w-full space-y-4" onSubmit={handleSubmit}>
                <div>
                    <label className="sr-only">Username</label>
                    <input
                        type="text"
                        required
                        className="block w-full rounded-xl border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-3 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                        placeholder="Username"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                    />
                </div>
                <div>
                    <label className="sr-only">Password</label>
                    <input
                        type="password"
                        required
                        className="block w-full rounded-xl border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-3 text-sm focus:border-indigo-500 focus:ring-indigo-500"
                        placeholder="Password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                    />
                </div>

                {error && <div className="text-red-500 text-sm text-center">{error}</div>}

                <button
                    type="submit"
                    disabled={busy}
                    className="w-full flex justify-center py-3 px-4 border border-transparent rounded-full shadow-sm text-sm font-medium text-white bg-gray-900 hover:bg-black dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100 disabled:opacity-50"
                >
                    {busy ? "Signing in..." : "Sign in"}
                </button>
            </form>
        </Card>
    );
}

// --- Step 2: Connect (Wizard StepConnect) ---
function StepConnect({
    onConnected,
    onGoToDashboard,
}: {
    onConnected: (r: Router) => void;
    onGoToDashboard: () => void;
}) {
    const protoDefaults: Record<RouterProto, number> = {
        rest: 443,
        "rest-http": 80,
        api: 8729,
        "api-plain": 8728,
    };
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
    const [confirmDeleteRouter, setConfirmDeleteRouter] = React.useState<Router | null>(
        null
    );

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

    React.useEffect(() => {
        loadRouters();
    }, [loadRouters]);

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
            try {
                await setActiveRouter(selectedRouter.id);
            } catch {
                // ignore
            }
            onConnected(selectedRouter);
        } catch (e: any) {
            const msg =
                e?.message || "Connection failed. Verify RouterOS service/port/firewall.";
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
            <div className="mb-3 text-gray-700 dark:text-gray-300">Choose a RouterOS profile</div>
            <div className="grid gap-6 md:grid-cols-2 flex-1">
                <div className="flex flex-col">
                    <div className="text-xs text-gray-500 mb-2">
                        Saved profiles (select one to continue)
                    </div>
                    <div className="flex-1 overflow-y-auto px-1 py-1 space-y-3 max-h-[400px]">
                        {loadingRouters ? (
                            <div className="text-sm text-gray-500">Loading profiles…</div>
                        ) : routers.length === 0 ? (
                            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-700 p-4 text-sm text-gray-500">
                                No profiles yet. Add one on the right.
                            </div>
                        ) : (
                            routers.map((r) => {
                                const isSelected = selectedId === r.id;
                                const status = testStatus[r.id];
                                return (
                                    <div
                                        key={r.id}
                                        onClick={() => setSelectedId(r.id)}
                                        className={`rounded-2xl ring-1 p-4 cursor-pointer transition hover:shadow-md ${isSelected
                                                ? "ring-gray-900 bg-gray-50 dark:ring-gray-100 dark:bg-gray-800"
                                                : "ring-gray-200 bg-white dark:ring-gray-700 dark:bg-gray-800/50"
                                            }`}
                                    >
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <div className="text-sm font-medium text-gray-900 dark:text-white">
                                                    {r.name}
                                                </div>
                                                <div className="text-xs text-gray-500">
                                                    {r.proto.toUpperCase()} · {r.host}:{r.port}
                                                </div>
                                                <div className="text-xs text-gray-500">
                                                    User: {r.username}
                                                </div>
                                            </div>
                                            {isSelected && (
                                                <span className="inline-flex items-center rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 px-2 py-0.5 text-[10px]">
                                                    Selected
                                                </span>
                                            )}
                                        </div>
                                        {status && (
                                            <div
                                                className={`mt-2 text-xs ${status === "OK" ? "text-green-700 dark:text-green-400" : "text-rose-600 dark:text-rose-400"
                                                    }`}
                                            >
                                                Status: {status}
                                            </div>
                                        )}
                                        <div className="mt-3 flex items-center gap-2">
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    handleTest(r.id);
                                                }}
                                                className="rounded-full bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:hover:bg-gray-600"
                                                disabled={testBusyId === r.id}
                                            >
                                                {testBusyId === r.id ? "Testing…" : "Test"}
                                            </button>
                                            <button
                                                type="button"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setConfirmDeleteRouter(r);
                                                }}
                                                className="rounded-full bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400 px-3 py-1 text-xs shadow hover:bg-rose-100 dark:hover:bg-rose-900/40"
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
                        <input
                            className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                            placeholder="Profile name"
                            value={form.name}
                            onChange={(e) => setForm({ ...form, name: e.target.value })}
                        />
                        <input
                            className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                            placeholder="Host / IP"
                            value={form.host}
                            onChange={(e) => setForm({ ...form, host: e.target.value })}
                        />
                        <div className="grid grid-cols-[1fr_auto] gap-3">
                            <select
                                className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                                value={form.proto}
                                onChange={(e) => {
                                    const next = e.target.value as RouterProto;
                                    setForm((prev) => ({
                                        ...prev,
                                        proto: next,
                                        port: prev.port || protoDefaults[next],
                                    }));
                                }}
                            >
                                <option value="rest">REST (HTTPS)</option>
                                <option value="rest-http">REST (HTTP)</option>
                                <option value="api">API (TLS)</option>
                                <option value="api-plain">API (Plain)</option>
                            </select>
                            <input
                                className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm w-24 md:w-28 focus:ring-2 focus:ring-gray-300"
                                placeholder="Port"
                                type="number"
                                value={form.port}
                                onChange={(e) =>
                                    setForm({ ...form, port: Number(e.target.value) })
                                }
                            />
                        </div>
                        <input
                            className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                            placeholder="Username"
                            value={form.username}
                            onChange={(e) => setForm({ ...form, username: e.target.value })}
                        />
                        <input
                            className="rounded-xl border border-gray-200 dark:border-gray-700 dark:bg-gray-800 dark:text-white px-4 py-2.5 text-sm focus:ring-2 focus:ring-gray-300"
                            placeholder="Password"
                            type="password"
                            value={form.password}
                            onChange={(e) => setForm({ ...form, password: e.target.value })}
                        />
                        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
                            <input
                                type="checkbox"
                                className="rounded border-gray-300 text-gray-900 focus:ring-gray-300"
                                checked={form.tls_verify}
                                onChange={(e) =>
                                    setForm({ ...form, tls_verify: e.target.checked })
                                }
                            />
                            Verify TLS certificates
                        </label>
                        {addErr && <div className="text-sm text-red-600">{addErr}</div>}
                        <button
                            type="button"
                            onClick={handleAddProfile}
                            disabled={addBusy}
                            className="rounded-xl px-4 py-2.5 text-sm bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 hover:bg-black dark:hover:bg-white disabled:opacity-50"
                        >
                            {addBusy ? "Saving…" : "Save profile"}
                        </button>
                    </div>
                </div>
            </div>
            <div className="mt-6 flex flex-col gap-3 pb-8">
                {connectErr && (
                    <div className="text-sm text-red-600 dark:text-red-400 text-center">
                        {connectErr}
                    </div>
                )}
                <div className="flex flex-wrap gap-3 justify-center">
                    <button
                        type="button"
                        className="rounded-full px-5 py-2 text-sm bg-gray-200 text-gray-800 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-200"
                        onClick={onGoToDashboard}
                    >
                        Go to Dashboard
                    </button>
                    <button
                        type="button"
                        disabled={!selectedRouter || connectBusy}
                        onClick={handleConnect}
                        className="rounded-full px-5 py-2 text-sm bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 hover:bg-black dark:hover:bg-white disabled:opacity-50"
                    >
                        {connectBusy ? "Connecting…" : "Import from Selected"}
                    </button>
                </div>
            </div>
            {confirmDeleteRouter && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
                    <div className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-800 ring-1 ring-gray-200 dark:ring-gray-700 shadow-lg p-6 grid gap-4">
                        <div className="text-lg font-semibold text-gray-900 dark:text-white">
                            Remove profile
                        </div>
                        <div className="text-sm text-gray-600 dark:text-gray-300">
                            This removes{" "}
                            <span className="font-medium text-gray-900 dark:text-white">
                                {confirmDeleteRouter.name}
                            </span>{" "}
                            from this app. It does not touch the RouterOS device.
                        </div>
                        <div className="flex items-center justify-end gap-3">
                            <button
                                type="button"
                                onClick={() => setConfirmDeleteRouter(null)}
                                className="rounded-full bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:hover:bg-gray-600"
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
                                        setSelectedId((prev) =>
                                            prev === confirmDeleteRouter.id ? null : prev
                                        );
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
                                {deleteBusyId === confirmDeleteRouter.id
                                    ? "Removing…"
                                    : "Delete profile"}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </Card>
    );
}

// --- Step 3: Interface (Wizard StepInterface) ---
function StepInterface({
    router,
    onSelected,
}: {
    router: Router | null;
    onSelected: (iface: string) => void;
}) {
    const [ifs, setIfs] = React.useState<string[]>([]);
    React.useEffect(() => {
        (async () => {
            try {
                if (!router) return;
                const fetched = await routerInterfaces(router.id);
                setIfs(fetched);
            } catch {
                setIfs([]);
            }
        })();
    }, [router?.id]);
    return (
        <Card className="p-4">
            <div className="mb-3 text-gray-700 dark:text-gray-300">Select a WireGuard interface</div>
            <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                {ifs.map((i) => (
                    <div
                        key={i}
                        onClick={() => onSelected(i)}
                        className="rounded-xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 p-4 cursor-pointer dark:bg-gray-800 dark:ring-gray-700"
                    >
                        <div className="text-sm text-gray-900 dark:text-white">{i}</div>
                    </div>
                ))}
                {ifs.length === 0 && (
                    <div className="text-sm text-gray-500">
                        No interfaces (check RouterOS REST/API settings).
                    </div>
                )}
            </div>
        </Card>
    );
}

// --- Step 4: Peers (Wizard StepPeers) ---
function StepPeers({
    router,
    iface,
    onSelectionChange,
}: {
    router: Router | null;
    iface: string;
    onSelectionChange?: (
        items: { interface: string; public_key: string; selected: boolean }[]
    ) => void;
}) {
    const [list, setList] = React.useState<PeerView[]>([]);
    const [selected, setSelected] = React.useState<Record<string, boolean>>({});
    React.useEffect(() => {
        (async () => {
            try {
                if (!router) return;
                const fetched = await routerPeers(router.id, iface);
                setList(fetched);
            } catch {
                setList([]);
            }
        })();
    }, [router?.id, iface]);
    // derive default selection: inbound peers preselected, outbound (0.0.0.0/0 or ::/0) unselected
    React.useEffect(() => {
        if (list.length === 0) {
            setSelected({});
            return;
        }
        const next: Record<string, boolean> = {};
        for (const p of list) {
            const addr = (p.allowed_address || "").trim();
            const outbound = addr === "0.0.0.0/0" || addr === "::/0";
            next[p.public_key] = !outbound;
        }
        setSelected(next);
        if (onSelectionChange) {
            const items = list.map((p) => ({
                interface: p.interface,
                public_key: p.public_key,
                selected: next[p.public_key],
            }));
            onSelectionChange(items);
        }
    }, [list]);

    const toggle = (pk: string) =>
        setSelected((prev) => {
            const updated = { ...prev, [pk]: !prev[pk] };
            if (onSelectionChange) {
                const items = list.map((p) => ({
                    interface: p.interface,
                    public_key: p.public_key,
                    selected: updated[p.public_key],
                }));
                onSelectionChange(items);
            }
            return updated;
        });
    const allSelectedCount = Object.values(selected).filter(Boolean).length;
    return (
        <Card className="p-4">
            <div className="mb-3 flex items-center justify-between">
                <div className="text-gray-700 dark:text-gray-300">Peers on {iface}</div>
                <div className="text-xs text-gray-500">{allSelectedCount} selected</div>
            </div>
            <div className="grid gap-5 md:gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {list.map((p) => {
                    const isSelected = !!selected[p.public_key];
                    const addr = (p.allowed_address || "").trim();
                    const outbound = addr === "0.0.0.0/0" || addr === "::/0";
                    return (
                        <div
                            key={p.public_key}
                            onClick={() => toggle(p.public_key)}
                            className={`relative cursor-pointer rounded-xl overflow-hidden ring-2 ${isSelected
                                    ? "ring-gray-900 bg-gray-50 dark:ring-gray-100 dark:bg-gray-800"
                                    : "ring-gray-200 dark:ring-gray-700 bg-white dark:bg-gray-800"
                                } ring-offset-2 ring-offset-gray-50 dark:ring-offset-gray-950 shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 p-4`}
                        >
                            {isSelected && (
                                <span className="absolute top-3 right-3 inline-flex items-center justify-center w-5 h-5 rounded-full bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900">
                                    <svg
                                        viewBox="0 0 24 24"
                                        width="12"
                                        height="12"
                                        fill="none"
                                        stroke="currentColor"
                                        strokeWidth="3"
                                        strokeLinecap="round"
                                        strokeLinejoin="round"
                                    >
                                        <path d="M20 6L9 17l-5-5" />
                                    </svg>
                                </span>
                            )}
                            <div className="flex items-center justify-between">
                                <div>
                                    <div className="text-sm text-gray-900 dark:text-white">
                                        {p.name || p.public_key.slice(0, 8)}
                                    </div>
                                    <div className="text-xs text-gray-500 pr-6 break-words">
                                        {p.allowed_address} · {p.endpoint || "no endpoint"}
                                    </div>
                                </div>
                                <span
                                    className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs mr-6 ${outbound
                                            ? "bg-amber-100 text-amber-800"
                                            : "bg-green-100 text-green-800"
                                        }`}
                                >
                                    <span
                                        className={`inline-block w-2 h-2 rounded-full ${outbound ? "bg-amber-500" : "bg-green-500"
                                            }`}
                                    />
                                    {outbound ? "Outbound" : "Inbound"}
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
