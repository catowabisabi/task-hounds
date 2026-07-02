export const DEFAULT_SERVER_URL = '';

const DATABASE_NAME = 'task-hounds-mobile';
const STORE_NAME = 'settings';
const SERVER_URL_KEY = 'serverUrl';

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function normalizeServerUrl(value: string): string {
  const withProtocol = /^https?:\/\//i.test(value.trim())
    ? value.trim()
    : `https://${value.trim()}`;
  return withProtocol.replace(/\/+$/, '');
}

export async function getServerUrl(): Promise<string> {
  const database = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = database
      .transaction(STORE_NAME, 'readonly')
      .objectStore(STORE_NAME)
      .get(SERVER_URL_KEY);
    request.onsuccess = () => {
      database.close();
      resolve(
        typeof request.result === 'string'
          ? normalizeServerUrl(request.result)
          : DEFAULT_SERVER_URL,
      );
    };
    request.onerror = () => {
      database.close();
      reject(request.error);
    };
  });
}

export async function saveServerUrl(value: string): Promise<string> {
  const normalized = normalizeServerUrl(value);
  const database = await openDatabase();
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readwrite');
    transaction.objectStore(STORE_NAME).put(normalized, SERVER_URL_KEY);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  database.close();
  return normalized;
}
