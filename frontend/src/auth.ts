import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const authority = import.meta.env.VITE_OIDC_AUTHORITY ?? "";
const clientId = import.meta.env.VITE_OIDC_CLIENT_ID ?? "";
const apiAudience = import.meta.env.VITE_OIDC_API_AUDIENCE ?? "";
const audienceParameter = import.meta.env.VITE_OIDC_AUDIENCE_PARAMETER ?? "resource";

export const authConfigured = Boolean(authority && clientId);

export const authManager = authConfigured
  ? new UserManager({
      authority,
      client_id: clientId,
      redirect_uri:
        import.meta.env.VITE_OIDC_REDIRECT_URI || `${window.location.origin}/auth/callback`,
      response_type: "code",
      scope: import.meta.env.VITE_OIDC_SCOPE || "openid profile email",
      automaticSilentRenew: true,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      ...(apiAudience ? { extraQueryParams: { [audienceParameter]: apiAudience } } : {}),
    })
  : null;
