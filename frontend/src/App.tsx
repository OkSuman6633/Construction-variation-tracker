import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  type ApiContext,
  type CaseSummary,
  type Job,
  type LocalSession,
  type LocalUser,
  type Organisation,
  type Project,
  type TeamMember,
  type Timeline,
} from "./api";
import { authConfigured, authManager } from "./auth";
import type { User as OidcUser } from "oidc-client-ts";

type Section = "overview" | "cases" | "projects" | "team";
type Notice = { tone: "success" | "error" | "info"; text: string };
type ActiveUser = { user_id: string; email: string | null; name?: string; must_change_password?: boolean };

const acceptedFiles =
  ".pdf,.docx,.xlsx,.eml,.jpg,.jpeg,.png,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,message/rfc822,image/jpeg,image/png";
const formatDate = (date: string) =>
  new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" }).format(
    new Date(date),
  );
const pretty = (value: string) => value.replaceAll("_", " ");

function Icon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    mark: "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5",
    home: "m3 10 9-7 9 7 M5 9v11h14V9 M9 20v-6h6v6",
    cases: "M4 5h16v15H4z M8 5V3h8v2 M8 10h8 M8 14h6",
    projects: "M3 8h18v12H3z M3 8l2-4h6l2 4 M13 12h4",
    team: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
    plus: "M12 5v14 M5 12h14",
    arrow: "M5 12h14 M13 6l6 6-6 6",
    upload: "M12 16V4 M7 9l5-5 5 5 M4 20h16",
    spark: "m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z",
    check: "m5 12 4 4L19 6",
    back: "M19 12H5 M11 18l-6-6 6-6",
    close: "M18 6 6 18 M6 6l12 12",
  };
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="icon">
      <path d={paths[name] ?? paths.mark} />
    </svg>
  );
}

