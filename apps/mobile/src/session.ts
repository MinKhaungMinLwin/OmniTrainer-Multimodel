import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const TOKEN_KEY = "omni.access-token";
const TENANT_KEY = "omni.tenant-id";

async function read(key: string): Promise<string | null> {
  if (Platform.OS === "web")
    return globalThis.localStorage?.getItem(key) ?? null;
  return SecureStore.getItemAsync(key);
}

async function write(key: string, value: string | null): Promise<void> {
  if (Platform.OS === "web") {
    if (value === null) globalThis.localStorage?.removeItem(key);
    else globalThis.localStorage?.setItem(key, value);
    return;
  }
  if (value === null) await SecureStore.deleteItemAsync(key);
  else await SecureStore.setItemAsync(key, value);
}

export const sessionStore = {
  token: () => read(TOKEN_KEY),
  tenantId: () => read(TENANT_KEY),
  setToken: (value: string | null) => write(TOKEN_KEY, value),
  setTenantId: (value: string | null) => write(TENANT_KEY, value),
};
