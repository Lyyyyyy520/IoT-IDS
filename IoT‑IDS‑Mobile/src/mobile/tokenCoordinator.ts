import { mobileApi, MobileApiError, type Tokens, type RefreshTokens } from './api';
import type { ServerConfig } from './config';
import { secureStorage } from './storage';

type Storage = Pick<typeof secureStorage, 'getRefresh' | 'setRefresh' | 'clearRefresh'>;
type Api = typeof mobileApi;
export const requiresRepair = (error: unknown): boolean => error instanceof MobileApiError &&
  (error.code === 'secure_storage_unavailable' ||
    (error.kind === 'http' && (error.status === 401 ||
    ['mobile_session_revoked', 'refresh_token_replay', 'mobile_user_disabled', 'mobile_user_ineligible', 're_pair_required'].includes(error.code))));

export class TokenCoordinator {
  private access: string | null = null;
  private expiresAt = 0;
  private refreshFlight: Promise<RefreshTokens> | null = null;
  private revision = 0;
  constructor(private config: () => ServerConfig, private storage: Storage = secureStorage,
    private api: Api = mobileApi) {}

  getAccessToken(): string | null { return this.access; }
  get hasAccess(): boolean { return this.access !== null; }

  async acceptClaim(tokens: Tokens): Promise<void> {
    if (tokens.user.role !== 'user') throw new MobileApiError('invalid_response', 201, 'invalid_response');
    try { await this.storage.setRefresh(tokens.refresh_token); }
    catch {
      try { await this.api.logout(this.config(), tokens.access_token); } catch { /* best effort */ }
      this.access = null;
      try { await this.storage.clearRefresh(); } catch { /* device storage unavailable */ }
      throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable');
    }
    this.access = tokens.access_token;
    this.expiresAt = Date.parse(tokens.access_expires_at);
  }

  async refresh(): Promise<RefreshTokens> {
    if (this.refreshFlight) return this.refreshFlight;
    const revision = this.revision;
    const flight = (async () => {
      let refresh: string | null;
      try { refresh = await this.storage.getRefresh(); }
      catch { throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable'); }
      if (!refresh) throw new MobileApiError('http', 401, 're_pair_required');
      let rotated: RefreshTokens;
      try { rotated = await this.api.refresh(this.config(), refresh); }
      catch (error) {
        if (requiresRepair(error)) await this.clearLocal();
        throw error;
      }
      if (revision !== this.revision) throw new MobileApiError('http', 401, 're_pair_required');
      try { await this.storage.setRefresh(rotated.refresh_token); }
      catch {
        try { await this.clearLocal(); } catch { /* memory is already invalidated */ }
        throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable');
      }
      if (revision !== this.revision) throw new MobileApiError('http', 401, 're_pair_required');
      this.access = rotated.access_token;
      this.expiresAt = Date.parse(rotated.access_expires_at);
      return rotated;
    })();
    this.refreshFlight = flight;
    try { return await flight; } finally { if (this.refreshFlight === flight) this.refreshFlight = null; }
  }

  async ensureFresh(): Promise<void> {
    if (!this.access || Date.now() >= this.expiresAt - 60_000) await this.refresh();
  }

  async authorized<T>(call: (token: string) => Promise<T>): Promise<T> {
    await this.ensureFresh();
    const first = this.access!;
    try { return await call(first); }
    catch (error) {
      if (!(error instanceof MobileApiError) || error.kind !== 'http' || error.status !== 401) throw error;
      if (['mobile_session_revoked', 'refresh_token_replay', 'mobile_user_disabled'].includes(error.code)) {
        await this.clearLocal(); throw error;
      }
      if (this.access === first) await this.refresh();
      try { return await call(this.access!); } // At most one retry.
      catch (retryError) {
        if (requiresRepair(retryError)) await this.clearLocal();
        throw retryError;
      }
    }
  }

  async logout(): Promise<boolean> {
    const token = this.access;
    await this.clearLocal();
    let serverReached = true;
    if (token) {
      try { await this.api.logout(this.config(), token); } catch { serverReached = false; }
    } else { serverReached = false; }
    return serverReached;
  }

  async clearLocal(): Promise<void> {
    this.revision += 1;
    this.access = null;
    this.expiresAt = 0;
    try { await this.storage.clearRefresh(); }
    catch { throw new MobileApiError('invalid_response', 0, 'secure_storage_unavailable'); }
  }
}