function App() {
  const [token, setToken] = useState("");
  const [tenantId, setTenantId] = useState(() => localStorage.getItem("cvea-tenant") ?? "");
  const [organisations, setOrganisations] = useState<Organisation[]>([]);
  const [user, setUser] = useState<ActiveUser | null>(null);
  const [isLocalAccount, setIsLocalAccount] = useState(false);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [localAuthEnabled, setLocalAuthEnabled] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [authError, setAuthError] = useState("");
  const [section, setSection] = useState<Section>("overview");
  const [projects, setProjects] = useState<Project[]>([]);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [selectedCase, setSelectedCase] = useState<CaseSummary | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [showCaseForm, setShowCaseForm] = useState(false);
  const [showConnection, setShowConnection] = useState(false);
  const [uploadDate, setUploadDate] = useState("");
  const [dateUnknown, setDateUnknown] = useState(false);
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [temporaryPassword, setTemporaryPassword] = useState<{ email: string; password: string } | null>(null);

  const context = useMemo<ApiContext | null>(
    () => (connected && token && tenantId ? { token, tenantId } : null),
    [connected, tenantId, token],
  );

  const refresh = useCallback(async (ctx: ApiContext) => {
    const [projectResult, caseResult] = await Promise.all([
      api.listProjects(ctx),
      api.listCases(ctx),
    ]);
    setProjects(projectResult.items);
    setCases(caseResult.items);
  }, []);

  useEffect(() => {
    const updateAccessToken = (session: OidcUser) => {
      if (session.access_token) setToken(session.access_token);
    };
    authManager?.events.addUserLoaded(updateAccessToken);
    return () => authManager?.events.removeUserLoaded(updateAccessToken);
  }, []);

  const connect = useCallback(async (
    bearer: string,
    requestedTenant: string,
    profile?: LocalUser,
  ) => {
    setConnecting(true);
    setNotice(null);
    setAuthError("");
    try {
      await api.health();
      const [identity, organisationResult] = await Promise.all([
        api.me(bearer),
        api.organisations(bearer),
      ]);
      const memberships = organisationResult.organisations;
      const allowedTenant = memberships.some((item) => item.tenant_id === requestedTenant);
      const tenant = allowedTenant ? requestedTenant : memberships[0]?.tenant_id ?? "";
      if (tenant) await refresh({ token: bearer, tenantId: tenant });
      else {
        setProjects([]);
        setCases([]);
      }
      setToken(bearer);
      setTenantId(tenant);
      setUser(profile ?? identity);
      setIsLocalAccount(Boolean(profile));
      setOrganisations(memberships);
      setConnected(true);
      if (tenant) localStorage.setItem("cvea-tenant", tenant);
      else localStorage.removeItem("cvea-tenant");
      setShowConnection(false);
    } catch (error) {
      setConnected(false);
      const message = errorMessage(error);
      setAuthError(message);
      setNotice({ tone: "error", text: message });
    } finally {
      setConnecting(false);
    }
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    const restoreSignIn = async () => {
      try {
        const mode = await api.authMode();
        if (cancelled) return;
        setLocalAuthEnabled(mode.local_password_enabled);
        const isCallback = window.location.pathname === "/auth/callback";
        const oidcSession = authManager
          ? isCallback
            ? await authManager.signinRedirectCallback()
            : await authManager.getUser()
          : null;
        if (isCallback) window.history.replaceState({}, "", "/");
        if (oidcSession && !oidcSession.expired && oidcSession.access_token) {
          await connect(oidcSession.access_token, localStorage.getItem("cvea-tenant") ?? "");
          return;
        }
        const storedToken = sessionStorage.getItem("cvea-access-token");
        const storedProfile = sessionStorage.getItem("cvea-profile");
        if (storedToken) {
          const profile = storedProfile ? (JSON.parse(storedProfile) as LocalUser) : undefined;
          await connect(storedToken, localStorage.getItem("cvea-tenant") ?? "", profile);
        }
      } catch (error) {
        if (!cancelled) setAuthError(errorMessage(error));
      } finally {
        if (!cancelled) setAuthReady(true);
      }
    };
    void restoreSignIn();
    return () => {
      cancelled = true;
    };
  }, [connect]);

  async function signIn() {
    if (!authManager) return;
    setConnecting(true);
    setAuthError("");
    try {
      await authManager.signinRedirect();
    } catch (error) {
      setAuthError(errorMessage(error));
      setConnecting(false);
    }
  }

  async function submitLocalAuth(event: React.FormEvent<HTMLFormElement>) {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      setConnecting(true);
      setAuthError("");
      try {
        const session = authMode === "signup"
          ? await api.localSignup({
              name: String(form.get("name") ?? "").trim(),
              email: String(form.get("email") ?? "").trim(),
              password: String(form.get("password") ?? ""),
            })
          : await api.localLogin({
              email: String(form.get("email") ?? "").trim(),
              password: String(form.get("password") ?? ""),
            });
        storeLocalSession(session);
        await connect(session.access_token, localStorage.getItem("cvea-tenant") ?? "", session.user);
      } catch (error) {
        setAuthError(errorMessage(error));
      } finally {
        setConnecting(false);
    }
  }

  async function signOut() {
    await authManager?.removeUser();
    setConnected(false);
    setConnecting(false);
    setToken("");
    sessionStorage.removeItem("cvea-access-token");
    sessionStorage.removeItem("cvea-profile");
    setTenantId("");
    setUser(null);
    setIsLocalAccount(false);
    setOrganisations([]);
    setProjects([]);
    setCases([]);
    setSelectedCase(null);
    setSelectedProject(null);
    setTimeline(null);
    setJob(null);
    localStorage.removeItem("cvea-tenant");
    setShowConnection(false);
    setTeamMembers([]);
    setTemporaryPassword(null);
  }

  async function changePassword(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token || !user) return;
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const profile: LocalUser = {
      user_id: user.user_id,
      email: user.email ?? "",
      name: user.name ?? user.email ?? "",
      must_change_password: false,
    };
    try {
      await api.changePassword(token, {
        current_password: String(form.get("current_password") ?? ""),
        new_password: String(form.get("new_password") ?? ""),
      });
      setUser({ ...user, must_change_password: false });
      sessionStorage.setItem("cvea-profile", JSON.stringify(profile));
      setNotice({ tone: "success", text: "Password updated." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function createOrganisation(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name") ?? "").trim();
    setBusy(true);
    try {
      const created = await api.createOrganisation(token, name);
      const ctx = { token, tenantId: created.tenant_id };
      setOrganisations([created]);
      setTenantId(created.tenant_id);
      localStorage.setItem("cvea-tenant", created.tenant_id);
      await refresh(ctx);
      setNotice({ tone: "success", text: "Organisation created. Your workspace is ready." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!context || !selectedCase?.active_job_id) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const latest = await api.getJob(context, selectedCase.active_job_id!);
        if (cancelled) return;
        setJob(latest);
        if (latest.status === "needs_review" || latest.status === "completed") {
          const [detail, result] = await Promise.all([
            api.getCase(context, selectedCase.case_id),
            api.getTimeline(context, selectedCase.case_id),
          ]);
          if (cancelled) return;
          setSelectedCase(detail);
          setTimeline(result);
          await refresh(context);
          setNotice({ tone: "success", text: "Analysis is ready for professional review." });
        } else if (latest.status === "failed") {
          const detail = await api.getCase(context, selectedCase.case_id);
          if (cancelled) return;
          setSelectedCase(detail);
          await refresh(context);
          setNotice({
            tone: "error",
            text: `${latest.failure_code ?? "Analysis failed"}: ${latest.failure_message ?? "Please review the job and retry."}`,
          });
        }
      } catch (error) {
        if (!cancelled) setNotice({ tone: "error", text: errorMessage(error) });
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [context, refresh, selectedCase?.active_job_id, selectedCase?.case_id]);

  async function openCase(item: CaseSummary) {
    if (!context) return;
    setSelectedCase(item);
    setSelectedProject(null);
    setTimeline(null);
    setJob(null);
    setSection("cases");
    try {
      const [detail, result] = await Promise.all([
        api.getCase(context, item.case_id),
        api.getTimeline(context, item.case_id),
      ]);
      setSelectedCase(detail);
      setTimeline(result);
      if (detail.active_job_id) setJob(await api.getJob(context, detail.active_job_id));
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function openProject(item: Project) {
    if (!context) return;
    try {
      const detail = await api.getProject(context, item.project_id);
      setSelectedProject(detail);
      setSelectedCase(null);
      setTimeline(null);
      setSection("projects");
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function createProject(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await api.createProject(context, {
        name: data.get("name"),
        reference: optional(data.get("reference")),
        client: optional(data.get("client")),
        site_address: optional(data.get("site_address")),
        contract: {
          form: data.get("form"),
          description: optional(data.get("contract_description")),
          jurisdiction: optional(data.get("jurisdiction")),
        },
      });
      await refresh(context);
      setShowProjectForm(false);
      setNotice({ tone: "success", text: "Project created." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function createCase(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    try {
      const result = await api.createCase(context, {
        project_id: data.get("project_id"),
        title: data.get("title"),
        alleged_change:
          (data.get("alleged_change") as string) ||
          "Reconcile and extract all variation events, scope changes, instructions, delays, quantities, and price consequences evidenced across all uploaded case documents into an audit-ready factual record.",
        contract: {
          form: data.get("form"),
          description: optional(data.get("contract_description")),
          jurisdiction: optional(data.get("jurisdiction")),
        },
      });
      await refresh(context);
      setShowCaseForm(false);
      const created = await api.getCase(context, result.case_id);
      await openCase(created);
      setNotice({ tone: "success", text: "Case created. Add evidence to get started." });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function uploadFiles(files: FileList | null) {
    if (!context || !selectedCase || !files?.length) return;
    setBusy(true);
    let succeeded = 0;
    try {
      for (const file of Array.from(files)) {
        await api.uploadEvidence(context, selectedCase.case_id, file, {
          recordedDate: uploadDate,
          dateUnknown,
          origin: "Uploaded via Fieldnote",
        });
        succeeded += 1;
      }
      await openCase(selectedCase);
      await refresh(context);
      setNotice({ tone: "success", text: `${succeeded} ${succeeded === 1 ? "document" : "documents"} added.` });
    } catch (error) {
      setNotice({
        tone: "error",
        text: `${succeeded ? `${succeeded} file(s) uploaded. ` : ""}${errorMessage(error)}`,
      });
      await openCase(selectedCase);
    } finally {
      setBusy(false);
      const input = document.getElementById("evidence-files") as HTMLInputElement | null;
      if (input) input.value = "";
    }
  }

  async function startAnalysis() {
    if (!context || !selectedCase) return;
    setBusy(true);
    try {
      const result = await api.analyse(context, selectedCase.case_id);
      const latest = await api.getJob(context, result.job_id);
      setJob(latest);
      const detail = await api.getCase(context, selectedCase.case_id);
      setSelectedCase(detail);
      setNotice({
        tone: "info",
        text: result.duplicate_active_job
          ? "This case already has an active analysis. Showing its progress."
          : "Analysis queued. You can keep this page open to follow progress.",
      });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function downloadReport(format: "pdf" | "docx" = "pdf") {
    if (!context || !selectedCase) return;
    setBusy(true);
    try {
      const report = selectedCase.report_refs?.length
        ? await api.getReport(context, selectedCase.case_id, format)
        : await api.createReport(context, selectedCase.case_id, format);
      const url = URL.createObjectURL(report.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = report.filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      const detail = await api.getCase(context, selectedCase.case_id);
      setSelectedCase(detail);
      setCases((current) =>
        current.map((item) => (item.case_id === detail.case_id ? detail : item)),
      );
      setNotice({
        tone: "success",
        text: format === "docx" ? "Word (DOCX) dossier downloaded." : "PDF dossier downloaded.",
      });
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  async function showTeam() {
    if (!context) return;
    setSelectedCase(null);
    setSelectedProject(null);
    setTimeline(null);
    setSection("team");
    setTemporaryPassword(null);
    try {
      const result = await api.listMembers(context);
      setTeamMembers(result.members);
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    }
  }

  async function addTeamMember(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context) return;
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setTemporaryPassword(null);
    try {
      const result = await api.createMember(context, {
        email: String(form.get("email") ?? "").trim(),
        role: String(form.get("role") ?? "qs"),
      });
      setTeamMembers((current) => [
        ...current.filter((member) => member.user_id !== result.member.user_id),
        result.member,
      ]);
      setTemporaryPassword(
        result.temporary_password
          ? { email: result.member.email ?? "", password: result.temporary_password }
          : null,
      );
      setNotice({
        tone: "success",
        text: result.temporary_password
          ? "Teammate added. Copy the temporary password now; it will not be shown again."
          : "Existing account added to this organisation.",
      });
      formElement.reset();
    } catch (error) {
      setNotice({ tone: "error", text: errorMessage(error) });
    } finally {
      setBusy(false);
    }
  }

  function changeTenant(nextTenant: string) {
    setTenantId(nextTenant);
    localStorage.setItem("cvea-tenant", nextTenant);
    setSelectedCase(null);
    setSelectedProject(null);
    setTimeline(null);
    setJob(null);
    setSection("overview");
    const profile: LocalUser | undefined = isLocalAccount && user?.email
      ? {
          user_id: user.user_id,
          name: user.name ?? user.email,
          email: user.email,
          must_change_password: Boolean(user.must_change_password),
        }
      : undefined;
    void connect(token, nextTenant, profile);
  }

  const projectNames = useMemo(
    () => new Map(projects.map((project) => [project.project_id, project.name])),
    [projects],
  );
  const openCount = cases.filter((item) => item.stage !== "review_complete").length;
  const readyCount = cases.filter((item) => item.stage === "needs_review").length;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => { setSelectedCase(null); setSelectedProject(null); setSection("overview"); }}>
          <span className="brand-mark"><Icon name="mark" /></span>
          <span>fieldnote<span className="brand-period">.</span><small>VARIATION EVIDENCE</small></span>
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav className="nav-list" aria-label="Main navigation">
          <NavButton icon="home" label="Overview" active={section === "overview" && !selectedCase && !selectedProject} onClick={() => { setSection("overview"); setSelectedCase(null); setSelectedProject(null); }} />
          <NavButton icon="cases" label="Cases" count={cases.length} active={section === "cases"} onClick={() => { setSection("cases"); setSelectedCase(null); setSelectedProject(null); }} />
          <NavButton icon="projects" label="Projects" count={projects.length} active={section === "projects"} onClick={() => { setSection("projects"); setSelectedCase(null); setSelectedProject(null); }} />
          {localAuthEnabled && ["owner", "admin"].includes(organisations.find((item) => item.tenant_id === tenantId)?.role ?? "") && <NavButton icon="team" label="Team" active={section === "team"} onClick={() => void showTeam()} />}
        </nav>
        <div className="sidebar-bottom">
          <div className={`connection-card ${connected ? "is-connected" : ""}`}>
            <span className="connection-dot" />
            <div className="connection-copy">
              <strong>{connected ? "API connected" : "Not connected"}</strong>
              <span>{connected ? organisations.find((item) => item.tenant_id === tenantId)?.name ?? tenantId : "Sign in to your workspace"}</span>
            </div>
            <button className="icon-button" aria-label="Configure API connection" onClick={() => setShowConnection(true)}><span className="gear">⚙</span></button>
          </div>
          <p className="sidebar-footnote">AI-assisted evidence preparation<br />for professional review.</p>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><span className="crumb-slash">/</span><strong>{selectedCase?.title ?? selectedProject?.name ?? sectionLabel(section)}</strong></div>
          <div className="topbar-right">
            <span className="status-indicator"><i className={connected ? "online" : ""} />{connected ? "All systems operational" : "API disconnected"}</span>
            {connected && organisations.length > 1 && <select className="tenant-select" aria-label="Organisation" value={tenantId} onChange={(event) => changeTenant(event.target.value)}>{organisations.map((item) => <option key={item.tenant_id} value={item.tenant_id}>{item.name}</option>)}</select>}
            <button className="avatar" title={user?.email ?? user?.user_id ?? "Account"} onClick={() => setShowConnection(true)}>{user?.user_id.slice(0, 1).toUpperCase() ?? "?"}</button>
          </div>
        </header>

        <div className="page-content">
          {notice && <div className={`notice notice-${notice.tone}`} role="status"><span>{notice.text}</span><button aria-label="Dismiss notification" onClick={() => setNotice(null)}><Icon name="close" /></button></div>}
          {!authReady ? (
            <div className="auth-loading"><span className="spinner" /><span>Checking your sign-in…</span></div>
          ) : !connected ? (
            <ConnectionPage
              mode={authMode}
              setMode={setAuthMode}
              localAuthEnabled={localAuthEnabled}
              oidcConfigured={authConfigured}
              onLocalSubmit={submitLocalAuth}
              onSignIn={() => void signIn()}
              connecting={connecting}
              error={authError}
            />
          ) : user?.must_change_password ? (
            <PasswordChangePage busy={busy} onSubmit={changePassword} />
          ) : !organisations.length ? (
            <OrganisationOnboarding user={user} busy={busy} onSubmit={createOrganisation} />
          ) : section === "team" ? (
            <TeamPage
              members={teamMembers}
              temporaryPassword={temporaryPassword}
              busy={busy}
              onSubmit={addTeamMember}
            />
          ) : selectedCase ? (
            <CasePage
              item={selectedCase}
              timeline={timeline}
              job={job}
              projectName={projectNames.get(selectedCase.project_id) ?? "Project"}
              busy={busy}
              uploadDate={uploadDate}
              setUploadDate={setUploadDate}
              dateUnknown={dateUnknown}
              setDateUnknown={setDateUnknown}
              onBack={() => { setSelectedCase(null); setTimeline(null); }}
              onUpload={uploadFiles}
              onAnalyse={startAnalysis}
              onReport={(format) => void downloadReport(format)}
              onRefresh={() => void openCase(selectedCase)}
            />
          ) : selectedProject ? (
            <ProjectDetail project={selectedProject} caseCount={cases.filter((item) => item.project_id === selectedProject.project_id).length} onBack={() => setSelectedProject(null)} onNewCase={() => setShowCaseForm(true)} onOpenCase={openCase} cases={cases.filter((item) => item.project_id === selectedProject.project_id)} />
          ) : section === "overview" ? (
            <Overview projects={projects} cases={cases} openCount={openCount} readyCount={readyCount} onNewProject={() => setShowProjectForm(true)} onNewCase={() => setShowCaseForm(true)} onOpenCase={openCase} onOpenProject={openProject} onGoCases={() => setSection("cases")} />
          ) : section === "cases" ? (
            <CasesPage cases={cases} projectNames={projectNames} onNew={() => setShowCaseForm(true)} onOpen={openCase} />
          ) : (
            <ProjectsPage projects={projects} cases={cases} onNew={() => setShowProjectForm(true)} onOpen={openProject} />
          )}
        </div>
        <footer className="page-footer"><span>FIELDNOTE · CONSTRUCTION VARIATION EVIDENCE</span><span>DRAFT WORKSPACE · HUMAN REVIEW REQUIRED</span></footer>
      </main>

      {showConnection && <Modal title="Your account" onClose={() => setShowConnection(false)}><AccountPanel user={user} localAuthEnabled={isLocalAccount} onSignOut={() => void signOut()} onChangePassword={changePassword} /></Modal>}
      {showProjectForm && <Modal title="New project" onClose={() => setShowProjectForm(false)}><ProjectForm busy={busy} onCancel={() => setShowProjectForm(false)} onSubmit={createProject} /></Modal>}
      {showCaseForm && <Modal title="Create a case" onClose={() => setShowCaseForm(false)}><CaseForm projects={projects} busy={busy} defaultProject={selectedProject?.project_id} onCancel={() => setShowCaseForm(false)} onSubmit={createCase} /></Modal>}
    </div>
  );
}

function NavButton({ icon, label, count, active, onClick }: { icon: string; label: string; count?: number; active: boolean; onClick: () => void }) {
  return <button className={`nav-item ${active ? "active" : ""}`} onClick={onClick}><Icon name={icon} /><span>{label}</span>{count !== undefined && count > 0 && <span className="nav-count">{count}</span>}</button>;
}

function ConnectionPage({
  mode,
  setMode,
  localAuthEnabled,
  oidcConfigured,
  onLocalSubmit,
  onSignIn,
  connecting,
  error,
}: {
  mode: "login" | "signup";
  setMode: (value: "login" | "signup") => void;
  localAuthEnabled: boolean;
  oidcConfigured: boolean;
  onLocalSubmit: (event: React.FormEvent<HTMLFormElement>) => void;
  onSignIn: () => void;
  connecting: boolean;
  error: string;
}) {
  return <div className="connect-page"><div className="connect-art"><div className="art-sun" /><div className="art-line art-line-one" /><div className="art-line art-line-two" /><div className="art-slab slab-one" /><div className="art-slab slab-two" /><div className="art-slab slab-three" /><span className="art-caption">A clearer record<br />of what changed.</span></div><div className="connect-content"><div className="eyebrow"><span className="eyebrow-line" />YOUR EVIDENCE WORKSPACE</div><h1>Make the record<br />work for you.</h1><p className="lead">Bring project documents together. Let the agent prepare a source-backed draft chronology for your team to review.</p>{localAuthEnabled && <><div className="auth-tabs"><button type="button" className={mode === "login" ? "selected" : ""} onClick={() => setMode("login")}>Sign in</button><button type="button" className={mode === "signup" ? "selected" : ""} onClick={() => setMode("signup")}>Create account</button></div><form className="local-auth-form" onSubmit={onLocalSubmit}>{mode === "signup" && <label>Your name<input name="name" autoComplete="name" required maxLength={200} /></label>}<label>Email address<input name="email" type="email" autoComplete="email" required maxLength={320} /></label><label>Password<input name="password" type="password" autoComplete={mode === "signup" ? "new-password" : "current-password"} required minLength={12} maxLength={128} /></label>{mode === "signup" && <small>Use at least 12 characters. Your account is stored locally in the SQLite database.</small>}<button className="button button-primary full-button" disabled={connecting}>{connecting ? <><span className="spinner" />Signing in…</> : <>{mode === "signup" ? "Create account" : "Sign in"} <Icon name="arrow" /></>}</button></form></>}{oidcConfigured && <button className="button button-outline full-button oidc-button" disabled={connecting} onClick={onSignIn}>Sign in with identity provider</button>}{!localAuthEnabled && !oidcConfigured && <div className="auth-setup-note">No sign-in method is enabled. Set <code>AUTH_DEV_HS256_SECRET</code> for local sign-in, or configure the OIDC provider.</div>}{error && <div className="auth-error-note" role="alert">{error}</div>}<div className="privacy-note"><span className="lock">◇</span>{localAuthEnabled ? "Local accounts and workspace data stay in your local database." : "Secure sign-in with your organisation’s identity provider."}</div></div></div>;
}

function OrganisationOnboarding({ user, busy, onSubmit }: { user: ActiveUser | null; busy: boolean; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <div className="org-onboarding"><div className="onboarding-mark"><Icon name="projects" /></div><div className="eyebrow">WELCOME{user?.email ? `, ${user.email.toUpperCase()}` : ""} <span className="eyebrow-line" /></div><h1>Create your organisation.</h1><p>Set up your private workspace to organise projects, variation cases and source evidence.</p><form className="org-create-form" onSubmit={onSubmit}><label htmlFor="organisation-name">Organisation name</label><input id="organisation-name" name="name" required minLength={2} maxLength={500} placeholder="e.g. North Works Ltd" /><button className="button button-primary" disabled={busy}>{busy ? "Creating workspace…" : <>Create workspace <Icon name="arrow" /></>}</button></form><div className="org-privacy-note">You’ll be the organisation owner. Your evidence is kept within this workspace.</div></div>;
}

function AccountPanel({ user, localAuthEnabled, onSignOut, onChangePassword }: { user: ActiveUser | null; localAuthEnabled: boolean; onSignOut: () => void; onChangePassword: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <div className="account-panel"><div className="account-avatar">{user?.user_id.slice(0, 1).toUpperCase() ?? "?"}</div><div><strong>{user?.name ?? user?.email ?? user?.user_id ?? "Signed in"}</strong><span>{user?.email ?? "Authenticated account"}</span></div>{localAuthEnabled && <PasswordChangeForm onSubmit={onChangePassword} />}<button className="button button-outline" onClick={onSignOut}>Sign out</button></div>;
}

function PasswordChangePage({ busy, onSubmit }: { busy: boolean; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <div className="org-onboarding"><div className="onboarding-mark"><Icon name="team" /></div><div className="eyebrow">ACCOUNT SECURITY <span className="eyebrow-line" /></div><h1>Set your password.</h1><p>This temporary teammate password must be replaced before using the workspace.</p><PasswordChangeForm busy={busy} onSubmit={onSubmit} /></div>;
}

function PasswordChangeForm({ busy = false, onSubmit }: { busy?: boolean; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <form className="password-change-form" onSubmit={onSubmit}><label>Current password<input name="current_password" type="password" autoComplete="current-password" minLength={12} maxLength={128} required /></label><label>New password<input name="new_password" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label><button className="button button-primary" disabled={busy}>{busy ? "Updating…" : "Update password"}</button></form>;
}

function TeamPage({ members, temporaryPassword, busy, onSubmit }: { members: TeamMember[]; temporaryPassword: { email: string; password: string } | null; busy: boolean; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void }) {
  return <><div className="page-heading heading-row"><div><div className="eyebrow">ORGANISATION <span className="eyebrow-line" /></div><h1>Team access</h1><p>Add teammates to this workspace. New accounts receive a temporary password to share once.</p></div></div><div className="team-grid"><section className="panel team-panel"><div className="panel-heading"><div><span className="section-kicker">MEMBERS</span><h2>{members.length} {members.length === 1 ? "person" : "people"}</h2></div></div><div className="team-member-list">{members.map((member) => <div className="team-member" key={member.user_id}><span className="account-avatar">{(member.name ?? member.email ?? "?").slice(0, 1).toUpperCase()}</span><div><strong>{member.name ?? "Teammate"}</strong><small>{member.email ?? member.user_id}</small></div><span className="stage-pill">{pretty(member.role)}</span></div>)}</div></section><section className="panel team-panel"><div className="panel-heading"><div><span className="section-kicker">INVITE A TEAMMATE</span><h2>Create or add account</h2></div></div><form className="team-invite-form" onSubmit={onSubmit}><label>Email address<input name="email" type="email" required maxLength={320} /></label><label>Workspace role<select name="role" defaultValue="qs"><option value="qs">Quantity surveyor</option><option value="admin">Admin</option><option value="external_reviewer">External reviewer</option><option value="viewer">Viewer</option></select></label><button className="button button-primary" disabled={busy}>{busy ? "Adding…" : "Add teammate"} <Icon name="arrow" /></button></form>{temporaryPassword && <div className="temporary-credential" role="status"><strong>Temporary password — copy it now</strong><span>{temporaryPassword.email}</span><code>{temporaryPassword.password}</code><small>This value is shown only in this response. The teammate must change it at first sign-in.</small></div>}</section></div></>;
}

function Overview({ projects, cases, openCount, readyCount, onNewProject, onNewCase, onOpenCase, onOpenProject, onGoCases }: { projects: Project[]; cases: CaseSummary[]; openCount: number; readyCount: number; onNewProject: () => void; onNewCase: () => void; onOpenCase: (item: CaseSummary) => void; onOpenProject: (item: Project) => void; onGoCases: () => void }) {
  const recent = cases.slice(0, 5);
  return <><div className="page-heading heading-row"><div><div className="eyebrow">YOUR WORKSPACE <span className="eyebrow-line" /></div><h1>Good work starts<br className="mobile-break" /> with a clear record.</h1><p>Keep instructions, correspondence and costs connected to the change they describe.</p></div><button className="button button-primary" onClick={onNewCase}><Icon name="plus" /> New case</button></div><div className="stats-grid"><StatCard label="Active cases" value={openCount} caption="Across your workspace" tone="blue" /><StatCard label="Ready for review" value={readyCount} caption="Draft findings to check" tone="green" /><StatCard label="Projects" value={projects.length} caption="Project workspaces" tone="sand" /><StatCard label="Evidence files" value={cases.reduce((sum, item) => sum + item.evidence.length, 0)} caption="Documents in your cases" tone="lilac" /></div><div className="overview-grid"><section className="panel recent-panel"><div className="panel-heading"><div><span className="section-kicker">RECENT ACTIVITY</span><h2>Casework</h2></div><button className="text-button" onClick={onGoCases}>All cases <Icon name="arrow" /></button></div>{recent.length ? <div className="case-list">{recent.map((item) => <CaseRow key={item.case_id} item={item} projectName={projects.find((project) => project.project_id === item.project_id)?.name ?? "Project"} onClick={() => onOpenCase(item)} />)}</div> : <EmptyState title="No cases yet" text="Start with a project and capture the change while the details are fresh." action="Create a case" onClick={onNewCase} />}</section><section className="panel project-panel"><div className="panel-heading"><div><span className="section-kicker">YOUR SITES</span><h2>Projects</h2></div><button className="round-add" aria-label="Create project" onClick={onNewProject}><Icon name="plus" /></button></div>{projects.length ? <div className="project-list">{projects.slice(0, 4).map((project) => <button className="project-mini" key={project.project_id} onClick={() => onOpenProject(project)}><span className="project-symbol"><Icon name="projects" /></span><span className="project-mini-copy"><strong>{project.name}</strong><small>{project.reference || project.client || "Project workspace"}</small></span><Icon name="arrow" /></button>)}</div> : <EmptyState title="Your next project" text="Set up a project to organise its variation cases." action="Add project" onClick={onNewProject} />}</section></div><div className="disclaimer-strip"><span className="disclaimer-icon">i</span><span><strong>Evidence preparation, not a legal conclusion.</strong> The agent produces drafts for professional review. It does not determine entitlement or issue notices.</span></div></>;
}

function StatCard({ label, value, caption, tone }: { label: string; value: number; caption: string; tone: string }) {
  return <div className={`stat-card stat-${tone}`}><div className="stat-top"><span>{label}</span><i /></div><strong>{value.toString().padStart(2, "0")}</strong><small>{caption}</small></div>;
}

function CasesPage({ cases, projectNames, onNew, onOpen }: { cases: CaseSummary[]; projectNames: Map<string, string>; onNew: () => void; onOpen: (item: CaseSummary) => void }) {
  return <><div className="page-heading heading-row"><div><div className="eyebrow">CASE REGISTER <span className="eyebrow-line" /></div><h1>Variation cases</h1><p>Each case brings the change narrative and its source documents together.</p></div><button className="button button-primary" onClick={onNew}><Icon name="plus" /> New case</button></div>{cases.length ? <div className="panel case-register">{cases.map((item) => <CaseRow key={item.case_id} item={item} projectName={projectNames.get(item.project_id) ?? "Project"} onClick={() => onOpen(item)} detailed />)}</div> : <section className="panel empty-panel"><EmptyState title="No cases in this workspace" text="Create a project case to begin collecting evidence." action="Create a case" onClick={onNew} /></section>}</>;
}

function CaseRow({ item, projectName, onClick, detailed = false }: { item: CaseSummary; projectName: string; onClick: () => void; detailed?: boolean }) {
  return <button className={`case-row ${detailed ? "case-row-detailed" : ""}`} onClick={onClick}><span className="case-row-icon"><Icon name="cases" /></span><span className="case-row-main"><strong>{item.title}</strong><small>{projectName}{item.evidence.length ? ` · ${item.evidence.length} ${item.evidence.length === 1 ? "document" : "documents"}` : " · Evidence needed"}</small></span><span className={`stage-pill stage-${item.stage}`}>{pretty(item.stage)}</span>{detailed && <span className="case-row-date">{formatDate(item.created_at)}</span>}<Icon name="arrow" /></button>;
}

function ProjectsPage({ projects, cases, onNew, onOpen }: { projects: Project[]; cases: CaseSummary[]; onNew: () => void; onOpen: (item: Project) => void }) {
  return <><div className="page-heading heading-row"><div><div className="eyebrow">PROJECT REGISTER <span className="eyebrow-line" /></div><h1>Projects</h1><p>Keep site details, contract context and related cases in one place.</p></div><button className="button button-primary" onClick={onNew}><Icon name="plus" /> New project</button></div>{projects.length ? <div className="project-grid">{projects.map((project) => <button className="project-card" key={project.project_id} onClick={() => onOpen(project)}><div className="project-card-top"><span className="project-symbol"><Icon name="projects" /></span><span className="subtle-arrow"><Icon name="arrow" /></span></div><h2>{project.name}</h2><p>{project.client || project.site_address || "Project workspace"}</p><div className="project-card-bottom"><span>{project.reference || pretty(project.contract.form)}</span><span>{cases.filter((item) => item.project_id === project.project_id).length} cases</span></div></button>)}</div> : <section className="panel empty-panel"><EmptyState title="No projects yet" text="Create your first project workspace." action="Create a project" onClick={onNew} /></section>}</>;
}

function ProjectDetail({ project, caseCount, cases, onBack, onNewCase, onOpenCase }: { project: Project; caseCount: number; cases: CaseSummary[]; onBack: () => void; onNewCase: () => void; onOpenCase: (item: CaseSummary) => void }) {
  return <><button className="back-link" onClick={onBack}><Icon name="back" /> All projects</button><div className="page-heading heading-row project-detail-heading"><div><div className="eyebrow">PROJECT <span className="eyebrow-line" /></div><h1>{project.name}</h1><p>{project.client || "Client not specified"}{project.site_address ? ` · ${project.site_address}` : ""}</p></div><button className="button button-primary" onClick={onNewCase}><Icon name="plus" /> New case</button></div><div className="detail-meta"><div><span>REFERENCE</span><strong>{project.reference || "Not set"}</strong></div><div><span>CONTRACT</span><strong>{pretty(project.contract.form)}</strong></div><div><span>JURISDICTION</span><strong>{project.contract.jurisdiction || "Not specified"}</strong></div><div><span>CASES</span><strong>{caseCount}</strong></div></div><section className="panel case-register"><div className="panel-heading"><div><span className="section-kicker">PROJECT RECORD</span><h2>Cases</h2></div></div>{cases.length ? cases.map((item) => <CaseRow key={item.case_id} item={item} projectName={project.name} onClick={() => onOpenCase(item)} detailed />) : <EmptyState title="No cases on this project" text="Create a case to start building an evidence record." action="Create a case" onClick={onNewCase} />}</section></>;
}

function CasePage({ item, timeline, job, projectName, busy, uploadDate, setUploadDate, dateUnknown, setDateUnknown, onBack, onUpload, onAnalyse, onReport, onRefresh }: { item: CaseSummary; timeline: Timeline | null; job: Job | null; projectName: string; busy: boolean; uploadDate: string; setUploadDate: (value: string) => void; dateUnknown: boolean; setDateUnknown: (value: boolean) => void; onBack: () => void; onUpload: (files: FileList | null) => void; onAnalyse: () => void; onReport: (format: "pdf" | "docx") => void; onRefresh: () => void }) {
  const active =
    (item.stage === "analysing" && job?.status !== "failed") ||
    job?.status === "queued" ||
    job?.status === "working";
  const canAnalyse = item.evidence.some((file) => file.parser_status === "parsed" && !file.duplicate_of) && !active;
  const hasReport = Boolean(item.report_refs?.length);
  const canGenerateReport = item.stage === "needs_review";

  const totalQuantified = timeline?.candidates.reduce((sum, c) => sum + (c.amount || 0), 0) ?? 0;
  const activeCurrency = timeline?.candidates.find((c) => c.currency)?.currency || "GBP";
  const hasInstructions = Boolean(timeline?.candidates.some((c) => c.related_instruction_refs.length > 0));
  const hasDelays = Boolean(
    timeline?.events.some(
      (e) =>
        e.event_type.includes("programme") ||
        e.event_type.includes("site") ||
        e.description.toLowerCase().includes("delay") ||
        e.description.toLowerCase().includes("disruption"),
    ),
  );

  return <><button className="back-link" onClick={onBack}><Icon name="back" /> All cases</button><div className="case-detail-header"><div><div className="eyebrow">{projectName.toUpperCase()} <span className="eyebrow-line" /></div><h1>{item.title}</h1><p className="case-subtitle">{item.alleged_change}</p><div className="case-header-meta"><span className={`stage-pill stage-${item.stage}`}>{pretty(item.stage)}</span><span>Updated {formatDate(item.created_at)}</span><span>Revision {timeline?.revision ?? item.revision}</span></div></div><div className="header-actions"><button className="button button-outline" onClick={onRefresh}>Refresh</button>{(hasReport || canGenerateReport) && <><button className="button button-outline" disabled={busy} onClick={() => onReport("pdf")}><Icon name="download" /> Download PDF</button><button className="button button-outline" disabled={busy} onClick={() => onReport("docx")}><Icon name="download" /> Download Word</button></>}<button className="button button-primary" disabled={!canAnalyse || busy} onClick={onAnalyse}>{active ? <><span className="spinner" />Analysing…</> : <><Icon name="spark" /> Prepare draft</>}</button></div></div>
    {active && <div className="job-progress"><div className="progress-spark"><Icon name="spark" /></div><div className="progress-copy"><strong>Agent is preparing the evidence draft</strong><span>{job?.stage ? pretty(job.stage) : "Queued"} · Attempt {job?.attempt ?? 0}</span></div><span className="progress-live"><i /> IN PROGRESS</span></div>}
    {!active && job?.status === "failed" && <div className="job-failed"><strong>Analysis needs attention</strong><span>{job.failure_code}: {job.failure_message}</span><button className="text-button" onClick={onAnalyse}>Try again <Icon name="arrow" /></button></div>}
    <div className="case-workspace"><div className="case-main-column">
    {timeline && (
      <section className="panel output-panel executive-summary-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">EXECUTIVE SUMMARY &amp; HIGHLIGHTS</span>
            <h2>What the files show</h2>
          </div>
          <span className="review-label">EVIDENCE-LED DRAFT</span>
        </div>
        <div className="executive-card">
          <p className="executive-text">
            {timeline.plain_english_summary?.text || item.alleged_change}
          </p>
          <div className="summary-tags">
            {timeline.candidates.length > 0 && (
              <span className="summary-tag tag-scope">
                {timeline.candidates.length} Scope Delta{timeline.candidates.length > 1 ? "s" : ""}
              </span>
            )}
            {hasInstructions && (
              <span className="summary-tag tag-instruction">Instructions Cited</span>
            )}
            {totalQuantified > 0 && (
              <span className="summary-tag tag-price">
                Quantified Impact: {activeCurrency} {totalQuantified.toLocaleString("en-GB")}
              </span>
            )}
            {hasDelays && (
              <span className="summary-tag tag-delay">Delays / Disruption Recorded</span>
            )}
          </div>
          <p className="executive-notice">
            Factual record assembled for UK subcontractor commercial/QS review. Organises evidence into a reviewable chronology; does not determine contractual entitlement or legal liability.
          </p>
        </div>
      </section>
    )}

    {timeline?.variation_positions && timeline.variation_positions.length > 0 && (
      <section className="panel output-panel variation-position-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">CORE POSITION ANALYSIS</span>
            <h2>Variation Position &amp; Scope Comparison</h2>
          </div>
          <span className="review-label">EVIDENCE POSITION</span>
        </div>
        {timeline.variation_positions.map((vp) => (
          <article key={vp.variation_id} className="variation-position-card">
            <div className="vp-header">
              <div className="vp-title-row">
                <span className="candidate-dot" />
                <h3>{vp.variation_title}</h3>
                <span className="doc-badge">{vp.evidence_status}</span>
              </div>
              <div className="vp-meta-row">
                <span><strong>Instruction:</strong> {vp.instruction_refs?.join(", ") || "None"}</span>
                <span><strong>Parties:</strong> {vp.parties?.join(" · ") || "Parties not stated"}</span>
              </div>
            </div>

            <div className="vp-scope-grid">
              <div className="vp-scope-box orig-scope">
                <h4>ORIGINAL POSITION (Q-101)</h4>
                <p className="vp-desc">{vp.original_position.work_description || "Original contract scope"}</p>
                <div className="vp-stats">
                  <span><strong>Qty:</strong> {vp.original_position.quantity ?? 100} {vp.original_position.quantity_unit ?? "units"}</span>
                  <span><strong>Amount:</strong> {vp.original_position.currency ?? "GBP"} {(vp.original_position.quoted_amount ?? 10000).toLocaleString("en-GB", { minimumFractionDigits: 2 })}</span>
                </div>
              </div>
              <div className="vp-scope-box chg-scope">
                <h4>CHANGED POSITION (INST-014 / Q-118)</h4>
                <p className="vp-desc">{vp.changed_position.work_description || "Changed scope"}</p>
                <div className="vp-stats">
                  <span><strong>Added Qty:</strong> +{vp.changed_position.added_quantity ?? 30} ({vp.changed_position.revised_total_quantity ?? 130} total)</span>
                  <span><strong>Revised Quote:</strong> {vp.changed_position.currency ?? "GBP"} {(vp.changed_position.revised_quoted_amount ?? 13600).toLocaleString("en-GB", { minimumFractionDigits: 2 })}</span>
                </div>
              </div>
            </div>

            {vp.quantity_progress && (
              <div className="vp-progress-box">
                <h4>QUANTITY &amp; WORK PROGRESS POSITION</h4>
                <div className="vp-progress-table-wrap">
                  <table className="vp-table">
                    <thead>
                      <tr>
                        <th>Item</th>
                        <th>Original Qty</th>
                        <th>Additional Requested</th>
                        <th>Total Proposed</th>
                        <th>Work Recorded Installed</th>
                        <th>Remaining Not Evidenced</th>
                        <th>Completion Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td><strong>{vp.quantity_progress.item_description}</strong></td>
                        <td>{vp.quantity_progress.original_quantity ?? 100}</td>
                        <td>+{vp.quantity_progress.additional_requested ?? 30}</td>
                        <td>{vp.quantity_progress.total_proposed ?? 130}</td>
                        <td><strong className="installed-qty">{vp.quantity_progress.recorded_installed ?? 22}</strong></td>
                        <td><strong className="unverified-qty">{vp.quantity_progress.remaining_not_evidenced ?? 8}</strong></td>
                        <td><small>{vp.quantity_progress.completion_evidence_status}</small></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="vp-note">
                  <em>Note: Work recorded as installed (22 units) is distinct from verified completion. Final sign-off unevidenced.</em>
                </p>
              </div>
            )}

            {vp.commercial_position && (
              <div className="vp-commercial-box">
                <h4>COMMERCIAL POSITION &amp; DISCLOSURE</h4>
                <div className="vp-comm-grid">
                  <div className="vp-comm-stat">
                    <span>Original Quote</span>
                    <strong>{vp.commercial_position.currency ?? "GBP"} {(vp.commercial_position.original_quoted_amount ?? 10000).toLocaleString("en-GB", { minimumFractionDigits: 2 })}</strong>
                  </div>
                  <div className="vp-comm-stat">
                    <span>Revised Proposal</span>
                    <strong>{vp.commercial_position.currency ?? "GBP"} {(vp.commercial_position.revised_quoted_amount ?? 13600).toLocaleString("en-GB", { minimumFractionDigits: 2 })}</strong>
                  </div>
                  <div className="vp-comm-stat highlight">
                    <span>Calculated Difference</span>
                    <strong>{vp.commercial_position.currency ?? "GBP"} {(vp.commercial_position.calculated_difference ?? 3600).toLocaleString("en-GB", { minimumFractionDigits: 2 })}</strong>
                  </div>
                </div>
                <p className="commercial-disclaimer-alert">
                  <strong>Calculated difference between quoted totals — not an established payable variation amount.</strong>
                </p>
              </div>
            )}

            {vp.unresolved_items && vp.unresolved_items.length > 0 && (
              <div className="vp-unresolved-box">
                <h4>UNRESOLVED ISSUES &amp; REQUIRED ACTIONS ({vp.unresolved_items.length})</h4>
                <div className="unresolved-list">
                  {vp.unresolved_items.map((unres, idx) => (
                    <div key={idx} className="unresolved-item-card">
                      <div className="unres-header">
                        <span className="unres-badge">{unres.category.replace(/_/g, " ").toUpperCase()}</span>
                        <strong>{unres.description}</strong>
                      </div>
                      <p className="unres-action">
                        <strong>Reviewer Action:</strong> {unres.reviewer_action}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </article>
        ))}
      </section>
    )}

    <section className="panel evidence-panel"><div className="panel-heading"><div><span className="section-kicker">SOURCE MATERIAL</span><h2>Evidence <span className="count-badge">{item.evidence.length}</span></h2></div><label htmlFor="evidence-files" className={`button button-outline upload-trigger ${busy || active ? "disabled-button" : ""}`}><Icon name="upload" /> Add files</label><input id="evidence-files" className="visually-hidden" type="file" accept={acceptedFiles} multiple disabled={busy || active} onChange={(event) => void onUpload(event.target.files)} /></div><div className="upload-details"><label>Document date <input type="date" value={uploadDate} disabled={dateUnknown} onChange={(event) => setUploadDate(event.target.value)} /></label><label className="check-label"><input type="checkbox" checked={dateUnknown} onChange={(event) => { setDateUnknown(event.target.checked); if (event.target.checked) setUploadDate(""); }} /> Date unknown</label><span>PDF, Word, Excel, email or image · 25 MB max</span></div>{item.evidence.length ? <div className="evidence-list">{item.evidence.map((file) => <EvidenceRow key={file.document_id} file={file} />)}</div> : <div className="evidence-empty"><span className="upload-illustration"><Icon name="upload" /></span><strong>No source documents yet</strong><p>Add the instruction, correspondence and cost records that describe this change.</p><label htmlFor="evidence-files" className="text-button">Choose documents <Icon name="arrow" /></label></div>}<div className="upload-footnote">Files are validated and scanned before the agent can use them.</div></section>
    {timeline?.document_summaries && timeline.document_summaries.length > 0 && (
      <section className="panel output-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">DOCUMENT REGISTER &amp; EVOLUTION</span>
            <h2>Document summaries &amp; changes <span className="count-badge">{timeline.document_summaries.length}</span></h2>
          </div>
        </div>
        {timeline.document_progression && timeline.document_progression.length > 1 && (
          <div className="progression-strip">
            {timeline.document_progression.map((step, idx) => (
              <span key={step.document_id} className="progression-step">
                <span className="step-num">{step.step}</span>
                <strong>{step.filename}</strong> ({step.category})
                {idx < (timeline.document_progression?.length ?? 0) - 1 && (
                  <span className="progression-arrow"><Icon name="arrow" /></span>
                )}
              </span>
            ))}
          </div>
        )}
        <div className="doc-summary-list">
          {timeline.document_summaries.map((doc) => (
            <article key={doc.document_id} className="doc-summary-card">
              <div className="doc-summary-header">
                <strong>{doc.filename}</strong>
                <div className="doc-summary-meta">
                  <span className="doc-badge">{doc.category}</span>
                  <span className="doc-date">{doc.date}</span>
                </div>
              </div>
              <p>{doc.summary}</p>
            </article>
          ))}
        </div>
      </section>
    )}
    {timeline && <><section className="panel output-panel"><div className="panel-heading"><div><span className="section-kicker">CHRONOLOGICAL RECORD OF CHANGES</span><h2>Draft chronology <span className="count-badge">{timeline.complete_chronology?.length ?? timeline.events.length}</span></h2></div><span className="review-label">NEEDS PROFESSIONAL REVIEW</span></div>{timeline.events.length ? <div className="timeline-list">{timeline.events.map((event) => <TimelineRow key={event.event_id} event={event} files={timeline.evidence} />)}</div> : <EmptyOutput title="No chronology yet" text="Once analysable evidence is added, ask the agent to prepare a draft." />}</section>
      <section className="panel output-panel"><div className="panel-heading"><div><span className="section-kicker">POTENTIAL VARIATIONS</span><h2>Candidate changes <span className="count-badge">{timeline.candidates.length}</span></h2></div></div>{timeline.candidates.length ? <div className="finding-list">{timeline.candidates.map((candidate) => <article className="candidate-card" key={candidate.candidate_id}><div className="candidate-title"><span className="candidate-dot" /><h3>{candidate.title}</h3><span className="review-label">{pretty(candidate.review_status)}</span></div>{candidate.proposed_scope_delta && <p>{candidate.proposed_scope_delta}</p>}<div className="finding-meta">{candidate.related_instruction_refs.length > 0 && <span>Refs: {candidate.related_instruction_refs.join(", ")}</span>}{candidate.amount !== null && candidate.amount !== undefined && <span>{candidate.currency} {candidate.amount.toLocaleString("en-GB")}</span>}<span>{Math.round(candidate.confidence * 100)}% model confidence · uncalibrated</span></div><EvidenceRefs references={candidate.evidence} files={timeline.evidence} /></article>)}</div> : <EmptyOutput title="No candidate changes" text="Candidate changes will appear here with their supporting sources." />}</section>
      <section className="panel output-panel"><div className="panel-heading"><div><span className="section-kicker">REVIEW CHECKLIST</span><h2>Questions &amp; gaps <span className="count-badge">{(timeline.review_checklist?.length ?? 0) || (timeline.reviewer_questions.length + timeline.missing_evidence.length + timeline.contradictions.length)}</span></h2></div></div><ReviewItems timeline={timeline} /></section>
    </>}
    </div><aside className="case-side-column"><section className="panel side-panel"><span className="section-kicker">CASE SNAPSHOT</span><h2>At a glance</h2><div className="snapshot-row"><span>Case stage</span><span className={`stage-pill stage-${item.stage}`}>{pretty(item.stage)}</span></div><div className="snapshot-row"><span>Evidence files</span><strong>{item.evidence.length}</strong></div><div className="snapshot-row"><span>Usable for analysis</span><strong>{item.evidence.filter((file) => file.parser_status === "parsed" && !file.duplicate_of).length}</strong></div><div className="snapshot-row"><span>Draft events</span><strong>{timeline?.complete_chronology?.length ?? timeline?.events.length ?? 0}</strong></div><div className="snapshot-row"><span>Revision</span><strong>{timeline?.revision ?? item.revision}</strong></div>{job && <div className="job-reference"><span>Latest job</span><code>{job.job_id}</code><span>{pretty(job.status)}{job.stage ? ` · ${pretty(job.stage)}` : ""}</span></div>}</section><div className="human-review-card"><div className="review-seal"><Icon name="check" /></div><span className="section-kicker">A HUMAN DECIDES</span><h3>Evidence is a starting point.</h3><p>Findings are drafts with uncalibrated confidence. A qualified professional must check every citation, deadline and conclusion.</p><span className="review-card-rule" /><small>NO ENTITLEMENT DETERMINED<br />NO NOTICES OR CLAIMS ISSUED</small></div><div className="supported-files"><strong>Accepted sources</strong><span>PDF · DOCX · XLSX<br />Email · JPG · PNG</span></div></aside></div>
    {timeline?.notice && <div className="disclaimer-strip timeline-notice"><span className="disclaimer-icon">i</span>{timeline.notice}</div>}
  </>;
}

function EvidenceRow({ file }: { file: import("./api").Evidence }) {
  const label = file.category ? pretty(file.category) : "Unclassified";
  return <div className="evidence-row"><span className={`file-kind ${file.mime_type === "application/pdf" ? "file-pdf" : file.mime_type.includes("image") ? "file-image" : "file-doc"}`}>{file.mime_type === "application/pdf" ? "PDF" : file.mime_type.includes("spreadsheet") ? "XLS" : file.mime_type.includes("image") ? "IMG" : file.mime_type === "message/rfc822" ? "EML" : "DOC"}</span><div className="evidence-main"><strong title={file.filename}>{file.filename}</strong><small>{label}{file.recorded_event_date ? ` · Document date ${formatDate(file.recorded_event_date)}` : ""}{file.duplicate_of ? " · Duplicate" : ""}</small></div><span className={`parser-status parser-${file.parser_status}`}>{pretty(file.parser_status)}</span><span className="evidence-pages">{file.page_count ? `${file.page_count} p` : ""}</span></div>;
}

function eventBadgeClass(eventType: string) {
  if (eventType.includes("instruction")) return "event-type-instruction";
  if (eventType.includes("scope")) return "event-type-scope";
  if (eventType.includes("date") || eventType.includes("programme") || eventType.includes("site")) return "event-type-delay";
  if (eventType.includes("quotation") || eventType.includes("cost") || eventType.includes("price")) return "event-type-price";
  return "event-type-general";
}

function TimelineRow({ event, files }: { event: import("./api").TimelineEvent; files: import("./api").Evidence[] }) {
  return <article className="timeline-row"><div className="timeline-date">{event.event_date ? formatDate(event.event_date) : "DATE NEEDED"}</div><div className="timeline-marker"><i /></div><div className="timeline-event"><div className="timeline-event-top"><span className={`event-type-badge ${eventBadgeClass(event.event_type)}`}>{pretty(event.event_type)}</span><span className={`review-label review-${event.review_status}`}>{pretty(event.review_status)}</span></div><p>{event.description}</p><div className="event-footer"><span>{event.basis === "explicitly_evidenced" ? "Source-backed" : pretty(event.basis)}{event.amount !== null && event.amount !== undefined ? ` · ${event.currency || ""} ${event.amount.toLocaleString("en-GB")}` : ""}{event.confidence ? ` · ${Math.round(event.confidence * 100)}% confidence` : ""}</span><EvidenceRefs references={event.evidence} files={files} /></div>{event.uncertainties.length > 0 && <div className="uncertainty-note">{event.uncertainties.join(" · ")}</div>}</div></article>;
}

function EvidenceRefs({ references, files }: { references: import("./api").EvidenceReference[]; files: import("./api").Evidence[] }) {
  if (!references.length) return null;
  return <span className="source-chips">{references.map((reference, index) => <span className="source-chip" key={`${reference.document_id ?? "author"}-${index}`} title={reference.quoted_span ?? reference.visual_region ?? undefined}>{files.find((file) => file.document_id === reference.document_id)?.filename ?? (reference.user_authored ? "User provided" : "Source")}{reference.page_ref ? ` · ${reference.page_ref}` : ""}{reference.visual_region ? ` · ${reference.visual_region}` : ""}</span>)}</span>;
}

function ReviewItems({ timeline }: { timeline: Timeline }) {
  const hasItems = timeline.reviewer_questions.length || timeline.missing_evidence.length || timeline.contradictions.length;
  if (!hasItems) return <EmptyOutput title="Nothing flagged for review" text="Any questions, missing evidence or conflicting dates will be listed here." />;
  return <div className="review-items">{timeline.contradictions.map((item) => <article key={item.contradiction_id} className="review-item"><span className="review-icon conflict">!</span><div><strong>{pretty(item.kind)} conflict</strong><p>{item.description}</p><EvidenceRefs references={item.evidence} files={timeline.evidence} /></div></article>)}{timeline.missing_evidence.map((item) => <article key={item.missing_id} className="review-item"><span className="review-icon gap">?</span><div><strong>Evidence gap{item.expected_document_kind ? ` · ${pretty(item.expected_document_kind)}` : ""}</strong><p>{item.description}</p></div></article>)}{timeline.reviewer_questions.map((item) => <article key={item.question_id} className="review-item"><span className="review-icon question">Q</span><div><strong>Reviewer question</strong><p>{item.question}</p></div></article>)}</div>;
}

function ProjectForm({ busy, onSubmit, onCancel }: { busy: boolean; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void; onCancel: () => void }) {
  return <form className="modal-form" onSubmit={onSubmit}><p className="modal-intro">Set the project context once, then link each variation case to this site.</p><label>Project name<input name="name" required maxLength={500} placeholder="e.g. Riverside Apartments" /></label><div className="form-two"><label>Project reference<input name="reference" maxLength={500} placeholder="e.g. RA-024" /></label><label>Client<input name="client" maxLength={500} placeholder="Client organisation" /></label></div><label>Site address<input name="site_address" maxLength={500} placeholder="Site location" /></label><div className="form-two"><label>Contract form<select name="form" defaultValue="unknown"><option value="unknown">Not specified</option><option value="jct">JCT</option><option value="nec">NEC</option><option value="bespoke">Bespoke</option></select></label><label>Jurisdiction<input name="jurisdiction" maxLength={500} placeholder="e.g. England and Wales" /></label></div><label>Contract details <span className="optional">Optional</span><textarea name="contract_description" maxLength={500} rows={2} placeholder="Contract edition or relevant project context" /></label><div className="modal-actions"><button type="button" className="button button-quiet" onClick={onCancel}>Cancel</button><button className="button button-primary" disabled={busy}>{busy ? "Creating…" : "Create project"} <Icon name="arrow" /></button></div></form>;
}

function CaseForm({ projects, busy, defaultProject, onSubmit, onCancel }: { projects: Project[]; busy: boolean; defaultProject?: string; onSubmit: (event: React.FormEvent<HTMLFormElement>) => void; onCancel: () => void }) {
  return <form className="modal-form" onSubmit={onSubmit}><p className="modal-intro">Link each variation case to a project. The agent will automatically extract and reconcile all scope changes, instructions, and cost impacts across your uploaded evidence.</p><label>Project<select name="project_id" required defaultValue={defaultProject ?? ""}><option value="" disabled>Select a project</option>{projects.map((project) => <option key={project.project_id} value={project.project_id}>{project.name}</option>)}</select></label><label>Case title<input name="title" required maxLength={500} placeholder="e.g. Additional drainage works" /></label><div className="form-two"><label>Contract form<select name="form" defaultValue="unknown"><option value="unknown">Not specified</option><option value="jct">JCT</option><option value="nec">NEC</option><option value="bespoke">Bespoke</option></select></label><label>Jurisdiction<input name="jurisdiction" maxLength={500} placeholder="e.g. England and Wales" /></label></div><label>Contract details <span className="optional">Optional</span><input name="contract_description" maxLength={500} placeholder="Edition or relevant context" /></label><div className="modal-actions"><button type="button" className="button button-quiet" onClick={onCancel}>Cancel</button><button className="button button-primary" disabled={busy || !projects.length}>{busy ? "Creating…" : "Create case"} <Icon name="arrow" /></button></div>{!projects.length && <span className="form-hint">Create a project before adding a case.</span>}</form>;
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const close = () => onClose();
    window.addEventListener("close-current-modal", close);
    const keydown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("close-current-modal", close);
      window.removeEventListener("keydown", keydown);
    };
  }, [onClose]);
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-heading"><div className="modal-header"><h2 id="modal-heading">{title}</h2><button className="icon-button" aria-label="Close dialog" onClick={onClose}><Icon name="close" /></button></div>{children}</section></div>;
}

function EmptyState({ title, text, action, onClick }: { title: string; text: string; action: string; onClick: () => void }) {
  return <div className="empty-state"><span className="empty-symbol"><Icon name="mark" /></span><strong>{title}</strong><p>{text}</p><button className="text-button" onClick={onClick}>{action} <Icon name="arrow" /></button></div>;
}

function EmptyOutput({ title, text }: { title: string; text: string }) {
  return <div className="empty-output"><span className="empty-output-dot" /><div><strong>{title}</strong><p>{text}</p></div></div>;
}

function storeLocalSession(session: LocalSession) {
  sessionStorage.setItem("cvea-access-token", session.access_token);
  sessionStorage.setItem("cvea-profile", JSON.stringify(session.user));
}
function sectionLabel(section: Section) {
  return section === "overview" ? "Overview" : section === "cases" ? "Cases" : section === "team" ? "Team" : "Projects";
}
function optional(value: FormDataEntryValue | null) { return typeof value === "string" && value.trim() ? value.trim() : null; }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : "Something went wrong. Please try again."; }

export default App;
