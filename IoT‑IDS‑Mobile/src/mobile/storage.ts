import * as SecureStore from 'expo-secure-store';
import * as Crypto from 'expo-crypto';

export const REFRESH_KEY = 'iot_ids_mobile_refresh_v1';
export const CLIENT_ID_KEY = 'iot_ids_mobile_client_instance_v1';

// Serialize every SecureStore operation, including clear and rotation.
let tail: Promise<unknown> = Promise.resolve();
function serial<T>(operation: () => Promise<T>): Promise<T> {
  const next = tail.then(operation, operation);
  tail = next.then(() => undefined, () => undefined);
  return next;
}

export const secureStorage = {
  getRefresh: () => serial(() => SecureStore.getItemAsync(REFRESH_KEY)),
  setRefresh: (value: string) => serial(() => SecureStore.setItemAsync(REFRESH_KEY, value)),
  clearRefresh: () => serial(() => SecureStore.deleteItemAsync(REFRESH_KEY)),
  async clientId(): Promise<string> {
    return serial(async () => {
      const existing = await SecureStore.getItemAsync(CLIENT_ID_KEY);
      if (existing) return existing;
      const generated = `client-${Crypto.randomUUID()}`;
      await SecureStore.setItemAsync(CLIENT_ID_KEY, generated);
      return generated;
    });
  },
  clearClientId: () => serial(() => SecureStore.deleteItemAsync(CLIENT_ID_KEY)),
};
