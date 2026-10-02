import { AppState, type AppStateStatus } from 'react-native';

/** Minimal application-lifecycle boundary used by screens that pause work in the background. */
export interface AppLifecycleAdapter {
  currentState(): AppStateStatus | null;
  subscribe(listener: (state: AppStateStatus) => void): () => void;
}

/** Native implementation; importing it does not subscribe or start any background work. */
export const nativeAppLifecycle: AppLifecycleAdapter = {
  currentState: () => AppState.currentState,
  subscribe: listener => {
    const subscription = AppState.addEventListener('change', listener);
    return () => subscription.remove();
  },
};
