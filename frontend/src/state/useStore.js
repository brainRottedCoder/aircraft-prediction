import { useSyncExternalStore } from 'react';
import { app, subscribe, getVersion } from './store.js';

// Re-renders the calling component whenever the store emits. Returns the shared state object.
export function useStore() {
  useSyncExternalStore(subscribe, getVersion);
  return app;
}
