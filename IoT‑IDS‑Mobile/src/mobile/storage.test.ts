import * as SecureStore from 'expo-secure-store';
import * as Crypto from 'expo-crypto';
import { CLIENT_ID_KEY, REFRESH_KEY, secureStorage } from './storage';

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(), setItemAsync: jest.fn(), deleteItemAsync: jest.fn(),
}));
jest.mock('expo-crypto', () => ({ randomUUID: jest.fn() }));

it('generates a random, stable client ID and stores it only in SecureStore', async () => {
  const items = new Map<string, string>();
  (SecureStore.getItemAsync as jest.Mock).mockImplementation(async (key: string) => items.get(key) ?? null);
  (SecureStore.setItemAsync as jest.Mock).mockImplementation(async (key: string, value: string) => { items.set(key, value); });
  (Crypto.randomUUID as jest.Mock).mockReturnValue('123e4567-e89b-42d3-a456-426614174000');
  const first = await secureStorage.clientId();
  const second = await secureStorage.clientId();
  expect(first).toBe('client-123e4567-e89b-42d3-a456-426614174000');
  expect(second).toBe(first);
  expect(Crypto.randomUUID).toHaveBeenCalledTimes(1);
  expect(items.get(CLIENT_ID_KEY)).toBe(first);
  await secureStorage.setRefresh('secret-value');
  expect(items.get(REFRESH_KEY)).toBe('secret-value');
  expect(SecureStore.setItemAsync).toHaveBeenCalledWith(REFRESH_KEY, 'secret-value');
});
